"""Awkward secrets from KeePass reach the containers unchanged.

Runs the real path of bootstrap.yaml on localhost: its task "Read secrets from KeePass" verbatim against a throwaway
KDBX holding awkward values (tests/unit/secret_values.py), then the .env tasks of the secrets role verbatim (only
their dest moved to a temporary directory), then reads each .env file with Docker Compose. That covers Jinja (a
KeePass value is never rendered as a template), the .env syntax and Compose's interpolation of $ in one go.
"""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from pykeepass import create_database

from secret_values import AWKWARD, AWKWARD_INFLUXDB, AWKWARD_VNC, compose_available, compose_reads

ANSIBLE_DIR = Path(__file__).parents[2]
SECRETS_ROLE = ANSIBLE_DIR / "roles" / "secrets"
# The master password is awkward too; it comes from the environment, as with bin/with-keepass.
PASSWORD = "master {{ 7 * 6 }} $HOME ' \" \\ #"
ENV_DIRS = ("/etc/docker/infrastructure/", "/etc/docker/smart-home/", "/etc/docker/opencloud/")

pytestmark = pytest.mark.skipif(not compose_available(), reason="needs the docker compose CLI (no daemon)")


def tasks_where(tasks, predicate):
    """The tasks in a task list, blocks included, for which predicate holds."""
    found = []
    for task in tasks:
        if predicate(task):
            found.append(task)
        for key in ("block", "rescue", "always"):
            found += tasks_where(task.get(key, []), predicate)
    return found


def bootstrap_play():
    return next(play for play in yaml.safe_load((ANSIBLE_DIR / "bootstrap.yaml").read_text())
                if play["name"] == "Bootstrap Raspberry Pi")


def secrets_tasks(out):
    """The secrets role's checks of the secret values and its .env tasks, with dest moved into out."""
    role = yaml.safe_load((SECRETS_ROLE / "tasks" / "main.yaml").read_text())
    checks = tasks_where(role, lambda task: "ansible.builtin.assert" in task and "secret" in task["name"].lower())
    assert checks, "the secrets role checks no secret values"
    env_tasks = tasks_where(
        role, lambda task: str(task.get("ansible.builtin.copy", {}).get("dest", "")).startswith(ENV_DIRS)
        and ".env" in task["ansible.builtin.copy"]["dest"])
    assert len(env_tasks) == 5, [task["name"] for task in env_tasks]
    for task in env_tasks:
        copy = task["ansible.builtin.copy"]
        copy["dest"] = str(out / copy["dest"].removeprefix("/etc/docker/").replace("/", "_"))
        copy.pop("owner", None)
        copy.pop("group", None)
        task.pop("when", None)
    return checks + env_tasks


# KeePass entries the secrets role writes into .env files; the InfluxDB and VNC ones take fewer characters.
GENERAL = ["pihole", "outbound", "webhook_id", "webhook_token", "restic", "grafana", "mosquitto", "github_user",
           "github_email", "opencloud"]
SPECIAL = {"influx_admin": AWKWARD_INFLUXDB, "influx_read": AWKWARD_INFLUXDB, "influx_write": AWKWARD_INFLUXDB,
           "vnc": AWKWARD_VNC}
# Three runs give every awkward value at least one general entry.
OFFSETS = [0, len(GENERAL), 2 * len(GENERAL)]


def secrets(offset):
    """One awkward value per KeePass entry; offset shifts which entry gets which."""
    values = {name: AWKWARD[(offset + index) % len(AWKWARD)] for index, name in enumerate(GENERAL)}
    for index, (name, candidates) in enumerate(SPECIAL.items()):
        values[name] = candidates[(offset + index) % len(candidates)]
    return values


def make_kdbx(path, values):
    defaults = yaml.safe_load((SECRETS_ROLE / "defaults" / "main.yaml").read_text())
    kp = create_database(str(path), password=PASSWORD)

    def group(name, parent=None):
        return kp.add_group(parent or kp.root_group, name)

    def entry(grp, title, password="", **fields):
        # pykeepass looks for a duplicate by XPath, which a quote in the username breaks: set it afterwards.
        item = kp.add_entry(grp, title, "", password)
        item.username = fields.pop("username", "")
        for key, value in fields.items():
            item.set_custom_property(key, value)

    pihole = group("Pihole")
    entry(pihole, "pihole", values["pihole"])
    entry(pihole, "outbound", values["outbound"])
    entry(group("Discord"), defaults["discord_docker_webhook_title"],
          id=values["webhook_id"], token=values["webhook_token"])
    entry(group("Backup"), defaults["resticprofile_passphrase_title"], values["restic"])
    smart_home = group("smart-home")
    iobroker = group("ioBroker", smart_home)
    entry(iobroker, "States Redis", port="9000")
    entry(iobroker, "Objects Redis", port="9001")
    influx = group("Influx DB", smart_home)
    entry(influx, "Influx admin", values["influx_admin"])
    entry(influx, "Influx read", values["influx_read"])
    entry(influx, "Influx write", values["influx_write"])
    entry(smart_home, "Grafana", values["grafana"])
    entry(smart_home, "mosquitto", values["mosquitto"])
    entry(group("deconz - Phoscon-GW", smart_home), "Deconz VNC", values["vnc"])
    entry(group("GitHub"), "NodeRed", username=values["github_user"], email=values["github_email"])
    entry(group("OpenCloud"), defaults["opencloud_admin_title"], values["opencloud"])
    kp.save()


