"""The backup_target role: dungeon as the restic sftp target.

The check-and-marker tasks run verbatim on localhost with the mount point in tmp_path. The SSD itself (mount, user,
sshd login) is proven by Molecule; here: nothing is written below an unmounted mount point, the fstab entry is
by UUID and nofail, clients are required, and every client key ends up in authorized_keys, restricted.
"""
import os
import subprocess

import pytest
import yaml

from render import ANSIBLE_DIR, role_task

FILE = "backup_target/tasks/main.yaml"
CHECK_TASKS = (
    "Check the backup SSD is plugged in",
    "Stop when the backup SSD is not plugged in",
    "Check the backup SSD is mounted",
    "Stop when the backup SSD is not mounted",
    "Mark the mount point as the backup SSD",
    "Ensure the restic repositories directory exists",
)
KEYS = {
    "raspi": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICc/eKct+1gtT9eqDeH8IBIi8XMjArF5mpxWommghE55",
    "testbed": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDhOThOZGl1G7FFAY2HUFIrMzi/wK+bcoVarIiZLnsDk",
}
UUID = "eeeb6563-1a18-4941-a7bd-72ec86622fa7"


def run_play(tmp_path, tasks, extra_vars, assert_clients=False):
    defaults = yaml.safe_load((ANSIBLE_DIR / "roles/backup_target/defaults/main.yaml").read_text())
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Run the backup_target tasks",
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {**defaults, "backup_target_uuid": UUID, "ansible_python_interpreter": "auto_silent", **extra_vars},
        "tasks": tasks,
    }]))
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(playbook)],
        cwd=ANSIBLE_DIR,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
    )


def test_stops_when_the_backup_ssd_is_not_mounted(tmp_path):
    mount = tmp_path / "backup"
    mount.mkdir()
    tasks = [role_task(FILE, name) for name in CHECK_TASKS]
    run = run_play(tmp_path, tasks, {"backup_target_mount": str(mount), "backup_target_manage_mount": False})
    assert run.returncode != 0, run.stdout
    assert "plug in the backup SSD" in run.stdout + run.stderr
    assert not (mount / ".backup-ssd").exists()
    assert not (mount / "restic").exists()


def test_mount_uses_uuid_and_nofail():
    mount = role_task(FILE, "Mount the backup SSD")["ansible.posix.mount"]
    assert mount["src"] == "UUID={{ backup_target_uuid }}"
    assert mount["opts"] == "{{ backup_target_options }}"
    assert mount["state"] == "{{ 'mounted' if backup_target_manage_mount | bool else 'present' }}"
    defaults = yaml.safe_load((ANSIBLE_DIR / "roles/backup_target/defaults/main.yaml").read_text())
    assert "nofail" in defaults["backup_target_options"].split(",")
    assert defaults["backup_target_manage_mount"] is True


def test_nothing_is_written_below_the_mount_before_the_mountpoint_check():
    names = [t["name"] for t in yaml.safe_load((ANSIBLE_DIR / "roles/backup_target/tasks/main.yaml").read_text())]
    check = names.index("Check the backup SSD is mounted")
    for name in ("Mark the mount point as the backup SSD", "Ensure the restic repositories directory exists"):
        assert names.index(name) > check


def test_clients_are_required(tmp_path):
    tasks = [role_task(FILE, "Require at least one backup client")]
    run = run_play(tmp_path, tasks, {"backup_target_clients": {}})
    assert run.returncode != 0, run.stdout
    assert "backup_target_clients" in run.stdout + run.stderr


def test_every_client_key_is_installed_restricted(tmp_path):
    task = role_task(FILE, "Allow the backup clients to log in")
    task["ansible.posix.authorized_key"]["path"] = str(tmp_path / "authorized_keys")
    task["ansible.posix.authorized_key"]["user"] = subprocess.run(
        ["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()
    task["become"] = False
    for _ in range(2):
        run = run_play(tmp_path, [task], {"backup_target_clients": KEYS})
        assert run.returncode == 0, run.stdout + run.stderr
    lines = (tmp_path / "authorized_keys").read_text().splitlines()
    assert sorted(lines) == sorted(f"restrict {key} {name}" for name, key in KEYS.items())
