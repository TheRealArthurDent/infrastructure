"""The playbooks target all hosts, so each one must refuse to run without --limit."""
import subprocess
from pathlib import Path

import pytest
import yaml

BOOTSTRAP = Path(__file__).parents[2]
PLAYBOOKS = ["bootstrap.yaml", "check-certificates.yaml", "setup-pihole.yaml"]


def run_playbook(playbook, *args):
    return subprocess.run(
        ["ansible-playbook", playbook, *args],
        cwd=BOOTSTRAP,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_main_play_targets_all_hosts(playbook):
    plays = yaml.safe_load((BOOTSTRAP / playbook).read_text())
    assert plays[-1]["hosts"] == "all"


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_refuses_to_run_without_limit(playbook):
    result = run_playbook(playbook)
    assert result.returncode != 0
    assert "Run with --limit <host>" in result.stdout
    # The guard fails before any real host is contacted or a secret is asked for.
    assert "raspi" not in result.stdout
    assert "testbed" not in result.stdout


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_limit_selects_only_that_host(playbook):
    result = run_playbook(playbook, "--limit", "testbed", "--list-hosts")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "testbed" in result.stdout
    assert "raspi" not in result.stdout
