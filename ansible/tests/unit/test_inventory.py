"""The inventory keeps shared values in group_vars and per-host values in host_vars (#5).

hosts.yaml only says how to reach a host. The refactor must not change what a host ends up with, so the resolved
variables are pinned here.
"""
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
INVENTORY = ANSIBLE_DIR / "inventory"

CONNECTION_VARS = {"ansible_host", "ansible_port", "ansible_user"}

EXPECTED = {
    "raspi": {
        "ansible_host": "192.168.1.2",
        "ansible_port": 1357,
        "ansible_user": "smarthome",
        "ansible_ssh_private_key_file": "~/.ssh/id_ed25519",
        "ansible_become": True,
        "dhparameter_bits": 4096,
    },
    "testbench": {
        "ansible_host": "192.168.1.7",
        "ansible_port": 4242,
        "ansible_user": "testuser",
        "ansible_ssh_private_key_file": "~/.ssh/id_ed25519",
        "ansible_become": True,
        "dhparameter_bits": 2048,
        "resticprofile_restore_drill_enabled": False,
        "keepass_database": "testbench",
    },
    "dungeon": {
        "ansible_host": "192.168.1.5",
        "ansible_port": 1359,
        "ansible_user": "dragon",
        "ansible_ssh_private_key_file": "~/.ssh/id_ed25519",
        "ansible_become": True,
        "dhparameter_bits": 4096,
        "keepass_database": "dungeon",
        "base_dns_servers": ["192.168.1.2"],
        "docker_daemon_dns": ["192.168.1.2"],
        "base_rsnapshot_enabled": False,
    },
}


@pytest.fixture(scope="module")
def hostvars():
    result = subprocess.run(
        ["ansible-inventory", "--list"], cwd=ANSIBLE_DIR, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)["_meta"]["hostvars"]


def test_hosts_yaml_only_has_connection_vars():
    hosts = yaml.safe_load((INVENTORY / "hosts.yaml").read_text())["all"]["hosts"]
    for host, variables in hosts.items():
        assert set(variables) <= CONNECTION_VARS, f"{host}: move {set(variables) - CONNECTION_VARS} to *_vars/"


@pytest.mark.parametrize("host", sorted(EXPECTED))
def test_host_has_its_own_host_vars(host):
    assert (INVENTORY / "host_vars" / f"{host}.yaml").is_file()


@pytest.mark.parametrize("host", sorted(EXPECTED))
def test_resolved_vars_are_unchanged(hostvars, host):
    resolved = {name: hostvars[host].get(name) for name in EXPECTED[host]}
    assert resolved == EXPECTED[host]
