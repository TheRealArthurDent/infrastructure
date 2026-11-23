"""The monitoring stack of dungeon with the real images, for the tests that need Docker (CI).

VictoriaMetrics, Grafana, the Telegraf agent and the Docker socket proxy it reads the containers from, as the stack
role renders them for dungeon, in a Compose project of their own: the project's default network instead of siz, generated container names, Grafana's data and the agent's
config in temporary directories and a throwaway admin password as the Compose secret.

The agent writes through nginx as on the host (#39): the rendered metrics vhost in front of VictoriaMetrics, with a
throwaway root CA and certificate for metrics.dungeon and a writers file holding the agent's password, hashed as
crypt() takes it (openssl passwd -6, like the Molecule check of the secrets role's file).
"""
import contextlib
import shutil
import subprocess
import time
import uuid
from urllib.parse import urlencode

import yaml

from render import rendered_tree

SERVICES = ("victoriametrics", "grafana", "telegraf-agent", "docker-socket-proxy", "nginx")
METRICS = "/resticprofile-metrics"
# The root CA and the certificates the agent reads, mounted where the agent's config expects them
CA_DIR = "/smoke-ca"
WRITER_PASSWORD = 'writer $x {{ z }} "q" \'s\' \\b #c :colon'
# nginx' main config: the server block as nginx.conf.j2 renders it for the metrics vhost, the rest left out
NGINX_CONF = """events {
}

http {
    server {
        listen              443 ssl;
        server_name         metrics.dungeon;
        ssl_certificate     /etc/nginx/dungeon.pem;
        ssl_certificate_key /etc/nginx/dungeon.key;

        include             snippets/metrics.conf;
    }
}
"""


def openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def make_certificates(directory):
    """A throwaway root CA and a certificate for metrics.dungeon signed by it."""
    directory.mkdir()
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "rootCA.key", "-out", "rootCA.pem",
            "-days", "90", "-subj", "/CN=Smoke Root CA", cwd=directory)
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "dungeon.key", "-out", "dungeon.csr",
            "-subj", "/CN=dungeon", cwd=directory)
    # Longer than the certificate alert's 21 days, so it stays quiet
    (directory / "san.ext").write_text("subjectAltName=DNS:dungeon,DNS:metrics.dungeon\n")
    openssl("x509", "-req", "-in", "dungeon.csr", "-CA", "rootCA.pem", "-CAkey", "rootCA.key", "-CAcreateserial",
            "-out", "dungeon.pem", "-days", "60", "-extfile", "san.ext", cwd=directory)
    for name in ("rootCA.pem", "dungeon.pem", "dungeon.key"):
        (directory / name).chmod(0o644)


def htpasswd_line(user, password):
    hashed = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password, capture_output=True, text=True,
                            check=True).stdout.strip()
    return f"{user}:{hashed}\n"
# Like the files resticprofile writes: its own backup.prom (HELP, TYPE, labels) and one of the run-after lines
BACKUP_PROM = """# HELP resticprofile_backup_status Backup status: 0=fail, 1=warning, 2=success.
# TYPE resticprofile_backup_status gauge
resticprofile_backup_status{profile="default"} 2
# HELP resticprofile_build_info resticprofile build information.
# TYPE resticprofile_build_info gauge
resticprofile_build_info{goversion="go1.24",version="0.33.1"} 1
# HELP restic_build_info restic build information.
# TYPE restic_build_info gauge
restic_build_info{version="0.19.1"} 1
"""
OFFSITE_PROM = 'resticprofile_offsite_last_success_timestamp_seconds{source="raspi"} 1763500000\n'


def docker_available():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@contextlib.contextmanager
def monitoring_stack(tmp_path, password, publish_grafana=False):
    """Yields the docker compose command of the running project; publish_grafana maps Grafana to a local port."""
    # The agent's interval shortened, so its first metrics arrive within the test
    # resticprofile's metrics from a directory of the test instead of the host's
    # the root CA and certificates from the test's directory
    tree = rendered_tree(tmp_path, "dungeon", {"monitoring_agent_interval": "5s",
                                               "monitoring_agent_resticprofile_metrics": METRICS,
                                               "monitoring_agent_root_ca": f"{CA_DIR}/rootCA.pem",
                                               "monitoring_agent_certificates": [f"{CA_DIR}/dungeon.pem",
                                                                                 f"{CA_DIR}/rootCA.pem"]})
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
    metrics = tmp_path / "resticprofile-metrics"
    metrics.mkdir()
    (metrics / "backup.prom").write_text(BACKUP_PROM)
    (metrics / "offsite_raspi_last_success.prom").write_text(OFFSITE_PROM)
    metrics.chmod(0o755)
    agent = services["telegraf-agent"]
    certificates = tmp_path / "certificates"
    make_certificates(certificates)
    agent["volumes"] = [f"{telegraf_dir}:/etc/telegraf:ro" if str(v).endswith(":/etc/telegraf:ro") else v
                        for v in agent["volumes"]] + [f"{metrics}:{METRICS}:ro", f"{certificates}:{CA_DIR}:ro"]
    # nginx with the rendered metrics vhost, reached by the agent under the same name as on the host
    nginx_dir = tmp_path / "nginx"
    (nginx_dir / "snippets").mkdir(parents=True)
    (nginx_dir / "nginx.conf").write_text(NGINX_CONF)
    (nginx_dir / "snippets" / "metrics.conf").write_text(tree["nginx/snippets/metrics.conf"])
    (nginx_dir / "metrics.htpasswd").write_text(htpasswd_line("dungeon", WRITER_PASSWORD))
    for name in ("dungeon.pem", "dungeon.key"):
        shutil.copy(certificates / name, nginx_dir / name)
    for path in nginx_dir.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    nginx_dir.chmod(0o755)
    services["nginx"] = {"image": services["nginx"]["image"], "volumes": [f"{nginx_dir}:/etc/nginx:ro"],
                         "networks": {"default": {"aliases": ["metrics.dungeon"]}}}
    writer_secret = tmp_path / "metrics_writer_password"
    writer_secret.write_text(WRITER_PASSWORD)
    # On the host the file belongs to Telegraf's user 100 (secrets role)
    writer_secret.chmod(0o644)
    secret = tmp_path / "grafana_admin_password"
    secret.write_text(password)
    # On the host the file belongs to Grafana's user 472 (secrets role); here the runner's user owns it
    secret.chmod(0o644)
    smoke = {
        "services": services,
        "volumes": {"victoriametrics_data": {"name": f"{project}_victoriametrics_data"}},
        "secrets": {"grafana_admin_password": {"file": str(secret)},
                    "metrics_writer_password": {"file": str(writer_secret)}},
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
