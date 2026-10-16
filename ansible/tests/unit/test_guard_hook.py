"""The Claude Code PreToolUse hook (.claude/hooks/guard.py) blocks risky tool calls and lets everyday work through."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "guard.py"
SETTINGS = HOOK.parent.parent / "settings.json"


def run_hook(tool_name, tool_input):
    event = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}
    return subprocess.run(
        [sys.executable, "-I", str(HOOK)], input=json.dumps(event), capture_output=True, text=True, check=False
    )


BLOCKED_COMMANDS = [
    # Production (raspi) and unlimited runs
    "cd ansible && ansible-playbook bootstrap.yaml --limit raspi",
    "ansible-playbook bootstrap.yaml -l raspi --check",
    "ansible-playbook bootstrap.yaml --limit=all",
    "ansible-playbook bootstrap.yaml --limit 'testbed,raspi'",
    "cd ansible && ../.venv/bin/ansible-playbook bootstrap.yaml",
    "bin/with-keepass ansible-playbook bootstrap.yaml --limit raspi",
    "KEEPASS_DB=x ansible-playbook setup-pihole.yaml -l raspi",
    "ansible raspi -m ping",
    "./deploy.sh",
    "./deploy.sh raspi 1357 smarthome",
    "bash deploy.sh",
    "git pull && ./deploy.sh",
    "ssh -p 1357 smarthome@192.168.1.2",
    "scp file smarthome@raspi:/tmp/",
    # main branch
    "git push origin main",
    "git push origin HEAD:main",
    "git push --force origin feat/x",
    "git push --mirror",
    # Secrets
    "bin/with-keepass --store",
    "ansible/bin/with-keepass --forget",
    "echo $KEEPASS_PASSWORD",
    "printenv KEEPASS_PASSWORD",
    "git add -f ansible/inventory/group_vars/all/local.yaml",
    "git add ansible/inventory/group_vars/all/local.yaml",
    "git add -Af ansible/",
    "git commit -m wip setup/.env",
    # The guardrails themselves
    "sed -i s/x/y/ .claude/settings.json",
    "rm .claude/hooks/guard.py",
    "echo '{}' > .claude/settings.local.json",
]

ALLOWED_COMMANDS = [
    "ansible-playbook bootstrap.yaml --limit testbed --check --diff",
    "bin/with-keepass ansible-playbook bootstrap.yaml --limit testbed",
    "ANSIBLE_ROLES_PATH=roles ansible-playbook -i localhost, --syntax-check --limit localhost converge.yaml",
    ".venv/bin/ansible-lint --strict ansible/",
    "cd ansible && ../.venv/bin/ansible-lint --strict",
    "pytest ansible/tests/unit/test_limit_guard.py",
    "cd ansible && ../.venv/bin/molecule test",
    "ansible-galaxy collection install -r ansible/requirements.yaml",
    "./deploy.sh testbed 4242 testuser",
    "cat deploy.sh",
    "git diff main -- deploy.sh setup/setup.sh",
    "grep -n ssh ansible/inventory/hosts.yaml | grep raspi",
    "ssh -p 4242 testuser@192.168.1.7",
    "git push -u origin claude/project-thread-f75dkr",
    "git push --force-with-lease origin claude/x",
    "git commit -m 'fix: raspi nginx redirect'",
    "git add ansible/inventory/group_vars/all/local.example.yaml",
    "git add -A && git commit -q -F msg.txt && git push -u origin claude/project-thread-f75dkr",
    "rm -f /tmp/x && git push origin claude/x",
    "git add .envrc.example",
    "cat .claude/settings.json",
    "git diff main -- .claude/hooks/guard.py",
]


def assert_blocked(result):
    # Exit code 2 alone isn't proof: Python also exits 2 when the script is missing.
    assert result.returncode == 2, result.stderr
    assert "Blocked by" in result.stderr


@pytest.mark.parametrize("command", BLOCKED_COMMANDS)
def test_blocks_risky_command(command):
    assert_blocked(run_hook("Bash", {"command": command}))


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_allows_everyday_command(command):
    result = run_hook("Bash", {"command": command})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "path",
    [
        "/repo/.claude/settings.json",
        "/repo/.claude/settings.local.json",
        "/repo/.claude/hooks/guard.py",
        "/home/mika/Passwords.kdbx",
    ],
)
@pytest.mark.parametrize("tool", ["Edit", "Write"])
def test_blocks_editing_guardrails_and_keepass_files(tool, path):
    assert_blocked(run_hook(tool, {"file_path": path}))


@pytest.mark.parametrize("path", ["/repo/.claude/skills/ansible-role/SKILL.md", "/repo/CLAUDE.md"])
def test_allows_editing_other_files(path):
    assert run_hook("Edit", {"file_path": path}).returncode == 0


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("mcp__github__push_files", {"branch": "main"}),
        ("mcp__github__create_or_update_file", {"branch": "main"}),
        ("mcp__github__delete_file", {"branch": "refs/heads/main"}),
        ("mcp__github__merge_pull_request", {"pullNumber": 1}),
        ("mcp__github__enable_pr_auto_merge", {"pullNumber": 1}),
    ],
)
def test_blocks_github_writes_to_main_and_merges(tool, tool_input):
    assert_blocked(run_hook(tool, tool_input))


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("mcp__github__push_files", {"branch": "claude/x"}),
        ("mcp__github__create_pull_request", {"base": "main"}),
    ],
)
def test_allows_github_feature_branch_work(tool, tool_input):
    assert run_hook(tool, tool_input).returncode == 0


def test_settings_route_all_guarded_tools_through_the_hook():
    settings = json.loads(SETTINGS.read_text())
    matchers = [entry["matcher"] for entry in settings["hooks"]["PreToolUse"]]
    for tool in ["Bash", "Edit", "Write", "mcp__github__push_files", "mcp__github__merge_pull_request"]:
        assert any(re.fullmatch(m, tool) for m in matchers), tool
