"""The retention of the default profile must find the snapshots it is meant to thin out.

resticprofile passes the backup sources to restic forget as --path filters, and restic compares them with the
snapshot's paths as they are. restic stores a source as a clean path, so '/source/' was saved as '/source' and the
filter '--path /source/' matched no snapshot: nothing was ever forgotten (113 snapshots of raspi instead of 20), and
the offsite copy uploaded the snapshots its own retention had removed again every day.
"""
import posixpath

import pytest
import yaml

from render import rendered_tree

HOSTS = ["raspi", "dungeon", "testbench"]


@pytest.fixture(scope="module", params=HOSTS)
def profiles(request, tmp_path_factory):
    tree = rendered_tree(tmp_path_factory.mktemp(request.param), request.param)
    return yaml.safe_load(tree["resticprofile/config/profiles.yaml"])


def test_backup_sources_are_clean_paths(profiles):
    sources = profiles["default"]["backup"]["source"]
    sources = [sources] if isinstance(sources, str) else sources
    assert all(source == posixpath.normpath(source) for source in sources), sources


def test_retention_filters_by_the_backup_source(profiles):
    # resticprofile's default (path: true) filters by the backup sources; another filter would need its own test
    assert profiles["default"]["retention"].get("path", True) is True
