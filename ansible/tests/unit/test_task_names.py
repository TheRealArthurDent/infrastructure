"""Task names are rendered before a loop runs, so a name with {{ item }} warns "'item' is undefined" on every run."""
import re
from pathlib import Path

import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
LOOP_VAR = re.compile(r"\{\{\s*item\b(?!_)")


def tasks(node):
    if isinstance(node, list):
        for entry in node:
            yield from tasks(entry)
    elif isinstance(node, dict):
        if "name" in node and isinstance(node["name"], str):
            yield node
        for key in ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers"):
            yield from tasks(node.get(key))


def test_no_task_name_uses_the_loop_variable():
    offenders = []
    for path in ANSIBLE_DIR.rglob("*.yaml"):
        if ".venv" in path.parts or "golden" in path.parts:
            continue
        try:
            content = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        offenders += [f"{path.relative_to(ANSIBLE_DIR)}: {t['name']}" for t in tasks(content) if LOOP_VAR.search(t["name"])]
    assert offenders == []
