"""certificate_check's checks on the root CA itself (Refs #10).

The root CA "Raspi Root" expired on 2026-11-02 without anyone noticing: the role kept signing host certificates with
it, and every client refused them. The role now reads the root's validity before it signs anything: it stops on an
expired root, warns when the root expires soon, and never issues a host certificate that outlives the root.

The tasks run verbatim on localhost against throwaway roots of different ages.
"""
import datetime
import os
import subprocess

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from render import ANSIBLE_DIR, role_task

TASKS = "certificate_check/tasks/main.yaml"
CHECKS = [
    "Read validity of the root CA",
    "Check the root CA has not expired",
    "Warn that the root CA expires soon",
    "Cap the host certificate at the expiry of the root CA",
]
DEFAULTS = yaml.safe_load((ANSIBLE_DIR / "roles/certificate_check/defaults/main.yaml").read_text())


def make_root(not_after_days, not_before_days=-1000):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + datetime.timedelta(days=not_before_days))
        .not_valid_after(now + datetime.timedelta(days=not_after_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode(), cert.not_valid_after_utc


def run_checks(tmp_path, root_pem):
    playbook = tmp_path / "play.yaml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Run the root CA checks",
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {**DEFAULTS, "certificate_check_root_ca_cert": root_pem},
        "tasks": [role_task(TASKS, name) for name in CHECKS] + [{
            "name": "Show the validity the host certificate gets",
            "ansible.builtin.debug": {"msg": "not_after=[{{ certificate_check_not_after }}]"},
        }],
    }]))
    run = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(playbook)],
        cwd=ANSIBLE_DIR, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        env={**os.environ, "ANSIBLE_NOCOLOR": "1"},
    )
    return run, " ".join((run.stdout + run.stderr).split())


def test_an_expired_root_stops_before_anything_is_signed(tmp_path):
    root, not_after = make_root(-13)
    run, out = run_checks(tmp_path, root)
    assert run.returncode != 0, out
    assert "The root CA expired on " + not_after.strftime("%Y-%m-%d") in out
    assert "ansible/README.md" in out
    assert "not_after=[" not in out


def test_a_root_that_expires_soon_warns_and_caps_the_host_certificate(tmp_path):
    root, not_after = make_root(60)
    run, out = run_checks(tmp_path, root)
    assert run.returncode == 0, out
    assert "The root CA expires on " + not_after.strftime("%Y-%m-%d") in out
    # x509_certificate_pipe takes an absolute ASN.1 time for ownca_not_after
    assert "not_after=[" + not_after.strftime("%Y%m%d%H%M%SZ") + "]" in out


def test_a_root_valid_for_years_neither_warns_nor_caps(tmp_path):
    root, _ = make_root(3650)
    run, out = run_checks(tmp_path, root)
    assert run.returncode == 0, out
    assert "The root CA expires on" not in out
    assert "not_after=[+{}d]".format(DEFAULTS["certificate_check_validity_days"]) in out


def test_the_warning_comes_well_before_the_last_renewal_of_a_host():
    # A host certificate lives a year; the warning must leave time to roll out a new root before the hosts need it.
    assert DEFAULTS["certificate_check_root_ca_warning_days"] >= DEFAULTS["certificate_check_validity_days"]


def test_the_host_certificate_is_signed_with_the_capped_validity():
    sign = role_task(TASKS, "Sign host certificate with the root CA on the control node")
    assert sign["community.crypto.x509_certificate_pipe"]["ownca_not_after"] == "{{ certificate_check_not_after }}"