def run_playbook(tmp_path, kdbx, tasks):
    play = bootstrap_play()
    read_secrets = next(task for task in play["pre_tasks"] if task["name"] == "Read secrets from KeePass")
    defaults = yaml.safe_load((SECRETS_ROLE / "defaults" / "main.yaml").read_text())
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Secrets",
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {
            **defaults,
            **play["vars"],
            "keepass_db": str(kdbx),
            "keepass_password": "{{ lookup('ansible.builtin.env', 'TEST_KEEPASS_PASSWORD') }}",
            "keepass_key": "",
            "stack_components": ["pihole", "outbound", "resticprofile", "healthmonitor"],
            "resticprofile_repository_is_sftp": False,
            "secrets_smart_home_env": True,
            "secrets_opencloud_env": True,
        },
        "tasks": [read_secrets, *tasks],
    }], sort_keys=False))
    env = {
        **os.environ,
        "ANSIBLE_CONFIG": str(ANSIBLE_DIR / "ansible.cfg"),
        "TEST_KEEPASS_PASSWORD": PASSWORD,
        "ANSIBLE_LOOKUP_PLUGINS": str(ANSIBLE_DIR / "lookup_plugins"),
        "ANSIBLE_FILTER_PLUGINS": str(SECRETS_ROLE / "filter_plugins"),
    }
    return subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)],
                          cwd=tmp_path, capture_output=True, text=True, env=env, check=False)


@pytest.mark.parametrize("offset", OFFSETS)
def test_awkward_keepass_values_reach_compose_unchanged(tmp_path, offset):
    values = secrets(offset)
    kdbx = tmp_path / "test.kdbx"
    make_kdbx(kdbx, values)
    out = tmp_path / "out"
    out.mkdir()
    result = run_playbook(tmp_path, kdbx, secrets_tasks(out))
    assert result.returncode == 0, result.stdout + result.stderr

    expected = {
        "infrastructure_.env": {"FTLCONF_webserver_api_password": values["pihole"], "WEBPASSWORD": values["pihole"],
                                "VIRTUAL_HOST": "pihole"},
        "infrastructure_.outbound.env": {"FTLCONF_webserver_api_password": values["outbound"],
                                         "WEBPASSWORD": values["outbound"], "VIRTUAL_HOST": "outbound"},
        "infrastructure_.healthmonitor.env": {
            "CONTAINERMON_NOTIFICATION_URL": f"discord://{values['webhook_token']}@{values['webhook_id']}"},
        "smart-home_.env": {
            "IOBROKER_STATES_REDIS_PORT": "9000",
            "IOBROKER_OBJECTS_REDIS_PORT": "9001",
            "INFLUXDB_ADMIN_PASSWORD": values["influx_admin"],
            "INFLUXDB_READ_PASSWORD": values["influx_read"],
            "INFLUXDB_WRITE_PASSWORD": values["influx_write"],
            "GRAFANA_PASSWORD": values["grafana"],
            "MOSQUITTO_PASSWD": values["mosquitto"],
            "DECONZ_VNC_PASSWORD": values["vnc"],
            "DECONZ_DEVICE_TYPE": "ttyACM0",
            "GITHUB_USER": values["github_user"],
            "GITHUB_EMAIL": values["github_email"],
        },
        "opencloud_.env": {"OPENCLOUD_ADMIN_PASSWORD": values["opencloud"]},
    }
    for name, keys in expected.items():
        from_env_file, interpolated = compose_reads(out / name, keys, tmp_path)
        assert from_env_file == keys, name
        assert interpolated == keys, name


def test_every_awkward_value_is_covered():
    covered = {value for offset in OFFSETS for value in secrets(offset).values()}
    assert set(AWKWARD) <= covered


@pytest.mark.parametrize(("entry", "value"), [
    ("influx_admin", "it's"),
    ("influx_read", "back\\slash"),
    ("influx_write", "it's"),
    ("vnc", "back\\slash"),
    ("pihole", "line\nbreak"),
    ("mosquitto", "tab\there"),
])
def test_values_the_consumers_cant_take_are_refused(tmp_path, entry, value):
    values = {**secrets(0), entry: value}
    kdbx = tmp_path / "test.kdbx"
    make_kdbx(kdbx, values)
    out = tmp_path / "out"
    out.mkdir()
    result = run_playbook(tmp_path, kdbx, secrets_tasks(out))
    assert result.returncode != 0
    assert not any(out.iterdir()), "no .env file may be written before the check"
    assert value not in result.stdout + result.stderr
