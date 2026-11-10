"""Secrets reach the files on the host verbatim, whatever characters KeePass holds.

Runs the real tasks: bootstrap.yaml's "Read secrets from KeePass" (with a stand-in for the keepass lookup that
returns one hostile value for every entry) and the secrets role's tasks that write files, with their paths moved
into a temporary directory. Then Docker Compose reads the .env files back (test_compose_env.py covers the quoting
itself). Jinja in a secret must not be evaluated, a number-like or list-like secret must stay a string, and
whitespace must survive.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
SECRETS_TASKS = ANSIBLE_DIR / "roles" / "secrets" / "tasks" / "main.yaml"

HOSTILE = [
    "a$XXX b ${Y} $$ end$",
    "{{ 7 * 7 }}",
    "{% if true %}x{% endif %}",
    "{# comment #}",
    "{{ lookup('ansible.builtin.env', 'HOME') }}",
    "it's \"quoted\" \\ back\\slash \\n",
    "hash #comment ; semi & amp | pipe",
    "`cmd` $(cmd) !bang %pct ^ * < > ~",
    "0123",
    "1e3",
    "true",
    "null",
    "[1, 2]",
    "{'a': 1}",
    " lead and trail ",
    "ümlaut € 🔑",
    "- dash: colon",
]

FAKE_KEEPASS = '''
import os
from ansible.plugins.lookup import LookupBase


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        return [os.environ["HOSTILE_SECRET"]]
'''

ENV_FILES = {
    "/etc/docker/infrastructure/.env": ["FTLCONF_webserver_api_password", "WEBPASSWORD"],
    "/etc/docker/infrastructure/.outbound.env": ["FTLCONF_webserver_api_password", "WEBPASSWORD"],
    "/etc/docker/smart-home/.env": [
        "IOBROKER_STATES_REDIS_PORT", "INFLUXDB_ADMIN_PASSWORD", "INFLUXDB_READ_PASSWORD",
        "INFLUXDB_WRITE_PASSWORD", "GRAFANA_PASSWORD", "MOSQUITTO_PASSWD", "DECONZ_VNC_PASSWORD", "GITHUB_USER",
        "GITHUB_EMAIL",
    ],
    "/etc/docker/opencloud/.env": ["OPENCLOUD_ADMIN_PASSWORD"],
}


def read_secrets_task():
    plays = yaml.safe_load((ANSIBLE_DIR / "bootstrap.yaml").read_text())
    bootstrap = next(play for play in plays if play["name"] == "Bootstrap Raspberry Pi")
    task = next(t for t in bootstrap["pre_tasks"] if t["name"] == "Read secrets from KeePass")
    return task, bootstrap["vars"]


def file_tasks(root):
    """The secrets role's tasks, with every path below root and without owner/group (CI doesn't run as root)."""
    def move(tasks):
        for task in tasks:
            if "block" in task:
                move(task["block"])
            for module in ("ansible.builtin.copy", "ansible.builtin.file"):
                if module in task:
                    args = task[module]
                    key = "dest" if "dest" in args else "path"
                    args[key] = f"{root}{args[key]}"
                    args.pop("owner", None)
                    args.pop("group", None)
        return tasks

    return move(yaml.safe_load(SECRETS_TASKS.read_text()))


@pytest.fixture(scope="module")
def playbook(tmp_path_factory):
    work = tmp_path_factory.mktemp("secret-values")
    (work / "lookup_plugins").mkdir()
    (work / "lookup_plugins" / "keepass.py").write_text(FAKE_KEEPASS)
    (work / "filter_plugins").mkdir()
    shutil.copy(ANSIBLE_DIR / "roles" / "secrets" / "filter_plugins" / "compose_env.py", work / "filter_plugins")
    task, play_vars = read_secrets_task()
    play = {
        "name": "Write the secrets",
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            **play_vars,
            "keepass_db": "unused", "keepass_password": "unused", "keepass_key": "",
            "stack_components": ["pihole", "outbound", "healthmonitor", "resticprofile"],
            "resticprofile_repository_is_sftp": True,
            "secrets_smart_home_env": True,
            "secrets_opencloud_env": True,
            "discord_docker_webhook_title": "t", "resticprofile_passphrase_title": "t",
            "resticprofile_ssh_key_title": "t", "opencloud_admin_title": "t",
            "resticprofile_backup_host": "nowhere",
        },
        "tasks": [task, {"name": "Write them", "ansible.builtin.include_tasks": "secrets.yaml"}],
    }
    (work / "playbook.yaml").write_text(yaml.safe_dump([play], sort_keys=False))
    return work


def compose_environment(work, env_file):
    """The environment Compose gives a service from env_file (`config` writes $ as $$)."""
    (work / "compose.yaml").write_text(f"services:\n  a:\n    image: busybox:1.36\n    env_file: [{env_file}]\n")
    config = subprocess.run(
        ["docker", "compose", "config", "--format", "json"], cwd=work, capture_output=True, text=True, check=True
    )
    return {k: v.replace("$$", "$") for k, v in json.loads(config.stdout)["services"]["a"]["environment"].items()}


@pytest.mark.parametrize("secret", HOSTILE)
def test_secrets_reach_the_files_verbatim(playbook, tmp_path, secret):
    (playbook / "secrets.yaml").write_text(yaml.safe_dump(file_tasks(tmp_path), sort_keys=False))
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", "playbook.yaml"],
        cwd=playbook, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
        env={**os.environ, "HOSTILE_SECRET": secret},
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # Files without Compose in between hold the value itself
    assert (tmp_path / "etc/docker/infrastructure/secrets/restic_passphrase").read_text() == f"{secret}\n"

    if shutil.which("docker") is None:
        pytest.skip("needs the docker compose CLI to read the .env files back")
    for dest, keys in ENV_FILES.items():
        env_file = tmp_path / dest.lstrip("/")
        environment = compose_environment(tmp_path, env_file)
        for key in keys:
            assert environment[key] == secret, f"{dest}: {key}"

    environment = compose_environment(tmp_path, tmp_path / "etc/docker/infrastructure/.healthmonitor.env")
    assert environment["CONTAINERMON_NOTIFICATION_URL"] == f"discord://{secret}@{secret}"
