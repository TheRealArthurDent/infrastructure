"""Renders what the stack role and its components put on a host, without a host: the components' templates, the
merged compose file and the restic-restore JSON files.

Shared by the template tests and the golden files (test_stack_golden.py). Rendering goes through the real inventory,
so host_vars and group_vars apply; the roles' defaults load at role-default precedence, as on a real run. The
compose file and the JSON files are rendered by running the tasks of roles/stack/tasks/main.yaml and
roles/resticprofile/tasks/restore.yaml verbatim.
"""
import json
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


# Role tasks the render reuses: (task file, task name, module) -> what the render keeps of them
ROLE_TASKS = [
    ("stack/tasks/main.yaml", "Merge the compose fragments of the components", "ansible.builtin.set_fact"),
    ("stack/tasks/main.yaml", "Ensure the compose file is present", "ansible.builtin.copy"),
    ("resticprofile/tasks/restore.yaml", "Ensure the restore helper host config is present", "ansible.builtin.copy"),
    (
        "resticprofile/tasks/restore.yaml",
        "Ensure the restore items of this stack are listed for the helper",
        "ansible.builtin.copy",
    ),
]
# Where the render writes what the copy tasks write on the host, relative to its output directory
RENDER_DESTS = {
    "{{ stack_compose_dir }}/docker-compose.yaml": "docker-compose.yaml",
    "/etc/restic-restore/config.json": "restic-restore/config.json",
    "/etc/restic-restore/stacks/{{ stack_project_name }}.json": "restic-restore/stacks/{{ stack_project_name }}.json",
}


def role_task(task_file, name):
    """The task called name in roles/<task_file>, searched in blocks too."""
    pending = list(yaml.safe_load((ROLES_DIR / task_file).read_text()))
    while pending:
        task = pending.pop(0)
        if task.get("name") == name:
            return task
        for key in ("block", "rescue", "always"):
            pending[:0] = task.get(key, [])
    raise LookupError(f"no task {name!r} in roles/{task_file}")


def role_tasks(out):
    """The ROLE_TASKS with their expressions verbatim; dest goes to out, role_path | dirname to the roles dir."""
    tasks = []
    for task_file, name, module in ROLE_TASKS:
        task = role_task(task_file, name)
        rendered = {"name": name, module: dict(task[module])}
        if "vars" in task:
            rendered["vars"] = task["vars"]
        if module == "ansible.builtin.copy":
            dest = RENDER_DESTS[task[module]["dest"]]
            rendered[module] = {"content": task[module]["content"], "dest": f"{out}/{dest}"}
        # yaml round trip so nested strings get the same replacement
        text = yaml.safe_dump(rendered).replace("role_path | dirname", repr(str(ROLES_DIR)))
        assert "role_path" not in text, f"{name}: role_path left that the render can't resolve"
        tasks.append(yaml.safe_load(text))
    return tasks


def component_templates():
    """(component, template path relative to its templates/) of every config template, not the compose fragments."""
    return [
        {"component": component, "path": str(p.relative_to(ROLES_DIR / component / "templates"))}
        for component in COMPONENTS
        for p in sorted((ROLES_DIR / component / "templates").rglob("*.j2"))
        if p.name != "docker-compose.yaml.j2"
    ]


def fragment_yaml(path):
    """The YAML of a compose fragment as written, for tests of what is static in it.

    Lines with only a Jinja statement ({% if %} / {% endif %}) are dropped, so every optional branch stays in.
    """
    return "".join(line for line in Path(path).read_text().splitlines(keepends=True) if not line.lstrip().startswith("{%"))


def rendered_tree(tmp_path, host, extra_vars=None):
    """Relative destination path -> content of every file the stack role renders for the host.

    extra_vars (a dict, e.g. {"stack_components": [...]}) is passed as -e @file and overrides everything else.
    """
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
        ] + role_tasks(out),
    }]))
    extra_args = []
    if extra_vars:
        extra_file = tmp_path / "extra_vars.json"
        extra_file.write_text(json.dumps(extra_vars))
        extra_args = ["-e", f"@{extra_file}"]
    result = subprocess.run(
        ["ansible-playbook", "-i", "inventory/", "--limit", host, *extra_args, str(playbook)],
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
