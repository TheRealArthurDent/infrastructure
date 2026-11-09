"""Golden files: today's rendered output of the stack role for each inventory host.

The refactor of the stack role into components (#14) must not change what lands on the hosts. Regenerate the files
after an intended change with: GOLDEN_UPDATE=1 pytest tests/unit/test_stack_golden.py
"""
import os
import shutil

import pytest
import yaml

from render import ANSIBLE_DIR, rendered_tree

GOLDEN = ANSIBLE_DIR / "tests" / "unit" / "golden"


@pytest.mark.parametrize("host", ["raspi", "testbed"])
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
