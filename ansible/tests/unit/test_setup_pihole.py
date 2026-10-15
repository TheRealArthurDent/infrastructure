"""setup-pihole.yaml against a fake Pi-hole API on localhost (#234).

The fake speaks just enough of the Pi-hole v6 REST API for the playbook: auth, DNS hosts and CNAME records,
block lists and the gravity action. It runs over HTTPS with a certificate from a throwaway CA, and the
playbook gets that CA's certificate from a throwaway KeePass database, like it does from the real one.
"""
import datetime
import json
import os
import ssl
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pykeepass import create_database

ANSIBLE_DIR = Path(__file__).parents[2]
PIHOLE_PASSWORD = "pihole-secret"
KEEPASS_PASSWORD = "test-master-password"

# Query parameters in the list URL: the DELETE path only matches if the playbook encodes them.
YOYO = "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext"
OLD_LIST = "https://old.example/list.php?a=1&b=2"

DESIRED = {
    "dns_a_records": [{"domain": "raspi", "address": "192.168.1.2"}],
    "dns_cname_records": [{"domain": "pihole.raspi", "target": "raspi"}],
    "dns_block_lists": ["https://adaway.org/hosts.txt", YOYO],
}


# --- certificates -------------------------------------------------------------------------------------------

def _name(common_name):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _make_ca(common_name):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _make_server_cert(ca_key, ca_cert, directory):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path = directory / "server.pem"
    key_path = directory / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    trusted_key, trusted_cert = _make_ca("Test Root CA")
    unknown_key, unknown_cert = _make_ca("Unknown CA")
    return {
        "ca_pem": trusted_cert.public_bytes(serialization.Encoding.PEM),
        "trusted": _make_server_cert(trusted_key, trusted_cert, tmp_path_factory.mktemp("trusted")),
        "unknown": _make_server_cert(unknown_key, unknown_cert, tmp_path_factory.mktemp("unknown")),
    }


@pytest.fixture(scope="module")
def keepass(tmp_path_factory, pki):
    directory = tmp_path_factory.mktemp("keepass")
    keyfile = directory / "test.key"
    keyfile.write_bytes(os.urandom(64))
    db = directory / "test.kdbx"
    kp = create_database(str(db), password=KEEPASS_PASSWORD, keyfile=str(keyfile))
    smart_home = kp.add_group(kp.root_group, "smart-home")
    kp.add_entry(smart_home, "pihole", "", PIHOLE_PASSWORD)
    domain = kp.add_group(kp.root_group, "Domain")
    ca = kp.add_entry(domain, "rootCA", "", "ca-passphrase")
    ca.add_attachment(kp.add_binary(pki["ca_pem"]), "rootCA.pem")
    kp.save()
    return {"db": str(db), "key": str(keyfile)}


# --- fake Pi-hole -------------------------------------------------------------------------------------------

class FakePihole:
    RECORD_ENDPOINTS = {"/api/config/dns/hosts": "hosts", "/api/config/dns/cnameRecords": "cnameRecords"}

    def __init__(self, hosts=(), cnames=(), lists=(), fail=None):
        self.records = {"hosts": list(hosts), "cnameRecords": list(cnames)}
        self.lists = list(lists)
        self.sessions = set()
        self.requests = []
        self.gravity_runs = 0
        # (method, path) that answers 500, to simulate a failure in the middle of a run
        self.fail = fail

    def handle(self, method, raw_path, headers, body):
        url = urlsplit(raw_path)
        query = parse_qs(url.query)
        self.requests.append((method, raw_path))

        if (method, url.path) == self.fail:
            return 500, {"error": "injected failure"}

        if url.path == "/api/auth":
            if method == "POST":
                if json.loads(body).get("password") != PIHOLE_PASSWORD:
                    return 401, {"error": "unauthorized"}
                sid = uuid.uuid4().hex
                self.sessions.add(sid)
                return 200, {"session": {"valid": True, "sid": sid}}
            if method == "DELETE" and headers.get("X-FTL-SID") in self.sessions:
                self.sessions.discard(headers["X-FTL-SID"])
                return 204, None
            return 401, {"error": "unauthorized"}

        if headers.get("X-FTL-SID") not in self.sessions:
            return 401, {"error": "unauthorized"}

        for endpoint, key in self.RECORD_ENDPOINTS.items():
            if url.path == endpoint and method == "GET":
                return 200, {"config": {"dns": {key: self.records[key]}}}
            if url.path.startswith(endpoint + "/"):
                record = unquote(url.path[len(endpoint) + 1:])
                return self._change(self.records[key], method, "PUT", record)

        if url.path == "/api/lists" and query.get("type") == ["block"]:
            if method == "GET":
                return 200, {"lists": [{"address": address, "type": "block"} for address in self.lists]}
            if method == "POST":
                return self._change(self.lists, "PUT", "PUT", json.loads(body)["address"])
        if url.path.startswith("/api/lists/") and query.get("type") == ["block"]:
            return self._change(self.lists, method, "PUT", unquote(url.path[len("/api/lists/"):]))

        if url.path == "/api/action/gravity" and method == "POST":
            self.gravity_runs += 1
            return 200, "  [✓] Building tree\n"

        return 404, {"error": "not found"}

    @staticmethod
    def _change(collection, method, add_method, value):
        if method == add_method:
            if value in collection:
                return 400, {"error": "already exists"}
            collection.append(value)
            return 201, {}
        if method == "DELETE":
            if value not in collection:
                return 404, {"error": "not found"}
            collection.remove(value)
            return 204, None
        return 405, {"error": "method not allowed"}

    def changing_requests(self):
        return [(method, path) for method, path in self.requests
                if method in ("PUT", "POST", "DELETE") and not path.startswith("/api/auth")]


