"""Golden files: today's rendered output of the stack role for each inventory host.

The refactor of the stack role into components (#14) must not change what lands on the hosts. Regenerate the files
after an intended change with: GOLDEN_UPDATE=1 pytest tests/unit/test_stack_golden.py

Image tags and digests are left out of the comparison: Renovate bumps them in the templates and cannot regenerate
the golden files, and test_stack_templates.py already checks that every image is pinned.
"""
import os
import shutil

import pytest
import yaml

from render import ANSIBLE_DIR, RENDER_DESTS, ROLE_TASKS, role_task, rendered_tree

GOLDEN = ANSIBLE_DIR / "tests" / "unit" / "golden"


def image_name(image):
    """The image reference without its tag and digest (a registry port is not a tag)."""
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name = name[: len(name) - len(last)] + last.split(":", 1)[0]
    return name


def without_image_tags(compose):
    for service in (compose or {}).get("services", {}).values():
        if "image" in service:
            service["image"] = image_name(service["image"])
    return compose


@pytest.mark.parametrize(
    "image,name",
    [
        ("nginx:1.31-alpine", "nginx"),
        ("creativeprojects/resticprofile:0.33.1", "creativeprojects/resticprofile"),
        ("ghcr.io/gethomepage/homepage:v2.8.0", "ghcr.io/gethomepage/homepage"),
        ("deconzcommunity/deconz:2.30@sha256:abc123", "deconzcommunity/deconz"),
        ("registry.local:5000/team/app:1.0", "registry.local:5000/team/app"),
        ("registry.local:5000/team/app", "registry.local:5000/team/app"),
    ],
)
def test_image_name_drops_tag_and_digest(image, name):
    assert image_name(image) == name


def test_a_new_image_tag_does_not_differ_from_the_golden_file():
    golden = {"services": {"resticprofile": {"image": "creativeprojects/resticprofile:0.33.1", "init": True}}}
    rendered = {"services": {"resticprofile": {"image": "creativeprojects/resticprofile:0.35.0", "init": True}}}
    assert without_image_tags(rendered) == without_image_tags(golden)


def test_another_image_still_differs_from_the_golden_file():
    golden = {"services": {"resticprofile": {"image": "creativeprojects/resticprofile:0.33.1"}}}
    rendered = {"services": {"resticprofile": {"image": "someone/resticprofile:0.33.1"}}}
    assert without_image_tags(rendered) != without_image_tags(golden)


@pytest.mark.parametrize("host", ["raspi", "dungeon", "testbench"])
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
            assert without_image_tags(yaml.safe_load(content)) == without_image_tags(yaml.safe_load(golden[name])), name
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
