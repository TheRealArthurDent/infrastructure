"""The agents' write path to the central part of the monitoring (#39), as the stack renders it.

Every agent writes through nginx on the host running the monitoring component (https://metrics.<central host>/write),
signed in with a credential of its own. On the central host the agent reaches nginx in the network siz under that
name; on any other host through the LAN, with the central host's IP from a lookup on the Pi (extra_hosts).
"""
import re
from pathlib import Path

import pytest
import yaml

from render import ANSIBLE_DIR, DUNGEON_IP, rendered_tree

ROLES_DIR = ANSIBLE_DIR / "roles"
SECRET = "/etc/docker/infrastructure/secrets/metrics_writer_password"


@pytest.fixture(scope="module")
def dungeon(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("dungeon-write-path"), "dungeon")


@pytest.fixture(scope="module")
def raspi(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("raspi-write-path"), "raspi")


@pytest.fixture(scope="module")
def testbench(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("testbench-write-path"), "testbench")


def compose(tree):
    return yaml.safe_load(tree["docker-compose.yaml"])


def server_block(nginx_conf, name):
    match = re.search(r"server \{\s*listen\s+443 ssl;[^}]*server_name\s+" + re.escape(name) + r" [^}]*\}", nginx_conf)
    assert match, f"no server block for {name}"
    return match.group(0)


def location(snippet, path):
    match = re.search(r"location " + re.escape(path) + r" \{([^}]*)\}", snippet)
    assert match, f"no location {path}"
    return match.group(1)


def test_the_central_host_serves_the_write_path(dungeon):
    block = server_block(dungeon["nginx/nginx.conf"], "metrics.dungeon")
    assert "include             snippets/ssl-certificates.conf;" in block
    assert "include             snippets/metrics.conf;" in block


def test_writes_need_a_credential_and_reach_victoriametrics(dungeon):
    write = location(dungeon["nginx/snippets/metrics.conf"], "= /write")
    assert "auth_basic_user_file /etc/nginx/metrics.htpasswd;" in write
    assert re.search(r"auth_basic \"[^\"]+\";", write)
    assert "set $victoriametrics victoriametrics:8428;" in write
    assert "proxy_pass http://$victoriametrics;" in write


def test_nothing_but_writes_and_the_health_endpoint_is_served(dungeon):
    snippet = dungeon["nginx/snippets/metrics.conf"]
    assert set(re.findall(r"location (.+?) \{", snippet)) == {"= /write", "= /health", "/"}
    assert "return 404;" in location(snippet, "/")
    health = location(snippet, "= /health")
    assert "return 200" in health
    assert "proxy_pass" not in health and "proxy_pass" not in location(snippet, "/")


def test_the_central_host_certificate_covers_the_write_path():
    dungeon = yaml.safe_load((ANSIBLE_DIR / "inventory/host_vars/dungeon.yaml").read_text())
    assert "metrics" in dungeon["certificate_check_subdomains"]
    assert "metrics" in dungeon["reverse_proxy_vhosts"]


def test_hosts_without_the_central_part_serve_no_write_path(testbench):
    assert "metrics." not in testbench["nginx/nginx.conf"]


def test_the_local_agent_reaches_nginx_by_name_in_siz(dungeon):
    services = compose(dungeon)["services"]
    assert "metrics.dungeon" in services["nginx"]["networks"]["siz"]["aliases"]
    agent = services["telegraf-agent"]
    assert sorted(agent["networks"]) == ["docker-api", "siz"]
    assert "extra_hosts" not in agent


@pytest.mark.parametrize("host", ["raspi", "testbench"])
def test_every_host_runs_the_agent(request, host):
    """Part of the stack's default components."""
    assert "telegraf-agent" in compose(request.getfixturevalue(host))["services"]
    assert "monitoring_agent" in yaml.safe_load((ROLES_DIR / "stack/defaults/main.yaml").read_text())["stack_components"]


@pytest.mark.parametrize("host", ["raspi", "testbench"])
def test_a_remote_agent_reaches_the_central_host_through_the_lan(request, host):
    agent = compose(request.getfixturevalue(host))["services"]["telegraf-agent"]
    # nginx is the stack's network with a route out (resticprofile reaches dungeon through it too); siz has none
    assert sorted(agent["networks"]) == ["docker-api", "nginx"]
    assert agent["extra_hosts"] == {"metrics.dungeon": DUNGEON_IP}


