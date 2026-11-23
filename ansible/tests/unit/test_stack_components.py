"""Per-host switching of the stack's components (#14): what leaving a component out does to the rendered stack."""
import json
import re
import shutil
import subprocess

import pytest
import yaml

from render import ANSIBLE_DIR, rendered_tree

ROLES_DIR = ANSIBLE_DIR / "roles"
WITHOUT_PIHOLE = ["reverse_proxy", "portainer", "unbound", "outbound", "resticprofile", "healthmonitor",
                  "docker_socket_proxy"]
WITHOUT_REVERSE_PROXY = ["portainer", "unbound", "pihole", "outbound", "resticprofile", "healthmonitor",
                         "docker_socket_proxy"]


@pytest.fixture(scope="module")
def without_pihole(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("without-pihole")
    return rendered_tree(tmp_path, "raspi", {"stack_components": WITHOUT_PIHOLE}), tmp_path


def test_leaving_pihole_out_drops_its_service_and_server_block(without_pihole):
    tree, _ = without_pihole
    compose = yaml.safe_load(tree["docker-compose.yaml"])
    assert sorted(compose["services"]) == sorted(
        ["nginx", "portainer", "unbound", "outbound", "resticprofile", "healthmonitor", "docker-socket-proxy"])
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
            if isinstance(volume, dict):  # the long syntax
                source = volume["source"] if volume.get("type") == "volume" else "/"
            else:
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


# The files the secrets role writes for a component -> the component whose host needs them (the ssh key pair only
# with an sftp repository)
SFTP = "'resticprofile' in stack_components and resticprofile_repository_is_sftp"
# The offsite copy's passphrase and key pair (#60), and the .ssh directory for either
OFFSITE = "'resticprofile' in stack_components and resticprofile_offsite_enabled | bool"
SSH_DIR = ["'resticprofile' in stack_components",
           "resticprofile_repository_is_sftp | bool or resticprofile_offsite_enabled | bool"]
# The directory of the Compose secrets, for every component that has one
SECRETS_DIR = "['resticprofile', 'monitoring', 'monitoring_agent'] | intersect(stack_components) | length > 0"
SECRET_FILES = {
    "/etc/docker/infrastructure/.env": "pihole",
    "/etc/docker/infrastructure/.outbound.env": "outbound",
    "/etc/docker/infrastructure/.healthmonitor.env": "healthmonitor",
    "/etc/docker/infrastructure/secrets": SECRETS_DIR,
    "/etc/docker/infrastructure/secrets/grafana_admin_password": "monitoring",
    "/etc/docker/infrastructure/secrets/metrics_writer_password": "monitoring_agent",
    "/etc/docker/infrastructure/nginx": "monitoring",
    "/etc/docker/infrastructure/nginx/metrics.htpasswd": "monitoring",
    "/etc/docker/infrastructure/secrets/restic_passphrase": "resticprofile",
    "/etc/docker/infrastructure/resticprofile": "resticprofile",
    "/etc/docker/infrastructure/resticprofile/.ssh": SSH_DIR,
    "/etc/docker/infrastructure/secrets/restic_offsite_passphrase": OFFSITE,
    "/etc/docker/infrastructure/resticprofile/.ssh/id_offsite": OFFSITE,
    "/etc/docker/infrastructure/resticprofile/.ssh/id_offsite.pub": OFFSITE,
    "/etc/docker/infrastructure/resticprofile/.ssh/id_ed25519": SFTP,
    "/etc/docker/infrastructure/resticprofile/.ssh/id_ed25519.pub": SFTP,
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
        condition = (component if component in (SFTP, SECRETS_DIR, OFFSITE, SSH_DIR)
                     else f"'{component}' in stack_components")
        assert found[target].get("when") == condition, target


def test_old_restic_init_script_and_generated_key_are_gone():
    # The key pair comes from KeePass and dungeon authorises it (backup_target_clients), so nothing is generated or
    # copied to the backup host any more. A script left from an earlier run is removed on every host.
    tasks = list(secrets_tasks())
    assert not [t for t in tasks if "community.crypto.openssh_keypair" in t]
    removal = [t for t in tasks if t.get("ansible.builtin.file", {}).get("path") == "/etc/docker/infrastructure/init-restic.sh"]
    assert len(removal) == 1
    assert removal[0]["ansible.builtin.file"]["state"] == "absent"


def test_dungeon_renders_only_its_vhosts(tmp_path):
    tree = rendered_tree(tmp_path, "dungeon")
    names = set(re.findall(r"server_name\s+(\S+) ", tree["nginx/nginx.conf"]))
    assert names == {"dungeon", "portainer.dungeon", "outbound.dungeon", "opencloud.dungeon", "paperless.dungeon",
                     "grafana.dungeon", "metrics.dungeon"}
    assert "listen              8443" not in tree["nginx/nginx.conf"]
    assert "location /health" in tree["nginx/nginx.conf"]


def test_vhosts_not_in_the_list_are_not_rendered(tmp_path):
    tree = rendered_tree(tmp_path, "raspi", {"reverse_proxy_vhosts": ["grafana"]})
    names = set(re.findall(r"server_name\s+(\S+) ", tree["nginx/nginx.conf"]))
    assert names == {"raspi", "grafana.raspi"}


def test_vhost_needs_its_component_as_well(tmp_path):
    components = [c for c in WITHOUT_PIHOLE if c != "outbound"]
    tree = rendered_tree(tmp_path, "raspi", {"stack_components": components})
    assert "outbound." not in tree["nginx/nginx.conf"]


def test_healthmonitor_needs_outbound(tmp_path):
    # healthmonitor's fragment sets dns: to the outbound Pi-hole's address
    result = run_components_check(tmp_path, ["healthmonitor"])
    assert result.returncode != 0
    assert "healthmonitor needs outbound" in result.stdout + result.stderr
    components = ["unbound", "outbound", "healthmonitor", "docker_socket_proxy"]
    assert run_components_check(tmp_path, components).returncode == 0


def test_opencloud_vhost_proxies_to_the_container(tmp_path):
    """private-cloud's opencloud container listens on 9200 in the network siz, without TLS (its README)."""
    tree = rendered_tree(tmp_path, "dungeon")
    assert "include             snippets/opencloud.conf;" in tree["nginx/nginx.conf"]
    snippet = tree["nginx/snippets/opencloud.conf"]
    assert "set $opencloud opencloud:9200;" in snippet
    assert "proxy_pass http://$opencloud;" in snippet
    assert "proxy_set_header X-Forwarded-Proto https;" in snippet
    # Uploads of any size, websockets for the web UI
    assert "client_max_body_size 0;" in snippet
    assert 'proxy_set_header Connection "upgrade";' in snippet


def test_raspi_has_no_opencloud_vhost(tmp_path):
    tree = rendered_tree(tmp_path, "raspi")
    assert "opencloud." not in tree["nginx/nginx.conf"]


def test_paperless_vhost_proxies_to_the_webserver(tmp_path):
    """private-cloud's paperless-webserver listens on 8000 in the network siz, without TLS (its README)."""
    tree = rendered_tree(tmp_path, "dungeon")
    assert "include             snippets/paperless.conf;" in tree["nginx/nginx.conf"]
    snippet = tree["nginx/snippets/paperless.conf"]
    assert "set $paperless paperless-webserver:8000;" in snippet
    assert "proxy_pass http://$paperless;" in snippet
    assert "proxy_set_header X-Forwarded-Proto https;" in snippet
    # Scans of many pages, websockets for the status updates
    assert "client_max_body_size 100M;" in snippet
    assert 'proxy_set_header Connection "upgrade";' in snippet


def test_raspi_has_no_paperless_vhost(tmp_path):
    tree = rendered_tree(tmp_path, "raspi")
    assert "paperless." not in tree["nginx/nginx.conf"]


def test_testbench_keeps_the_default_vhosts_and_sans_plus_private_cloud():
    """testbench lists them in full to add opencloud and paperless; a vhost added to a default must reach it too."""
    import yaml
    from pathlib import Path

    ansible = Path(__file__).resolve().parents[2]
    testbench = yaml.safe_load((ansible / "inventory/host_vars/testbench.yaml").read_text())
    for role, var in (("reverse_proxy", "reverse_proxy_vhosts"), ("certificate_check", "certificate_check_subdomains")):
        default = yaml.safe_load((ansible / f"roles/{role}/defaults/main.yaml").read_text())[var]
        assert sorted(testbench[var]) == sorted(default + ["opencloud", "paperless"]), var


@pytest.mark.parametrize("host", ["dungeon", "testbench"])
def test_containers_reach_opencloud_through_nginx_without_dns(tmp_path, host):
    """OpenCloud fetches its own OIDC discovery from https://opencloud.<host>; Docker resolves it to nginx in siz."""
    compose = yaml.safe_load(rendered_tree(tmp_path, host)["docker-compose.yaml"])
    siz = compose["services"]["nginx"]["networks"]["siz"]
    assert f"opencloud.{host}" in siz["aliases"]
    assert "nginx" in compose["services"]["nginx"]["networks"]


def test_raspi_nginx_gets_no_network_alias(tmp_path):
    compose = yaml.safe_load(rendered_tree(tmp_path, "raspi")["docker-compose.yaml"])
    assert compose["services"]["nginx"]["networks"] == {"nginx": None, "siz": None}


@pytest.fixture(scope="module")
def dungeon_compose(tmp_path_factory):
    tree = rendered_tree(tmp_path_factory.mktemp("dungeon"), "dungeon")
    return yaml.safe_load(tree["docker-compose.yaml"])


def test_dungeon_runs_the_monitoring_component(dungeon_compose):
    """The central part of the monitoring (#39): VictoriaMetrics and a Grafana behind nginx."""
    services = dungeon_compose["services"]
    assert {"victoriametrics", "grafana"} <= set(services)
    assert undefined_references(dungeon_compose) == []


def test_grafana_reads_its_admin_password_from_a_compose_secret(dungeon_compose):
    grafana = dungeon_compose["services"]["grafana"]
    assert grafana["secrets"] == ["grafana_admin_password"]
    assert dungeon_compose["secrets"]["grafana_admin_password"] == {
        "file": "/etc/docker/infrastructure/secrets/grafana_admin_password"}
    environment = grafana["environment"]
    assert environment["GF_SECURITY_ADMIN_PASSWORD__FILE"] == "/run/secrets/grafana_admin_password"
    # No password as a plain variable, and no env file
    assert "GF_SECURITY_ADMIN_PASSWORD" not in environment
    assert "env_file" not in grafana
    assert environment["GF_SERVER_ROOT_URL"] == "https://grafana.dungeon"


def test_grafana_is_reachable_through_nginx_only(dungeon_compose):
    grafana = dungeon_compose["services"]["grafana"]
    assert grafana["networks"] == ["siz"]
    assert "ports" not in grafana
    assert "ports" not in dungeon_compose["services"]["victoriametrics"]


def test_victoriametrics_keeps_twelve_months_in_a_named_volume(dungeon_compose):
    victoriametrics = dungeon_compose["services"]["victoriametrics"]
    assert "-retentionPeriod=12M" in victoriametrics["command"]
    assert "-storageDataPath=/storage" in victoriametrics["command"]
    assert victoriametrics["volumes"] == ["victoriametrics_data:/storage"]
    assert dungeon_compose["volumes"]["victoriametrics_data"] == {"name": "victoriametrics_data"}


def test_monitoring_has_memory_limits(dungeon_compose):
    for name in ("victoriametrics", "grafana"):
        assert dungeon_compose["services"][name]["deploy"]["resources"]["limits"]["memory"], name


def test_raspi_runs_no_monitoring(tmp_path):
    tree = rendered_tree(tmp_path, "raspi")
    services = yaml.safe_load(tree["docker-compose.yaml"])["services"]
    assert not {"victoriametrics", "grafana", "telegraf-agent"} & set(services)
    assert "telegraf/telegraf.conf" not in tree


@pytest.fixture(scope="module")
def dungeon_tree(tmp_path_factory):
    return rendered_tree(tmp_path_factory.mktemp("dungeon-agent"), "dungeon")


def test_dungeon_runs_the_monitoring_agent(dungeon_compose):
    """The agent part of the monitoring (#39): Telegraf collects the host's and the containers' metrics."""
    agent = dungeon_compose["services"]["telegraf-agent"]
    assert agent["image"].startswith("telegraf:")
    assert agent["networks"] == ["siz", "docker-api"]
    assert "ports" not in agent
    assert agent["deploy"]["resources"]["limits"]["memory"]
    assert undefined_references(dungeon_compose) == []


def test_the_agent_reads_the_host_read_only(dungeon_compose):
    # The Docker API comes through the proxy (test_docker_socket_proxy.py)
    agent = dungeon_compose["services"]["telegraf-agent"]
    assert {"type": "bind", "source": "/", "target": "/hostfs", "read_only": True,
            "bind": {"propagation": "rslave"}} in agent["volumes"]
    assert "/etc/docker/infrastructure/telegraf:/etc/telegraf:ro" in agent["volumes"]
    environment = agent["environment"]
    assert environment["HOST_PROC"] == "/hostfs/proc"
    assert environment["HOST_SYS"] == "/hostfs/sys"
    assert environment["HOST_MOUNT_PREFIX"] == "/hostfs"
    # Reloads its config when the playbook changes it
    assert agent["command"] == ["telegraf", "--watch-config", "poll"]


def test_the_agent_writes_as_its_host(dungeon_tree):
    """Where it writes to: test_monitoring_write_path.py."""
    conf = dungeon_tree["telegraf/telegraf.conf"]
    assert '[[outputs.influxdb]]' in conf
    assert "skip_database_creation = true" in conf
    assert 'hostname = "dungeon"' in conf


def test_the_agent_collects_host_container_and_disk_metrics(dungeon_tree):
    conf = dungeon_tree["telegraf/telegraf.conf"]
    for plugin in ("cpu", "mem", "swap", "disk", "diskio", "system", "temp", "net", "docker"):
        assert f"[[inputs.{plugin}]]" in conf, plugin
    # The disk input covers every mount, the backup SSD at /media/backup too; pseudo file systems are left out
    assert "mount_points" not in conf
    assert '"overlay"' in conf and '"tmpfs"' in conf


def test_the_agent_keeps_the_container_labels_out(dungeon_tree):
    """Compose puts a dozen labels on each container; as tags they would multiply the series."""
    conf = dungeon_tree["telegraf/telegraf.conf"]
    # An empty docker_label_include would keep them all
    assert 'docker_label_exclude = ["*"]' in conf
    assert "docker_label_include" not in conf
    assert "perdevice_include = []" in conf


def test_the_agent_reads_the_backup_metrics_of_resticprofile(dungeon_tree):
    """resticprofile writes Prometheus text files (backup.prom, offsite_*.prom); the agent reads them through /hostfs."""
    conf = dungeon_tree["telegraf/telegraf.conf"]
    assert "[[inputs.file]]" in conf
    assert 'files = ["/hostfs/var/lib/resticprofile/metrics/*.prom"]' in conf
    assert 'data_format = "prometheus"' in conf
    # One measurement resticprofile with the metric names as fields: VictoriaMetrics stores
    # resticprofile_<field>, i.e. the metric names resticprofile wrote, once the processor took their prefix off
    assert "prometheus_metric_version = 2" in conf
    assert 'name_override = "resticprofile"' in conf
    assert '[[processors.strings.trim_prefix]]' in conf
    assert 'prefix = "resticprofile_"' in conf
    assert 'fieldexclude = ["resticprofile_build_info", "restic_build_info"]' in conf
