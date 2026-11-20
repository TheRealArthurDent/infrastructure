"""Offsite copy of the restic repositories (#60): dungeon copies the repositories on its backup SSD with restic copy
to an sftp target outside the house, applies the local retention there and checks a part of the data every month.

The target's host and user come from KeePass (render.py stands in for them), so the repo only knows the ssh alias.
"""
import re
import stat
import subprocess
import time

import pytest
import yaml

from render import ROLES_DIR, rendered_tree

SOURCES = ["raspi", "dungeon"]
# Each copy has its own time: they share a lock, and a long first copy of raspi made dungeon's give up after 2h
SCHEDULES = {"raspi": "*-*-* 05:00:00", "dungeon": "*-*-* 04:00:00"}
OFFSITE = "/run/secrets/restic_offsite_passphrase"
LOCAL = "/run/secrets/restic_passphrase"
COMPOSE = ROLES_DIR / "resticprofile" / "templates" / "docker-compose.yaml.j2"


@pytest.fixture(scope="module")
def dungeon(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("dungeon"), "dungeon")


@pytest.fixture(scope="module")
def profiles(dungeon):
    return yaml.safe_load(dungeon["resticprofile/config/profiles.yaml"])


@pytest.fixture(scope="module")
def resticprofile(dungeon):
    return yaml.safe_load(dungeon["docker-compose.yaml"])["services"]["resticprofile"]


@pytest.mark.parametrize("source", SOURCES)
def test_copy_profile_copies_the_local_repository_offsite(profiles, source):
    profile = profiles[f"offsite-copy-{source}"]
    assert profile["repository"] == f"local:/backup/restic/{source}"
    # dungeon reads raspi's repository with a key of its own (restic key add) that has dungeon's passphrase
    assert profile["password-file"] == LOCAL
    # A missing source is an error, never a new empty repository
    assert profile.get("initialize", False) is False
    copy = profile["copy"]
    assert copy["repository"] == f"sftp:offsite:restic/{source}"
    assert copy["password-file"] == OFFSITE
    assert copy["initialize"] is True
    # Same chunker parameters as the source, or deduplication across copies doesn't work
    assert copy["initialize-copy-chunker-params"] is True
    assert copy["schedule"] == SCHEDULES[source]


def test_the_copies_start_at_different_times(profiles):
    schedules = [profiles[f"offsite-copy-{source}"]["copy"]["schedule"] for source in SOURCES]
    assert len(set(schedules)) == len(schedules), schedules


@pytest.mark.parametrize("source", SOURCES)
def test_copy_profile_needs_the_backup_ssd(profiles, source):
    run_before = profiles[f"offsite-copy-{source}"]["run-before"]
    assert any("/backup/.backup-ssd" in command and "exit 1" in command for command in run_before), run_before


@pytest.mark.parametrize("source", SOURCES)
def test_copy_is_followed_by_the_retention_and_then_the_success_metric(profiles, source):
    run_after = profiles[f"offsite-copy-{source}"]["copy"]["run-after"]
    forget = next(i for i, c in enumerate(run_after) if f"--name offsite-{source} forget" in c)
    metric = next(i for i, c in enumerate(run_after) if f"/metrics/offsite_{source}_last_success.prom" in c)
    assert forget < metric
    assert f'resticprofile_offsite_last_success_timestamp_seconds{{source=\\"{source}\\"}}' in run_after[metric]


@pytest.mark.parametrize("source", SOURCES)
def test_copy_records_its_start_and_writes_the_offsite_metrics_last(profiles, source):
    copy = profiles[f"offsite-copy-{source}"]["copy"]
    assert any(f"> /tmp/offsite-copy-{source}.start" in c for c in copy["run-before"]), copy["run-before"]
    run_after = copy["run-after"]
    script = next(i for i, c in enumerate(run_after) if "/etc/resticprofile/offsite-metrics.sh" in c)
    success = next(i for i, c in enumerate(run_after) if f"offsite_{source}_last_success.prom" in c)
    # The metrics need the finished retention; a failing restic stats must not cost the success metric
    assert success < script
    assert f"sh /etc/resticprofile/offsite-metrics.sh {source} " in run_after[script]
    assert f"/tmp/offsite-copy-{source}.start" in run_after[script]


