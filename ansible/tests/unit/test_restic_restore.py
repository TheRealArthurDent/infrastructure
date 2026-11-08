"""The restic-restore helper restores a stack's items from a restic snapshot (raspberry#287, core of raspberry#286).

resticprofile backs up the whole host, so the helper is a host service: every stack on the host lists its items
in /etc/restic-restore/stacks/<stack>.json and gets its own clean-slate marker.

The tests run the helper against a real local restic repository. Instead of the resticprofile container, the
runners in the config call restic directly, and the snapshot is taken of a directory standing in for /source.
A fake docker on PATH records which containers the helper stops and starts.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
HELPER = ANSIBLE_DIR / "roles" / "stack" / "files" / "restic-restore"
STACK_DEFAULTS = ANSIBLE_DIR / "roles" / "stack" / "defaults" / "main.yaml"
RSNAPSHOT_CONF = ANSIBLE_DIR / "roles" / "base" / "files" / "etc" / "rsnapshot.conf"
COMPOSE = ANSIBLE_DIR / "roles" / "stack" / "templates" / "docker-compose.yaml.j2"

pytestmark = pytest.mark.skipif(
    not (shutil.which("restic") and shutil.which("rsync")), reason="needs restic and rsync (CI installs them)"
)

FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  inspect) case " $FAKE_DOCKER_RUNNING " in *" $4 "*) echo true ;; *) echo false ;; esac ;;
esac
"""


def load_helper():
    loader = importlib.machinery.SourceFileLoader("restic_restore", str(HELPER))
    spec = importlib.util.spec_from_loader("restic_restore", loader)
    module = importlib.util.module_from_spec(spec)
    # No __pycache__ next to the helper in the role's files/
    dont_write_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = dont_write_bytecode
    return module


class Env:
    """A host tree, a snapshot source tree, a restic repository and a helper config in tmp_path."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.host = tmp_path / "host"          # stands in for the host's /etc
        self.source = tmp_path / "source"      # stands in for /source/etc in the resticprofile container
        self.repo = tmp_path / "repo"
        self.staging = tmp_path / "staging"
        self.state = tmp_path / "state"
        self.rsnapshot = tmp_path / "rsnapshot"  # stands in for /var/cache/backup
        self.marker = self.state / "infrastructure.done"
        self.drill_metrics = tmp_path / "metrics" / "restore_drill.prom"
        self.password = tmp_path / "password"
        self.password.write_text("test\n")
        self.docker_log = tmp_path / "docker.log"
        self.docker_log.touch()
        restic = ["restic", "-r", str(self.repo), "--password-file", str(self.password), "--quiet"]
        items = {
                "portainer": {
                    "paths": [str(self.host / "portainer" / "data")],
                    "containers": ["portainer"],
                    "preserve": [],
                },
                "pihole": {
                    "paths": [str(self.host / "pihole" / "pihole")],
                    "containers": ["pihole"],
                    "preserve": ["pihole-FTL.db*"],
                },
        }
        stacks = tmp_path / "stacks"
        stacks.mkdir()
        (stacks / "infrastructure.json").write_text(json.dumps({"items": items}))
        self.config = {
            "stacks_dir": str(stacks),
            "state_dir": str(self.state),
            "source_mounts": {str(self.host): str(self.source)},
            "staging": {"host": str(self.staging), "restic": str(self.staging)},
            "runners": {"exec": restic, "oneoff": restic},
            "rsnapshot": {"root": str(self.rsnapshot), "host": "localhost"},
            "drill": {"metrics": str(tmp_path / "metrics" / "restore_drill.prom"), "parts": 12},
        }
        self.config_path = tmp_path / "config.json"
        self.config_path.write_text(json.dumps(self.config))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(FAKE_DOCKER)
        (bin_dir / "docker").chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_DOCKER_LOG", str(self.docker_log))
        monkeypatch.setenv("FAKE_DOCKER_RUNNING", "")
        subprocess.run([*restic, "init"], check=True, capture_output=True)

    def write(self, root, rel, content):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def rsnapshot_write(self, snapshot, rel, content, age_hours=0):
        """Writes a file into an rsnapshot snapshot, which keeps the host's paths below <root>/<snapshot>/<host>."""
        path = self.write(self.rsnapshot / snapshot / "localhost" / str(self.host).lstrip("/"), rel, content)
        mtime = time.time() - age_hours * 3600
        os.utime(self.rsnapshot / snapshot, (mtime, mtime))
        return path

    def backup(self):
        subprocess.run(
            ["restic", "-r", str(self.repo), "--password-file", str(self.password), "--quiet", "backup",
             str(self.source)],
            check=True, capture_output=True,
        )
        out = subprocess.run(
            ["restic", "-r", str(self.repo), "--password-file", str(self.password), "snapshots", "--json",
             "latest"],
            check=True, capture_output=True, text=True,
        ).stdout
        return json.loads(out)[0]["id"]

    def run(self, *args):
        """Runs the helper as a program; returns (exit code, stdout, stderr)."""
        result = subprocess.run(
            [sys.executable, str(HELPER), "--config", str(self.config_path), *args],
            capture_output=True, text=True,
        )
        return result.returncode, result.stdout, result.stderr

    def docker_calls(self):
        return self.docker_log.read_text().splitlines()


