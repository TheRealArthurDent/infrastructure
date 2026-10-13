"""Unit tests for the keepass lookup plugin, run against a throwaway KDBX."""
import importlib.util
from pathlib import Path

import pytest
from ansible.errors import AnsibleError
from pykeepass import create_database

PLUGIN = Path(__file__).parents[2] / "lookup_plugins" / "keepass.py"
PASSWORD = "test-master-password"


def load_plugin():
    spec = importlib.util.spec_from_file_location("keepass_lookup", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LookupModule()


@pytest.fixture(scope="module")
def kdbx(tmp_path_factory):
    path = tmp_path_factory.mktemp("kdbx") / "test.kdbx"
    kp = create_database(str(path), password=PASSWORD)

    smart_home = kp.add_group(kp.root_group, "smart-home")
    kp.add_entry(smart_home, "pihole", "admin", "pihole-secret")
    iobroker = kp.add_group(smart_home, "ioBroker")
    states = kp.add_entry(iobroker, "States Redis", "", "")
    states.set_custom_property("port", "9000")

    backup = kp.add_group(kp.root_group, "Backup")
    restic = kp.add_entry(backup, "Resticprofile", "", "")
    restic.set_custom_property("passphrase", "restic-secret")

    # Same title in two groups: the group must decide which one is returned.
    other = kp.add_group(kp.root_group, "other")
    kp.add_entry(other, "duplicate", "", "from-other")
    kp.add_entry(smart_home, "duplicate", "", "from-smart-home")

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
    # Currently passes only via the whole-database fallback, see test_unknown_group_raises.
    assert lookup(kdbx, group="smart-home/ioBroker", title="States Redis", field="port") == ["9000"]


@pytest.mark.xfail(strict=True, reason="#235: unknown group silently searches the whole database")
def test_unknown_group_raises(kdbx):
    with pytest.raises(AnsibleError):
        lookup(kdbx, group="no-such-group", title="pihole")


def test_attachment_export(kdbx, tmp_path):
    out = tmp_path / "export" / "rootCA.pem"
    result = lookup(kdbx, group="Domain", title="rootCA", attachment="rootCA.pem", output_path=str(out))
    assert result == [str(out)]
    assert out.read_bytes() == b"-----BEGIN CERTIFICATE-----\n"


def test_missing_attachment_raises(kdbx, tmp_path):
    with pytest.raises(AnsibleError, match="not found"):
        lookup(kdbx, group="Domain", title="rootCA", attachment="nope.pem", output_path=str(tmp_path / "x"))


def test_attachment_export_prints_nothing(kdbx, tmp_path, capfd):
    # Lookup output ends up in terminals and CI logs; the plugin must stay silent (#230).
    lookup(kdbx, group="Domain", title="rootCA", attachment="rootCA.pem", output_path=str(tmp_path / "rootCA.pem"))
    out, err = capfd.readouterr()
    assert out == ""
    assert err == ""
