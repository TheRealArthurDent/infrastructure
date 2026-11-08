"""Renovate keeps the stack's pinned images up to date (raspberry#238).

The compose file only exists as the stack role's Jinja template, which Renovate's docker-compose manager does not
pick up by default.
"""
import fnmatch
import json
import re
from pathlib import Path

REPO_DIR = Path(__file__).parents[3]
COMPOSE_TEMPLATE = "ansible/roles/stack/templates/docker-compose.yaml.j2"


def matches(pattern, path):
    # Renovate reads "/.../" as a regex and anything else as a glob.
    if len(pattern) > 1 and pattern.startswith("/") and pattern.endswith("/"):
        return re.search(pattern[1:-1], path) is not None
    return fnmatch.fnmatch(path, pattern)


def test_renovate_updates_the_compose_template():
    config = json.loads((REPO_DIR / "renovate.json").read_text())
    patterns = config.get("docker-compose", {}).get("managerFilePatterns", [])
    assert any(matches(p, COMPOSE_TEMPLATE) for p in patterns), patterns