def serve(pihole, cert):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            status, payload = pihole.handle(self.command, self.path, self.headers, body)
            self.send_response(status)
            if payload is None:
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_header("Content-Type", "text/plain" if isinstance(payload, str) else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _dispatch

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*cert)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def run(tmp_path, keepass, pki):
    servers = []

    def _run(pihole, cert="trusted"):
        server = serve(pihole, pki[cert])
        servers.append(server)
        extra_vars = tmp_path / "vars.json"
        extra_vars.write_text(json.dumps({
            **DESIRED,
            "pihole_host": "localhost",
            "pihole_port": server.server_address[1],
            "keepass_db": keepass["db"],
            "keepass_key": keepass["key"],
        }))
        return subprocess.run(
            # testbed is not reachable from here: if anything tries to connect to it, the run fails.
            ["ansible-playbook", "setup-pihole.yaml", "--limit", "testbed", "-e", f"@{extra_vars}"],
            cwd=ANSIBLE_DIR,
            env={**os.environ, "KEEPASS_PASSWORD": KEEPASS_PASSWORD, "ANSIBLE_TIMEOUT": "3"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )

    yield _run
    for server in servers:
        server.shutdown()
        server.server_close()


def output(result):
    return result.stdout + result.stderr


# --- tests --------------------------------------------------------------------------------------------------

def test_syncs_records_and_lists_without_touching_the_pi(run):
    pihole = FakePihole(hosts=["10.0.0.9 old"], cnames=["old.raspi,raspi"],
                        lists=["https://adaway.org/hosts.txt", OLD_LIST])

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert pihole.records["hosts"] == ["192.168.1.2 raspi"]
    assert pihole.records["cnameRecords"] == ["pihole.raspi,raspi"]
    assert sorted(pihole.lists) == sorted(DESIRED["dns_block_lists"])


def test_encodes_list_urls_in_the_path(run):
    pihole = FakePihole(lists=[OLD_LIST])

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert ("DELETE", "/api/lists/https%3A%2F%2Fold.example%2Flist.php%3Fa%3D1%26b%3D2?type=block") \
        in pihole.requests


def test_updates_gravity_once_after_list_changes(run):
    pihole = FakePihole(lists=[OLD_LIST])

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert pihole.gravity_runs == 1


def test_second_run_changes_nothing(run):
    pihole = FakePihole()
    assert run(pihole).returncode == 0
    pihole.requests.clear()

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert pihole.changing_requests() == []
    assert pihole.gravity_runs == 1


def test_logs_out_when_a_task_fails(run):
    pihole = FakePihole(fail=("GET", "/api/lists"))

    result = run(pihole)

    assert result.returncode != 0
    assert ("DELETE", "/api/auth") in pihole.requests, output(result)
    assert pihole.sessions == set()


def test_logs_out_after_a_successful_run(run):
    pihole = FakePihole()

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert pihole.sessions == set()


def test_rejects_a_certificate_from_an_unknown_ca(run):
    pihole = FakePihole()

    result = run(pihole, cert="unknown")

    assert result.returncode != 0
    assert pihole.requests == [], "the password must not be sent to a server we can't verify"


def test_password_never_shows_up_in_output(run):
    result = run(FakePihole())

    assert result.returncode == 0, output(result)
    assert PIHOLE_PASSWORD not in output(result)
    assert KEEPASS_PASSWORD not in output(result)


def test_block_list_urls_are_not_html_escaped():
    config = yaml.safe_load((ANSIBLE_DIR / "configs" / "pihole.yaml").read_text())

    assert [url for url in config["dns_block_lists"] if "&amp;" in url] == []
