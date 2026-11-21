"""Long-running tasks must not hang on a silent ssh connection, and the longest one doesn't run on the Pi at all.

Generating 4096-bit Diffie-Hellman parameters takes about an hour on a Pi. The ssh connection stayed silent all that
time; somewhere on the way (WSL2's NAT, the router) it was dropped, and without keepalives Ansible waited forever
although the task had finished on the Pi. Run async instead, it failed at its end, because the module's payload
below /tmp on the Pi had vanished. Now the control node generates them and copies them to the host.
"""
import shlex
import subprocess
from pathlib import Path

import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
BASE_TASKS = ANSIBLE_DIR / "roles" / "base" / "tasks" / "main.yaml"


def ssh_options():
    # Ask Ansible itself: its ini parser drops continuation lines that Python's configparser would accept.
    dump = subprocess.run(
        ["ansible-config", "dump", "--type", "connection", "--only-changed"],
        cwd=ANSIBLE_DIR, capture_output=True, text=True, check=True,
    ).stdout
    value = next(line for line in dump.splitlines() if line.startswith("ssh_args(")).split(" = ", 1)[1]
    args = shlex.split(value)
    return dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "-o")


def test_ssh_detects_a_dead_connection():
    options = ssh_options()

    assert options.get("ServerAliveInterval") == "30"
    assert options.get("ServerAliveCountMax") == "4"


def test_ssh_args_keep_the_existing_options():
    options = ssh_options()

    assert options.get("ControlMaster") == "auto"
    assert options.get("ControlPersist") == "60s"
    assert options.get("StrictHostKeyChecking") == "accept-new"


def dhparam_tasks():
    tasks = yaml.safe_load(BASE_TASKS.read_text())
    check = next(task for task in tasks if "community.crypto.openssl_dhparam" in task)
    generate = next(task for task in tasks if "Diffie-Hellman" in task.get("name", "") and "block" in task)
    return check, generate


def test_dhparam_check_runs_on_the_host_in_check_mode():
    check, _ = dhparam_tasks()

    assert check["community.crypto.openssl_dhparam"]["path"] == "/etc/ssl/certs/dhparam.pem"
    assert check.get("check_mode") is True
    assert check.get("register") == "base_dhparam_check"
    assert "changed_when" not in check
    assert "delegate_to" not in check


def test_dhparam_generation_only_starts_when_the_check_finds_a_change():
    _, generate = dhparam_tasks()

    assert generate.get("when") == ["base_dhparam_check.changed", "not ansible_check_mode"]


def test_dhparam_generation_runs_on_the_control_node():
    # On raspi, 4096 bits took an hour, and the async run failed at its very end because the module's payload below
    # /tmp on the Pi was gone. The control node needs minutes and no async job.
    _, generate = dhparam_tasks()
    steps = generate["block"]
    dhparam = next(task for task in steps if "community.crypto.openssl_dhparam" in task)

    assert dhparam.get("delegate_to") == "localhost"
    assert dhparam.get("become") is False
    assert dhparam["community.crypto.openssl_dhparam"]["size"] == "{{ dhparameter_bits }}"
    assert not any("async" in task for task in steps)


def test_dhparam_file_is_copied_to_the_host():
    check, generate = dhparam_tasks()
    copy = next(task for task in generate["block"] if "ansible.builtin.copy" in task)["ansible.builtin.copy"]

    assert copy["dest"] == check["community.crypto.openssl_dhparam"]["path"]
    assert (copy["owner"], copy["group"], copy["mode"]) == ("root", "root", "0644")


def test_dhparam_temporary_file_on_the_control_node_is_removed():
    _, generate = dhparam_tasks()
    cleanup = generate.get("always", [])

    assert any(
        task.get("ansible.builtin.file", {}).get("state") == "absent" and task.get("delegate_to") == "localhost"
        for task in cleanup
    )
