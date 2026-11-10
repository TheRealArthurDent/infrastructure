"""Unit tests for the dotenv_quote filter: a value in a .env file must reach the container unchanged."""
import importlib.util
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError

from secret_values import AWKWARD, compose_available, compose_reads

PLUGIN = Path(__file__).parents[2] / "roles" / "secrets" / "filter_plugins" / "dotenv.py"

needs_compose = pytest.mark.skipif(not compose_available(), reason="needs the docker compose CLI (no daemon)")


def dotenv_quote(value):
    spec = importlib.util.spec_from_file_location("dotenv_filter", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FilterModule().filters()["dotenv_quote"](value)


@needs_compose
def test_compose_reads_every_awkward_value_back_unchanged(tmp_path):
    keys = {f"V{index}": value for index, value in enumerate(AWKWARD)}
    env_file = tmp_path / "test.env"
    env_file.write_text("".join(f"{key}={dotenv_quote(value)}\n" for key, value in keys.items()))
    from_env_file, interpolated = compose_reads(env_file, keys, tmp_path)
    assert from_env_file == keys
    assert interpolated == keys


@needs_compose
def test_unquoted_values_really_break(tmp_path):
    """The reason for the filter: Compose expands $abc in an unquoted .env value."""
    env_file = tmp_path / "test.env"
    env_file.write_text("PW=tes$abc$bla{xyz}blub\n")
    from_env_file, _ = compose_reads(env_file, ["PW"], tmp_path)
    assert from_env_file["PW"] == "tes{xyz}blub"


@pytest.mark.parametrize("value", AWKWARD)
def test_quoted_value_is_one_double_quoted_line(value):
    quoted = dotenv_quote(value)
    assert quoted.startswith('"') and quoted.endswith('"')
    assert "\n" not in quoted


def test_numbers_are_quoted_as_text():
    assert dotenv_quote(9000) == '"9000"'


def test_empty_value_stays_empty():
    assert dotenv_quote("") == '""'


@pytest.mark.parametrize("value", ["line\nbreak", "carriage\rreturn", "nul\0byte", "bell\a"])
def test_control_characters_are_refused(value):
    with pytest.raises(AnsibleFilterError, match="control character"):
        dotenv_quote(value)


def test_refusal_does_not_show_the_value():
    with pytest.raises(AnsibleFilterError) as err:
        dotenv_quote("SECRET-VALUE\n")
    assert "SECRET-VALUE" not in str(err.value)