def test_copies_run_one_after_the_other(profiles):
    locks = {profiles[f"offsite-copy-{s}"]["lock"] for s in SOURCES}
    assert len(locks) == 1
    assert all(profiles[f"offsite-copy-{s}"]["copy"]["schedule-lock-wait"] for s in SOURCES)


@pytest.mark.parametrize("source", SOURCES)
def test_offsite_repository_keeps_what_the_local_one_keeps(profiles, source):
    profile = profiles[f"offsite-{source}"]
    assert profile["repository"] == f"sftp:offsite:restic/{source}"
    assert profile["password-file"] == OFFSITE
    forget = profile["forget"]
    retention = profiles["default"]["retention"]
    for key in ["keep-within-daily", "keep-within-weekly", "keep-within-monthly", "keep-yearly"]:
        assert forget[key] == retention[key], key
    assert forget["prune"] is True
    # Run by the copy profile only, never on a schedule of its own
    assert "schedule" not in forget


@pytest.mark.parametrize("source", SOURCES)
def test_offsite_repository_is_checked_monthly(profiles, source):
    check = profiles[f"offsite-{source}"]["check"]
    assert check["read-data-subset"].endswith("%")
    assert check["schedule"]
    assert any(f"/metrics/offsite_{source}_check.prom" in c for c in check["run-after"])


def test_ssh_config_reaches_the_offsite_target_with_its_own_key(dungeon):
    config = dungeon["resticprofile/ssh_config"]
    lines = [line.strip() for line in config.splitlines()]
    assert "Host offsite" in lines
    assert "HostName offsite.example.org" in lines
    assert "User offsite-user" in lines
    assert "Port 23" in lines
    assert "IdentityFile /root/.ssh/id_offsite" in lines
    assert "IdentitiesOnly yes" in lines
    # dungeon has no sftp repository of its own: nothing for the backup host
    assert not any(line.startswith("Port 1359") for line in lines)


@pytest.mark.parametrize("url", ["https://offsite.example.org/", "sftp://offsite.example.org:23", "offsite.example.org"])
def test_ssh_config_takes_the_bare_host_from_a_url(tmp_path, url):
    # The KeePass entry's URL field may hold a link to the target rather than the bare host name
    tree = rendered_tree(tmp_path, "dungeon", {"resticprofile_offsite_host": url})
    lines = [line.strip() for line in tree["resticprofile/ssh_config"].splitlines()]
    assert "HostName offsite.example.org" in lines


def test_ssh_config_keeps_the_backup_host_after_the_offsite_block(tmp_path):
    # A host with both: the global options must not land in the offsite Host block, and ssh takes the first value
    tree = rendered_tree(tmp_path, "raspi", {"resticprofile_offsite_sources": ["raspi"]})
    lines = [line.strip() for line in tree["resticprofile/ssh_config"].splitlines() if line.strip()]
    offsite_port = lines.index("Port 23")
    host_all = lines.index("Host *")
    assert lines.index("Host offsite") < offsite_port < host_all < lines.index("Port 1359")
    assert lines.index("IdentityFile /root/.ssh/id_ed25519") > host_all


def test_compose_mounts_ssh_and_the_offsite_passphrase(resticprofile):
    volumes = [str(v) for v in resticprofile["volumes"]]
    assert any(v.endswith(":/root/.ssh") for v in volumes)
    assert any("ssh_config:/etc/ssh/ssh_config:ro" in v for v in volumes)
    assert "restic_offsite_passphrase" in resticprofile["secrets"]
    assert resticprofile["environment"]["RESTICPROFILE_OFFSITE_SOURCES"] == "raspi dungeon"
    # The offsite host resolves through DNS, there's no backup host to pin for a local repository
    assert "extra_hosts" not in resticprofile


def test_compose_declares_the_offsite_secret_file(dungeon):
    secrets = yaml.safe_load(dungeon["docker-compose.yaml"])["secrets"]
    assert secrets["restic_offsite_passphrase"]["file"] == "/etc/docker/infrastructure/secrets/restic_offsite_passphrase"


