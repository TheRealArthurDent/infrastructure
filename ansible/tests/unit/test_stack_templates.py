"""The stack role renders the same files deploy.sh produces today (#238).

Until the bash path is removed, setup/ and docker-compose.yaml stay as the fallback for deploy.sh, and the
stack role carries Jinja copies of them. This test renders the role's templates and compares them with what
deploy.sh/run.sh make of the originals, so the copies can't drift apart.
"""
import subprocess
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
REPO_DIR = ANSIBLE_DIR.parent
ROLE_DIR = ANSIBLE_DIR / "roles" / "stack"
TEMPLATES = ROLE_DIR / "templates"
DUNGEON_IP = "192.0.2.10"
# The port hard-coded in setup/resticprofile/ssh_config; the role takes it from KeePass.
DUNGEON_SSH_PORT = 1359


def expected_tree(host):
    """What deploy.sh (sed %HOSTNAME%) and run.sh (compose variables) produce for host."""
    files = {}
    for subdir in ("nginx", "resticprofile"):
        for path in sorted((REPO_DIR / "setup" / subdir).rglob("*")):
            if path.is_file():
                files[str(path.relative_to(REPO_DIR / "setup"))] = path.read_text().replace("%HOSTNAME%", host)
    compose = (REPO_DIR / "docker-compose.yaml").read_text()
    files["docker-compose.yaml"] = compose.replace("${HOSTNAME}", host).replace("${DUNGEON_IP}", DUNGEON_IP)
    return files


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
def test_templates_render_like_deploy_sh(tmp_path, host):
    expected = expected_tree(host)
    rendered = rendered_tree(tmp_path, host)
    assert sorted(rendered) == sorted(expected)
    for name, content in expected.items():
        assert rendered[name] == content, f"{name} differs from the deploy.sh output"


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
