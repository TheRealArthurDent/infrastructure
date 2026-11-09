"""Templates of the stack role, which deploys the infrastructure stack (raspberry#238)."""
import re

import pytest
import yaml

from render import ANSIBLE_DIR, rendered_tree


@pytest.mark.parametrize("host", ["raspi", "testbed"])
def test_templates_leave_no_placeholders(tmp_path, host):
    # %HOSTNAME% and ${...} were the bash scripts' placeholders; nothing may survive the rendering.
    for name, content in rendered_tree(tmp_path, host).items():
        assert not re.search(r"%HOSTNAME%|\$\{(HOSTNAME|DUNGEON_IP)\}|\{\{", content), name


def test_compose_images_are_pinned(tmp_path):
    # Renovate updates the pinned versions (CLAUDE.md); an unpinned image would change on any pull.
    compose = yaml.safe_load(rendered_tree(tmp_path, "raspi")["docker-compose.yaml"])
    for service, config in compose["services"].items():
        image = config["image"]
        assert re.search(r":[^/:]+$", image) and not image.endswith(":latest"), f"{service}: {image}"


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
