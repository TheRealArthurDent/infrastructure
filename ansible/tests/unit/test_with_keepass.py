"""bin/with-keepass: runs a command with the KeePass master password from Windows Credential Manager (#267)."""
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
name = re.search(r"\$name = '([^']*)'", script).group(1)
vault = json.load(open(store)) if os.path.exists(store) else {}
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
            **{k: v for k, v in os.environ.items() if k != "KEEPASS_PASSWORD"},
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


def test_strips_a_carriage_return_from_powershell_output(fake):
    fake.vault.write_text(json.dumps({"raspberry-keepass": "secret\r\n"}))
    result = run(fake, *PRINT_PASSWORD)
    assert result.stdout == b"secret"


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


def test_credential_name_can_be_changed(fake):
    run(fake, "--store", stdin="secret\n", KEEPASS_CREDENTIAL="other-db")
    assert fake.stored() == {"other-db": "secret"}


def test_rejects_a_credential_name_that_would_break_the_powershell_script(fake):
    result = run(fake, "--store", stdin="secret\n", KEEPASS_CREDENTIAL="x'; Remove-Item C:\\")
    assert result.returncode != 0
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
