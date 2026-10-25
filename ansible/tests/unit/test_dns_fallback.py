"""The host gets its DNS fallback before anything in the bootstrap needs name resolution.

Pi-hole is the network's DNS server and runs on the Pi itself. On a freshly installed Pi it doesn't exist yet,
so apt can't resolve deb.debian.org unless the fallback is in place and applied first.
"""
from pathlib import Path

import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
BASE_TASKS = ANSIBLE_DIR / "roles" / "base" / "tasks" / "main.yaml"
FALLBACK_TASK = "Ensure the host falls back to another DNS server while Pi-hole is down"


def task_names(tasks):
    return [task.get("name") for task in tasks]


def test_dns_fallback_is_applied_before_the_first_package_install():
    tasks = yaml.safe_load(BASE_TASKS.read_text())
    names = task_names(tasks)
    fallback = names.index(FALLBACK_TASK)
    first_apt = next(i for i, task in enumerate(tasks) if "ansible.builtin.apt" in task)
    flush = next(
        (i for i, task in enumerate(tasks) if task.get("ansible.builtin.meta") == "flush_handlers"),
        None,
    )

    assert fallback < first_apt, "the DNS fallback must be written before the first apt task"
    assert flush is not None and fallback < flush < first_apt, (
        "NetworkManager must be reloaded (handlers flushed) between the DNS fallback and the first apt task"
    )


def test_base_is_the_first_role_after_ssh_in_bootstrap():
    plays = yaml.safe_load((ANSIBLE_DIR / "bootstrap.yaml").read_text())
    bootstrap = next(play for play in plays if play["name"] == "Bootstrap Raspberry Pi")
    roles = [role if isinstance(role, str) else role["role"] for role in bootstrap["roles"]]

    assert roles[:2] == ["ssh", "base"], "base sets up DNS, so it must run before any role that downloads"
