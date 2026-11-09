"""Renders what the stack role puts on a host, without a host: the templates and the restic-restore JSON files.

Shared by the template tests and the golden files (test_stack_golden.py). Rendering goes through the real inventory,
so host_vars and group_vars apply; the role's defaults load at role-default precedence, as on a real run.
"""
import os
import subprocess
from pathlib import Path

import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
ROLE_DIR = ANSIBLE_DIR / "roles" / "stack"
TEMPLATES = ROLE_DIR / "templates"
DUNGEON_IP = "192.0.2.10"
DUNGEON_SSH_PORT = 1359


def rendered_tree(tmp_path, host):
    """Relative destination path -> content of every file the stack role renders for the host."""
    out = tmp_path / "out"
    templates = sorted(str(p.relative_to(TEMPLATES)) for p in TEMPLATES.rglob("*.j2"))
    # A role holding only the defaults: they must rank below group_vars and host_vars, which vars_files would not do
    roles = tmp_path / "roles"
    (roles / "stack_defaults" / "tasks").mkdir(parents=True)
    (roles / "stack_defaults" / "tasks" / "main.yaml").write_text("[]\n")
    (roles / "stack_defaults" / "defaults").mkdir()
    (roles / "stack_defaults" / "defaults" / "main.yaml").symlink_to(ROLE_DIR / "defaults" / "main.yaml")
    playbook = tmp_path / "render.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Render the stack templates",
        "hosts": "all",
        "connection": "local",
        "gather_facts": False,
        "roles": ["stack_defaults"],
        "vars": {
            "stack_dungeon_ip": DUNGEON_IP,
            "dungeon_ssh_port": DUNGEON_SSH_PORT,
            "ansible_python_interpreter": "auto_silent",
        },
        "tasks": [
            {
                "name": "Ensure output directories",
                "ansible.builtin.file": {"path": f"{out}/{{{{ item | dirname }}}}", "state": "directory"},
                "loop": templates + ["restic-restore/stacks/placeholder"],
            },
            {
                "name": "Render template",
                "ansible.builtin.template": {
                    "src": f"{TEMPLATES}/{{{{ item }}}}",
                    "dest": f"{out}/{{{{ item | regex_replace('\\.j2$', '') }}}}",
                },
                "loop": templates,
            },
            # The same expressions as roles/stack/tasks/restore.yaml
            {
                "name": "Render the restore helper host config",
                "ansible.builtin.copy": {
                    "content": "{{ stack_restore_config | to_nice_json }}\n",
                    "dest": f"{out}/restic-restore/config.json",
                },
                "vars": {"stack_restore_config": {
                    "stacks_dir": "/etc/restic-restore/stacks",
                    "state_dir": "{{ stack_restore_state_dir }}",
                    "source_mounts": "{{ stack_restore_source_mounts }}",
                    "staging": "{{ stack_restore_staging }}",
                    "runners": "{{ stack_restore_runners }}",
                    "rsnapshot": "{{ stack_restore_rsnapshot }}",
                    "drill": (
                        "{{ {'metrics': stack_restore_drill.metrics, 'parts': stack_restore_drill.parts | int,\n"
                        "    'enabled': stack_restore_drill_enabled | bool} }}"
                    ),
                }},
            },
            {
                "name": "Render the restore items of this stack",
                "ansible.builtin.copy": {
                    "content": "{{ {'items': restore_items} | to_nice_json }}\n",
                    "dest": f"{out}/restic-restore/stacks/{{{{ stack_project_name }}}}.json",
                },
            },
        ],
    }]))
    result = subprocess.run(
        ["ansible-playbook", "-i", "inventory/", "--limit", host, str(playbook)],
        cwd=ANSIBLE_DIR,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "ANSIBLE_ROLES_PATH": str(roles)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {str(p.relative_to(out)): p.read_text() for p in sorted(out.rglob("*")) if p.is_file()}
