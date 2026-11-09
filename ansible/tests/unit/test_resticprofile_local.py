"""resticprofile with a local repository on the backup SSD (dungeon backs up to itself, #15).

The sftp-only parts (ssh key, ssh_config, extra_hosts, lookups) must be gone, the SSD is bind-mounted into the
container, a backup fails without the SSD's marker file, and the clean-slate restore stops before it runs.
"""
import subprocess
import os

import yaml

from render import ANSIBLE_DIR, role_task, rendered_tree

LOCAL = {"resticprofile_repository": "local:/backup/restic/dungeon", "resticprofile_backup_mount": "/media/backup"}
MARKER_TASKS = [
    "Check the backup SSD marker",
    "Probe the backup SSD mount point",
    "Report the missing backup SSD marker in check mode",
    "Ensure the backup SSD is mounted",
]


def service(tmp_path, extra_vars):
    return yaml.safe_load(rendered_tree(tmp_path, "raspi", extra_vars)["docker-compose.yaml"])["services"]["resticprofile"]


def test_local_repository_mounts_the_backup_ssd(tmp_path):
    resticprofile = service(tmp_path, LOCAL)
    mounts = [v for v in resticprofile["volumes"] if isinstance(v, dict) and v["target"] == "/backup"]
    assert len(mounts) == 1
    assert mounts[0]["type"] == "bind"
    assert mounts[0]["source"] == "/media/backup"
    assert mounts[0]["bind"]["propagation"] == "rslave"
    assert "extra_hosts" not in resticprofile
    assert not [v for v in resticprofile["volumes"] if "/.ssh" in str(v) or "ssh_config" in str(v)]


def test_local_repository_backup_fails_without_the_ssd_marker(tmp_path):
    profiles = yaml.safe_load(rendered_tree(tmp_path, "raspi", LOCAL)["resticprofile/config/profiles.yaml"])
    # Profile level: resticprofile 0.33.1 runs the profile's run-before, then "initialize" (restic init), then the
    # section's run-before. A guard in the backup section would come after init created the repository.
    run_before = profiles["default"]["run-before"]
    assert any("/backup/.backup-ssd" in command and "exit 1" in command for command in run_before), run_before
    assert profiles["default"]["initialize"] is True
    assert not any(".backup-ssd" in c for c in profiles["default"]["backup"]["run-before"])


def test_local_repository_profile(tmp_path):
    profiles = yaml.safe_load(rendered_tree(tmp_path, "raspi", LOCAL)["resticprofile/config/profiles.yaml"])
    assert profiles["default"]["repository"] == "local:/backup/restic/dungeon"


def test_sftp_repository_keeps_the_ssh_parts(tmp_path):
    tree = rendered_tree(tmp_path, "raspi")
    resticprofile = yaml.safe_load(tree["docker-compose.yaml"])["services"]["resticprofile"]
    assert "extra_hosts" in resticprofile
    assert any("/.ssh" in str(v) for v in resticprofile["volumes"])
    assert not any(isinstance(v, dict) for v in resticprofile["volumes"])
    profiles = yaml.safe_load(tree["resticprofile/config/profiles.yaml"])
    assert profiles["default"]["repository"] == "sftp:restic@dungeon:/media/backup/restic/raspi"
    assert "run-before" not in profiles["default"]


def run_restore_check(tmp_path, marker, mount, check, extra_vars=None):
    tasks = [role_task("resticprofile/tasks/restore.yaml", name) for name in MARKER_TASKS]
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Run the marker check",
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "resticprofile_backup_marker": str(marker),
            "resticprofile_backup_mount": mount,
            "ansible_python_interpreter": "auto_silent",
            **(extra_vars or {}),
        },
        "tasks": tasks,
    }]))
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", *(["--check"] if check else []), str(playbook)],
        cwd=ANSIBLE_DIR, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
    )


def test_restore_stops_without_the_ssd_marker(tmp_path):
    # tmp_path is no mount point: the SSD is really missing, in check mode too
    marker = tmp_path / ".backup-ssd"
    for check in (False, True):
        run = run_restore_check(tmp_path, marker, str(tmp_path), check)
        assert run.returncode != 0, run.stdout
        assert str(marker) in run.stdout + run.stderr


def test_check_mode_gets_through_a_mounted_ssd_whose_marker_is_not_created_yet(tmp_path):
    # Takeover of a hand-deployed dungeon: the SSD is mounted, backup_target only reports the marker as changed.
    marker = tmp_path / ".backup-ssd"
    run = run_restore_check(tmp_path, marker, "/", True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "created by backup_target in the real run" in run.stdout
    assert not marker.exists()


def test_a_real_run_without_the_marker_stops_even_on_a_mount_point(tmp_path):
    # backup_target creates the marker before; if it is missing nothing may restore
    run = run_restore_check(tmp_path, tmp_path / ".backup-ssd", "/", False)
    assert run.returncode != 0, run.stdout
    assert "is missing" in run.stdout + run.stderr


def test_check_mode_gets_through_a_ssd_that_backup_target_mounts_in_the_real_run(tmp_path):
    # Fresh dungeon, SSD plugged in: --check doesn't mount, backup_target noted it
    run = run_restore_check(tmp_path, tmp_path / ".backup-ssd", str(tmp_path), True,
                            {"backup_target_mount_pending": True})
    assert run.returncode == 0, run.stdout + run.stderr
    assert "created by backup_target in the real run" in run.stdout


def test_restore_continues_with_the_ssd_marker(tmp_path):
    marker = tmp_path / ".backup-ssd"
    marker.write_text("")
    for check in (False, True):
        run = run_restore_check(tmp_path, marker, str(tmp_path), check)
        assert run.returncode == 0, run.stdout + run.stderr


def test_restore_ignores_the_marker_with_an_sftp_repository(tmp_path):
    run = run_restore_check(tmp_path, tmp_path / ".backup-ssd", "", False)
    assert run.returncode == 0, run.stdout + run.stderr


def test_the_restore_helper_task_file_installs_rsync():
    """The helper runs rsync (apply, diff). It used to come with the rsnapshot package, which dungeon doesn't install."""
    task = role_task("resticprofile/tasks/restore.yaml", "Ensure rsync is installed for the restore helper")
    assert task["ansible.builtin.apt"] == {"name": "rsync", "state": "present"}


def run_restore_hint(tmp_path, rc, sftp):
    task = role_task("resticprofile/tasks/restore.yaml", "Ensure the restore succeeded")
    playbook = tmp_path / "hint.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Run the hint",
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "resticprofile_restore": {"rc": rc, "stderr": "restic said no"},
            "resticprofile_repository_is_sftp": sftp,
            "resticprofile_repository": "local:/backup/restic/dungeon",
            "ansible_python_interpreter": "auto_silent",
        },
        "tasks": [task],
    }]))
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(playbook)],
        cwd=ANSIBLE_DIR, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
    )


def test_hint_10_names_the_ssh_key_for_an_sftp_repository(tmp_path):
    run = run_restore_hint(tmp_path, 10, True)
    assert run.returncode != 0
    assert "backup_target_clients" in run.stdout


def test_hint_10_names_the_ssd_for_a_local_repository(tmp_path):
    run = run_restore_hint(tmp_path, 10, False)
    assert run.returncode != 0
    out = " ".join(run.stdout.split())
    assert "backup SSD holds local:/backup/restic/dungeon" in out
    assert "-e skip_infrastructure_restore=true" in out
    assert "backup_target_clients" not in out
