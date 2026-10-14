"""Where the playbooks find the KeePass database, and how often they open it (#235)."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml

BOOTSTRAP = Path(__file__).parents[2]
PLAYBOOKS = ["bootstrap.yaml", "check-certificates.yaml", "setup-pihole.yaml"]


def plays(playbook):
    return yaml.safe_load((BOOTSTRAP / playbook).read_text())


def keepass_var(name, **env):
    result = subprocess.run(
        ["ansible", "testbed", "-m", "ansible.builtin.debug", "-a", f"var={name}"],
        cwd=BOOTSTRAP,
        env={**os.environ, **env},
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


@pytest.mark.parametrize(
    "name, default",
    [("keepass_db", "Raspberry.kdbx"), ("keepass_key", "raspberry.key")],
)
def test_keepass_paths_have_a_default(name, default, monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    monkeypatch.delenv("KEEPASS_KEY", raising=False)
    out = keepass_var(name)
    assert "undefined" not in out
    assert default in out


def test_keepass_paths_can_be_overridden_from_the_environment():
    assert "/tmp/other.kdbx" in keepass_var("keepass_db", KEEPASS_DB="/tmp/other.kdbx")
    assert "/tmp/other.key" in keepass_var("keepass_key", KEEPASS_KEY="/tmp/other.key")


def test_bootstrap_does_not_resolve_secrets_lazily():
    # Play vars are templated on every use, so a lookup there re-decrypts the database each time.
    for play in plays("bootstrap.yaml"):
        for name, value in play.get("vars", {}).items():
            assert "lookup('keepass'" not in str(value), name