@pytest.mark.parametrize("host", ["dungeon", "raspi", "testbench"])
def test_every_agent_writes_through_nginx_as_its_host(request, host):
    conf = request.getfixturevalue(host)["telegraf/telegraf.conf"]
    assert 'urls = ["https://metrics.dungeon"]' in conf
    assert f'username = "{host}"' in conf
    assert 'password = "@{docker_secrets:metrics_writer_password}"' in conf
    assert '[[secretstores.docker]]\n  id = "docker_secrets"' in conf
    # The private root CA, from the host's file system
    assert 'tls_ca = "/hostfs/etc/ssl/certs/rootCA.pem"' in conf
    assert "victoriametrics:8428" not in conf
    assert "insecure_skip_verify" not in conf


@pytest.mark.parametrize("host", ["dungeon", "raspi", "testbench"])
def test_the_agent_reads_its_password_from_a_compose_secret(request, host):
    stack = compose(request.getfixturevalue(host))
    agent = stack["services"]["telegraf-agent"]
    assert agent["secrets"] == ["metrics_writer_password"]
    assert stack["secrets"]["metrics_writer_password"] == {"file": SECRET}
    assert "env_file" not in agent
    assert not [value for value in agent["environment"].values() if "PASSWORD" in str(value).upper()]


@pytest.mark.parametrize("host", ["dungeon", "raspi", "testbench"])
def test_the_agent_measures_the_certificate_lifetimes(request, host):
    conf = request.getfixturevalue(host)["telegraf/telegraf.conf"]
    assert "[[inputs.x509_cert]]" in conf
    assert (f'sources = ["/hostfs/etc/docker/infrastructure/nginx/{host}.pem", '
            '"/hostfs/etc/ssl/certs/rootCA.pem"]') in conf


@pytest.mark.parametrize("host", ["dungeon", "raspi", "testbench"])
def test_the_agent_turns_unhealthy_when_the_central_host_is_unreachable(request, host):
    """healthmonitor reports an unhealthy container, so a host outside the central one notices its outage."""
    healthcheck = compose(request.getfixturevalue(host))["services"]["telegraf-agent"]["healthcheck"]
    assert healthcheck["test"] == [
        "CMD-SHELL",
        "SSL_CERT_FILE=/hostfs/etc/ssl/certs/rootCA.pem wget -q -O /dev/null https://metrics.dungeon/health",
    ]
    assert healthcheck["interval"] and healthcheck["retries"] >= 2


def test_the_central_part_needs_the_write_path(tmp_path):
    """Without nginx and its metrics vhost, no agent could write."""
    tasks = yaml.safe_load((ROLES_DIR / "stack/tasks/components.yaml").read_text())
    names = [task["name"] for task in tasks]
    assert "Ensure the write path of the monitoring is served wherever its central part runs" in names


def test_the_secrets_role_writes_the_agent_password_and_the_writers_file():
    text = (ROLES_DIR / "secrets/tasks/main.yaml").read_text()
    assert SECRET in text
    assert "/etc/docker/infrastructure/nginx/metrics.htpasswd" in text


def test_dungeon_accepts_the_agents_of_every_host():
    dungeon = yaml.safe_load((ANSIBLE_DIR / "inventory/host_vars/dungeon.yaml").read_text())
    hosts = yaml.safe_load((ANSIBLE_DIR / "inventory/hosts.yaml").read_text())["all"]["hosts"]
    assert sorted(dungeon["monitoring_writers"]) == sorted(hosts)


def test_the_central_host_is_named_in_the_inventory_only():
    """Nothing in the roles may assume dungeon: the central part can move by changing the inventory."""
    group_vars = yaml.safe_load((ANSIBLE_DIR / "inventory/group_vars/all/monitoring.yaml").read_text())
    assert group_vars["monitoring_agent_central_host"] == "dungeon"
    for role in ("monitoring", "monitoring_agent"):
        for path in (ROLES_DIR / role).rglob("*"):
            if path.is_file():
                code = [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]
                assert not [line for line in code if "dungeon" in line], path
