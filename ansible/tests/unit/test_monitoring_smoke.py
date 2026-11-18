"""Smoke test of the monitoring component (#39) with the real images: what Molecule can't do without a Docker daemon.

Starts VictoriaMetrics, Grafana and the Telegraf agent as rendered for dungeon, with a throwaway password as the
Compose secret, and checks that VictoriaMetrics stores and returns a metric, Grafana takes the password from the
secret unchanged and reaches VictoriaMetrics, and the agent delivers the host's and the containers' metrics. Runs
where Docker runs (CI), skipped elsewhere.
"""
import base64
import time

import pytest

from monitoring_stack import docker_available, exec_in, monitoring_stack, query_until

# Characters Compose would interpolate or a .env file would need quoted; ${...} is ruled out by the secrets role
PASSWORD = 'smoke $x {{ z }} "q" \'s\' \\b #c'

pytestmark = pytest.mark.skipif(not docker_available(), reason="no Docker daemon")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with monitoring_stack(tmp_path_factory.mktemp("monitoring-smoke"), PASSWORD) as base:
        yield base


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
