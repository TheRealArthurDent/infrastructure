"""Pi-hole answers DNS on every interface, whatever order Docker gives its networks.

Both Pi-holes sit on two Docker networks. With listening mode SINGLE, Pi-hole only answers on one interface. After a
reboot of raspi, published port 53 led to the other one, so all DNS requests from the LAN timed out silently.
"""
from pathlib import Path

import pytest
import yaml

REPO_DIR = Path(__file__).parents[3]


@pytest.mark.parametrize("service", ["pihole", "outbound"])
def test_pihole_listens_on_all_interfaces(service):
    compose = yaml.safe_load((REPO_DIR / "docker-compose.yaml").read_text())

    assert compose["services"][service]["environment"].get("FTLCONF_dns_listeningMode") == "ALL"