def staging_dirs(env):
    return list(env.staging.iterdir()) if env.staging.exists() else []


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


# Path translation


def test_snapshot_path_uses_the_longest_matching_mount():
    helper = load_helper()
    mounts = {"/etc": "/source/etc", "/var/lib/docker/volumes": "/source/docker-volumes", "/": "/x"}
    assert helper.snapshot_path("/etc/docker/a", mounts) == "/source/etc/docker/a"
    assert helper.snapshot_path("/var/lib/docker/volumes/v/_data", mounts) == "/source/docker-volumes/v/_data"


def test_snapshot_path_rejects_a_path_outside_the_mounts():
    helper = load_helper()
    with pytest.raises(helper.RestoreError, match="not in the backup"):
        helper.snapshot_path("/opt/x", {"/etc": "/source/etc"})


def test_snapshot_path_does_not_match_a_sibling_with_the_same_prefix():
    helper = load_helper()
    with pytest.raises(helper.RestoreError):
        helper.snapshot_path("/etcetera/x", {"/etc": "/source/etc"})


def test_default_source_mounts_match_the_resticprofile_container():
    defaults = yaml.safe_load(STACK_DEFAULTS.read_text())
    service = yaml.safe_load(COMPOSE.read_text())["services"]["resticprofile"]
    mounts = {}
    for volume in service["volumes"]:
        host, container = volume.split(":")[:2]
        if container.startswith("/source/"):
            mounts[host] = container
    assert defaults["stack_restore_source_mounts"] == mounts


def test_default_staging_matches_the_restore_target_mount():
    defaults = yaml.safe_load(STACK_DEFAULTS.read_text())
    service = yaml.safe_load(COMPOSE.read_text())["services"]["resticprofile"]
    staging = defaults["stack_restore_staging"]
    assert f"{staging['host']}:{staging['restic']}:rw" in service["volumes"]


def test_resticprofile_schedules_backups_only_after_every_stack_is_restored():
    defaults = yaml.safe_load(STACK_DEFAULTS.read_text())
    service = yaml.safe_load(COMPOSE.read_text())["services"]["resticprofile"]
    mounts = dict(v.split(":")[:2] for v in service["volumes"])
    assert mounts[defaults["stack_restore_state_dir"]] == "/restore-state", "the marker directory is mounted"
    assert defaults["stack_backup_stacks"] == ["{{ stack_project_name }}"]
    script = service["command"][-1]
    loop = script.index("for stack in {{ stack_backup_stacks | join(' ') }}; do")
    wait = script.index("while [ ! -f /restore-state/$$stack.done ]")
    assert loop < wait < script.index("resticprofile schedule")


def test_stage_copes_with_resticprofile_output_around_the_json(env):
    # resticprofile prints its own lines on stdout, before and after restic's JSON (seen on testbed)
    noisy = env.tmp / "bin" / "noisy-restic"
    noisy.write_text('#!/bin/sh\necho "[INFO] profile default"\n"$@"\nrc=$?\necho "[INFO] done"\nexit $rc\n')
    noisy.chmod(0o755)
    env.config["runners"]["exec"] = [str(noisy), *env.config["runners"]["exec"]]
    env.config_path.write_text(json.dumps(env.config))
    env.write(env.source, "portainer/data/portainer.db", "from backup")
    snapshot = env.backup()

    code, out, err = env.run("stage", "infrastructure/portainer")

    assert code == 0, err
    staging = Path(out.strip().splitlines()[-1])
    assert json.loads((staging / ".restic-restore.json").read_text())["snapshot"] == snapshot


# stage


def test_stage_restores_the_item_into_a_new_staging_dir(env):
    env.write(env.source, "portainer/data/portainer.db", "from backup")
    snapshot = env.backup()

    code, out, err = env.run("stage", "infrastructure/portainer")

    assert code == 0, err
    staging = Path(out.strip())
    assert staging.parent == env.staging
    restored = staging / str(env.source).lstrip("/") / "portainer" / "data" / "portainer.db"
    assert restored.read_text() == "from backup"
    meta = json.loads((staging / ".restic-restore.json").read_text())
    assert meta == {"stack": "infrastructure", "item": "portainer", "snapshot": snapshot}


