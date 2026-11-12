"""Templates of the stack role and its components, which deploy the infrastructure stack (raspberry#238, #14)."""
import re

import pytest
import yaml

from render import ANSIBLE_DIR, COMPONENTS, rendered_tree

ROLES_DIR = ANSIBLE_DIR / "roles"


@pytest.mark.parametrize("host", ["raspi", "testbench"])
def test_templates_leave_no_placeholders(tmp_path, host):
    # %HOSTNAME% and ${...} were the bash scripts' placeholders; nothing may survive the rendering.
    for name, content in rendered_tree(tmp_path, host).items():
        assert not re.search(r"%HOSTNAME%|\$\{(HOSTNAME|DUNGEON_IP)\}|\{\{", content), name


def compose_fragments():
    return sorted(ROLES_DIR.glob("*/templates/docker-compose.yaml.j2"))


def test_every_component_has_a_compose_fragment():
    assert sorted(p.parts[-3] for p in compose_fragments()) == sorted(COMPONENTS)


@pytest.mark.parametrize("fragment", compose_fragments(), ids=lambda p: p.parts[-3])
def test_compose_images_are_pinned(fragment):
    # Renovate updates the pinned versions (CLAUDE.md); an unpinned image would change on any pull.
    compose = yaml.safe_load(fragment.read_text())
    assert compose.get("services"), f"{fragment} defines no service"
    for service, config in compose["services"].items():
        image = config["image"]
        assert re.search(r":[^/:]+$", image) and not image.endswith(":latest"), f"{service}: {image}"


def stack_tasks():
    """The tasks of the stack role's main.yaml, including the ones nested in blocks."""
    pending = list(yaml.safe_load((ROLES_DIR / "stack" / "tasks" / "main.yaml").read_text()))
    while pending:
        task = pending.pop(0)
        for key in ("block", "rescue", "always"):
            pending[:0] = task.get(key, [])
        yield task


def test_stack_imports_components():
    # Static imports, so the stack tag from bootstrap.yaml reaches the components' tasks (--tags stack).
    tasks = list(stack_tasks())
    assert not [t for t in tasks if "ansible.builtin.include_role" in t or "include_role" in t]
    imports = [t for t in tasks if "ansible.builtin.import_role" in t]
    main_imports = {
        t["ansible.builtin.import_role"]["name"]: t
        for t in imports
        if t["ansible.builtin.import_role"].get("tasks_from", "main") == "main"
    }
    assert list(main_imports) == COMPONENTS, "every component, in the order of the component map"
    for name, task in main_imports.items():
        assert task.get("when") == f"'{name}' in stack_components", name
    restore = [t for t in imports if t["ansible.builtin.import_role"].get("tasks_from") == "restore"]
    assert [t["ansible.builtin.import_role"]["name"] for t in restore] == ["resticprofile"]
    assert tasks.index(restore[0]) > max(tasks.index(t) for t in main_imports.values())


def main_play():
    return yaml.safe_load((ANSIBLE_DIR / "bootstrap.yaml").read_text())[-1]


def test_bootstrap_deploys_the_stack_with_its_own_tag():
    roles = main_play()["roles"]
    stack = [r for r in roles if isinstance(r, dict) and r.get("role") == "stack"]
    assert stack, "stack role is not in bootstrap.yaml"
    assert "stack" in stack[0].get("tags", [])


def test_bootstrap_reads_secrets_even_with_tags():
    # With --tags stack, untagged pre_tasks are skipped and the KeePass secrets would be undefined.
    for task in main_play()["pre_tasks"]:
        assert "always" in task.get("tags", []), f"pre_task '{task['name']}' is not tagged always"
