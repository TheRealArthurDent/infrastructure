"""Unit tests for the compose_merge filter plugin."""
import importlib.util
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError

PLUGIN = Path(__file__).parents[2] / "filter_plugins" / "compose_merge.py"


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