def test_stage_only_restores_the_item(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    env.backup()

    code, out, err = env.run("stage", "infrastructure/portainer")

    assert code == 0, err
    assert not list(Path(out.strip()).rglob("gravity.db"))


def test_stage_takes_the_given_snapshot(env):
    env.write(env.source, "portainer/data/portainer.db", "old")
    old = env.backup()
    env.write(env.source, "portainer/data/portainer.db", "new")
    env.backup()

    code, out, err = env.run("stage", "infrastructure/portainer", "--snapshot", old[:8])

    assert code == 0, err
    assert next(Path(out.strip()).rglob("portainer.db")).read_text() == "old"
    assert json.loads((Path(out.strip()) / ".restic-restore.json").read_text())["snapshot"] == old


def test_stage_rejects_an_unknown_item(env):
    code, _, err = env.run("stage", "infrastructure/nextcloud")
    assert code != 0
    assert "unknown item nextcloud" in err


def test_stage_rejects_an_unknown_stack(env):
    code, _, err = env.run("stage", "nextcloud/data")
    assert code != 0
    assert "unknown stack nextcloud" in err


def test_usage_errors_do_not_look_like_a_missing_repository(env):
    # argparse exits with 2; Ansible must not answer that with the init-restic.sh hint
    code, _, _ = env.run("clean-slate", "--marker", "/x")
    assert code == 2
    assert code not in (10, 11)


def test_stage_needs_stack_and_item(env):
    code, _, err = env.run("stage", "portainer")
    assert code != 0
    assert "STACK/ITEM" in err


# apply


def test_apply_mirrors_the_staged_data_and_keeps_preserved_files(env):
    env.write(env.source, "pihole/pihole/gravity.db", "from backup")
    env.backup()
    env.write(env.host, "pihole/pihole/gravity.db", "changed")
    env.write(env.host, "pihole/pihole/stray.conf", "not in the backup")
    env.write(env.host, "pihole/pihole/pihole-FTL.db", "excluded from the backup")
    _, out, _ = env.run("stage", "infrastructure/pihole")

    code, _, err = env.run("apply", out.strip())

    assert code == 0, err
    live = env.host / "pihole" / "pihole"
    assert (live / "gravity.db").read_text() == "from backup"
    assert not (live / "stray.conf").exists()
    assert (live / "pihole-FTL.db").read_text() == "excluded from the backup"


def test_apply_creates_a_missing_target(env):
    env.write(env.source, "portainer/data/portainer.db", "from backup")
    env.backup()
    _, out, _ = env.run("stage", "infrastructure/portainer")

    code, _, err = env.run("apply", out.strip())

    assert code == 0, err
    assert (env.host / "portainer" / "data" / "portainer.db").read_text() == "from backup"


def test_apply_stops_and_starts_only_running_containers(env, monkeypatch):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.backup()
    _, out, _ = env.run("stage", "infrastructure/portainer")
    monkeypatch.setenv("FAKE_DOCKER_RUNNING", "portainer")

    code, _, err = env.run("apply", out.strip())

    assert code == 0, err
    calls = env.docker_calls()
    assert "stop portainer" in calls
    assert calls[-1] == "start portainer"


def test_apply_leaves_stopped_containers_stopped(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.backup()
    _, out, _ = env.run("stage", "infrastructure/portainer")

    code, _, err = env.run("apply", out.strip())

    assert code == 0, err
    assert not [c for c in env.docker_calls() if c.startswith(("stop", "start"))]


def test_apply_without_containers_does_not_call_docker(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.backup()
    _, out, _ = env.run("stage", "infrastructure/portainer")

    code, _, err = env.run("apply", out.strip(), "--no-containers")

    assert code == 0, err
    assert env.docker_calls() == []


def test_apply_refuses_a_path_missing_from_the_snapshot(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.backup()
    _, out, _ = env.run("stage", "infrastructure/portainer")
    shutil.rmtree(next(Path(out.strip()).rglob("data")))
    env.write(env.host, "portainer/data/portainer.db", "live")

    code, _, err = env.run("apply", out.strip())

    assert code != 0
    assert "not in the snapshot" in err
    assert (env.host / "portainer" / "data" / "portainer.db").read_text() == "live"


def test_apply_refuses_a_dir_that_is_not_a_staging_dir(env, tmp_path):
    code, _, err = env.run("apply", str(tmp_path))
    assert code != 0
    assert "not a staging directory" in err


# clean-slate


def clean_slate(env, *args):
    code, out, err = env.run("--runner", "oneoff", "clean-slate", "infrastructure", *args)
    return code, (json.loads(out) if code == 0 else None), err


def test_clean_slate_restores_every_item_and_writes_the_marker(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    snapshot = env.backup()

    code, result, err = clean_slate(env)

    assert code == 0, err
    assert result["changed"] is True
    assert (env.host / "portainer" / "data" / "portainer.db").read_text() == "portainer"
    assert (env.host / "pihole" / "pihole" / "gravity.db").read_text() == "pihole"
    assert env.marker.read_text().strip() == snapshot
    assert env.docker_calls() == []
    assert not staging_dirs(env), "staging dirs are removed after the restore"


def test_clean_slate_markers_are_per_stack(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    env.backup()
    env.state.mkdir()
    (env.state / "smart-home.done").write_text("abc\n")

    code, result, err = clean_slate(env)

    assert code == 0, err
    assert result["changed"] is True
    assert env.marker.exists()


def test_clean_slate_does_nothing_once_the_marker_exists(env):
    env.marker.parent.mkdir()
    env.marker.write_text("abc\n")

    code, result, err = clean_slate(env)

    assert code == 0, err
    assert result["changed"] is False
    assert not (env.host / "portainer").exists()


def test_clean_slate_skip_writes_the_marker_without_restoring(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.backup()

    code, result, err = clean_slate(env, "--skip")

    assert code == 0, err
    assert result["changed"] is True
    assert env.marker.read_text().startswith("skipped: --skip")
    assert not (env.host / "portainer").exists()


def test_clean_slate_skips_a_host_with_existing_data(env):
    env.write(env.source, "portainer/data/portainer.db", "from backup")
    env.backup()
    env.write(env.host, "portainer/data/portainer.db", "live")
    (env.host / "pihole" / "pihole").mkdir(parents=True)

    code, result, err = clean_slate(env)

    assert code == 0, err
    assert result["changed"] is True
    assert env.marker.read_text().startswith("skipped: existing data")
    assert (env.host / "portainer" / "data" / "portainer.db").read_text() == "live"


def test_clean_slate_treats_empty_item_dirs_as_fresh(env):
    env.write(env.source, "portainer/data/portainer.db", "from backup")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    snapshot = env.backup()
    (env.host / "portainer" / "data").mkdir(parents=True)

    code, _, err = clean_slate(env)

    assert code == 0, err
    assert env.marker.read_text().strip() == snapshot


def test_clean_slate_without_a_snapshot_fails_with_the_skip_hint(env):
    code, _, err = clean_slate(env)

    assert code == 11, "Ansible tells the user how to skip the restore on exit code 11"
    assert "no snapshot" in err
    assert not env.marker.exists()


def test_clean_slate_without_repository_access_fails_with_the_init_hint(env):
    shutil.rmtree(env.repo)

    code, _, err = clean_slate(env)

    assert code == 10, "Ansible points the user to init-restic.sh on exit code 10"
    assert not env.marker.exists()


def test_clean_slate_fails_if_an_item_is_missing_from_the_snapshot(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.backup()

    code, _, err = clean_slate(env)

    assert code != 0
    assert "not in the snapshot" in err
    assert not env.marker.exists()
    assert not (env.host / "portainer").exists(), "nothing is applied before every item is staged"
    assert not staging_dirs(env), "the staged items are removed again"


def test_clean_slate_finishes_an_interrupted_restore(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    snapshot = env.backup()
    # A run that died after restoring portainer: the data is there, the marker isn't
    env.write(env.host, "portainer/data/portainer.db", "portainer")
    env.marker.parent.mkdir()
    Path(f"{env.marker}.restoring").write_text(f"{snapshot}\n")

    code, _, err = clean_slate(env)

    assert code == 0, err
    assert (env.host / "pihole" / "pihole" / "gravity.db").read_text() == "pihole"
    assert env.marker.read_text().strip() == snapshot
    assert not Path(f"{env.marker}.restoring").exists()


def test_clean_slate_dry_run_changes_nothing(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.backup()

    code, result, err = clean_slate(env, "--dry-run")

    assert code == 0, err
    assert result["changed"] is True
    assert "portainer" in result["msg"]
    assert not env.marker.exists()
    assert not (env.host / "portainer").exists()


def test_clean_slate_takes_the_given_snapshot(env):
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    env.write(env.source, "portainer/data/portainer.db", "old")
    old = env.backup()
    env.write(env.source, "portainer/data/portainer.db", "new")
    env.backup()

    code, _, err = clean_slate(env, "--snapshot", old)

    assert code == 0, err
    assert (env.host / "portainer" / "data" / "portainer.db").read_text() == "old"
    assert env.marker.read_text().strip() == old


# snapshots (raspberry#286)


def test_snapshots_lists_every_snapshot_with_host_paths(env):
    env.write(env.source, "portainer/data/portainer.db", "old")
    old = env.backup()
    env.write(env.source, "portainer/data/portainer.db", "new")
    new = env.backup()

    code, out, err = env.run("snapshots")

    assert code == 0, err
    lines = out.strip().splitlines()
    assert [line.split()[2] for line in lines] == [old[:8], new[:8]]
    assert str(env.host) in lines[0]
    assert str(env.source) not in out


def test_snapshots_copes_with_resticprofile_output_around_the_json(env):
    noisy = env.tmp / "bin" / "noisy-restic"
    noisy.write_text('#!/bin/sh\necho "[INFO] profile default"\n"$@"\nrc=$?\necho "[INFO] done"\nexit $rc\n')
    noisy.chmod(0o755)
    env.config["runners"]["exec"] = [str(noisy), *env.config["runners"]["exec"]]
    env.config_path.write_text(json.dumps(env.config))
    env.write(env.source, "portainer/data/portainer.db", "x")
    snapshot = env.backup()

    code, out, err = env.run("snapshots")

    assert code == 0, err
    assert snapshot[:8] in out
    assert "[INFO]" not in out


def test_snapshots_from_rsnapshot_lists_the_newest_first(env):
    env.rsnapshot_write("weekly.0", "portainer/data/portainer.db", "weekly", age_hours=72)
    env.rsnapshot_write("daily.0", "portainer/data/portainer.db", "daily", age_hours=1)
    env.rsnapshot_write("daily.1", "portainer/data/portainer.db", "daily", age_hours=25)
    (env.rsnapshot / "not-a-snapshot").mkdir()

    code, out, err = env.run("snapshots", "--from", "rsnapshot")

    assert code == 0, err
    assert [line.split()[-1] for line in out.strip().splitlines()] == ["daily.0", "daily.1", "weekly.0"]


# ls and find (raspberry#286)


def test_ls_lists_a_host_path_in_the_snapshot(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.write(env.source, "portainer/data/certs/cert.pem", "x")
    env.write(env.source, "pihole/pihole/gravity.db", "x")
    env.backup()

    code, out, err = env.run("ls", str(env.host / "portainer" / "data"))

    assert code == 0, err
    data = env.host / "portainer" / "data"
    paths = [line.split()[-1] for line in out.strip().splitlines()]
    assert paths == [str(data), str(data / "certs"), str(data / "portainer.db")]


def test_ls_takes_the_given_snapshot(env):
    env.write(env.source, "portainer/data/old.db", "x")
    old = env.backup()
    (env.source / "portainer" / "data" / "old.db").unlink()
    env.write(env.source, "portainer/data/new.db", "x")
    env.backup()

    code, out, err = env.run("ls", str(env.host / "portainer" / "data"), "--snapshot", old[:8])

    assert code == 0, err
    assert "old.db" in out
    assert "new.db" not in out


def test_ls_rejects_a_path_outside_the_backup(env):
    code, _, err = env.run("ls", "/opt/somewhere")
    assert code != 0
    assert "not in the backup" in err


def test_find_prints_host_paths_of_the_matches(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.write(env.source, "pihole/pihole/gravity.db", "x")
    env.backup()

    code, out, err = env.run("find", "*.db")

    assert code == 0, err
    assert sorted(out.strip().splitlines()) == [
        str(env.host / "pihole" / "pihole" / "gravity.db"),
        str(env.host / "portainer" / "data" / "portainer.db"),
    ]


def test_find_searches_the_latest_snapshot_by_default(env):
    env.write(env.source, "portainer/data/old.db", "x")
    old = env.backup()
    (env.source / "portainer" / "data" / "old.db").unlink()
    env.write(env.source, "portainer/data/new.db", "x")
    env.backup()

    _, latest, _ = env.run("find", "old.db")
    code, given, err = env.run("find", "old.db", "--snapshot", old[:8])

    assert code == 0, err
    assert latest.strip() == ""
    assert given.strip() == str(env.host / "portainer" / "data" / "old.db")


# stage from rsnapshot and of a host path (raspberry#286)


def test_stage_from_rsnapshot_takes_the_newest_local_copy(env):
    env.rsnapshot_write("daily.1", "portainer/data/portainer.db", "older", age_hours=25)
    env.rsnapshot_write("daily.0", "portainer/data/portainer.db", "newest", age_hours=1)

    code, out, err = env.run("stage", "infrastructure/portainer", "--from", "rsnapshot")

    assert code == 0, err
    staging = Path(out.strip())
    assert next(staging.rglob("portainer.db")).read_text() == "newest"
    meta = json.loads((staging / ".restic-restore.json").read_text())
    assert meta == {"stack": "infrastructure", "item": "portainer", "snapshot": "rsnapshot:daily.0"}


def test_stage_from_rsnapshot_takes_the_given_snapshot(env):
    env.rsnapshot_write("daily.1", "portainer/data/portainer.db", "older", age_hours=25)
    env.rsnapshot_write("daily.0", "portainer/data/portainer.db", "newest", age_hours=1)

    code, out, err = env.run("stage", "infrastructure/portainer", "--from", "rsnapshot", "--snapshot", "daily.1")

    assert code == 0, err
    assert next(Path(out.strip()).rglob("portainer.db")).read_text() == "older"


def test_stage_from_rsnapshot_can_be_applied(env):
    env.rsnapshot_write("daily.0", "pihole/pihole/gravity.db", "from rsnapshot")
    env.write(env.host, "pihole/pihole/gravity.db", "changed")
    env.write(env.host, "pihole/pihole/pihole-FTL.db", "live")
    _, out, _ = env.run("stage", "infrastructure/pihole", "--from", "rsnapshot")

    code, _, err = env.run("apply", out.strip())

    assert code == 0, err
    assert (env.host / "pihole" / "pihole" / "gravity.db").read_text() == "from rsnapshot"
    assert (env.host / "pihole" / "pihole" / "pihole-FTL.db").read_text() == "live"


def test_stage_from_rsnapshot_falls_back_to_restic_without_a_local_copy(env):
    env.write(env.source, "portainer/data/portainer.db", "from restic")
    snapshot = env.backup()

    code, out, err = env.run("stage", "infrastructure/portainer", "--from", "rsnapshot")

    assert code == 0, err
    assert "falling back to restic" in err
    staging = Path(out.strip())
    assert next(staging.rglob("portainer.db")).read_text() == "from restic"
    assert json.loads((staging / ".restic-restore.json").read_text())["snapshot"] == snapshot


def test_stage_from_rsnapshot_does_not_fall_back_for_a_named_snapshot(env):
    env.write(env.source, "portainer/data/portainer.db", "from restic")
    env.backup()

    code, _, err = env.run("stage", "infrastructure/portainer", "--from", "rsnapshot", "--snapshot", "daily.3")

    assert code != 0
    assert "daily.3" in err
    assert not staging_dirs(env)


def test_stage_of_a_host_path_restores_only_that_path(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.write(env.source, "portainer/data/certs/cert.pem", "cert")
    env.backup()
    wanted = env.host / "portainer" / "data" / "certs"

    code, out, err = env.run("stage", str(wanted))

    assert code == 0, err
    staging = Path(out.strip())
    assert next(staging.rglob("cert.pem")).read_text() == "cert"
    assert not list(staging.rglob("portainer.db"))
    assert json.loads((staging / ".restic-restore.json").read_text())["paths"] == [str(wanted)]


def test_stage_of_a_host_path_missing_from_the_snapshot_fails_and_leaves_nothing(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.backup()

    code, _, err = env.run("stage", str(env.host / "nothing" / "here"))

    assert code != 0
    assert "not in the snapshot" in err
    assert not staging_dirs(env)


def test_apply_refuses_a_staged_host_path(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.backup()
    _, out, _ = env.run("stage", str(env.host / "portainer" / "data"))

    code, _, err = env.run("apply", out.strip())

    assert code != 0
    assert "copy what you need" in err
    assert not (env.host / "portainer").exists()


# diff (raspberry#286)


def test_diff_shows_what_apply_would_change_and_changes_nothing(env):
    env.write(env.source, "pihole/pihole/gravity.db", "from backup")
    env.write(env.source, "pihole/pihole/custom.list", "same")
    env.backup()
    env.write(env.host, "pihole/pihole/gravity.db", "changed!")
    env.write(env.host, "pihole/pihole/custom.list", "same")
    env.write(env.host, "pihole/pihole/stray.conf", "not in the backup")
    env.write(env.host, "pihole/pihole/pihole-FTL.db", "preserved")
    _, out, _ = env.run("stage", "infrastructure/pihole")

    code, diff, err = env.run("diff", out.strip())

    assert code == 0, err
    assert str(env.host / "pihole" / "pihole") in diff
    assert "gravity.db" in diff
    assert "deleting" in diff and "stray.conf" in diff
    assert "custom.list" not in diff
    assert "pihole-FTL.db" not in diff
    assert (env.host / "pihole" / "pihole" / "gravity.db").read_text() == "changed!"
    assert (env.host / "pihole" / "pihole" / "stray.conf").exists()


def test_diff_reports_no_changes(env):
    env.write(env.source, "portainer/data/portainer.db", "same")
    env.backup()
    _, out, _ = env.run("stage", "infrastructure/portainer")
    env.run("apply", out.strip())

    code, diff, err = env.run("diff", out.strip())

    assert code == 0, err
    assert "no changes" in diff


def test_diff_works_on_a_staged_host_path(env):
    env.write(env.source, "portainer/data/certs/cert.pem", "from backup")
    env.backup()
    env.write(env.host, "portainer/data/certs/cert.pem", "live!")
    _, out, _ = env.run("stage", str(env.host / "portainer" / "data" / "certs"))

    code, diff, err = env.run("diff", out.strip())

    assert code == 0, err
    assert "cert.pem" in diff


def test_diff_of_a_missing_live_path_shows_everything_as_new(env):
    env.write(env.source, "portainer/data/portainer.db", "x")
    env.backup()
    _, out, _ = env.run("stage", "infrastructure/portainer")

    code, diff, err = env.run("diff", out.strip())

    assert code == 0, err
    assert "portainer.db" in diff
    assert not (env.host / "portainer").exists()


# clean (raspberry#286)


def make_staging_dir(env, name, age_days):
    staging = env.staging / name
    (staging / "data").mkdir(parents=True)
    meta = staging / ".restic-restore.json"
    meta.write_text("{}")
    mtime = time.time() - age_days * 86400
    os.utime(meta, (mtime, mtime))
    return staging


def test_clean_removes_staging_dirs_older_than_a_week(env):
    old = make_staging_dir(env, "20261001-000000-infrastructure-portainer", age_days=8)
    recent = make_staging_dir(env, "20261019-000000-infrastructure-portainer", age_days=1)
    other = env.staging / "not-from-the-helper"
    other.mkdir()

    code, out, err = env.run("clean")

    assert code == 0, err
    assert not old.exists()
    assert recent.exists()
    assert other.exists()
    assert str(old) in out


def test_clean_takes_the_given_age(env):
    staging = make_staging_dir(env, "20261019-000000-infrastructure-portainer", age_days=1)

    code, _, err = env.run("clean", "--older-than", "0")

    assert code == 0, err
    assert not staging.exists()


def test_clean_all_removes_every_staging_dir(env):
    staging = make_staging_dir(env, "20261020-000000-infrastructure-portainer", age_days=0)

    code, _, err = env.run("clean", "--all")

    assert code == 0, err
    assert not staging.exists()


def test_clean_without_staging_dir_does_nothing(env):
    code, out, err = env.run("clean")
    assert code == 0, err
    assert out.strip() == ""


# rsnapshot config


def test_default_rsnapshot_config_matches_rsnapshot_conf():
    defaults = yaml.safe_load(STACK_DEFAULTS.read_text())
    conf = [line.split() for line in RSNAPSHOT_CONF.read_text().splitlines() if line and not line.startswith("#")]
    root = next(fields[1] for fields in conf if fields[0] == "snapshot_root")
    backups = {fields[1]: fields[2] for fields in conf if fields[0] == "backup"}
    rsnapshot = defaults["stack_restore_rsnapshot"]
    assert PurePosixPath(root) == PurePosixPath(rsnapshot["root"])
    # Every restic source mount is also in the local copy, below the same destination
    for host in defaults["stack_restore_source_mounts"]:
        assert backups[f"{host}/"] == f"{rsnapshot['host']}/"


# drill (raspberry#288): a periodic check that the backup restores, reported through the resticprofile healthcheck


def drill_metrics(env):
    """The drill's metrics file as {name: value}."""
    return {
        line.split()[0]: float(line.split()[1])
        for line in env.drill_metrics.read_text().splitlines()
        if line and not line.startswith("#")
    }


def test_drill_reads_a_different_part_of_the_data_each_month():
    helper = load_helper()
    assert helper.drill_subset(1, 12) == "1/12"
    assert helper.drill_subset(12, 12) == "12/12"
    # Fewer parts than months: the parts repeat
    assert helper.drill_subset(5, 4) == "1/4"


def test_drill_checks_the_repository_stages_every_item_and_reports_success(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    env.backup()
    before = time.time()

    code, out, err = env.run("drill", "--read-data-subset", "1/1")

    assert code == 0, err
    assert "infrastructure/pihole" in out and "infrastructure/portainer" in out
    metrics = drill_metrics(env)
    assert metrics["restic_restore_drill_success"] == 1
    assert metrics["restic_restore_drill_last_success_timestamp_seconds"] >= int(before)
    assert metrics["restic_restore_drill_last_run_timestamp_seconds"] >= int(before)
    assert not staging_dirs(env), "the drill removes what it staged"
    assert not (env.host / "portainer").exists(), "the drill never puts anything in place"
    assert not env.marker.exists()


def test_drill_covers_the_items_of_every_stack(env):
    other = {"db": {"paths": [str(env.host / "other" / "db")], "containers": ["db"], "preserve": []}}
    (Path(env.config["stacks_dir"]) / "other.json").write_text(json.dumps({"items": other}))
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    env.backup()

    code, _, err = env.run("drill", "--read-data-subset", "1/1")

    assert code != 0, "other/db is missing from the snapshot"
    assert "other" in err
    assert drill_metrics(env)["restic_restore_drill_success"] == 0


def test_drill_fails_if_an_item_is_missing_from_the_snapshot(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.backup()

    code, _, err = env.run("drill", "--read-data-subset", "1/1")

    assert code != 0
    assert "not in the snapshot" in err
    metrics = drill_metrics(env)
    assert metrics["restic_restore_drill_success"] == 0
    assert "restic_restore_drill_last_success_timestamp_seconds" not in metrics, "there never was a success"
    assert not staging_dirs(env)


def test_drill_fails_on_damaged_data_in_the_repository(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    env.backup()
    for pack in (env.repo / "data").rglob("*"):
        if pack.is_file():
            pack.chmod(0o644)
            data = bytearray(pack.read_bytes())
            data[len(data) // 2] ^= 0xFF
            pack.write_bytes(bytes(data))

    code, _, err = env.run("drill", "--read-data-subset", "1/1")

    assert code != 0
    assert "check" in err
    assert drill_metrics(env)["restic_restore_drill_success"] == 0


def test_drill_failure_keeps_the_time_of_the_last_success(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    env.backup()
    assert env.run("drill", "--read-data-subset", "1/1")[0] == 0
    last_success = drill_metrics(env)["restic_restore_drill_last_success_timestamp_seconds"]
    shutil.rmtree(env.repo)

    code, _, _ = env.run("drill", "--read-data-subset", "1/1")

    assert code != 0
    metrics = drill_metrics(env)
    assert metrics["restic_restore_drill_success"] == 0
    assert metrics["restic_restore_drill_last_success_timestamp_seconds"] == last_success


def test_drill_metrics_go_to_the_metrics_mount_of_the_resticprofile_container():
    defaults = yaml.safe_load(STACK_DEFAULTS.read_text())
    service = yaml.safe_load(COMPOSE.read_text())["services"]["resticprofile"]
    mounts = dict(v.split(":")[:2] for v in service["volumes"])
    metrics = PurePosixPath(defaults["stack_restore_drill"]["metrics"])
    assert mounts[str(metrics.parent)] == "/metrics"
    assert f"/metrics/{metrics.name}" in service["healthcheck"]["test"][-1]


class Healthcheck:
    """Runs the resticprofile healthcheck from the compose template against metrics files in a temp dir."""

    def __init__(self, tmp_path):
        service = yaml.safe_load(COMPOSE.read_text())["services"]["resticprofile"]
        # Compose turns $$ into $
        self.script = service["healthcheck"]["test"][-1].replace("$$", "$").replace("/metrics/", f"{tmp_path}/")
        self.dir = tmp_path

    def backup(self, age_hours):
        stamp = int(time.time() - age_hours * 3600)
        (self.dir / "backup_last_success.prom").write_text(
            f'resticprofile_backup_last_success_timestamp_seconds{{profile="default"}} {stamp}\n'
        )

    def drill(self, success, age_days=None):
        now = int(time.time())
        lines = [f"restic_restore_drill_success {success}", f"restic_restore_drill_last_run_timestamp_seconds {now}"]
        if age_days is not None:
            lines.append(f"restic_restore_drill_last_success_timestamp_seconds {int(now - age_days * 86400)}")
        (self.dir / "restore_drill.prom").write_text("\n".join(lines) + "\n")

    def healthy(self):
        return subprocess.run(["sh", "-c", self.script]).returncode == 0


@pytest.fixture
def healthcheck(tmp_path):
    return Healthcheck(tmp_path)


def test_healthcheck_is_healthy_before_the_first_drill(healthcheck):
    healthcheck.backup(age_hours=1)
    assert healthcheck.healthy()


def test_healthcheck_is_healthy_after_a_recent_drill(healthcheck):
    healthcheck.backup(age_hours=1)
    healthcheck.drill(1, age_days=20)
    assert healthcheck.healthy()


def test_healthcheck_is_unhealthy_after_a_failed_drill(healthcheck):
    healthcheck.backup(age_hours=1)
    healthcheck.drill(0, age_days=2)
    assert not healthcheck.healthy()


def test_healthcheck_is_unhealthy_after_a_failed_first_drill(healthcheck):
    healthcheck.backup(age_hours=1)
    healthcheck.drill(0)
    assert not healthcheck.healthy()


def test_healthcheck_is_unhealthy_when_the_last_drill_is_too_old(healthcheck):
    # Monthly drill: one missed run makes it unhealthy
    healthcheck.backup(age_hours=1)
    healthcheck.drill(1, age_days=36)
    assert not healthcheck.healthy()


def test_healthcheck_still_needs_a_recent_backup(healthcheck):
    healthcheck.backup(age_hours=27)
    healthcheck.drill(1, age_days=1)
    assert not healthcheck.healthy()


def test_drill_by_hand_on_a_host_without_the_monthly_drill_records_nothing(env):
    # A recorded success would turn the healthcheck red 35 days later, with no monthly drill to renew it
    env.config["drill"]["enabled"] = False
    env.config_path.write_text(json.dumps(env.config))
    # Recorded while the drill was still enabled
    env.drill_metrics.parent.mkdir(parents=True)
    env.drill_metrics.write_text("restic_restore_drill_success 1\n")
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.write(env.source, "pihole/pihole/gravity.db", "pihole")
    env.backup()

    code, out, err = env.run("drill", "--read-data-subset", "1/1")

    assert code == 0, err
    assert "infrastructure/portainer" in out
    assert "not recorded" in err
    assert not env.drill_metrics.exists()
