"""Shared tasks that check the KeePass configuration and get the master password (#267)."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
TASKS = ANSIBLE_DIR / "tasks" / "keepass_credentials.yaml"
PLAYBOOKS = ["bootstrap.yaml", "check-certificates.yaml", "setup-pihole.yaml"]


@pytest.fixture
def keepass_files(tmp_path):
    db = tmp_path / "test.kdbx"
    key = tmp_path / "test.key"
    db.write_bytes(b"")
    key.write_bytes(b"")
    return db, key


def run_tasks(tmp_path, *extra_vars, password=None):
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Get KeePass credentials",
        "hosts": "localhost",
        "gather_facts": False,
        "tasks": [
            {"ansible.builtin.import_tasks": str(TASKS)},
            {
                "name": "Check the password",
                "ansible.builtin.assert": {"that": "keepass_password == 'LEAKCANARY-master'", "quiet": True},
            },
        ],
    }]))
    env = {k: v for k, v in os.environ.items() if k not in ("KEEPASS_PASSWORD", "KEEPASS_DB", "KEEPASS_KEY")}
    if password is not None:
        env["KEEPASS_PASSWORD"] = password
    args = [arg for var in extra_vars for arg in ("-e", var)]
    return subprocess.run(
        ["ansible-playbook", "-v", str(playbook), *args],
        cwd=ANSIBLE_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_takes_the_password_from_the_environment(tmp_path, keepass_files):
    db, key = keepass_files
    result = run_tasks(tmp_path, f"keepass_db={db}", f"keepass_key={key}", password="LEAKCANARY-master")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LEAKCANARY" not in result.stdout + result.stderr


def test_key_file_is_optional(tmp_path, keepass_files):
    db, _ = keepass_files
    result = run_tasks(tmp_path, f"keepass_db={db}", "keepass_key=", password="LEAKCANARY-master")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("db", ["", "/nonexistent/test.kdbx"])
def test_fails_with_a_hint_when_the_database_is_not_configured(tmp_path, keepass_files, db):
    _, key = keepass_files
    result = run_tasks(tmp_path, f"keepass_db={db}", f"keepass_key={key}", password="LEAKCANARY-master")
    assert result.returncode != 0
    assert "local.example.yaml" in result.stdout


def test_fails_with_a_hint_when_the_key_file_is_missing(tmp_path, keepass_files):
    db, _ = keepass_files
    result = run_tasks(tmp_path, f"keepass_db={db}", "keepass_key=/nonexistent/test.key", password="x")
    assert result.returncode != 0
    assert "local.example.yaml" in result.stdout


def main_play(playbook):
    return yaml.safe_load((ANSIBLE_DIR / playbook).read_text())[-1]


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_playbooks_do_not_prompt_before_checking_the_environment(playbook):
    prompts = [p["name"] for p in main_play(playbook).get("vars_prompt", [])]
    assert "keepass_password" not in prompts


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_playbooks_get_the_credentials_before_the_first_lookup(playbook):
    play = main_play(playbook)
    tasks = play.get("pre_tasks", []) + play.get("tasks", [])
    imports = [i for i, t in enumerate(tasks) if t.get("ansible.builtin.import_tasks") == "tasks/keepass_credentials.yaml"]
    lookups = [i for i, t in enumerate(tasks) if "lookup('keepass'" in str(t)]
    assert imports, "tasks/keepass_credentials.yaml is not imported"
    assert lookups
    assert imports[0] < lookups[0]
