"""Values in the .env files the secrets role writes reach Docker Compose verbatim.

Compose reads the files as env files (env_file:, --env-file) and interpolates unquoted values: a password with
`$XXX` lost that part on dungeon. The compose_env filter writes each value double-quoted, with `\\`, `"` and `$`
escaped, which Compose reads back literally.
"""
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
PLUGIN = ANSIBLE_DIR / "roles" / "secrets" / "filter_plugins" / "compose_env.py"
SECRETS_TASKS = ANSIBLE_DIR / "roles" / "secrets" / "tasks" / "main.yaml"

TRICKY = [
    "plain",
    "a$XXX b",
    "a${XXX}b",
    "$$",
    "ends$",
    "it's",
    'quote"d',
    "back\\slash",
    "end\\",
    "new\\nline",
    "back\\t tab \\$ dollar",
    "two\nlines",
    "hash #comment",
    "tab\tx",
    "x'$y\"z\\",
    "`cmd` $(cmd) !bang %pct ^&*|<>;",
    " lead and trail ",
    "",
]


def compose_env():
    spec = importlib.util.spec_from_file_location("compose_env", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FilterModule().filters()["compose_env"]


def test_values_are_double_quoted_with_backslash_quote_and_dollar_escaped():
    quote = compose_env()
    assert quote("a$XXX b") == '"a\\$XXX b"'
    assert quote('x"\\') == '"x\\"\\\\"'
    assert quote(8080) == '"8080"'


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker compose CLI")
@pytest.mark.parametrize("value", TRICKY)
def test_compose_reads_the_value_back_verbatim(tmp_path, value):
    """Through env_file: and through ${VAR} interpolation from --env-file, like the stacks use them."""
    (tmp_path / "x.env").write_text(f"P={compose_env()(value)}\n")
    (tmp_path / "compose.yaml").write_text(
        "services:\n  a:\n    image: busybox:1.36\n    env_file: [x.env]\n    environment:\n      VIA: ${P}\n"
    )
    result = subprocess.run(
        ["docker", "compose", "--env-file", "x.env", "config", "--format", "json"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    environment = json.loads(result.stdout)["services"]["a"]["environment"]
    # `config` escapes $ as $$ in its own output, so the output can be read again
    assert {k: v.replace("$$", "$") for k, v in environment.items()} == {"P": value, "VIA": value}


def env_file_contents():
    """(dest, content) of every .env file the secrets role writes, including the ones nested in blocks."""
    pending = list(yaml.safe_load(SECRETS_TASKS.read_text()))
    while pending:
        task = pending.pop(0)
        pending[:0] = task.get("block", [])
        copy = task.get("ansible.builtin.copy", {})
        if str(copy.get("dest", "")).endswith(".env"):
            yield copy["dest"], copy["content"]


def test_the_role_writes_env_files():
    assert len(list(env_file_contents())) >= 5


@pytest.mark.parametrize("dest,content", list(env_file_contents()), ids=lambda x: x if x.startswith("/") else "")
def test_every_templated_env_value_goes_through_compose_env(dest, content):
    for line in content.splitlines():
        if "{{" in line:
            assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\{\{ .+ \| compose_env \}\}", line), f"{dest}: {line}"
