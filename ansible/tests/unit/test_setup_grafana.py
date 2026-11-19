"""setup-grafana.yaml (#39, smart-home#11): the data source, folders and dashboards of the repo, through Grafana's API.

Runs the playbook from the control node against a real Grafana next to a real VictoriaMetrics and Telegraf agent
(monitoring_stack, needs Docker: CI), over plain HTTP on a local port instead of https://grafana.<host>. The admin
password and the root CA come from a throwaway KeePass database, like they do from the real one.
"""
import base64
import datetime
import glob
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pykeepass import create_database

from monitoring_stack import docker_available, grafana_port, monitoring_stack, query_until

ANSIBLE_DIR = Path(__file__).parents[2]
KEEPASS_PASSWORD = "test-master-password"
# Anything but ${...} and blanks at the ends, which the secrets role refuses
PASSWORD = 'grafana $x {{ z }} "q" \'s\' \\b #c'
DASHBOARD = json.loads((ANSIBLE_DIR / "configs/grafana/dashboards/infrastructure/hosts.json").read_text())

needs_docker = pytest.mark.skipif(not docker_available(), reason="no Docker daemon")


def ca_pem():
    """A throwaway CA certificate: uri loads it even for plain HTTP."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def keepass(tmp_path_factory):
    directory = tmp_path_factory.mktemp("keepass")
    keyfile = directory / "test.key"
    keyfile.write_bytes(os.urandom(64))
    db = directory / "test.kdbx"
    kp = create_database(str(db), password=KEEPASS_PASSWORD, keyfile=str(keyfile))
    monitoring = kp.add_group(kp.root_group, "Monitoring")
    kp.add_entry(monitoring, "Grafana admin", "", PASSWORD)
    domain = kp.add_group(kp.root_group, "Domain")
    ca = kp.add_entry(domain, "rootCA", "", "ca-passphrase")
    ca.add_attachment(kp.add_binary(ca_pem()), "rootCA.pem")
    kp.save()
    return {"db": str(db), "key": str(keyfile)}


def run_playbook(tmp_path, keepass, url, limit="dungeon", args=()):
    extra_vars = tmp_path / "vars.json"
    extra_vars.write_text(json.dumps({
        "grafana_api_url": url,
        "keepass_databases": {name: keepass for name in ("raspberry", "dungeon", "testbench")},
    }))
    return subprocess.run(
        # The hosts are not reachable from here: if anything tries to connect to one, the run fails.
        ["ansible-playbook", "setup-grafana.yaml", "--limit", limit, "-e", f"@{extra_vars}", *args],
        cwd=ANSIBLE_DIR,
        env={**os.environ, "KEEPASS_PASSWORD": KEEPASS_PASSWORD, "KEEPASS_PASSWORD_DUNGEON": KEEPASS_PASSWORD,
             "KEEPASS_PASSWORD_TESTBENCH": KEEPASS_PASSWORD, "ANSIBLE_TIMEOUT": "3", "ANSIBLE_NOCOLOR": "1"},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600,
    )


def output(result):
    return result.stdout + result.stderr


def changed(result):
    return int(re.search(r"changed=(\d+)", result.stdout).group(1))


def test_a_host_without_grafana_is_refused_before_anything_is_contacted(tmp_path, keepass):
    result = run_playbook(tmp_path, keepass, "http://127.0.0.1:1", limit="raspi")
    assert result.returncode != 0
    assert "raspi runs no Grafana" in " ".join(output(result).split())


def test_a_run_needs_exactly_one_host(tmp_path, keepass):
    result = run_playbook(tmp_path, keepass, "http://127.0.0.1:1", limit="all")
    assert result.returncode != 0
    assert "Run with --limit <one host>" in output(result)


# --- against a real Grafana -------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def grafana(tmp_path_factory):
    with monitoring_stack(tmp_path_factory.mktemp("setup-grafana"), PASSWORD, publish_grafana=True) as base:
        yield {"base": base, "url": f"http://127.0.0.1:{grafana_port(base)}"}


def api(grafana, method, path, body=None):
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        grafana["url"] + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


@pytest.fixture(scope="module")
def applied(grafana, keepass, tmp_path_factory):
    """Grafana after the first run, and that run."""
    result = run_playbook(tmp_path_factory.mktemp("first-run"), keepass, grafana["url"])
    assert result.returncode == 0, output(result)
    return result


@needs_docker
def test_the_first_run_creates_the_data_source_folder_and_dashboard(grafana, applied):
    assert changed(applied) > 0
    status, datasource = api(grafana, "GET", "/api/datasources/uid/victoriametrics")
    assert status == 200, datasource
    assert (datasource["name"], datasource["type"], datasource["url"], datasource["isDefault"]) == (
        "VictoriaMetrics", "prometheus", "http://victoriametrics:8428", True)
    status, folder = api(grafana, "GET", "/api/folders/infrastructure")
    assert status == 200 and folder["title"] == "Infrastructure", folder
    status, dashboard = api(grafana, "GET", "/api/dashboards/uid/hosts")
    assert status == 200, dashboard
    assert dashboard["meta"]["folderUid"] == "infrastructure"
    assert dashboard["dashboard"]["title"] == "Hosts"


@needs_docker
def test_the_password_stays_out_of_the_output(applied):
    assert PASSWORD not in output(applied)
    assert KEEPASS_PASSWORD not in output(applied)


@needs_docker
def test_grafana_reaches_victoriametrics_through_the_data_source(grafana, applied):
    status, health = api(grafana, "GET", "/api/datasources/uid/victoriametrics/health")
    assert status == 200 and health["status"] == "OK", health


@needs_docker
def test_a_second_run_changes_nothing(grafana, applied, keepass, tmp_path):
    result = run_playbook(tmp_path, keepass, grafana["url"])
    assert result.returncode == 0, output(result)
    assert changed(result) == 0, output(result)


@needs_docker
def test_what_the_repo_lacks_is_deleted(grafana, applied, keepass, tmp_path):
    assert api(grafana, "POST", "/api/datasources", {
        "uid": "extra", "name": "Extra", "type": "prometheus", "access": "proxy", "url": "http://nowhere:9090"})[0] == 200
    assert api(grafana, "POST", "/api/folders", {"uid": "old", "title": "Old"})[0] == 200
    assert api(grafana, "POST", "/api/dashboards/db", {
        "dashboard": {"uid": "extra", "title": "Extra", "panels": []}, "folderUid": "old"})[0] == 200
    # A changed dashboard and data source come back as in the repo
    status, hosts = api(grafana, "GET", "/api/dashboards/uid/hosts")
    hosts["dashboard"]["title"] = "Changed in the UI"
    assert api(grafana, "POST", "/api/dashboards/db", {
        "dashboard": hosts["dashboard"], "folderUid": "infrastructure", "overwrite": True})[0] == 200

    check = run_playbook(tmp_path, keepass, grafana["url"], args=["--check"])
    assert check.returncode == 0, output(check)
    assert api(grafana, "GET", "/api/datasources/uid/extra")[0] == 200, "check mode deleted the data source"

    result = run_playbook(tmp_path, keepass, grafana["url"])
    assert result.returncode == 0, output(result)
    assert api(grafana, "GET", "/api/datasources/uid/extra")[0] == 404
    assert api(grafana, "GET", "/api/dashboards/uid/extra")[0] == 404
    assert api(grafana, "GET", "/api/folders/old")[0] == 404
    assert api(grafana, "GET", "/api/dashboards/uid/hosts")[1]["dashboard"]["title"] == "Hosts"


def panel_queries():
    for panel in DASHBOARD["panels"]:
        for target in panel["targets"]:
            yield pytest.param(target["expr"], id=f"{panel['title']} {target['refId']}")


@needs_docker
@pytest.mark.parametrize("expr", panel_queries())
def test_every_panel_query_finds_the_agents_metrics(grafana, expr):
    if expr.startswith("temp_temp") and not glob.glob("/sys/class/hwmon/*/temp*_input"):
        pytest.skip("this machine has no temperature sensor for the agent to read")
    promql = expr.replace("$host", "dungeon").replace("$__rate_interval", "1m")
    assert '"result":[{' in query_until(grafana["base"], promql, '"result":[{'), promql
