"""Offsite copy of the restic repositories (#60): dungeon copies the repositories on its backup SSD with restic copy
to an sftp target outside the house, applies the local retention there and checks a part of the data every month.

The target's host and user come from KeePass (render.py stands in for them), so the repo only knows the ssh alias.
"""
import subprocess
import time

import pytest
import yaml

from render import ROLES_DIR, rendered_tree

SOURCES = ["raspi", "dungeon"]
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
    assert copy["schedule"] == "*-*-* 05:00:00"


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
