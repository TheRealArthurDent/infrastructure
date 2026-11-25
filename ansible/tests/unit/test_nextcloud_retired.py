"""Nextcloud is retired: its data moved to private-cloud's OpenCloud on dungeon (2026-11-16), so no host gets a vhost,
a SAN, a DNS record or backup excludes for it any more."""
import yaml

from render import ANSIBLE_DIR, ROLES_DIR


def test_no_role_default_or_host_names_nextcloud():
    files = [
        ROLES_DIR / "reverse_proxy/defaults/main.yaml",
        ROLES_DIR / "certificate_check/defaults/main.yaml",
        *sorted((ANSIBLE_DIR / "inventory/host_vars").glob("*.yaml")),
    ]
    for path in files:
        data = yaml.safe_load(path.read_text()) or {}
        for key in ("reverse_proxy_vhosts", "certificate_check_subdomains"):
            assert "nextcloud" not in data.get(key, []), f"{path.name}: {key}"


def test_no_nextcloud_dns_record():
    config = (ANSIBLE_DIR / "configs/pihole.yaml").read_text()
    assert "nextcloud." not in config


def test_no_nextcloud_templates():
    assert not (ROLES_DIR / "reverse_proxy/templates/nginx/snippets/nextcloud.conf.j2").exists()
    assert "nextcloud" not in (ROLES_DIR / "reverse_proxy/templates/nginx/nginx.conf.j2").read_text()
    assert "nextcloud" not in (ROLES_DIR / "resticprofile/templates/resticprofile/config/excludes.j2").read_text()
