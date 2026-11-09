"""Unit tests for the compose_merge filter plugin."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError

PLUGIN = Path(__file__).parents[2] / "roles" / "stack" / "filter_plugins" / "compose_merge.py"


def compose_merge(fragments):
    spec = importlib.util.spec_from_file_location("compose_merge_filter", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FilterModule().filters()["compose_merge"](fragments)


def test_merges_all_sections_in_order():
    merged = compose_merge([
        {"services": {"a": {"image": "a"}}, "networks": {"n1": {}}},
        {"services": {"b": {"image": "b"}}, "volumes": {"v": {}}, "secrets": {"s": {}}},
    ])
    assert merged == {
        "services": {"a": {"image": "a"}, "b": {"image": "b"}},
        "networks": {"n1": {}},
        "volumes": {"v": {}},
        "secrets": {"s": {}},
    }
    assert list(merged["services"]) == ["a", "b"]


def test_duplicate_service_names_service_and_indexes():
    with pytest.raises(AnsibleFilterError) as err:
        compose_merge([{"services": {"web": {}}}, {}, {"services": {"web": {}}}])
    message = str(err.value)
    assert "web" in message and "0" in message and "2" in message


def test_duplicate_network_fails():
    with pytest.raises(AnsibleFilterError, match="siz"):
        compose_merge([{"networks": {"siz": {}}}, {"networks": {"siz": {}}}])


def test_unknown_top_level_key_fails():
    with pytest.raises(AnsibleFilterError, match="x-foo.*fragment 1"):
        compose_merge([{"services": {}}, {"x-foo": {}}])


def test_empty_list_gives_empty_dict():
    assert compose_merge([]) == {}


def test_role_embedded_plugin_loads_for_a_playbook_outside_ansible(tmp_path):
    """A role that ships the plugin makes the filter available to any playbook using it (e.g. Molecule's)."""
    role = tmp_path / "roles" / "probe"
    (role / "tasks").mkdir(parents=True)
    (role / "filter_plugins").symlink_to(PLUGIN.parent)
    (role / "tasks" / "main.yaml").write_text(
        "- name: Merge\n  ansible.builtin.assert:\n    that: \"([{'services': {'a': {}}}] | compose_merge) == {'services': {'a': {}}}\"\n"
    )
    playbook = tmp_path / "play.yaml"
    playbook.write_text("- hosts: localhost\n  gather_facts: false\n  roles: [probe]\n")
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[defaults]\n")
    result = subprocess.run(
        ["ansible-playbook", str(playbook)], cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "ANSIBLE_ROLES_PATH": str(tmp_path / "roles"), "ANSIBLE_CONFIG": str(cfg)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
