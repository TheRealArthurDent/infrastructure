"""The restic-restore helper restores a stack's items from a restic snapshot (#287, core of #286).

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
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
HELPER = ANSIBLE_DIR / "roles" / "stack" / "files" / "restic-restore"
STACK_DEFAULTS = ANSIBLE_DIR / "roles" / "stack" / "defaults" / "main.yaml"
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
        self.marker = self.state / "infrastructure.done"
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
    assert not list(env.staging.iterdir()), "staging dirs are removed after the restore"


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

    assert code == 3, "Ansible tells the user how to skip the restore on exit code 3"
    assert "no snapshot" in err
    assert not env.marker.exists()


def test_clean_slate_without_repository_access_fails_with_the_init_hint(env):
    shutil.rmtree(env.repo)

    code, _, err = clean_slate(env)

    assert code == 2, "Ansible points the user to init-restic.sh on exit code 2"
    assert not env.marker.exists()


def test_clean_slate_fails_if_an_item_is_missing_from_the_snapshot(env):
    env.write(env.source, "portainer/data/portainer.db", "portainer")
    env.backup()

    code, _, err = clean_slate(env)

    assert code != 0
    assert "not in the snapshot" in err
    assert not env.marker.exists()
    assert not (env.host / "portainer").exists(), "nothing is applied before every item is staged"


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
