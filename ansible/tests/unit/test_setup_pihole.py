"""setup-pihole.yaml against a fake Pi-hole API on localhost (#234).

The fake speaks just enough of the Pi-hole v6 REST API for the playbook: auth, DNS hosts and CNAME records,
block lists, the gravity action, groups, clients and domains. It runs over HTTPS with a certificate from a throwaway CA, and the
playbook gets that CA's certificate from a throwaway KeePass database, like it does from the real one.
The playbook configures several Pi-hole instances (the main one and outbound, #293), each with its own password.
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
OUTBOUND_PASSWORD = "outbound-secret"
KEEPASS_PASSWORD = "test-master-password"

# Query parameters in the list URL: the DELETE path only matches if the playbook encodes them.
YOYO = "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext"
OLD_LIST = "https://old.example/list.php?a=1&b=2"

# One instance in pihole_instances, without name, host and port (the test fills those in).
DESIRED = {
    "a_records": [{"domain": "raspi", "address": "192.168.1.2"}],
    "cname_records": [{"domain": "pihole.raspi", "target": "raspi"}],
    "block_lists": ["https://adaway.org/hosts.txt", YOYO],
    "groups": [{"name": "kids", "comment": "Kids' devices"}, {"name": "iot"}],
    "clients": [{"client": "192.168.1.50", "comment": "Tablet", "groups": ["Default", "kids"]}],
    "domains": [
        {"domain": "example.com", "type": "allow", "kind": "exact", "groups": ["kids"]},
        # '/' and '\\' in a regex: the item path only matches if the playbook encodes them.
        {"domain": r"(^|\.)tracker\.example/x$", "type": "deny", "kind": "regex", "comment": "Tracker"},
    ],
}
GROUP_MANAGEMENT = ("groups", "clients", "domains")
# An egress filter like outbound: nothing but a few allowed domains resolves.
OUTBOUND_DESIRED = {
    "a_records": [],
    "cname_records": [],
    "block_lists": [],
    "groups": [],
    "clients": [],
    "domains": [
        {"domain": ".*", "type": "deny", "kind": "regex"},
        {"domain": "registry.npmjs.org", "type": "allow", "kind": "exact"},
    ],
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
    kp.add_entry(smart_home, "outbound", "", OUTBOUND_PASSWORD)
    domain = kp.add_group(kp.root_group, "Domain")
    ca = kp.add_entry(domain, "rootCA", "", "ca-passphrase")
    ca.add_attachment(kp.add_binary(pki["ca_pem"]), "rootCA.pem")
    kp.save()
    return {"db": str(db), "key": str(keyfile)}


# --- fake Pi-hole -------------------------------------------------------------------------------------------

class FakePihole:
    RECORD_ENDPOINTS = {"/api/config/dns/hosts": "hosts", "/api/config/dns/cnameRecords": "cnameRecords"}

    def __init__(self, hosts=(), cnames=(), lists=(), groups=(), clients=(), domains=(), fail=None,
                 unavailable_after_change=0, unavailable_as="bad_gateway", password=PIHOLE_PASSWORD):
        self.password = password
        self.records = {"hosts": list(hosts), "cnameRecords": list(cnames)}
        self.lists = list(lists)
        self.next_group_id = 1
        self.groups = [{"id": 0, "name": "Default", "comment": "The default group", "enabled": True}]
        for group in groups:
            self._add_group(group)
        self.clients = [{"comment": "", "groups": [0], **client} for client in clients]
        self.domains = [{"comment": "", "groups": [0], "enabled": True, **domain} for domain in domains]
        self.sessions = set()
        self.requests = []
        self.gravity_runs = 0
        # (method, path) that answers 500, to simulate a failure in the middle of a run
        self.fail = fail
        # FTL restarts after some changes; nginx answers 502 to the next requests until it's back
        self.unavailable_after_change = unavailable_after_change
        self.unavailable_as = unavailable_as
        self.unavailable = 0

    def handle(self, method, raw_path, headers, body):
        url = urlsplit(raw_path)
        query = parse_qs(url.query)
        self.requests.append((method, raw_path))

        if (method, url.path) == self.fail:
            return 500, {"error": "injected failure"}
        if self.unavailable:
            self.unavailable -= 1
            if self.unavailable_as == "database":
                return 500, {"error": {"key": "database_error", "message": "Could not read domains from database table",
                                       "hint": "Database not available"}}
            return 502, "<html><body><h1>502 Bad Gateway</h1></body></html>"
        status, payload = self._handle(method, url, query, headers, body)
        if method in ("PUT", "POST", "DELETE") and url.path != "/api/auth" and status < 300:
            self.unavailable = self.unavailable_after_change
        return status, payload

    def _handle(self, method, url, query, headers, body):

        if url.path == "/api/auth":
            if method == "POST":
                if json.loads(body).get("password") != self.password:
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

        if url.path.startswith("/api/groups"):
            return self._groups(method, url.path, body)
        if url.path.startswith("/api/clients"):
            return self._clients(method, url.path, body)
        if url.path.startswith("/api/domains"):
            return self._domains(method, url.path, body)

        return 404, {"error": "not found"}

    def group_ids(self, *names):
        return sorted(group["id"] for group in self.groups if group["name"] in names)

    def _add_group(self, group):
        self.groups.append({"comment": "", "enabled": True, **group, "id": self.next_group_id})
        self.next_group_id += 1

    def _check_group_ids(self, ids):
        return set(ids) <= {group["id"] for group in self.groups}

    @staticmethod
    def _processed(item):
        return {"processed": {"success": [{"item": item}], "errors": []}}

    @staticmethod
    def _item(path, prefix):
        return unquote(path[len(prefix):]) if path.startswith(prefix) else None

    def _groups(self, method, path, body):
        name = self._item(path, "/api/groups/")
        group = next((g for g in self.groups if g["name"] == name), None)
        if path == "/api/groups" and method == "GET":
            return 200, {"groups": self.groups}
        if path == "/api/groups" and method == "POST":
            new = json.loads(body)
            if any(g["name"] == new["name"] for g in self.groups):
                return 400, {"error": "already exists"}
            self._add_group(new)
            return 201, self._processed(new["name"])
        if group is None:
            return 404, {"error": "not found"}
        if method == "PUT":
            group.update(json.loads(body))
            return 200, self._processed(name)
        if method == "DELETE":
            if group["id"] == 0:
                return 400, {"error": "the default group can't be deleted"}
            self.groups.remove(group)
            for item in self.clients + self.domains:
                item["groups"] = [i for i in item["groups"] if i != group["id"]]
            return 204, None
        return 405, {"error": "method not allowed"}

    def _clients(self, method, path, body):
        address = self._item(path, "/api/clients/")
        client = next((c for c in self.clients if c["client"] == address), None)
        if path == "/api/clients" and method == "GET":
            return 200, {"clients": [{**c, "name": None, "id": n} for n, c in enumerate(self.clients)]}
        if method in ("POST", "PUT") and not self._check_group_ids(json.loads(body)["groups"]):
            return 400, {"error": "unknown group"}
        if path == "/api/clients" and method == "POST":
            new = json.loads(body)
            if any(c["client"] == new["client"] for c in self.clients):
                return 400, {"error": "already exists"}
            self.clients.append(new)
            return 201, self._processed(new["client"])
        if client is None:
            return 404, {"error": "not found"}
        if method == "PUT":
            client.update(json.loads(body))
            return 200, self._processed(address)
        if method == "DELETE":
            self.clients.remove(client)
            return 204, None
        return 405, {"error": "method not allowed"}

    def _domains(self, method, path, body):
        if path == "/api/domains" and method == "GET":
            return 200, {"domains": [{**d, "unicode": d["domain"], "id": n} for n, d in enumerate(self.domains)]}
        parts = path[len("/api/domains/"):].split("/")
        if len(parts) < 2 or parts[0] not in ("allow", "deny") or parts[1] not in ("exact", "regex"):
            return 404, {"error": "not found"}
        kind = {"type": parts[0], "kind": parts[1]}
        if method in ("POST", "PUT") and not self._check_group_ids(json.loads(body)["groups"]):
            return 400, {"error": "unknown group"}
        if len(parts) == 2 and method == "POST":
            new = {**json.loads(body), **kind}
            if any(d["domain"] == new["domain"] and d["type"] == new["type"] and d["kind"] == new["kind"]
                   for d in self.domains):
                return 400, {"error": "already exists"}
            self.domains.append(new)
            return 201, self._processed(new["domain"])
        if len(parts) != 3:
            return 404, {"error": "not found"}
        domain = next((d for d in self.domains
                       if d["domain"] == unquote(parts[2]) and d["type"] == kind["type"] and d["kind"] == kind["kind"]),
                      None)
        if domain is None:
            return 404, {"error": "not found"}
        if method == "PUT":
            domain.update(json.loads(body))
            return 200, self._processed(domain["domain"])
        if method == "DELETE":
            self.domains.remove(domain)
            return 204, None
        return 405, {"error": "method not allowed"}

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

    def _run(pihole, cert="trusted", desired=None, instances=None, args=()):
        """Run against one fake Pi-hole, or against several given as instances: [(name, fake, desired)]."""
        if instances is None:
            instances = [("pihole", pihole, DESIRED if desired is None else desired)]
        configured = []
        for name, fake, config in instances:
            server = serve(fake, pki[cert])
            servers.append(server)
            configured.append({**config, "name": name, "host": "localhost", "port": server.server_address[1]})
        extra_vars = tmp_path / "vars.json"
        extra_vars.write_text(json.dumps({
            "pihole_instances": configured,
            "pihole_api_delay": 0,
            "keepass_db": keepass["db"],
            "keepass_key": keepass["key"],
        }))
        return subprocess.run(
            # testbed is not reachable from here: if anything tries to connect to it, the run fails.
            ["ansible-playbook", "setup-pihole.yaml", "--limit", "testbed", "-e", f"@{extra_vars}", *args],
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
    assert sorted(pihole.lists) == sorted(DESIRED["block_lists"])


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


def test_check_mode_reports_changes_without_making_them(run):
    pihole = FakePihole(hosts=["10.0.0.9 old"], lists=[OLD_LIST])

    result = run(pihole, args=("--check", "--diff"))

    assert result.returncode == 0, output(result)
    assert pihole.changing_requests() == []
    assert pihole.records["hosts"] == ["10.0.0.9 old"]
    assert "Hosts to delete: ['10.0.0.9 old']" in result.stdout
    assert "Lists to delete: ['" + OLD_LIST + "']" in result.stdout
    assert ("DELETE", "/api/auth") in pihole.requests


@pytest.mark.parametrize("unavailable_as", ["bad_gateway", "database"])
def test_waits_for_the_api_when_pi_hole_restarts_after_a_change(run, unavailable_as):
    pihole = FakePihole(unavailable_as=unavailable_as, hosts=["10.0.0.9 old"], cnames=["old.raspi,raspi"], lists=[OLD_LIST],
                        groups=[{"name": "old"}], clients=[{"client": "192.168.1.99"}],
                        domains=[{"domain": "old.example", "type": "deny", "kind": "exact"}],
                        unavailable_after_change=2)

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert pihole.records["cnameRecords"] == ["pihole.raspi,raspi"]
    assert sorted(pihole.lists) == sorted(DESIRED["block_lists"])
    assert sorted(g["name"] for g in pihole.groups) == ["Default", "iot", "kids"]
    assert [c["client"] for c in pihole.clients] == ["192.168.1.50"]
    assert len(pihole.domains) == 2


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


def configured_instances():
    return yaml.safe_load((ANSIBLE_DIR / "configs" / "pihole.yaml").read_text())["pihole_instances"]


def test_block_list_urls_are_not_html_escaped():
    urls = [url for instance in configured_instances() for url in instance["block_lists"]]

    assert [url for url in urls if "&amp;" in url] == []


def test_syncs_groups_and_keeps_the_default_group(run):
    pihole = FakePihole(groups=[{"name": "old"}, {"name": "iot", "comment": "stale", "enabled": False}])

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert sorted((g["name"], g["comment"], g["enabled"]) for g in pihole.groups) == [
        ("Default", "The default group", True),
        ("iot", "", True),
        ("kids", "Kids' devices", True),
    ]


def test_syncs_clients_with_groups_by_name(run):
    pihole = FakePihole(clients=[{"client": "192.168.1.99"}, {"client": "192.168.1.50", "comment": "Old"}])

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert pihole.clients == [
        {"client": "192.168.1.50", "comment": "Tablet", "groups": pihole.group_ids("Default", "kids")},
    ]


def test_syncs_domains_and_encodes_them_in_the_path(run):
    old_regex = r"^ads/[0-9]+\.example$"
    pihole = FakePihole(domains=[
        {"domain": old_regex, "type": "deny", "kind": "regex"},
        {"domain": "example.com", "type": "allow", "kind": "exact", "enabled": False},
    ])

    result = run(pihole)

    assert result.returncode == 0, output(result)
    assert sorted((d["domain"], d["type"], d["kind"], d["comment"], d["groups"], d["enabled"])
                  for d in pihole.domains) == sorted([
        ("example.com", "allow", "exact", "", pihole.group_ids("kids"), True),
        (r"(^|\.)tracker\.example/x$", "deny", "regex", "Tracker", [0], True),
    ])
    assert ("DELETE", "/api/domains/deny/regex/%5Eads%2F%5B0-9%5D%2B%5C.example%24") in pihole.requests


def test_leaves_groups_clients_and_domains_alone_when_not_configured(run):
    pihole = FakePihole(groups=[{"name": "manual"}], clients=[{"client": "192.168.1.99"}],
                        domains=[{"domain": "manual.example", "type": "deny", "kind": "exact"}])
    # configs/pihole.yaml defines the lists, and extra vars can't undefine them, so they're set to null.
    desired = {**DESIRED, **dict.fromkeys(GROUP_MANAGEMENT)}

    result = run(pihole, desired=desired)

    assert result.returncode == 0, output(result)
    assert [g["name"] for g in pihole.groups] == ["Default", "manual"]
    assert [c["client"] for c in pihole.clients] == ["192.168.1.99"]
    assert [d["domain"] for d in pihole.domains] == ["manual.example"]


def test_fails_on_an_unknown_group_before_changing_clients_or_domains(run):
    pihole = FakePihole(clients=[{"client": "192.168.1.99"}])
    desired = {**DESIRED, "clients": [{"client": "192.168.1.50", "groups": ["nope"]}]}

    result = run(pihole, desired=desired)

    assert result.returncode != 0
    assert "nope" in output(result)
    assert [c["client"] for c in pihole.clients] == ["192.168.1.99"]
    assert [d for d in pihole.changing_requests() if d[1].startswith(("/api/clients", "/api/domains"))] == []


@pytest.mark.parametrize("instance", configured_instances(), ids=lambda instance: instance["name"])
def test_clients_and_domains_in_the_config_only_use_configured_groups(instance):
    known = {"Default"} | {group["name"] for group in instance["groups"] or []}

    used = {group for item in (instance["clients"] or []) + (instance["domains"] or [])
            for group in item.get("groups", ["Default"])}

    assert used <= known


def test_configures_every_instance_with_its_own_password(run):
    main = FakePihole(domains=[{"domain": "old.example", "type": "deny", "kind": "exact"}])
    outbound = FakePihole(password=OUTBOUND_PASSWORD, lists=["https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"],
                          domains=[{"domain": "api.telegram.org", "type": "allow", "kind": "exact"}])

    result = run(None, instances=[("pihole", main, DESIRED), ("outbound", outbound, OUTBOUND_DESIRED)])

    assert result.returncode == 0, output(result)
    assert sorted(main.lists) == sorted(DESIRED["block_lists"])
    assert len(main.domains) == 2
    assert outbound.lists == []
    assert sorted((d["domain"], d["type"], d["kind"]) for d in outbound.domains) == [
        (".*", "deny", "regex"),
        ("registry.npmjs.org", "allow", "exact"),
    ]
    assert main.sessions == set() and outbound.sessions == set()
    assert OUTBOUND_PASSWORD not in output(result)


def test_second_run_changes_no_instance(run):
    main, outbound = FakePihole(), FakePihole(password=OUTBOUND_PASSWORD)
    instances = [("pihole", main, DESIRED), ("outbound", outbound, OUTBOUND_DESIRED)]
    assert run(None, instances=instances).returncode == 0
    main.requests.clear()
    outbound.requests.clear()

    result = run(None, instances=instances)

    assert result.returncode == 0, output(result)
    assert main.changing_requests() == [] and outbound.changing_requests() == []


def test_logs_out_of_every_instance_when_a_later_one_fails(run):
    main = FakePihole()
    outbound = FakePihole(password=OUTBOUND_PASSWORD, fail=("GET", "/api/lists"))

    result = run(None, instances=[("pihole", main, DESIRED), ("outbound", outbound, OUTBOUND_DESIRED)])

    assert result.returncode != 0
    assert main.sessions == set() and outbound.sessions == set(), output(result)


def test_configured_instances_are_the_main_pihole_and_outbound():
    assert [instance["name"] for instance in configured_instances()] == ["pihole", "outbound"]


def test_outbound_only_resolves_allowed_domains():
    """outbound is the egress filter for the smart-home containers: everything not allowed is denied (#293)."""
    outbound = next(instance for instance in configured_instances() if instance["name"] == "outbound")
    domains = {(d["type"], d["kind"], d["domain"]) for d in outbound["domains"]}

    assert ("deny", "regex", ".*") in domains
    # A block list would only cost a gravity download: '.*' already blocks everything.
    assert outbound["block_lists"] == []
