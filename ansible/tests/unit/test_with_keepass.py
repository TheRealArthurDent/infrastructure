"""bin/with-keepass: runs a command with the KeePass master password from Windows Credential Manager (raspberry#267)."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ANSIBLE_DIR = Path(__file__).parents[2]
WRAPPER = ANSIBLE_DIR / "bin" / "with-keepass"
PASSWORD = "LEAKCANARY ä'\"$x `y` \\z"

# Stands in for powershell.exe: keeps credentials in a JSON file and logs each call's argv.
FAKE_POWERSHELL = r'''#!PYTHON
import base64, json, os, re, sys
store = os.environ["FAKE_VAULT"]
with open(os.environ["FAKE_CALLS"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
script = base64.b64decode(sys.argv[sys.argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
match = re.search(r"\$name = '([^']*)'", script)
name = match.group(1) if match else None
vault = json.load(open(store)) if os.path.exists(store) else {}
if "FindAllByUserName('keepass')" in script:
    if not vault:
        sys.exit(3)
    for resource, password in vault.items():
        wire = base64.b64encode(password.encode("utf-8")).decode()
        sys.stdout.buffer.write(f"{resource} {wire}\r\n".encode())
    sys.exit(0)
if "RetrievePassword" in script:
    if name not in vault:
        sys.exit(3)
    sys.stdout.buffer.write(vault[name].encode("utf-8"))
elif ".Add(" in script:
    vault[name] = sys.stdin.buffer.read().decode("utf-8")
elif ".Remove(" in script:
    vault.pop(name, None)
json.dump(vault, open(store, "w"))
'''


@pytest.fixture
def fake(tmp_path):
    exe = tmp_path / "powershell.exe"
    exe.write_text(FAKE_POWERSHELL.replace("PYTHON", sys.executable))
    exe.chmod(0o755)

    class Fake:
        vault = tmp_path / "vault.json"
        calls = tmp_path / "calls.jsonl"
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("KEEPASS_")},
            "KEEPASS_POWERSHELL": str(exe),
            "FAKE_VAULT": str(tmp_path / "vault.json"),
            "FAKE_CALLS": str(tmp_path / "calls.jsonl"),
        }

        def stored(self):
            return json.loads(self.vault.read_text()) if self.vault.exists() else {}

        def argv_log(self):
            return self.calls.read_text() if self.calls.exists() else ""

    return Fake()


def run(fake, *args, stdin="", **env):
    return subprocess.run(
        [str(WRAPPER), *args],
        env={**fake.env, **env},
        input=stdin.encode("utf-8"),
        capture_output=True,
        timeout=30,
    )


PRINT_PASSWORD = ["sh", "-c", 'printf %s "${KEEPASS_PASSWORD-unset}"']


def test_store_saves_the_password_from_stdin(fake):
    result = run(fake, "--store", stdin=PASSWORD + "\n")
    assert result.returncode == 0, result.stderr
    assert fake.stored() == {"raspberry-keepass": PASSWORD}


def test_store_never_passes_the_password_as_an_argument(fake):
    run(fake, "--store", stdin=PASSWORD + "\n")
    commands = [json.loads(line) for line in fake.argv_log().splitlines()]
    assert commands
    for argv in commands:
        script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        assert "LEAKCANARY" not in script


def test_store_refuses_an_empty_password(fake):
    result = run(fake, "--store", stdin="\n")
    assert result.returncode != 0
    assert fake.stored() == {}


def test_runs_the_command_with_the_stored_password(fake):
    run(fake, "--store", stdin=PASSWORD + "\n")
    result = run(fake, *PRINT_PASSWORD)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8") == PASSWORD


def test_accepts_windows_line_endings_from_powershell(fake):
    fake.vault.write_text(json.dumps({"raspberry-keepass": "secret"}))
    result = run(fake, *PRINT_PASSWORD)
    assert result.stdout == b"secret"


def test_keeps_trailing_whitespace_of_a_password(fake):
    fake.vault.write_text(json.dumps({"raspberry-keepass": "secret \n\n"}))
    result = run(fake, *PRINT_PASSWORD)
    assert result.stdout == b"secret \n\n"


def test_passes_the_exit_code_through(fake):
    run(fake, "--store", stdin="secret\n")
    assert run(fake, "sh", "-c", "exit 7").returncode == 7


def test_without_a_stored_password_runs_the_command_so_ansible_prompts(fake):
    result = run(fake, *PRINT_PASSWORD)
    assert result.returncode == 0
    assert result.stdout == b"unset"
    assert b"--store" in result.stderr


def test_without_powershell_runs_the_command_so_ansible_prompts(fake):
    result = run(fake, *PRINT_PASSWORD, KEEPASS_POWERSHELL="/nonexistent/powershell.exe")
    assert result.returncode == 0
    assert result.stdout == b"unset"
    assert b"Credential Manager" in result.stderr


def test_keeps_a_password_that_is_already_set(fake):
    run(fake, "--store", stdin="stored\n")
    result = run(fake, *PRINT_PASSWORD, KEEPASS_PASSWORD="from-env")
    assert result.stdout == b"from-env"


def test_forget_removes_the_stored_password(fake):
    run(fake, "--store", stdin="secret\n")
    result = run(fake, "--forget")
    assert result.returncode == 0, result.stderr
    assert fake.stored() == {}


PRINT_ALL = ["sh", "-c", 'printf "%s|%s|%s" "${KEEPASS_PASSWORD-unset}" "${KEEPASS_PASSWORD_DUNGEON-unset}" "${KEEPASS_PASSWORD_MY_DB-unset}"']


def test_store_takes_a_database_name(fake):
    result = run(fake, "--store", "dungeon", stdin=PASSWORD + "\n")
    assert result.returncode == 0, result.stderr
    assert fake.stored() == {"dungeon-keepass": PASSWORD}


def test_store_of_a_named_database_never_passes_the_password_as_an_argument(fake):
    run(fake, "--store", "dungeon", stdin=PASSWORD + "\n")
    commands = [json.loads(line) for line in fake.argv_log().splitlines()]
    assert commands
    for argv in commands:
        assert "LEAKCANARY" not in " ".join(argv)
        script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        assert "LEAKCANARY" not in script


def test_forget_takes_a_database_name(fake):
    fake.vault.write_text(json.dumps({"raspberry-keepass": "a", "dungeon-keepass": "b"}))
    result = run(fake, "--forget", "dungeon")
    assert result.returncode == 0, result.stderr
    assert fake.stored() == {"raspberry-keepass": "a"}


def test_exports_every_stored_password(fake):
    run(fake, "--store", stdin=PASSWORD + "\n")
    run(fake, "--store", "dungeon", stdin=PASSWORD + "2\n")
    result = run(fake, *PRINT_ALL)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8") == f"{PASSWORD}|{PASSWORD}2|unset"


def test_exports_a_name_with_digits_and_underscores(fake):
    fake.vault.write_text(json.dumps({"my_db-keepass": "x"}))
    assert run(fake, *PRINT_ALL).stdout == b"unset|unset|x"


def test_keeps_every_password_already_set(fake):
    fake.vault.write_text(json.dumps({"raspberry-keepass": "a", "dungeon-keepass": "b"}))
    result = run(fake, *PRINT_ALL, KEEPASS_PASSWORD_DUNGEON="preset")
    assert result.stdout == b"a|preset|unset"


def test_ignores_stored_credentials_that_are_not_keepass_databases(fake):
    fake.vault.write_text(json.dumps({"other": "a", "My-DB-keepass": "b", "my db-keepass": "c"}))
    result = run(fake, *PRINT_ALL)
    assert result.stdout == b"unset|unset|unset"


@pytest.mark.parametrize("bad", ["my-db", "Dungeon", "a b", "x'; Remove-Item C:\\"])
def test_rejects_an_invalid_database_name(fake, bad):
    for option in ("--store", "--forget"):
        result = run(fake, option, bad, stdin="secret\n")
        assert result.returncode == 2
        assert b"database name" in result.stderr
    assert fake.stored() == {}
    assert fake.argv_log() == ""


def test_powershell_writes_plain_text_without_progress_records(fake):
    # Otherwise PowerShell writes progress ("preparing modules for first use") as CLIXML to stderr.
    run(fake, "--store", stdin="secret\n")
    run(fake, *PRINT_PASSWORD)
    run(fake, "--forget")
    for line in fake.argv_log().splitlines():
        argv = json.loads(line)
        assert argv[argv.index("-OutputFormat") + 1] == "Text"
        script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        assert script.startswith("$ProgressPreference = 'SilentlyContinue'")


def test_without_a_command_prints_usage(fake):
    result = run(fake)
    assert result.returncode != 0
    assert b"Usage" in result.stderr
