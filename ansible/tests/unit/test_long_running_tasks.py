"""Long-running tasks must not hang on a silent ssh connection.

Generating 4096-bit Diffie-Hellman parameters takes about an hour on a Pi. The ssh connection stays silent all that
time; somewhere on the way (WSL2's NAT, the router) it was dropped, and without keepalives Ansible waited forever
although the task had finished on the Pi.
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
    dhparam = [task for task in tasks if "community.crypto.openssl_dhparam" in task]
    check = next(task for task in dhparam if task.get("check_mode"))
    generate = next(task for task in dhparam if not task.get("check_mode"))
    return check, generate


def test_dhparam_generation_runs_async_and_is_polled():
    _, dhparam = dhparam_tasks()

    # Polling opens a new short connection every 30 s, so a dropped connection can't hang the run.
    assert dhparam.get("poll") == 30
    # 4096 bits take about an hour on raspi; leave room for a slower Pi.
    assert dhparam.get("async") == 7200


def test_dhparam_generation_only_starts_when_the_check_finds_a_change():
    # Even with nothing to do, an async task waits for its first poll, so a quick dry run decides first.
    check, generate = dhparam_tasks()

    assert check["community.crypto.openssl_dhparam"] == generate["community.crypto.openssl_dhparam"]
    assert check.get("register") == "base_dhparam_check"
    assert "changed_when" not in check
    assert generate.get("when") == ["base_dhparam_check.changed", "not ansible_check_mode"]