def test_without_offsite_sources_nothing_changes(tmp_path):
    tree = rendered_tree(tmp_path, "raspi")
    profiles = yaml.safe_load(tree["resticprofile/config/profiles.yaml"])
    assert not [name for name in profiles if name.startswith("offsite")]
    resticprofile = yaml.safe_load(tree["docker-compose.yaml"])["services"]["resticprofile"]
    assert "restic_offsite_passphrase" not in resticprofile["secrets"]
    assert "RESTICPROFILE_OFFSITE_SOURCES" not in resticprofile["environment"]
    assert "Host offsite" not in tree["resticprofile/ssh_config"]


class Healthcheck:
    """Runs the resticprofile healthcheck from the compose template against metrics files in a temp dir."""

    def __init__(self, tmp_path, sources):
        service = yaml.safe_load(COMPOSE.read_text())["services"]["resticprofile"]
        self.script = service["healthcheck"]["test"][-1].replace("$$", "$").replace("/metrics/", f"{tmp_path}/")
        self.dir = tmp_path
        self.sources = sources
        stamp = int(time.time() - 3600)
        (tmp_path / "backup_last_success.prom").write_text(
            f'resticprofile_backup_last_success_timestamp_seconds{{profile="default"}} {stamp}\n'
        )

    def copied(self, source, age_hours):
        stamp = int(time.time() - age_hours * 3600)
        (self.dir / f"offsite_{source}_last_success.prom").write_text(
            f'resticprofile_offsite_last_success_timestamp_seconds{{source="{source}"}} {stamp}\n'
        )

    def checked(self, source, age_days):
        stamp = int(time.time() - age_days * 86400)
        (self.dir / f"offsite_{source}_check.prom").write_text(
            f'resticprofile_offsite_check_last_success_timestamp_seconds{{source="{source}"}} {stamp}\n'
        )

    def healthy(self):
        env = {"PATH": "/usr/bin:/bin", "RESTICPROFILE_OFFSITE_SOURCES": " ".join(self.sources)}
        return subprocess.run(["sh", "-c", self.script], env=env).returncode == 0


def test_healthcheck_is_healthy_after_recent_copies(tmp_path):
    healthcheck = Healthcheck(tmp_path, SOURCES)
    healthcheck.copied("raspi", 2)
    healthcheck.copied("dungeon", 30)
    assert healthcheck.healthy()


def test_healthcheck_is_unhealthy_when_a_copy_is_older_than_36_hours(tmp_path):
    healthcheck = Healthcheck(tmp_path, SOURCES)
    healthcheck.copied("raspi", 2)
    healthcheck.copied("dungeon", 37)
    assert not healthcheck.healthy()


def test_healthcheck_is_unhealthy_before_the_first_copy(tmp_path):
    healthcheck = Healthcheck(tmp_path, SOURCES)
    healthcheck.copied("raspi", 2)
    assert not healthcheck.healthy()


def test_healthcheck_is_unhealthy_when_the_monthly_check_is_overdue(tmp_path):
    healthcheck = Healthcheck(tmp_path, SOURCES)
    healthcheck.copied("raspi", 2)
    healthcheck.copied("dungeon", 2)
    healthcheck.checked("raspi", 10)
    assert healthcheck.healthy()
    healthcheck.checked("dungeon", 36)
    assert not healthcheck.healthy()


def test_healthcheck_ignores_offsite_without_sources(tmp_path):
    assert Healthcheck(tmp_path, []).healthy()


class OffsiteMetrics:
    """Runs the rendered offsite-metrics.sh with stand-ins for restic and ssh on its PATH."""

    STATS = ('{"total_size":5368709120,"total_uncompressed_size":6442450944,"compression_ratio":1.2,'
             '"total_blob_count":1234,"snapshots_count":42}')

    def __init__(self, tmp_path, script):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.metrics = tmp_path / "metrics"
        self.metrics.mkdir()
        self.script = tmp_path / "offsite-metrics.sh"
        self.script.write_text(script)
        self.calls = tmp_path / "calls"
        self.fake("restic", f"echo \"restic $*\" >> {self.calls}; echo '{self.STATS}'")
        self.fake("ssh", "exit 255")

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def run(self, source="raspi", started=None):
        started = int(time.time()) - 600 if started is None else started
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "METRICS_DIR": str(self.metrics)}
        return subprocess.run(["sh", str(self.script), source, f"sftp:offsite:restic/{source}", str(started)],
                              env=env, capture_output=True, text=True)

    def lines(self, name):
        return (self.metrics / name).read_text().splitlines()


