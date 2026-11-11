"""The secrets role's check that the restic key pair from KeePass is the one the backup host accepts.

The task runs verbatim against an inventory with a backup host "dungeon" that lists clients. Only public keys are
involved; the private key must never show up in the output.
"""
import os
import subprocess

import yaml

from render import ANSIBLE_DIR, role_task

TASK = "Ensure the restic SSH key in KeePass is the one the backup host accepts"
RASPI = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICc/eKct+1gtT9eqDeH8IBIi8XMjArF5mpxWommghE55"
TESTBED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDhOThOZGl1G7FFAY2HUFIrMzi/wK+bcoVarIiZLnsDk"
PRIVATE = "LEAKCANARY-private-key"


def run_assert(tmp_path, host, public_key, backup_host_listed=True, key_title="Resticprofile SSH testbench"):
    local = {"ansible_connection": "local", "ansible_python_interpreter": "auto_silent"}
    hosts = {host: local}
    if backup_host_listed:
        hosts["dungeon"] = {**local, "backup_target_clients": {"raspi": RASPI, "testbench": TESTBED}}
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"all": {"hosts": hosts}}))
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Run the key assert",
        "hosts": host,
        "gather_facts": False,
        "vars": {
            "stack_components": ["resticprofile"],
            "resticprofile_repository_is_sftp": True,
            "resticprofile_backup_host": "dungeon",
            "resticprofile_ssh_key_title": key_title,
            "resticprofile_ssh_public_key": public_key,
            "resticprofile_ssh_private_key": PRIVATE,
        },
        "tasks": [role_task("secrets/tasks/main.yaml", TASK)],
    }]))
    return subprocess.run(
        ["ansible-playbook", "-i", str(inventory), str(playbook)],
        cwd=ANSIBLE_DIR, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
    )


def test_the_matching_key_passes_whatever_the_comment_and_trailing_newline(tmp_path):
    run = run_assert(tmp_path, "raspi", RASPI + " root@raspberry\n")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "skipping" not in run.stdout


def test_a_different_key_fails_with_the_next_step(tmp_path):
    run = run_assert(tmp_path, "raspi", TESTBED + " root@testbench")
    assert run.returncode != 0, run.stdout
    out = " ".join((run.stdout + run.stderr).split())
    assert "Resticprofile SSH testbench" in out
    assert "backup_target_clients.raspi" in out
    assert "Replace the attachments" in out
    assert PRIVATE not in out


def test_a_host_the_backup_host_does_not_list_is_skipped(tmp_path):
    # Molecule's raspi-trixie, or a new host without an entry yet
    run = run_assert(tmp_path, "other", TESTBED)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "skipping" in run.stdout


def test_a_missing_backup_host_in_the_inventory_is_skipped(tmp_path):
    run = run_assert(tmp_path, "raspi", TESTBED, backup_host_listed=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "skipping" in run.stdout
