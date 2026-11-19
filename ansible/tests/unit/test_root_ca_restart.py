"""certificate_check restarts the containers that trust the root CA when it replaces the root CA.

Apps mount /etc/ssl/certs/rootCA.pem as a single-file bind mount. Ansible replaces the file with a new inode, so the
container keeps seeing the old certificate, and Go services read their trust store only at start anyway. After the
root CA renewal (2026-11-15), private-cloud's OpenCloud failed every upload with 500 until a manual restart. Apps opt in
with a label; the restart itself needs a Docker daemon, which Molecule doesn't have, so these tests check the wiring.
"""
import yaml

from render import ANSIBLE_DIR, ROLES_DIR

ROLE = ROLES_DIR / "certificate_check"
HANDLER = "Restart containers that trust the root CA"
LABEL = "root-ca.restart-on-change=true"


def load(path):
    return yaml.safe_load(path.read_text())


def handlers():
    return [h for h in load(ROLE / "handlers/main.yaml") if h.get("listen") == HANDLER]


def test_replacing_the_root_ca_notifies_the_restart():
    task = next(t for t in load(ROLE / "tasks/main.yaml") if t.get("name") == "Ensure root CA certificate is present")
    assert HANDLER in task.get("notify", [])


def test_the_restart_finds_the_running_containers_by_label():
    find = handlers()[0]
    info = find["community.docker.docker_host_info"]
    assert info["containers"] is True
    assert info.get("containers_all", False) is False
    assert info["containers_filters"]["label"] == "{{ certificate_check_restart_label }}"
    assert load(ROLE / "defaults/main.yaml")["certificate_check_restart_label"] == LABEL


def test_the_restart_restarts_each_found_container():
    restart = handlers()[1]
    assert restart["ansible.builtin.command"]["argv"][:2] == ["docker", "restart"]
    assert restart["loop"] == "{{ certificate_check_ca_containers.containers }}"


def test_the_restart_can_be_switched_off_where_no_docker_daemon_runs():
    assert load(ROLE / "defaults/main.yaml")["certificate_check_restart_containers"] is True
    for handler in handlers():
        assert "certificate_check_restart_containers | bool" in handler["when"]
    converge = load(ANSIBLE_DIR / "molecule/default/converge.yaml")
    assert converge[0]["vars"]["certificate_check_restart_containers"] is False
