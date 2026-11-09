"""Renders what the stack role and its components put on a host, without a host: the components' templates, the
merged compose file and the restic-restore JSON files.

Shared by the template tests and the golden files (test_stack_golden.py). Rendering goes through the real inventory,
so host_vars and group_vars apply; the roles' defaults load at role-default precedence, as on a real run. The
expressions for the compose file and the JSON files are copies of the ones in roles/stack/tasks/main.yaml and
roles/resticprofile/tasks/restore.yaml.
"""
import os
import subprocess
from pathlib import Path

import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
ROLES_DIR = ANSIBLE_DIR / "roles"
# The stack's components, in the order of the component map (#14)
COMPONENTS = ["reverse_proxy", "portainer", "unbound", "pihole", "outbound", "resticprofile", "healthmonitor"]
DUNGEON_IP = "192.0.2.10"
DUNGEON_SSH_PORT = 1359


def component_templates():
    """(component, template path relative to its templates/) of every config template, not the compose fragments."""
    return [
        {"component": component, "path": str(p.relative_to(ROLES_DIR / component / "templates"))}
        for component in COMPONENTS
        for p in sorted((ROLES_DIR / component / "templates").rglob("*.j2"))
        if p.name != "docker-compose.yaml.j2"
    ]


def rendered_tree(tmp_path, host):
    """Relative destination path -> content of every file the stack role renders for the host."""
    out = tmp_path / "out"
    templates = component_templates()
    # Roles holding only the defaults: they must rank below group_vars and host_vars, which vars_files would not do
    roles = tmp_path / "roles"
    for role in ["stack", *COMPONENTS]:
        (roles / f"{role}_defaults" / "tasks").mkdir(parents=True)
        (roles / f"{role}_defaults" / "tasks" / "main.yaml").write_text("[]\n")
        (roles / f"{role}_defaults" / "defaults").symlink_to(ROLES_DIR / role / "defaults")
    playbook = tmp_path / "render.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Render the stack templates",
        "hosts": "all",
        "connection": "local",
        "gather_facts": False,
        "roles": [f"{role}_defaults" for role in ["stack", *COMPONENTS]],
        "vars": {
            "resticprofile_backup_host_ip": DUNGEON_IP,
            "dungeon_ssh_port": DUNGEON_SSH_PORT,
            "ansible_python_interpreter": "auto_silent",
        },
        "tasks": [
            {
                "name": "Ensure output directories",
                "ansible.builtin.file": {"path": f"{out}/{{{{ item | dirname }}}}", "state": "directory"},
                "loop": [t["path"] for t in templates] + ["restic-restore/stacks/placeholder"],
            },
            {
                "name": "Render template",
                "ansible.builtin.template": {
                    "src": f"{ROLES_DIR}/{{{{ item.component }}}}/templates/{{{{ item.path }}}}",
                    "dest": f"{out}/{{{{ item.path | regex_replace('\\.j2$', '') }}}}",
                },
                "loop": templates,
                "when": "item.component in stack_components",
            },
            # The same expressions as roles/stack/tasks/main.yaml, with the roles directory for role_path | dirname
            {
                "name": "Merge the compose fragments of the components",
                "ansible.builtin.set_fact": {"stack_compose_definition": (
                    "{{ (query('ansible.builtin.template', *stack_compose_fragments)"
                    " | map('ansible.builtin.from_yaml') | list\n"
                    "    + [stack_compose_networks])\n"
                    "   | compose_merge }}"
                )},
                "vars": {"stack_compose_fragments": (
                    "{{ stack_components\n"
                    "   | map('ansible.builtin.regex_replace', '^(.+)$',\n"
                    f"         '{ROLES_DIR}' ~ '/\\1/templates/docker-compose.yaml.j2')\n"
                    "   | list }}"
                )},
            },
            {
                "name": "Render the compose file",
                "ansible.builtin.copy": {
                    "content": (
                        "# Merged by the stack role from the components' compose fragments (#14). Don't edit, run the"
                        " playbook.\n"
                        "{{ stack_compose_definition | ansible.builtin.to_nice_yaml(indent=2) }}"
                    ),
                    "dest": f"{out}/docker-compose.yaml",
                },
            },
            # The same expressions as roles/resticprofile/tasks/restore.yaml
            {
                "name": "Render the restore helper host config",
                "ansible.builtin.copy": {
                    "content": "{{ resticprofile_restore_config | to_nice_json }}\n",
                    "dest": f"{out}/restic-restore/config.json",
                },
                "vars": {"resticprofile_restore_config": {
                    "stacks_dir": "/etc/restic-restore/stacks",
                    "state_dir": "{{ resticprofile_restore_state_dir }}",
                    "source_mounts": "{{ resticprofile_restore_source_mounts }}",
                    "staging": "{{ resticprofile_restore_staging }}",
                    "runners": "{{ resticprofile_restore_runners }}",
                    "rsnapshot": "{{ resticprofile_restore_rsnapshot }}",
                    "drill": (
                        "{{ {'metrics': resticprofile_restore_drill.metrics,"
                        " 'parts': resticprofile_restore_drill.parts | int,\n"
                        "    'enabled': resticprofile_restore_drill_enabled | bool} }}"
                    ),
                }},
            },
            {
                "name": "Render the restore items of this stack",
                "ansible.builtin.copy": {
                    "content": "{{ {'items': stack_restore_items} | to_nice_json }}\n",
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
        env={
            **os.environ,
            "ANSIBLE_ROLES_PATH": str(roles),
            # compose_merge, shipped with the stack role
            "ANSIBLE_FILTER_PLUGINS": str(ROLES_DIR / "stack" / "filter_plugins"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {str(p.relative_to(out)): p.read_text() for p in sorted(out.rglob("*")) if p.is_file()}
