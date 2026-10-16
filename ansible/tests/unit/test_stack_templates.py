"""Templates of the stack role, which deploys the infrastructure stack (#238)."""
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
ROLE_DIR = ANSIBLE_DIR / "roles" / "stack"
TEMPLATES = ROLE_DIR / "templates"
DUNGEON_IP = "192.0.2.10"
DUNGEON_SSH_PORT = 1359


def rendered_tree(tmp_path, host):
    out = tmp_path / "out"
    templates = sorted(str(p.relative_to(TEMPLATES)) for p in TEMPLATES.rglob("*.j2"))
    playbook = tmp_path / "render.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Render the stack templates",
        "hosts": "all",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [str(ROLE_DIR / "defaults" / "main.yaml")],
        "vars": {
            "stack_dungeon_ip": DUNGEON_IP,
            "dungeon_ssh_port": DUNGEON_SSH_PORT,
            "ansible_python_interpreter": "auto_silent",
        },
        "tasks": [
            {
                "name": "Ensure output directories",
                "ansible.builtin.file": {"path": f"{out}/{{{{ item | dirname }}}}", "state": "directory"},
                "loop": templates,
            },
            {
                "name": "Render template",
                "ansible.builtin.template": {
                    "src": f"{TEMPLATES}/{{{{ item }}}}",
                    "dest": f"{out}/{{{{ item | regex_replace('\\.j2$', '') }}}}",
                },
                "loop": templates,
            },
        ],
    }]))
    result = subprocess.run(
        ["ansible-playbook", "-i", f"{host},", str(playbook)],
        cwd=ANSIBLE_DIR,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {str(p.relative_to(out)): p.read_text() for p in sorted(out.rglob("*")) if p.is_file()}


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
