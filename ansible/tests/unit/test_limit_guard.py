"""The playbooks target all hosts, so each one must refuse to run without --limit."""
import subprocess
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
PLAYBOOKS = ["bootstrap.yaml", "check-certificates.yaml", "setup-pihole.yaml"]


def run_playbook(playbook, *args):
    return subprocess.run(
        ["ansible-playbook", playbook, *args],
        cwd=ANSIBLE_DIR,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_main_play_targets_all_hosts(playbook):
    plays = yaml.safe_load((ANSIBLE_DIR / playbook).read_text())
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
@pytest.mark.parametrize("limit", ["all", "*", "testbed,raspi", "testbed:raspi"])
def test_refuses_a_limit_that_matches_several_hosts(playbook, limit):
    # With any --limit, the localhost guard play matches no host and is skipped, so a limit that selects every
    # host would otherwise run straight against production too.
    result = run_playbook(playbook, "--limit", limit)
    assert result.returncode != 0
    assert "Run with --limit <one host>" in result.stdout
    # Stopped before the real play: no task of it ran.
    plays = yaml.safe_load((ANSIBLE_DIR / playbook).read_text())
    assert f"PLAY [{plays[-1]['name']}]" not in result.stdout


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_guards_run_with_tags(playbook):
    # `--tags stack` skips every untagged task, so the guards need the `always` tag.
    result = run_playbook(playbook, "--tags", "stack", "--list-tasks")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Ensure the run is limited to explicit hosts" in result.stdout
    assert "Ensure the run is limited to one host" in result.stdout


@pytest.mark.parametrize("playbook", PLAYBOOKS)
@pytest.mark.parametrize("limit", [[], ["--limit", "all"]], ids=["no-limit", "limit-all"])
def test_refuses_with_tags(playbook, limit):
    result = run_playbook(playbook, "--tags", "stack", *limit)
    assert result.returncode != 0
    assert "Run with --limit <" in result.stdout


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_limit_selects_only_that_host(playbook):
    result = run_playbook(playbook, "--limit", "testbed", "--list-hosts")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "testbed" in result.stdout
    assert "raspi" not in result.stdout
