"""The secrets role's check of the Grafana admin password (#39).

Grafana reads GF_SECURITY_ADMIN_PASSWORD__FILE into its config, trims blanks at both ends and then expands every
${NAME} and $__provider{...} in it, as in grafana.ini: a password with ${y} silently becomes a different one. The task
runs verbatim; the password comes in as !unsafe, like a KeePass lookup's result.
"""
import json
import os
import subprocess

import pytest
import yaml

from render import ANSIBLE_DIR, role_task

TASK = "Ensure Grafana takes the admin password unchanged"


def run_check(tmp_path, password, components=("monitoring",)):
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"all": {"hosts": {"testbench": {
        "ansible_connection": "local", "ansible_python_interpreter": "auto_silent",
    }}}}))
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text(f"monitoring_grafana_admin_password: !unsafe {json.dumps(password)}\n")
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Run the Grafana password check",
        "hosts": "testbench",
        "gather_facts": False,
        "vars": {"stack_components": list(components)},
        "vars_files": [str(secrets)],
        "tasks": [role_task("secrets/tasks/main.yaml", TASK)],
    }]))
    return subprocess.run(
        ["ansible-playbook", "-i", str(inventory), str(playbook)],
        cwd=ANSIBLE_DIR, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
    )


@pytest.mark.parametrize("password", [
    'smoke $x {{ z }} "q" \'s\' \\b #c',
    "$y {y} $abc{d} ${} $__{x}",
    "inner blanks are fine",
])
def test_a_password_grafana_keeps_passes(tmp_path, password):
    run = run_check(tmp_path, password)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("password", [
    "abc${y}def",
    "$__env{HOME}",
    "x$__file{/etc/hostname}",
    " leading blank",
    "trailing blank ",
])
def test_a_password_grafana_would_change_fails_without_showing_it(tmp_path, password):
    run = run_check(tmp_path, password)
    assert run.returncode != 0, run.stdout
    out = " ".join((run.stdout + run.stderr).split())
    assert "Monitoring/Grafana admin" in out
    assert password.strip() not in out


def test_hosts_without_monitoring_skip_the_check(tmp_path):
    run = run_check(tmp_path, "${y}", components=("reverse_proxy",))
    assert run.returncode == 0, run.stdout + run.stderr
    assert "skipping" in run.stdout
