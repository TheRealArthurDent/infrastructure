"""The base role's check that the host has enough swap (#50).

Without swap, a host under memory pressure can only drop file-backed pages, including the code of running programs,
and reads them right back: raspi ran at load 5-8 with mostly iowait after its in-place upgrade to trixie had lost its
swap. The task runs verbatim with the facts given here instead of gathered ones.
"""
import os
import subprocess

import yaml

from render import ANSIBLE_DIR, role_task

TASK = "Ensure the host has enough swap"


def run_check(tmp_path, swaptotal_mb, **variables):
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"all": {"hosts": {"testbench": {
        "ansible_connection": "local", "ansible_python_interpreter": "auto_silent",
    }}}}))
    defaults = yaml.safe_load((ANSIBLE_DIR / "roles/base/defaults/main.yaml").read_text())
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Run the swap check",
        "hosts": "testbench",
        "gather_facts": False,
        "vars": {
            "base_swap_min_mb": defaults["base_swap_min_mb"],
            **variables,
            "ansible_facts": {"swaptotal_mb": swaptotal_mb},
        },
        "tasks": [role_task("base/tasks/main.yaml", TASK)],
    }]))
    return subprocess.run(
        ["ansible-playbook", "-i", str(inventory), str(playbook)],
        cwd=ANSIBLE_DIR, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
    )


def test_the_default_asks_for_one_gigabyte():
    defaults = yaml.safe_load((ANSIBLE_DIR / "roles/base/defaults/main.yaml").read_text())
    assert defaults["base_swap_min_mb"] == 1024


def test_enough_swap_passes(tmp_path):
    run = run_check(tmp_path, 1024)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "skipping" not in run.stdout


def test_no_swap_fails_with_what_is_there_what_is_needed_and_how_to_fix_it(tmp_path):
    run = run_check(tmp_path, 0)
    assert run.returncode != 0, run.stdout
    out = " ".join((run.stdout + run.stderr).split())
    assert "testbench has 0 MB of swap, base_swap_min_mb asks for 1024 MB" in out
    assert "rpi-swap" in out


def test_too_little_swap_fails(tmp_path):
    run = run_check(tmp_path, 512)
    assert run.returncode != 0, run.stdout
    assert "512 MB of swap" in " ".join(run.stdout.split())


def test_a_lower_minimum_per_host_is_honoured(tmp_path):
    run = run_check(tmp_path, 512, base_swap_min_mb=512)
    assert run.returncode == 0, run.stdout + run.stderr


def test_zero_turns_the_check_off(tmp_path):
    run = run_check(tmp_path, 0, base_swap_min_mb=0)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "skipping" in run.stdout
