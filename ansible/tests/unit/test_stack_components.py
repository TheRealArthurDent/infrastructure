"""Per-host switching of the stack's components (#14): what leaving a component out does to the rendered stack."""
import json
import shutil
import subprocess

import pytest
import yaml

from render import ANSIBLE_DIR, rendered_tree

ROLES_DIR = ANSIBLE_DIR / "roles"
WITHOUT_PIHOLE = ["reverse_proxy", "portainer", "unbound", "outbound", "resticprofile", "healthmonitor"]
WITHOUT_REVERSE_PROXY = ["portainer", "unbound", "pihole", "outbound", "resticprofile", "healthmonitor"]


@pytest.fixture(scope="module")
def without_pihole(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("without-pihole")
    return rendered_tree(tmp_path, "raspi", {"stack_components": WITHOUT_PIHOLE}), tmp_path


def test_leaving_pihole_out_drops_its_service_and_server_block(without_pihole):
    tree, _ = without_pihole
    compose = yaml.safe_load(tree["docker-compose.yaml"])
    assert sorted(compose["services"]) == sorted(
        ["nginx", "portainer", "unbound", "outbound", "resticprofile", "healthmonitor"])
    conf = tree["nginx/nginx.conf"]
    assert "server_name         pihole." not in conf
    assert "server_name         outbound." in conf


def test_leaving_pihole_out_drops_its_restore_item(without_pihole):
    tree, _ = without_pihole
    items = json.loads(tree["restic-restore/stacks/infrastructure.json"])["items"]
    assert list(items) == ["portainer"]


def undefined_references(compose):
    """What the merged compose uses without defining it: networks, volumes, secrets and depends_on targets."""
    problems = []
    services = compose["services"]
    for name, service in services.items():
        networks = service.get("networks", [])
        for network in networks:
            if network not in compose.get("networks", {}):
                problems.append(f"{name}: network {network}")
        for dependency in service.get("depends_on", []):
            if dependency not in services:
                problems.append(f"{name}: depends_on {dependency}")
        for secret in service.get("secrets", []):
            if secret not in compose.get("secrets", {}):
                problems.append(f"{name}: secret {secret}")
        for volume in service.get("volumes", []):
            source = volume.split(":")[0]
            if not source.startswith("/") and source not in compose.get("volumes", {}):
                problems.append(f"{name}: volume {source}")
    return problems


def test_compose_without_pihole_defines_everything_it_uses(without_pihole):
    tree, _ = without_pihole
    assert undefined_references(yaml.safe_load(tree["docker-compose.yaml"])) == []


def test_compose_without_reverse_proxy_defines_everything_it_uses(tmp_path):
    # unbound and resticprofile attach to the nginx network, which the stack defines itself
    tree = rendered_tree(tmp_path, "raspi", {"stack_components": WITHOUT_REVERSE_PROXY})
    compose = yaml.safe_load(tree["docker-compose.yaml"])
    assert "nginx" not in compose["services"]
    assert "nginx" in compose["networks"]
    assert undefined_references(compose) == []


@pytest.mark.skipif(
    shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode,
    reason="docker compose is not available",
)
def test_compose_without_pihole_passes_docker_compose_config(without_pihole):
    tree, tmp_path = without_pihole
    compose_file = tmp_path / "docker-compose.yaml"
    compose_file.write_text(tree["docker-compose.yaml"])
    # The files the compose file references (env files, secrets) don't exist here: dummies in the same paths would
    # need root, so validate the file structure without them
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "config", "--quiet", "--no-env-resolution"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def run_components_check(tmp_path, components):
    playbook = tmp_path / "check.yaml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {"stack_components": components},
        "tasks": [{"ansible.builtin.import_tasks": str(ROLES_DIR / "stack" / "tasks" / "components.yaml")}],
    }]))
    return subprocess.run(
        ["ansible-playbook", str(playbook)], cwd=ANSIBLE_DIR, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("components", [["pihole"], ["outbound", "portainer"], ["pihole", "outbound"]])
def test_pihole_and_outbound_need_unbound(tmp_path, components):
    result = run_components_check(tmp_path, components)
    assert result.returncode != 0
    assert "pihole and outbound need unbound" in result.stdout + result.stderr


@pytest.mark.parametrize("components", [["unbound", "pihole", "outbound"], ["portainer"], []])
def test_components_without_a_missing_dependency_pass(tmp_path, components):
    result = run_components_check(tmp_path, components)
    assert result.returncode == 0, result.stdout + result.stderr


def test_stack_checks_the_components_first():
    tasks = yaml.safe_load((ROLES_DIR / "stack" / "tasks" / "main.yaml").read_text())
    first_import = next(t for t in tasks if "ansible.builtin.import_tasks" in t)
    assert first_import["ansible.builtin.import_tasks"] == "components.yaml"
    assert tasks.index(first_import) < min(i for i, t in enumerate(tasks) if "ansible.builtin.import_role" in t)


def secrets_tasks():
    pending = yaml.safe_load((ROLES_DIR / "secrets" / "tasks" / "main.yaml").read_text())
    while pending:
        task = pending.pop(0)
        for key in ("block", "rescue", "always"):
            pending[:0] = task.get(key, [])
        yield task


# The files the secrets role writes for a component -> the component whose host needs them
SECRET_FILES = {
    "/etc/docker/infrastructure/.env": "pihole",
    "/etc/docker/infrastructure/.outbound.env": "outbound",
    "/etc/docker/infrastructure/.healthmonitor.env": "healthmonitor",
    "/etc/docker/infrastructure/secrets": "resticprofile",
    "/etc/docker/infrastructure/secrets/restic_passphrase": "resticprofile",
    "/etc/docker/infrastructure/resticprofile": "resticprofile",
    "/etc/docker/infrastructure/resticprofile/.ssh": "resticprofile",
    "/etc/docker/infrastructure/resticprofile/.ssh/id_ed25519": "resticprofile",
    "/etc/docker/infrastructure/resticprofile/.ssh/id_ed25519.pub": "resticprofile",
}


def test_secrets_are_written_only_for_enabled_components():
    found = {}
    for task in secrets_tasks():
        for module in ("ansible.builtin.copy", "ansible.builtin.file"):
            if module in task:
                target = task[module].get("dest") or task[module]["path"]
                if target in SECRET_FILES:
                    found[target] = task
    assert sorted(found) == sorted(SECRET_FILES)
    for target, component in SECRET_FILES.items():
        assert found[target].get("when") == f"'{component}' in stack_components", target


def test_old_restic_init_script_and_generated_key_are_gone():
    # The key pair comes from KeePass and dungeon authorises it (backup_target_clients), so nothing is generated or
    # copied to the backup host any more. A script left from an earlier run is removed on every host.
    tasks = list(secrets_tasks())
    assert not [t for t in tasks if "community.crypto.openssh_keypair" in t]
    removal = [t for t in tasks if t.get("ansible.builtin.file", {}).get("path") == "/etc/docker/infrastructure/init-restic.sh"]
    assert len(removal) == 1
    assert removal[0]["ansible.builtin.file"]["state"] == "absent"
