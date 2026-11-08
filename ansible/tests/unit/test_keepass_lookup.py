"""Unit tests for the keepass lookup plugin, run against a throwaway KDBX."""
import importlib.util
from pathlib import Path

import pytest
from ansible.errors import AnsibleError
from pykeepass import create_database

PLUGIN = Path(__file__).parents[2] / "lookup_plugins" / "keepass.py"
PASSWORD = "test-master-password"


def load_module():
    """Load a fresh copy of the plugin module, so every test starts with an empty cache."""
    spec = importlib.util.spec_from_file_location("keepass_lookup", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plugin():
    return load_module().LookupModule()


@pytest.fixture(scope="module")
def kdbx(tmp_path_factory):
    path = tmp_path_factory.mktemp("kdbx") / "test.kdbx"
    kp = create_database(str(path), password=PASSWORD)

    smart_home = kp.add_group(kp.root_group, "smart-home")
    kp.add_entry(smart_home, "pihole", "admin", "pihole-secret")
    iobroker = kp.add_group(smart_home, "ioBroker")
    states = kp.add_entry(iobroker, "States Redis", "", "")
    states.set_custom_property("port", "9000")
    influx = kp.add_group(smart_home, "Influx DB")
    kp.add_entry(influx, "Influx admin", "", "influx-secret")

    backup = kp.add_group(kp.root_group, "Backup")
    restic = kp.add_entry(backup, "Resticprofile", "", "")
    restic.set_custom_property("passphrase", "restic-secret")

    # Same title in two groups: the group must decide which one is returned.
    other = kp.add_group(kp.root_group, "other")
    kp.add_entry(other, "duplicate", "", "from-other")
    kp.add_entry(smart_home, "duplicate", "", "from-smart-home")

    # A second "ioBroker" group: only the full path tells the two apart.
    legacy_iobroker = kp.add_group(other, "ioBroker")
    legacy_states = kp.add_entry(legacy_iobroker, "States Redis", "", "")
    legacy_states.set_custom_property("port", "1111")

    # Same title twice below one group: ambiguous.
    twice = kp.add_group(kp.root_group, "twice")
    kp.add_entry(twice, "entry", "", "first")
    kp.add_entry(kp.add_group(twice, "sub"), "entry", "", "second")

    domain = kp.add_group(kp.root_group, "Domain")
    ca = kp.add_entry(domain, "rootCA", "", "ca-passphrase")
    ca.add_attachment(kp.add_binary(b"-----BEGIN CERTIFICATE-----\n"), "rootCA.pem")

    kp.save()
    return str(path)


def lookup(kdbx, **kwargs):
    return load_plugin().run([], db=kdbx, password=PASSWORD, **kwargs)


def test_password_is_default_field(kdbx):
    assert lookup(kdbx, group="smart-home", title="pihole") == ["pihole-secret"]


def test_username_field(kdbx):
    assert lookup(kdbx, group="smart-home", title="pihole", field="username") == ["admin"]


def test_custom_field(kdbx):
    assert lookup(kdbx, group="Backup", title="Resticprofile", field="passphrase") == ["restic-secret"]


def test_missing_entry_raises(kdbx):
    with pytest.raises(AnsibleError, match="not found"):
        lookup(kdbx, group="smart-home", title="does-not-exist")


def test_wrong_master_password_fails(kdbx):
    with pytest.raises(Exception):
        load_plugin().run([], db=kdbx, password="wrong", group="smart-home", title="pihole")


def test_group_disambiguates_duplicate_titles(kdbx):
    assert lookup(kdbx, group="smart-home", title="duplicate") == ["from-smart-home"]


def test_nested_group_path(kdbx):
    assert lookup(kdbx, group="smart-home/ioBroker", title="States Redis", field="port") == ["9000"]
    assert lookup(kdbx, group="other/ioBroker", title="States Redis", field="port") == ["1111"]


def test_single_group_name_finds_nested_group(kdbx):
    # Unambiguous group names keep working without their full path.
    assert lookup(kdbx, group="Influx DB", title="Influx admin") == ["influx-secret"]


def test_unknown_group_raises(kdbx):
    # Used to fall back to searching the whole database (raspberry#235).
    with pytest.raises(AnsibleError, match='Group "no-such-group" not found'):
        lookup(kdbx, group="no-such-group", title="pihole")


def test_unknown_group_path_raises(kdbx):
    with pytest.raises(AnsibleError, match="not found"):
        lookup(kdbx, group="Backup/ioBroker", title="States Redis")


def test_ambiguous_group_raises(kdbx):
    with pytest.raises(AnsibleError, match='Group "ioBroker" is ambiguous'):
        lookup(kdbx, group="ioBroker", title="States Redis")


def test_ambiguous_entry_raises(kdbx):
    with pytest.raises(AnsibleError, match='Entry "twice/entry" is ambiguous'):
        lookup(kdbx, group="twice", title="entry")


def test_database_is_decrypted_once(kdbx, monkeypatch):
    # Ansible creates a new LookupModule for every lookup call, so the cache must outlive the instance.
    module = load_module()
    opened = []
    real = module.PyKeePass

    def counting(*args, **kwargs):
        opened.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "PyKeePass", counting)
    for _ in range(3):
        module.LookupModule().run([], db=kdbx, password=PASSWORD, group="smart-home", title="pihole")
    module.LookupModule().run([], db=kdbx, password=PASSWORD, group="Backup", title="Resticprofile")
    assert len(opened) == 1


