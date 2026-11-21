"""Tasks delegated to the control node run as the user, not as root.

The inventory sets `ansible_become: true` for every host (group_vars/all/connection.yaml). A connection variable wins
over the `become` keyword, so `become: false` on a task delegated to localhost is ignored and the task runs through
sudo on the control node. The DH parameters were then written by root with mode 0600, and the copy action, which reads
its source as the user, failed with "Permission denied". Only a task variable outranks the inventory.
"""
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]


def yaml_files():
    for path in sorted(ANSIBLE_DIR.glob("*.yaml")) + sorted((ANSIBLE_DIR / "roles").glob("*/tasks/*.yaml")) \
            + sorted((ANSIBLE_DIR / "tasks").glob("*.yaml")):
        yield path


def tasks_in(items, inherited_vars=None):
    """Yield (task, effective vars) for every task and block, descending into plays and blocks."""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        own_vars = {**(inherited_vars or {}), **(item.get("vars") or {})}
        yield item, own_vars
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            yield from tasks_in(item.get(key), own_vars)


def delegated_tasks():
    found = []
    for path in yaml_files():
        for task, task_vars in tasks_in(yaml.safe_load(path.read_text())):
            if task.get("delegate_to") in ("localhost", "127.0.0.1"):
                found.append(pytest.param(task, task_vars, id=f"{path.relative_to(ANSIBLE_DIR)}:{task.get('name')}"))
    return found


def test_there_are_delegated_tasks():
    assert len(delegated_tasks()) >= 5


@pytest.mark.parametrize("task, task_vars", delegated_tasks())
def test_delegated_task_turns_off_become_by_variable(task, task_vars):
    assert task_vars.get("ansible_become") is False, (
        "a task delegated to localhost needs `vars: {ansible_become: false}`; `become: false` alone loses against "
        "ansible_become from the inventory"
    )
