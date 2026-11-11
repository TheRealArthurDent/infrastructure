"""Awkward secret values for the tests, and how Docker Compose reads a .env file.

Each value is a classic way to break a password on its way from KeePass to a container: Compose interpolation
(`$`, `${}`), Jinja (`{{ }}`, `{% %}`, `{# #}`), shell metacharacters, quotes and backslashes, a `#` that a .env
parser may take for a comment, leading and trailing blanks, values YAML would turn into numbers, booleans or null,
YAML tags and anchors, printf-style placeholders and non-ASCII characters.
"""
import json
import shutil
import subprocess

AWKWARD = [
    "tes$abc$bla{xyz}blub",
    "${HOME}$$x$",
    "$",
    "{{ 7 * 6 }}",
    "{% raw %}x{% endraw %}{# c #}",
    "a\"b'c`d",
    "back\\slash\\",
    "\\n\\t\\$x\\\"",
    " lead and trail ",
    "a #not-a-comment",
    "#start",
    "a=b=c",
    "$(id) `id`; rm -rf / && echo |",
    "äöü€ß",
    "0123",
    "true",
    "null",
    "%s %(x)s %%",
    "!unsafe *anchor &ref",
    "x: y - z",
    '"',
    "'",
    "\\",
    "'quoted'",
    '"quoted"',
]

# InfluxDB's init script puts its passwords into InfluxQL in single quotes, so they can't hold ' or \ (the secrets
# role refuses them). These are the awkward values that are left.
AWKWARD_INFLUXDB = [value for value in AWKWARD if "'" not in value and "\\" not in value]

# deCONZ' start script writes its VNC password with dash's echo, which expands backslash escapes.
AWKWARD_VNC = [value for value in AWKWARD if "\\" not in value]


def compose_available():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True, check=False).returncode == 0


def compose_reads(env_file, keys, workdir):
    """What Compose makes of the keys in env_file, both as env_file: of a service and as the project's .env.

    Returns (from env_file:, from ${KEY} interpolation). `docker compose config` needs no daemon. Its output
    escapes every literal $ as $$, which is undone here.
    """
    compose = {
        "services": {
            "env_file": {"image": "alpine", "env_file": [str(env_file)]},
            "interpolated": {"image": "alpine", "environment": {key: "${%s}" % key for key in keys}},
        }
    }
    (workdir / "compose.yaml").write_text(json.dumps(compose))
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "config", "--format", "json"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]

    def unescape(environment):
        return {key: (value or "").replace("$$", "$") for key, value in environment.items() if key in keys}

    return unescape(services["env_file"]["environment"]), unescape(services["interpolated"]["environment"])
