"""The stack role's "Ensure the stack is up" task in check mode.

Compose's --dry-run only pretends to pull a new image tag and then fails to recreate the container with it
("No such image"). In check mode that must not fail the run; any other Compose error still must. The task runs
verbatim against a stub community.docker.docker_compose_v2 that returns a canned result.
"""
import json
import os
import subprocess

import pytest
import yaml

from render import ANSIBLE_DIR, role_task

TASK = "Ensure the stack is up"
NOTE = "Note the image that check mode couldn't pull"
NO_SUCH_IMAGE = (
    "Error when processing container pihole: Error response from daemon: No such image: pihole/pihole:2026.10.1\n"
    "Error when processing container outbound: Error response from daemon: No such image: pihole/pihole:2026.10.1\n"
    "General error: Error response from daemon: No such image: pihole/pihole:2026.10.1"
)
OTHER_ERROR = NO_SUCH_IMAGE + "\nError when processing container nginx: port is already allocated"

STUB_MODULE = '''#!/usr/bin/python
import json
from ansible.module_utils.basic import AnsibleModule

def main():
    module = AnsibleModule(
        argument_spec={key: {"type": "raw"} for key in ("definition", "project_name", "state", "pull")},
        supports_check_mode=True,
    )
    with open(RESULT_FILE) as f:
        result = json.load(f)
    if result.pop("failed", False):
        module.fail_json(**result)
    module.exit_json(**result)

main()
'''


def run_task(tmp_path, result, check):
    """Runs the role's task against a stub module returning result; returns the CompletedProcess."""
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps(result))
    modules = tmp_path / "collections" / "ansible_collections" / "community" / "docker" / "plugins" / "modules"
    modules.mkdir(parents=True)
    (modules / "docker_compose_v2.py").write_text(STUB_MODULE.replace("RESULT_FILE", repr(str(result_file))))
    tasks = [role_task("stack/tasks/main.yaml", name) for name in (TASK, NOTE)]
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Run the compose task",
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "stack_compose_definition": {"services": {}},
            "stack_project_name": "infrastructure",
            "stack_network_siz": {"changed": False},
            "stack_network_outbound": {"changed": False},
            "ansible_python_interpreter": "auto_silent",
        },
        "tasks": tasks,
    }]))
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", *(["--check"] if check else []), str(playbook)],
        cwd=ANSIBLE_DIR,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "ANSIBLE_COLLECTIONS_PATH": str(tmp_path / "collections"), "ANSIBLE_NOCOLOR": "1"},
    )


def test_check_mode_tolerates_a_new_image_tag_that_compose_cannot_pull(tmp_path):
    run = run_task(tmp_path, {"failed": True, "changed": True, "msg": NO_SUCH_IMAGE}, check=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "changed=1" in run.stdout
    assert "failed=0" in run.stdout
    assert "The real run pulls them" in run.stdout


def test_check_mode_still_fails_on_any_other_compose_error(tmp_path):
    run = run_task(tmp_path, {"failed": True, "changed": True, "msg": OTHER_ERROR}, check=True)
    assert run.returncode != 0
    assert "port is already allocated" in run.stdout


@pytest.mark.parametrize("msg", [NO_SUCH_IMAGE, OTHER_ERROR])
def test_a_real_run_fails_on_every_compose_error(tmp_path, msg):
    run = run_task(tmp_path, {"failed": True, "changed": True, "msg": msg}, check=False)
    assert run.returncode != 0


def test_a_successful_run_stays_successful(tmp_path):
    run = run_task(tmp_path, {"changed": False}, check=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "changed=0" in run.stdout
    assert "The real run pulls them" not in run.stdout
