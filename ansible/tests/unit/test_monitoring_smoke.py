"""Smoke test of the monitoring component (#39) with the real images: what Molecule can't do without a Docker daemon.

Starts VictoriaMetrics, Grafana and the Telegraf agent as rendered for dungeon, with a throwaway password as the
Compose secret, and checks that VictoriaMetrics stores and returns a metric, Grafana takes the password from the
secret unchanged and reaches VictoriaMetrics, and the agent delivers the host's and the containers' metrics. Runs
where Docker runs (CI), skipped elsewhere.
"""
import base64
import os
import shutil
import subprocess
import time
import uuid

import pytest
import yaml

from render import rendered_tree

# Characters Compose would interpolate or a .env file would need quoted; ${...} is ruled out by the secrets role
PASSWORD = 'smoke $x {{ z }} "q" \'s\' \\b #c'


def docker_available():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not docker_available(), reason="no Docker daemon")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("monitoring-smoke")
    # The agent's interval shortened, so its first metrics arrive within the test
    tree = rendered_tree(tmp_path, "dungeon", {"monitoring_agent_interval": "5s"})
    compose = yaml.safe_load(tree["docker-compose.yaml"])
    project = f"smoke-{uuid.uuid4().hex[:8]}"
    services = {name: dict(compose["services"][name]) for name in ("victoriametrics", "grafana", "telegraf-agent")}
    for service in services.values():
        # The project's default network and generated names, so nothing collides with a running stack
        service.pop("networks")
        service.pop("container_name")
        service.pop("hostname")
    grafana_data = tmp_path / "grafana-data"
    grafana_data.mkdir()
    grafana_data.chmod(0o777)
    services["grafana"]["volumes"] = [f"{grafana_data}:/var/lib/grafana"]
    telegraf_dir = tmp_path / "telegraf"
    telegraf_dir.mkdir()
    (telegraf_dir / "telegraf.conf").write_text(tree["telegraf/telegraf.conf"])
    agent = services["telegraf-agent"]
    agent["volumes"] = [f"{telegraf_dir}:/etc/telegraf:ro" if str(v).endswith(":/etc/telegraf:ro") else v
                        for v in agent["volumes"]]
    # The group of this machine's Docker socket instead of the render's placeholder
    agent["user"] = f"telegraf:{os.stat('/var/run/docker.sock').st_gid}"
    secret = tmp_path / "grafana_admin_password"
    secret.write_text(PASSWORD)
    # On the host the file belongs to Grafana's user 472 (secrets role); here the runner's user owns it
    secret.chmod(0o644)
    smoke = {
        "services": services,
        "volumes": {"victoriametrics_data": {"name": f"{project}_victoriametrics_data"}},
        "secrets": {"grafana_admin_password": {"file": str(secret)}},
    }
    compose_file = tmp_path / "docker-compose.yaml"
    compose_file.write_text(yaml.safe_dump(smoke))
    base = ["docker", "compose", "-p", project, "-f", str(compose_file)]
    try:
        up = subprocess.run([*base, "up", "-d", "--wait", "--wait-timeout", "300"], capture_output=True, text=True,
                            timeout=600)
        logs = subprocess.run([*base, "logs"], capture_output=True, text=True).stdout
        assert up.returncode == 0, up.stderr + logs
        yield base
    finally:
        subprocess.run([*base, "down", "-v"], capture_output=True, timeout=120)


def exec_in(base, service, *command):
    return subprocess.run([*base, "exec", "-T", service, *command], capture_output=True, text=True, timeout=60)


def test_victoriametrics_stores_and_returns_a_metric(stack):
    write = exec_in(stack, "victoriametrics", "wget", "-qO-", "--post-data", "smoke,host=test value=42",
                    "http://127.0.0.1:8428/write")
    assert write.returncode == 0, write.stderr
    exec_in(stack, "victoriametrics", "wget", "-qO-", "http://127.0.0.1:8428/internal/force_flush")
    for _ in range(30):
        query = exec_in(stack, "victoriametrics", "wget", "-qO-",
                        "http://127.0.0.1:8428/api/v1/query?query=smoke_value")
        if '"42"' in query.stdout:
            break
        time.sleep(1)
    assert '"host":"test"' in query.stdout and '"42"' in query.stdout, query.stdout + query.stderr


def test_grafana_takes_the_admin_password_from_the_secret(stack):
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    user = exec_in(stack, "grafana", "wget", "-qO-", "--header", f"Authorization: Basic {token}",
                   "http://localhost:3000/api/user")
    assert user.returncode == 0, user.stderr
    assert '"login":"admin"' in user.stdout


def test_grafana_rejects_a_wrong_password(stack):
    token = base64.b64encode(b"admin:admin").decode()
    user = exec_in(stack, "grafana", "wget", "-qO-", "--header", f"Authorization: Basic {token}",
                   "http://localhost:3000/api/user")
    assert user.returncode != 0


def test_grafana_reaches_victoriametrics(stack):
    health = exec_in(stack, "grafana", "wget", "-qO-", "http://victoriametrics:8428/health")
    assert health.stdout.strip() == "OK", health.stdout + health.stderr


def query_until(base, promql, expected, attempts=60):
    """The VictoriaMetrics query result once expected is in it, or the last one."""
    for _ in range(attempts):
        exec_in(base, "victoriametrics", "wget", "-qO-", "http://127.0.0.1:8428/internal/force_flush")
        query = exec_in(base, "victoriametrics", "wget", "-qO-",
                        f"http://127.0.0.1:8428/api/v1/query?query={promql}")
        if expected in query.stdout:
            break
        time.sleep(2)
    return query.stdout + query.stderr


def test_the_agent_delivers_host_metrics_tagged_with_its_host(stack):
    assert '"host":"dungeon"' in query_until(stack, "cpu_usage_idle", '"host":"dungeon"')


def test_the_agent_reports_the_host_disks_by_their_host_path(stack):
    """HOST_MOUNT_PREFIX strips /hostfs: the root file system shows up as /, as on the host."""
    assert '"path":"/"' in query_until(stack, "disk_used_percent", '"path":"/"')


def test_the_agent_delivers_container_metrics_without_labels(stack):
    result = query_until(stack, "docker_container_mem_usage", "victoriametrics")
    assert "victoriametrics" in result
    assert "com.docker.compose" not in result
    assert "org.opencontainers" not in result
