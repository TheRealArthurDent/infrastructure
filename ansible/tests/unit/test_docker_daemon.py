"""The Docker daemon config the docker role writes to /etc/docker/daemon.json.

Without a size limit the default json-file driver lets a container's log grow until the disk is full: on dungeon a
container printing the same prompt in a loop filled the NVMe within hours (#70).
"""
import json
from pathlib import Path

import jinja2
import yaml

ROLE_DIR = Path(__file__).parents[2] / "roles" / "docker"
TEMPLATE = ROLE_DIR / "templates" / "etc" / "docker" / "daemon.json.j2"


def rendered_config(**overrides):
    defaults = yaml.safe_load((ROLE_DIR / "defaults" / "main.yaml").read_text())
    defaults["docker_daemon_dns"] = ["192.0.2.2"]
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["to_json"] = json.dumps
    return json.loads(env.from_string(TEMPLATE.read_text()).render(**{**defaults, **overrides}))


def test_container_logs_are_capped_and_rotated():
    config = rendered_config()

    assert config["log-driver"] == "local"
    assert config["log-opts"] == {"max-size": "10m", "max-file": "3"}


# dockerd refuses to start when a log-opt is a number instead of a string.
def test_log_options_are_strings_even_when_set_as_numbers():
    config = rendered_config(docker_daemon_log_opts={"max-size": "50m", "max-file": 5})

    assert config["log-opts"] == {"max-size": "50m", "max-file": "5"}


def test_existing_daemon_settings_are_kept():
    config = rendered_config()

    assert config["dns"] == ["192.0.2.2"]
    assert config["max-concurrent-uploads"] == 1
    assert config["max-concurrent-downloads"] == 1
