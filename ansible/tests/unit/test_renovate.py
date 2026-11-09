"""Renovate keeps the stack's pinned images up to date (raspberry#238).

The compose file only exists as the components' Jinja fragments (roles/<component>/templates/docker-compose.yaml.j2),
which Renovate's docker-compose manager does not pick up by default.
"""
import fnmatch
import json
import re
from pathlib import Path

import pytest
import yaml

REPO_DIR = Path(__file__).parents[3]
COMPOSE_FRAGMENTS = sorted(
    str(p.relative_to(REPO_DIR)) for p in (REPO_DIR / "ansible" / "roles").glob("*/templates/docker-compose.yaml.j2")
)


def matches(pattern, path):
    # Renovate reads "/.../" as a regex and anything else as a glob.
    if len(pattern) > 1 and pattern.startswith("/") and pattern.endswith("/"):
        return re.search(pattern[1:-1], path) is not None
    return fnmatch.fnmatch(path, pattern)


def test_there_are_compose_fragments():
    assert COMPOSE_FRAGMENTS


@pytest.mark.parametrize("fragment", COMPOSE_FRAGMENTS)
def test_renovate_updates_the_compose_fragments(fragment):
    config = json.loads((REPO_DIR / "renovate.json").read_text())
    patterns = config.get("docker-compose", {}).get("managerFilePatterns", [])
    assert any(matches(p, fragment) for p in patterns), patterns


@pytest.mark.parametrize("fragment", COMPOSE_FRAGMENTS)
def test_compose_fragments_are_valid_yaml(fragment):
    # Renovate parses the fragments as they are, Jinja and all: a {% if %} line of its own breaks that and Renovate
    # silently stops finding the images. Conditions go into YAML comments ("# {% if ... +%}").
    compose = yaml.safe_load((REPO_DIR / fragment).read_text())
    assert compose["services"]
