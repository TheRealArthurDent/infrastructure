"""nginx config of the stack role: redirects and dead config (raspberry#238)."""
import re

import pytest

from test_stack_templates import rendered_tree


@pytest.fixture(scope="module")
def nginx(tmp_path_factory):
    tree = rendered_tree(tmp_path_factory.mktemp("render"), "raspi")
    return {name: content for name, content in tree.items() if name.startswith("nginx/")}


def redirects(content):
    return re.findall(r"return\s+30[12]\s+(\S+);", content)


def test_redirects_have_no_empty_port(nginx):
    # https://$host:$request_uri and https://$host:/admin leave an empty port after the colon
    for name, content in nginx.items():
        for target in redirects(content):
            assert not re.search(r"\$host:(?!\$server_port)", target), f"{name}: {target}"


def test_http_redirects_to_https_with_the_same_uri(nginx):
    assert redirects(nginx["nginx/nginx.conf"]) == ["https://$host$request_uri"]


@pytest.mark.parametrize("snippet", ["pihole.conf", "pihole_outbound.conf"])
def test_pihole_root_redirects_to_the_dashboard(nginx, snippet):
    assert redirects(nginx[f"nginx/snippets/{snippet}"]) == ["https://$host/admin/"]


def test_no_dead_config(nginx):
    conf = nginx["nginx/nginx.conf"]
    # nginx doesn't publish port 53, Pi-hole does; nothing uses the PHP upstream
    assert "stream {" not in conf
    assert "php-handler" not in conf
