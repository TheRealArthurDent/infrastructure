"""The secrets role writes private-cloud's Paperless secrets as Compose secret files (homelab-security#1).

Paperless reads PAPERLESS_*_FILE and Postgres POSTGRES_PASSWORD_FILE, so the values never pass through a .env file
(no interpolation, no quoting, not in docker inspect). Compose bind-mounts a file secret as it is, so each file
belongs to the user of the container that reads it: Paperless runs as 1000, Postgres (Alpine) as 70, both set in
private-cloud's paperless/docker-compose.yaml.
"""

from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parents[2] / "roles" / "secrets"
SECRETS_DIR = "/etc/docker/paperless/secrets"
# file -> (KeePass fact, owner variable)
FILES = {
    "secret_key": ("paperless_secret_key", "secrets_paperless_uid"),
    "admin_password": ("paperless_admin_password", "secrets_paperless_uid"),
    "db_password": ("paperless_db_password", "secrets_paperless_uid"),
    "postgres_password": ("paperless_db_password", "secrets_paperless_postgres_uid"),
}


def defaults():
    return yaml.safe_load((ROLE / "defaults" / "main.yaml").read_text())


def paperless_block():
    tasks = yaml.safe_load((ROLE / "tasks" / "main.yaml").read_text())
    pending = list(tasks)
    while pending:
        task = pending.pop(0)
        if task.get("name") == "Create Paperless secrets":
            return task
        for key in ("block", "rescue", "always"):
            pending[:0] = task.get(key, [])
    raise AssertionError("no task 'Create Paperless secrets'")


def module_args(task):
    return task.get("ansible.builtin.copy") or task.get("ansible.builtin.file")


def test_only_hosts_running_paperless_get_the_secrets():
    assert paperless_block()["when"] == "secrets_paperless | bool"
    assert defaults()["secrets_paperless"] is False


def test_the_secrets_directory_is_roots_alone():
    directory = next(module_args(t) for t in paperless_block()["block"] if "ansible.builtin.file" in t)
    assert directory["path"] == SECRETS_DIR
    assert (directory["owner"], directory["group"], directory["mode"]) == ("root", "root", "0700")


def test_each_secret_file_belongs_to_the_container_user_reading_it():
    copies = {Path(module_args(t)["dest"]).name: t for t in paperless_block()["block"] if "ansible.builtin.copy" in t}
    assert sorted(copies) == sorted(FILES)
    for name, (fact, owner) in FILES.items():
        task = copies[name]
        args = module_args(task)
        assert args["dest"] == f"{SECRETS_DIR}/{name}", name
        # The value as is: no trailing newline (Paperless copies the file into the variable byte for byte)
        assert args["content"] == "{{ " + fact + " }}", name
        assert args["owner"] == "{{ " + owner + " }}", name
        assert (args["group"], args["mode"]) == ("root", "0400"), name
        assert task["no_log"] is True, name


def test_the_owners_are_the_container_users_of_private_cloud():
    assert defaults()["secrets_paperless_uid"] == 1000
    assert defaults()["secrets_paperless_postgres_uid"] == 70


def test_paperless_gets_no_env_file():
    tasks = (ROLE / "tasks" / "main.yaml").read_text()
    assert "/etc/docker/paperless/.env" not in tasks