@pytest.fixture
def offsite_metrics(tmp_path, dungeon):
    return OffsiteMetrics(tmp_path, dungeon["resticprofile/config/offsite-metrics.sh"])


def test_offsite_metrics_hold_duration_size_and_snapshots(offsite_metrics):
    result = offsite_metrics.run("raspi")
    assert result.returncode == 0, result.stderr
    lines = offsite_metrics.lines("offsite_raspi.prom")
    assert 'resticprofile_offsite_size_bytes{source="raspi"} 5368709120' in lines
    assert 'resticprofile_offsite_snapshots{source="raspi"} 42' in lines
    duration = next(line for line in lines if line.startswith('resticprofile_offsite_duration_seconds{source="raspi"}'))
    assert 599 <= int(duration.split()[-1]) <= 660
    calls = (offsite_metrics.calls).read_text()
    assert "-r sftp:offsite:restic/raspi" in calls
    assert f"--password-file {OFFSITE}" in calls
    assert "stats --mode raw-data --json" in calls
    # No half-written file left behind
    assert sorted(p.name for p in offsite_metrics.metrics.iterdir()) == ["offsite_raspi.prom"]


def test_offsite_metrics_hold_the_targets_disk_usage(offsite_metrics):
    # The provider's shell answers df in 1K blocks
    offsite_metrics.fake("ssh", '[ "$1" = offsite ] && [ "$2" = df ] || exit 1\n'
                                'echo "Filesystem 1K-blocks Used Available Use% Mounted on"\n'
                                'echo "u123456 1073741824 268435456 805306368 25% /home"')
    assert offsite_metrics.run().returncode == 0
    assert offsite_metrics.lines("offsite_disk.prom") == [
        "resticprofile_offsite_disk_size_bytes 1099511627776",
        "resticprofile_offsite_disk_used_bytes 274877906944",
    ]


def test_offsite_metrics_skip_the_disk_when_the_target_has_no_df(offsite_metrics):
    assert offsite_metrics.run().returncode == 0
    assert not (offsite_metrics.metrics / "offsite_disk.prom").exists()


def test_offsite_metrics_fail_when_restic_stats_fails(offsite_metrics):
    offsite_metrics.fake("restic", "echo 'Fatal: unable to open repository' >&2; exit 1")
    assert offsite_metrics.run().returncode != 0
    assert not (offsite_metrics.metrics / "offsite_raspi.prom").exists()


def test_without_offsite_sources_there_is_no_metrics_script(tmp_path):
    assert "resticprofile/config/offsite-metrics.sh" not in rendered_tree(tmp_path, "raspi")


# What resticprofile itself writes to backup.prom (prometheus-save-to-file, extended-status)
RESTICPROFILE_BACKUP_METRICS = {
    "resticprofile_backup_status", "resticprofile_backup_duration_seconds", "resticprofile_backup_added_bytes",
    "resticprofile_backup_processed_bytes", "resticprofile_backup_files_new", "resticprofile_backup_files_changed",
    "resticprofile_backup_files_unmodified", "resticprofile_backup_files_processed",
}


def test_the_backup_dashboard_shows_only_metrics_that_are_written(dungeon):
    dashboard = (ROLES_DIR.parent / "configs/grafana/dashboards/infrastructure/backups.json").read_text()
    shown = set(re.findall(r"resticprofile_\w+", dashboard))
    assert {"resticprofile_offsite_disk_used_bytes", "resticprofile_backup_last_success_timestamp_seconds"} <= shown
    written = dungeon["resticprofile/config/profiles.yaml"] + dungeon["resticprofile/config/offsite-metrics.sh"]
    assert sorted(m for m in shown if m not in written and m not in RESTICPROFILE_BACKUP_METRICS) == []