def test_cache_does_not_bypass_the_password(kdbx):
    module = load_module()
    module.LookupModule().run([], db=kdbx, password=PASSWORD, group="smart-home", title="pihole")
    with pytest.raises(Exception):
        module.LookupModule().run([], db=kdbx, password="wrong", group="smart-home", title="pihole")


def test_cache_sees_changes_to_the_database(tmp_path):
    path = str(tmp_path / "changing.kdbx")
    kp = create_database(path, password=PASSWORD)
    entry = kp.add_entry(kp.root_group, "token", "", "old")
    group = kp.add_group(kp.root_group, "g")
    kp.move_entry(entry, group)
    kp.save()

    module = load_module()
    assert module.LookupModule().run([], db=path, password=PASSWORD, group="g", title="token") == ["old"]

    kp = module.PyKeePass(path, password=PASSWORD)
    kp.find_entries(title="token", first=True).password = "new-and-longer"
    kp.save()
    assert module.LookupModule().run([], db=path, password=PASSWORD, group="g", title="token") == ["new-and-longer"]


def test_attachment_export(kdbx, tmp_path):
    out = tmp_path / "export" / "rootCA.pem"
    result = lookup(kdbx, group="Domain", title="rootCA", attachment="rootCA.pem", output_path=str(out))
    assert result == [str(out)]
    assert out.read_bytes() == b"-----BEGIN CERTIFICATE-----\n"


def test_attachment_content_without_output_path(kdbx):
    # Returns the attachment as text instead of writing it to disk (raspberry#229).
    assert lookup(kdbx, group="Domain", title="rootCA", attachment="rootCA.pem") == [
        "-----BEGIN CERTIFICATE-----\n"
    ]


def test_missing_attachment_raises(kdbx, tmp_path):
    with pytest.raises(AnsibleError, match="not found"):
        lookup(kdbx, group="Domain", title="rootCA", attachment="nope.pem", output_path=str(tmp_path / "x"))


def test_attachment_export_prints_nothing(kdbx, tmp_path, capfd):
    # Lookup output ends up in terminals and CI logs; the plugin must stay silent (raspberry#230).
    lookup(kdbx, group="Domain", title="rootCA", attachment="rootCA.pem", output_path=str(tmp_path / "rootCA.pem"))
    out, err = capfd.readouterr()
    assert out == ""
    assert err == ""
