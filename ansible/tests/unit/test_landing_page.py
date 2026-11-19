"""The landing page at https://<host>/ (#4): Homepage with a tile per web UI of the host and the host's resources."""
import re

import pytest
import yaml

from render import ROLES_DIR, rendered_tree
from test_stack_components import WITHOUT_PIHOLE, run_components_check, undefined_references

# What Homepage copies from its skeleton when a file is missing; the config is mounted read-only, so all must exist
HOMEPAGE_FILES = ["bookmarks.yaml", "custom.css", "custom.js", "docker.yaml", "kubernetes.yaml", "proxmox.yaml",
                  "services.yaml", "settings.yaml", "widgets.yaml"]
# vhosts that get no tile: Nextcloud doesn't run any more and is going away
WITHOUT_TILE = {"nextcloud"}


@pytest.fixture(scope="module")
def raspi(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("landing-raspi"), "raspi")


@pytest.fixture(scope="module")
def dungeon(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("landing-dungeon"), "dungeon")


@pytest.fixture(scope="module")
def testbench(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("landing-testbench"), "testbench")


def tiles(tree):
    """Group -> {tile name -> tile} of services.yaml."""
    groups = yaml.safe_load(tree["homepage/services.yaml"])
    result = {}
    for group in groups:
        (name, entries), = group.items()
        result[name] = {tile_name: tile for entry in entries for tile_name, tile in entry.items()}
    return result


def all_tiles(tree):
    return {name: tile for group in tiles(tree).values() for name, tile in group.items()}


def server_block(conf, server_name):
    """The server block listening on 443 whose server_name starts with server_name."""
    for block in re.findall(r"    server \{\n(.*?)\n    \}", conf, flags=re.S):
        if re.search(rf"server_name\s+{re.escape(server_name)} ", block) and "listen              443" in block:
            return block
    raise LookupError(server_name)


def test_homepage_runs_unprivileged_in_the_network_siz(raspi):
    compose = yaml.safe_load(raspi["docker-compose.yaml"])
    homepage = compose["services"]["homepage"]
    assert homepage["image"].startswith("ghcr.io/gethomepage/homepage:v")
    assert homepage["networks"] == ["siz"]
    assert "ports" not in homepage
    assert homepage["user"] == "1000:1000"
    assert homepage["read_only"] is True
    assert homepage["cap_drop"] == ["ALL"]
    assert homepage["security_opt"] == ["no-new-privileges:true"]
    assert homepage["deploy"]["resources"]["limits"]["memory"]
    assert "/etc/docker/infrastructure/homepage:/app/config:ro" in homepage["volumes"]
    assert undefined_references(compose) == []


def test_homepage_answers_only_to_the_host_name_and_logs_to_stdout(raspi, dungeon):
    for host, tree in (("raspi", raspi), ("dungeon", dungeon)):
        environment = yaml.safe_load(tree["docker-compose.yaml"])["services"]["homepage"]["environment"]
        assert environment["HOMEPAGE_ALLOWED_HOSTS"] == host
        # The config directory is read-only, no log file there
        assert environment["LOG_TARGETS"] == "stdout"


def test_homepage_gets_every_config_file(raspi):
    assert sorted(name.removeprefix("homepage/") for name in raspi if name.startswith("homepage/")) == HOMEPAGE_FILES


def test_the_host_name_leads_to_the_landing_page(raspi):
    block = server_block(raspi["nginx/nginx.conf"], "raspi")
    assert "include             snippets/landing-page.conf;" in block
    assert "root " not in block
    snippet = raspi["nginx/snippets/landing-page.conf"]
    assert "set $landing_page_host homepage:3000;" in snippet
    assert "proxy_pass http://$landing_page_host;" in snippet
    # Homepage checks the host name (HOMEPAGE_ALLOWED_HOSTS)
    assert "proxy_set_header Host $host;" in snippet


def test_without_the_landing_page_the_host_name_keeps_the_nginx_page(tmp_path):
    tree = rendered_tree(tmp_path, "raspi", {"stack_components": WITHOUT_PIHOLE})
    block = server_block(tree["nginx/nginx.conf"], "raspi")
    assert "landing-page" not in block
    assert "root                /usr/share/nginx/html;" in block
    assert "homepage" not in yaml.safe_load(tree["docker-compose.yaml"])["services"]
    assert not [name for name in tree if name.startswith("homepage/")]


def test_the_landing_page_needs_the_reverse_proxy(tmp_path):
    result = run_components_check(tmp_path, ["landing_page"])
    assert result.returncode != 0
    assert "landing_page needs reverse_proxy" in result.stdout + result.stderr
    assert run_components_check(tmp_path, ["reverse_proxy", "landing_page"]).returncode == 0


