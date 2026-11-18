"""The monitoring stack of dungeon with the real images, for the tests that need Docker (CI).

VictoriaMetrics, Grafana and the Telegraf agent as the stack role renders them for dungeon, in a Compose project of
their own: the project's default network instead of siz, generated container names, Grafana's data and the agent's
config in temporary directories and a throwaway admin password as the Compose secret.
"""
import contextlib
import os
import shutil
import subprocess
import time
import uuid
from urllib.parse import urlencode

import yaml

from render import rendered_tree

SERVICES = ("victoriametrics", "grafana", "telegraf-agent")


def docker_available():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@contextlib.contextmanager
def monitoring_stack(tmp_path, password, publish_grafana=False):
    """Yields the docker compose command of the running project; publish_grafana maps Grafana to a local port."""
    # The agent's interval shortened, so its first metrics arrive within the test
    tree = rendered_tree(tmp_path, "dungeon", {"monitoring_agent_interval": "5s"})
    compose = yaml.safe_load(tree["docker-compose.yaml"])
    project = f"smoke-{uuid.uuid4().hex[:8]}"
    services = {name: dict(compose["services"][name]) for name in SERVICES}
    for service in services.values():
        # The project's default network and generated names, so nothing collides with a running stack
        service.pop("networks")
        service.pop("container_name")
        service.pop("hostname")
    grafana_data = tmp_path / "grafana-data"
    grafana_data.mkdir()
    grafana_data.chmod(0o777)
    services["grafana"]["volumes"] = [f"{grafana_data}:/var/lib/grafana"]
    if publish_grafana:
        services["grafana"]["ports"] = ["127.0.0.1::3000"]
    telegraf_dir = tmp_path / "telegraf"
    telegraf_dir.mkdir()
    (telegraf_dir / "telegraf.conf").write_text(tree["telegraf/telegraf.conf"])
    agent = services["telegraf-agent"]
    agent["volumes"] = [f"{telegraf_dir}:/etc/telegraf:ro" if str(v).endswith(":/etc/telegraf:ro") else v
                        for v in agent["volumes"]]
    # The group of this machine's Docker socket instead of the render's placeholder
    agent["user"] = f"telegraf:{os.stat('/var/run/docker.sock').st_gid}"
    secret = tmp_path / "grafana_admin_password"
    secret.write_text(password)
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


def grafana_port(base):
    port = subprocess.run([*base, "port", "grafana", "3000"], capture_output=True, text=True, check=True)
    return int(port.stdout.strip().rsplit(":", 1)[1])


def query_until(base, promql, expected, attempts=60):
    """The VictoriaMetrics query result once expected is in it, or the last one."""
    for _ in range(attempts):
        exec_in(base, "victoriametrics", "wget", "-qO-", "http://127.0.0.1:8428/internal/force_flush")
        query = exec_in(base, "victoriametrics", "wget", "-qO-", "--post-data", urlencode({"query": promql}),
                        "http://127.0.0.1:8428/api/v1/query")
        if expected in query.stdout:
            break
        time.sleep(2)
    return query.stdout + query.stderr
