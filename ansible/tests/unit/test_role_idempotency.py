"""Static rules that keep a second bootstrap run at changed=0 (#237). Molecule's idempotence step can only catch
some of them: a package upgrade only changes something when Debian has published an update."""

from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
ROLE_TASK_FILES = sorted((ANSIBLE_DIR / "roles").glob("*/tasks/*.yaml"))


def tasks(path):
    """Every task in a task file, including the ones nested in blocks."""
    pending = list(yaml.safe_load(path.read_text()) or [])
    while pending:
        task = pending.pop()
        for key in ("block", "rescue", "always"):
            pending.extend(task.get(key, []))
        yield task


@pytest.mark.parametrize("path", ROLE_TASK_FILES, ids=lambda p: str(p.relative_to(ANSIBLE_DIR)))
def test_roles_dont_upgrade_packages(path):
    # Upgrades belong in update.yaml: in a role they change on every run once Debian has published updates.
    for task in tasks(path):
        apt = task.get("ansible.builtin.apt") or {}
        assert "upgrade" not in apt, f"{task['name']} upgrades packages"


@pytest.mark.parametrize("path", ROLE_TASK_FILES, ids=lambda p: str(p.relative_to(ANSIBLE_DIR)))
def test_roles_dont_ignore_errors(path):
    # An expected failure gets a precise failed_when, so a real one still stops the run.
    for task in tasks(path):
        assert "ignore_errors" not in task, f"{task['name']} ignores errors"


def test_sysctl_settings_are_applied():
    # A file in /etc/sysctl.d only takes effect after a reboot; ansible.posix.sysctl writes and applies it.
    base = list(tasks(ANSIBLE_DIR / "roles/base/tasks/main.yaml"))
    sysctl = [task["ansible.posix.sysctl"] for task in base if "ansible.posix.sysctl" in task]
    assert any(s.get("name") == "vm.overcommit_memory" and str(s.get("value")) == "1" for s in sysctl)
    for task in base:
        dest = (task.get("ansible.builtin.copy") or {}).get("dest", "")
        assert not dest.startswith("/etc/sysctl.d/"), f"{task['name']} only copies a sysctl file"


def test_ansible_posix_is_pinned():
    requirements = yaml.safe_load((ANSIBLE_DIR / "requirements.yaml").read_text())
    versions = {c["name"]: c.get("version") for c in requirements["collections"]}
    assert versions.get("ansible.posix"), "ansible.posix is missing or not pinned in requirements.yaml"


def test_update_playbook_upgrades_and_reports_reboot():
    plays = yaml.safe_load((ANSIBLE_DIR / "update.yaml").read_text())
    update_tasks = [task for play in plays for task in play.get("tasks", [])]
    apt = [task["ansible.builtin.apt"] for task in update_tasks if "ansible.builtin.apt" in task]
    assert any(a.get("upgrade") == "dist" for a in apt)
    assert any(
        (task.get("ansible.builtin.stat") or {}).get("path") == "/var/run/reboot-required" for task in update_tasks
    )
