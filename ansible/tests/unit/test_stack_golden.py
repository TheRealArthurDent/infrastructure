"""Golden files: today's rendered output of the stack role for each inventory host.

The refactor of the stack role into components (#14) must not change what lands on the hosts. Regenerate the files
after an intended change with: GOLDEN_UPDATE=1 pytest tests/unit/test_stack_golden.py
"""
import os
import shutil

import pytest
import yaml

from render import ANSIBLE_DIR, RENDER_DESTS, ROLE_TASKS, role_task, rendered_tree

GOLDEN = ANSIBLE_DIR / "tests" / "unit" / "golden"


@pytest.mark.parametrize("host", ["raspi", "dungeon", "testbed"])
def test_rendered_output_matches_golden_files(tmp_path, host):
    tree = rendered_tree(tmp_path, host)
    golden_dir = GOLDEN / host
    if os.environ.get("GOLDEN_UPDATE"):
        shutil.rmtree(golden_dir, ignore_errors=True)
        for name, content in tree.items():
            (golden_dir / name).parent.mkdir(parents=True, exist_ok=True)
            (golden_dir / name).write_text(content)
    golden = {str(p.relative_to(golden_dir)): p.read_text() for p in sorted(golden_dir.rglob("*")) if p.is_file()}
    assert sorted(tree) == sorted(golden)
    for name, content in tree.items():
        if name == "docker-compose.yaml":
            assert yaml.safe_load(content) == yaml.safe_load(golden[name]), name
        else:
            assert content == golden[name], name


@pytest.mark.parametrize("task_file,name,module", ROLE_TASKS, ids=[t[1] for t in ROLE_TASKS])
def test_render_reuses_the_role_tasks(task_file, name, module):
    # The golden files only cover the role if render.py finds these tasks: a rename must fail here, not drop them.
    task = role_task(task_file, name)
    assert module in task, f"{name} no longer uses {module}"
    if module == "ansible.builtin.copy":
        assert task[module]["dest"] in RENDER_DESTS, f"{name}: dest {task[module]['dest']} unknown to render.py"


def test_role_task_fails_on_a_missing_task():
    with pytest.raises(LookupError):
        role_task("stack/tasks/main.yaml", "No such task")
