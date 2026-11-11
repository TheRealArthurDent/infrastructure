"""Where the playbooks find the KeePass database (raspberry#267), and how often they open it (raspberry#235)."""
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
PLAYBOOKS = ["bootstrap.yaml", "check-certificates.yaml", "setup-pihole.yaml"]
LOCAL_CONFIG = "inventory/group_vars/all/local.yaml"
LOCAL_EXAMPLE = "inventory/group_vars/all/local.example.yaml"


# local.example.yaml is loaded as inventory vars too; the tests start from no configured databases, with testbench
# reading the raspberry database unless a test names another one.
NO_DATABASES = ["-e", '{"local_keepass_databases": {}}']
RASPBERRY = ["-e", "keepass_database=raspberry"]


def plays(playbook):
    return yaml.safe_load((ANSIBLE_DIR / playbook).read_text())


def keepass_var(name, *args):
    result = subprocess.run(
        ["ansible", "testbench", "-m", "ansible.builtin.debug", "-a", f"var={name}", *RASPBERRY, *args],
        cwd=ANSIBLE_DIR,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_playbooks_do_not_hard_code_keepass_paths(playbook):
    for play in plays(playbook):
        assert "keepass_db" not in play.get("vars", {})
        assert "keepass_key" not in play.get("vars", {})


def test_no_keepass_path_is_committed():
    # Paths depend on the user's file system, so they live in an ignored local file (raspberry#267).
    tracked = subprocess.run(
        ["git", "ls-files", "inventory"], cwd=ANSIBLE_DIR, capture_output=True, text=True, check=True
    ).stdout.split()
    for path in tracked:
        if path.endswith(".example.yaml"):
            continue
        text = (ANSIBLE_DIR / path).read_text()
        assert ".kdbx" not in text, path
        assert ".key" not in text, path


def test_local_config_is_ignored_by_git():
    result = subprocess.run(["git", "check-ignore", "-q", LOCAL_CONFIG], cwd=ANSIBLE_DIR)
    assert result.returncode == 0


def test_example_documents_the_local_config():
    example = yaml.safe_load((ANSIBLE_DIR / LOCAL_EXAMPLE).read_text())
    # The old keys stay documented as comments: the example is loaded as inventory vars too, and an active
    # local_keepass_db there would win over a user's local_keepass_databases.
    assert set(example) == {"local_keepass_databases"}
    assert set(example["local_keepass_databases"]) == {"raspberry", "dungeon", "testbench"}


def test_keepass_paths_come_from_the_local_config(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    monkeypatch.delenv("KEEPASS_KEY", raising=False)
    local = [*NO_DATABASES, "-e", "local_keepass_db=/data/my.kdbx", "-e", "local_keepass_key=/data/my.key"]
    assert '"keepass_db": "/data/my.kdbx"' in keepass_var("keepass_db", *local)
    assert '"keepass_key": "/data/my.key"' in keepass_var("keepass_key", *local)


def test_keepass_paths_are_empty_when_not_configured(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    monkeypatch.delenv("KEEPASS_KEY", raising=False)
    unset = [*NO_DATABASES, "-e", "local_keepass_db=", "-e", "local_keepass_key="]
    assert '"keepass_db": ""' in keepass_var("keepass_db", *unset)
    assert '"keepass_key": ""' in keepass_var("keepass_key", *unset)


def test_keepass_paths_expand_the_home_directory(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    out = keepass_var("keepass_db", *NO_DATABASES, "-e", "local_keepass_db=~/my.kdbx")
    assert f'"keepass_db": "{Path.home()}/my.kdbx"' in out


def test_keepass_paths_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.setenv("KEEPASS_DB", "/tmp/other.kdbx")
    monkeypatch.setenv("KEEPASS_KEY", "/tmp/other.key")
    local = [*NO_DATABASES, "-e", "local_keepass_db=/data/my.kdbx", "-e", "local_keepass_key=/data/my.key"]
    assert '"keepass_db": "/tmp/other.kdbx"' in keepass_var("keepass_db", *local)
    assert '"keepass_key": "/tmp/other.key"' in keepass_var("keepass_key", *local)


def test_bootstrap_does_not_resolve_secrets_lazily():
    # Play vars are templated on every use, so a lookup there re-decrypts the database each time.
    for play in plays("bootstrap.yaml"):
        for name, value in play.get("vars", {}).items():
            assert "lookup('keepass'" not in str(value), name


def host_vars(**values):
    return ["-e", json.dumps(values)]


def test_named_database_comes_from_local_databases(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    monkeypatch.delenv("KEEPASS_KEY", raising=False)
    args = host_vars(local_keepass_databases={"dungeon": {"db": "/d.kdbx", "key": ""}}, keepass_database="dungeon")
    assert '"keepass_db": "/d.kdbx"' in keepass_var("keepass_db", *args)
    assert '"keepass_key": ""' in keepass_var("keepass_key", *args)


def test_legacy_local_paths_are_the_raspberry_database(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    monkeypatch.delenv("KEEPASS_KEY", raising=False)
    args = host_vars(local_keepass_db="/r.kdbx", local_keepass_key="/r.key", local_keepass_databases={})
    databases = keepass_var("keepass_databases", *args)
    assert '"db": "/r.kdbx"' in databases
    assert '"key": "/r.key"' in databases
    assert '"keepass_db": "/r.kdbx"' in keepass_var("keepass_db", *args)


def test_env_overrides_only_the_raspberry_database(monkeypatch):
    monkeypatch.setenv("KEEPASS_DB", "/tmp/o.kdbx")
    monkeypatch.setenv("KEEPASS_KEY", "/tmp/o.key")
    args = host_vars(
        local_keepass_databases={"dungeon": {"db": "/d.kdbx", "key": "/d.key"}, "raspberry": {"db": "/r.kdbx"}},
        keepass_database="dungeon",
    )
    assert '"keepass_db": "/d.kdbx"' in keepass_var("keepass_db", *args)
    assert '"keepass_ca_db": "/tmp/o.kdbx"' in keepass_var("keepass_ca_db", *args)
    assert '"keepass_ca_key": "/tmp/o.key"' in keepass_var("keepass_ca_key", *args)


def test_ca_database_defaults_to_raspberry(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    monkeypatch.delenv("KEEPASS_KEY", raising=False)
    args = host_vars(
        local_keepass_db="/r.kdbx",
        local_keepass_databases={"dungeon": {"db": "/d.kdbx", "key": ""}},
        keepass_database="dungeon",
    )
    assert '"keepass_ca_database": "raspberry"' in keepass_var("keepass_ca_database", *args)
    assert '"keepass_ca_db": "/r.kdbx"' in keepass_var("keepass_ca_db", *args)


def test_unconfigured_database_has_empty_paths(monkeypatch):
    monkeypatch.delenv("KEEPASS_DB", raising=False)
    args = host_vars(keepass_database="dungeon", local_keepass_databases={})
    assert '"keepass_db": ""' in keepass_var("keepass_db", *args)


SMART_HOME_KEYS = {
    "iobroker_states_redis_port", "iobroker_objects_redis_port", "influxdb_admin_password", "influxdb_read_password",
    "influxdb_write_password", "grafana_password", "mosquitto_password", "deconz_vnc_password", "nodered_github_user",
    "nodered_github_email",
}
# The facts of the "Read secrets from KeePass" task -> the condition that switches their lookup on
SFTP = "'resticprofile' in stack_components and resticprofile_repository_is_sftp"
LOOKUP_CONDITIONS = {
    "pihole_password": "'pihole' in stack_components",
    "outbound_password": "'outbound' in stack_components",
    "discord_docker_webhook_id": "'healthmonitor' in stack_components",
    "discord_docker_webhook_token": "'healthmonitor' in stack_components",
    "restic_passphrase": "'resticprofile' in stack_components",
    "resticprofile_ssh_private_key": SFTP,
    "resticprofile_ssh_public_key": SFTP,
    "dungeon_ssh_port": SFTP,
    **{key: "secrets_smart_home_env | bool" for key in SMART_HOME_KEYS},
    "opencloud_admin_password": "secrets_opencloud_env | bool",
}


def keepass_facts():
    bootstrap = next(play for play in plays("bootstrap.yaml") if play["name"] == "Bootstrap Raspberry Pi")
    task = next(t for t in bootstrap["pre_tasks"] if t["name"] == "Read secrets from KeePass")
    return task["ansible.builtin.set_fact"]


def test_bootstrap_only_looks_up_what_the_host_needs():
    # A lookup of a part the host doesn't run would need an entry in the host's database (dungeon has no pihole).
    facts = keepass_facts()
    assert set(facts) == set(LOOKUP_CONDITIONS)
    for name, condition in LOOKUP_CONDITIONS.items():
        value = " ".join(str(facts[name]).split())
        assert value.startswith("{{ lookup('keepass'"), name
        assert value.endswith(f"if {condition} else '' }}}}"), name
    assert "title=discord_docker_webhook_title" in str(facts["discord_docker_webhook_id"])
    assert "title=discord_docker_webhook_title" in str(facts["discord_docker_webhook_token"])
    assert "group='OpenCloud', title=opencloud_admin_title" in str(facts["opencloud_admin_password"])


def test_restic_key_pair_comes_from_the_hosts_entry():
    facts = keepass_facts()
    for name, attachment in (("resticprofile_ssh_private_key", "id_ed25519"), ("resticprofile_ssh_public_key", "id_ed25519.pub")):
        value = " ".join(str(facts[name]).split())
        assert "title=resticprofile_ssh_key_title" in value, name
        assert "group='Backup'" in value, name
        assert f"attachment='{attachment}'" in value, name


def test_pihole_passwords_come_from_the_pihole_group():
    # Both Pi-hole instances belong to the infrastructure, not to the smart-home stack.
    facts = keepass_facts()
    for name, title in (("pihole_password", "pihole"), ("outbound_password", "outbound")):
        value = " ".join(str(facts[name]).split())
        assert f"group='Pihole', title='{title}'" in value, name
        assert "field=" not in value, name


def test_restic_passphrase_is_the_entrys_password():
    value = " ".join(str(keepass_facts()["restic_passphrase"]).split())
    assert "group='Backup', title=resticprofile_passphrase_title" in value
    assert "field=" not in value


def test_testbench_has_its_own_database_with_the_default_titles():
    # testbench reads everything from testbench.kdbx under the same entry names as raspi; nothing of it stays in raspi's
    # database (its restic repository is throwaway, so changing raspi's passphrase doesn't touch it)
    testbench = yaml.safe_load((ANSIBLE_DIR / "inventory" / "host_vars" / "testbench.yaml").read_text())
    defaults = yaml.safe_load((ANSIBLE_DIR / "roles" / "secrets" / "defaults" / "main.yaml").read_text())
    titles = {name for name in defaults if name.endswith("_title")}
    assert titles == {
        "discord_docker_webhook_title", "resticprofile_ssh_key_title", "resticprofile_passphrase_title",
        "opencloud_admin_title",
    }
    assert testbench["keepass_database"] == "testbench"
    assert titles.isdisjoint(testbench)


def test_the_stack_tag_also_writes_the_secrets():
    # `--tags stack` reads the secrets (the lookups are tagged always); the secrets role must write them too, or a
    # changed passphrase or Pi-hole password never reaches the files the stack mounts.
    bootstrap = next(play for play in plays("bootstrap.yaml") if play["name"] == "Bootstrap Raspberry Pi")
    secrets = next(r for r in bootstrap["roles"] if r == "secrets" or (isinstance(r, dict) and r.get("role") == "secrets"))
    assert isinstance(secrets, dict) and "stack" in secrets.get("tags", [])
