"""The Docker API behind a read-only proxy: only portainer and the proxy itself mount the Docker socket.

Access to the socket is root on the host, read-only mount or not. healthmonitor and the monitoring agent only read the
container list, their stats and the engine info, so they get them from the proxy, which refuses everything else.
"""
import re

import pytest
import yaml

from render import rendered_tree
from test_stack_components import run_components_check, undefined_references

SOCKET = "/var/run/docker.sock"
PROXY_URL = "tcp://docker-socket-proxy:2375"


@pytest.fixture(scope="module", params=["raspi", "dungeon", "testbench"])
def compose(request, tmp_path_factory):
    tree = rendered_tree(tmp_path_factory.mktemp(request.param), request.param)
    return yaml.safe_load(tree["docker-compose.yaml"])


@pytest.fixture(scope="module")
def dungeon_tree(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("dungeon-proxy"), "dungeon")


def mounts_socket(service):
    for volume in service.get("volumes", []):
        source = volume.get("source") if isinstance(volume, dict) else volume.split(":")[0]
        if source == SOCKET:
            return True
    return False


def test_only_portainer_and_the_proxy_mount_the_docker_socket(compose):
    assert sorted(name for name, service in compose["services"].items() if mounts_socket(service)) == [
        "docker-socket-proxy", "portainer"]
    assert undefined_references(compose) == []


def test_the_proxy_only_allows_reading_containers_and_info(compose):
    proxy = compose["services"]["docker-socket-proxy"]
    # Pinned by tag and digest: the image holds the socket (renovate.json waits a month and never auto-merges it)
    assert re.fullmatch(r"linuxserver/socket-proxy:\d+\.\d+\.\d+-r\d+-ls\d+@sha256:[0-9a-f]{64}", proxy["image"])
    environment = proxy["environment"]
    assert environment["CONTAINERS"] == "1"
    assert environment["INFO"] == "1"
    assert environment["POST"] == "0"
    # Everything else the image offers stays at its default 0; none of it may be switched on here
    allowed = {"CONTAINERS", "INFO", "EVENTS", "PING", "VERSION", "DISABLE_IPV6"}
    assert {key for key, value in environment.items() if value == "1"} <= allowed
    assert f"{SOCKET}:{SOCKET}:ro" in proxy["volumes"]


def test_the_proxy_is_hardened_and_unreachable_from_outside(compose):
    proxy = compose["services"]["docker-socket-proxy"]
    assert proxy["read_only"] is True
    assert proxy["tmpfs"] == ["/run"]
    assert proxy["cap_drop"] == ["ALL"]
    assert proxy["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in proxy
    assert proxy["networks"] == ["docker-api"]
    assert compose["networks"]["docker-api"] == {"name": "docker-api", "internal": True}


def test_healthmonitor_reads_the_docker_api_through_the_proxy(compose):
    healthmonitor = compose["services"]["healthmonitor"]
    assert f"DOCKER_HOST={PROXY_URL}" in healthmonitor["environment"]
    assert "docker-api" in healthmonitor["networks"]
    assert "docker-socket-proxy" in healthmonitor["depends_on"]


def test_the_agent_reads_the_docker_api_through_the_proxy(dungeon_tree):
    compose = yaml.safe_load(dungeon_tree["docker-compose.yaml"])
    agent = compose["services"]["telegraf-agent"]
    assert "docker-api" in agent["networks"]
    assert "docker-socket-proxy" in agent["depends_on"]
    # The image's own user: without the socket it needs no docker group
    assert agent["user"] == "telegraf"
    assert f'endpoint = "{PROXY_URL}"' in dungeon_tree["telegraf/telegraf.conf"]
    assert "docker.sock" not in dungeon_tree["telegraf/telegraf.conf"]


@pytest.mark.parametrize("component", ["healthmonitor", "monitoring_agent"])
def test_docker_api_clients_need_the_proxy(tmp_path, component):
    components = ["unbound", "outbound", component]
    result = run_components_check(tmp_path, components)
    assert result.returncode != 0
    assert f"{component} needs docker_socket_proxy" in result.stdout + result.stderr
    assert run_components_check(tmp_path, [*components, "docker_socket_proxy"]).returncode == 0