def test_every_host_runs_the_landing_page(raspi, dungeon, testbench):
    for tree in (raspi, dungeon, testbench):
        assert "homepage" in yaml.safe_load(tree["docker-compose.yaml"])["services"]


def test_raspi_shows_its_tiles_by_group(raspi):
    assert {group: sorted(entries) for group, entries in tiles(raspi).items()} == {
        "Infrastructure": ["Pi-hole", "Pi-hole outbound", "Portainer"],
        "Monitoring": ["Grafana"],
        "Smart home": ["Lovelace", "Node-RED", "Phoscon", "Phoscon VNC", "ioBroker"],
    }


def test_dungeon_shows_only_its_own_web_uis(dungeon):
    assert {group: sorted(entries) for group, entries in tiles(dungeon).items()} == {
        "Infrastructure": ["Pi-hole outbound", "Portainer"],
        "Monitoring": ["Grafana"],
        "Cloud": ["OpenCloud", "Paperless"],
    }


def test_a_vhost_without_its_component_gets_no_tile(tmp_path):
    tree = rendered_tree(tmp_path, "raspi", {"stack_components": [*WITHOUT_PIHOLE, "landing_page"]})
    assert "Pi-hole" not in all_tiles(tree)
    assert "Pi-hole outbound" in all_tiles(tree)


def test_tiles_link_to_the_vhosts_of_their_host(dungeon):
    assert all_tiles(dungeon)["Portainer"]["href"] == "https://portainer.dungeon"
    assert all_tiles(dungeon)["OpenCloud"]["href"] == "https://opencloud.dungeon"
    for tile in all_tiles(dungeon).values():
        assert tile["icon"]
        assert tile["description"]


def test_every_vhost_has_a_tile_or_is_left_out_on_purpose():
    template = (ROLES_DIR / "reverse_proxy" / "templates" / "nginx" / "nginx.conf.j2").read_text()
    vhosts = set(re.findall(r"'([\w-]+)' in reverse_proxy_vhosts", template))
    services = yaml.safe_load((ROLES_DIR / "landing_page" / "defaults" / "main.yaml").read_text())[
        "landing_page_services"]
    assert set(services) == vhosts - WITHOUT_TILE


def test_status_dots_ask_the_upstream_nginx_proxies_to(testbench):
    """The container checks the service inside the network siz: no DNS, no certificate, no detour through nginx."""
    conf = testbench["nginx/nginx.conf"]
    checked = 0
    for name, tile in all_tiles(testbench).items():
        if "siteMonitor" not in tile:
            continue
        vhost = tile["href"].removeprefix("https://").removesuffix(".testbench")
        snippet = re.search(r"include\s+snippets/(?!ssl)(\S+);", server_block(conf, f"{vhost}.testbench"))[1]
        upstream = re.match(r"http://([\w-]+:\d+)", tile["siteMonitor"])[1]
        assert f"{upstream};" in testbench[f"nginx/snippets/{snippet}"], name
        checked += 1
    assert checked >= 8


def test_the_page_shows_the_resources_of_its_host(raspi):
    widgets = yaml.safe_load(raspi["homepage/widgets.yaml"])
    resources = next(widget["resources"] for widget in widgets if "resources" in widget)
    assert resources["cpu"] is True
    assert resources["memory"] is True
    assert resources["disk"] == "/"
    assert resources["cputemp"] is True
    assert resources["uptime"] is True


def test_the_page_is_named_after_its_host_and_calls_nobody(raspi):
    settings = yaml.safe_load(raspi["homepage/settings.yaml"])
    assert settings["title"] == "raspi"
    # In the network siz, without internet; no update check, no version in the footer
    assert settings["disableUpdateCheck"] is True
    assert settings["hideVersion"] is True
    # Only the groups the host has, in a fixed order, the other hosts last
    assert list(settings["layout"]) == ["Infrastructure", "Monitoring", "Smart home", "Hosts"]


def test_the_page_links_the_other_hosts(raspi):
    bookmarks = yaml.safe_load(raspi["homepage/bookmarks.yaml"])
    (group, entries), = bookmarks[0].items()
    assert group == "Hosts"
    links = {name: entry[0]["href"] for item in entries for name, entry in item.items()}
    assert links == {"dungeon": "https://dungeon", "testbench": "https://testbench"}


def test_the_config_files_are_valid_yaml(raspi, dungeon):
    for tree in (raspi, dungeon):
        for name in HOMEPAGE_FILES:
            if name.endswith(".yaml"):
                yaml.safe_load(tree[f"homepage/{name}"])
