"""Where the playbooks find the KeePass database (#267), and how often they open it (#235)."""
import subprocess
from pathlib import Path

import pytest
import yaml

BOOTSTRAP = Path(__file__).parents[2]
PLAYBOOKS = ["bootstrap.yaml", "check-certificates.yaml", "setup-pihole.yaml"]
LOCAL_CONFIG = "inventory/group_vars/all/local.yaml"
LOCAL_EXAMPLE = "inventory/group_vars/all/local.example.yaml"


def plays(playbook):
    return yaml.safe_load((BOOTSTRAP / playbook).read_text())


def keepass_var(name, *args):
    result = subprocess.run(
        ["ansible", "testbed", "-m", "ansible.builtin.debug", "-a", f"var={name}", *args],
        cwd=BOOTSTRAP,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_playbooks_do_not_hard_code_keepass_paths(playbook):
    for play in plays(playbook):
        assert "keepass_db" not in play.get("vars", {})
        assert "keepass_key" not in play.get("vars", {})


def test_no_keepass_path_is_committed():
    # Paths depend on the user's file system, so they live in an ignored local file (#267).
    tracked = subprocess.run(
        ["git", "ls-files", "inventory"], cwd=BOOTSTRAP, capture_output=True, text=True, check=True
    ).stdout.split()
    for path in tracked:
        if path.endswith(".example.yaml"):
            continue
        text = (BOOTSTRAP / path).read_text()
        assert ".kdbx" not in text, path
        assert ".key" not in text, path


def test_local_config_is_ignored_by_git():
    result = subprocess.run(["git", "check-ignore", "-q", LOCAL_CONFIG], cwd=BOOTSTRAP)
    assert result.returncode == 0


def test_example_documents_the_local_config():
    example = yaml.safe_load((BOOTSTRAP / LOCAL_EXAMPLE).read_text())
    assert set(example) == {"local_keepass_db", "local_keepass_key"}


def test_keepass_paths_come_from_the_local_config(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    monkeypatch.delenv("KEEPASS_KEY", raising=False)
    local = ["-e", "local_keepass_db=/data/my.kdbx", "-e", "local_keepass_key=/data/my.key"]
    assert '"keepass_db": "/data/my.kdbx"' in keepass_var("keepass_db", *local)
    assert '"keepass_key": "/data/my.key"' in keepass_var("keepass_key", *local)


def test_keepass_paths_are_empty_when_not_configured(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    monkeypatch.delenv("KEEPASS_KEY", raising=False)
    unset = ["-e", "local_keepass_db=", "-e", "local_keepass_key="]
    assert '"keepass_db": ""' in keepass_var("keepass_db", *unset)
    assert '"keepass_key": ""' in keepass_var("keepass_key", *unset)


def test_keepass_paths_expand_the_home_directory(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    out = keepass_var("keepass_db", "-e", "local_keepass_db=~/my.kdbx")
    assert f'"keepass_db": "{Path.home()}/my.kdbx"' in out


def test_keepass_paths_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.setenv("KEEPASS_DB", "/tmp/other.kdbx")
    monkeypatch.setenv("KEEPASS_KEY", "/tmp/other.key")
    local = ["-e", "local_keepass_db=/data/my.kdbx", "-e", "local_keepass_key=/data/my.key"]
    assert '"keepass_db": "/tmp/other.kdbx"' in keepass_var("keepass_db", *local)
    assert '"keepass_key": "/tmp/other.key"' in keepass_var("keepass_key", *local)


def test_bootstrap_does_not_resolve_secrets_lazily():
    # Play vars are templated on every use, so a lookup there re-decrypts the database each time.
    for play in plays("bootstrap.yaml"):
        for name, value in play.get("vars", {}).items():
            assert "lookup('keepass'" not in str(value), name
