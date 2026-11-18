"""The Grafana config setup-grafana.yaml applies (configs/grafana.yaml and the dashboards, #39).

Checks what Grafana wouldn't refuse but would show broken: a dashboard in a folder the config doesn't create, a
uid that doesn't match its file, a panel on a data source that doesn't exist.
"""
import json
from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).parents[2]
CONFIG = yaml.safe_load((ANSIBLE_DIR / "configs/grafana.yaml").read_text())
DASHBOARD_DIR = ANSIBLE_DIR / "configs/grafana/dashboards"
DASHBOARDS = sorted(DASHBOARD_DIR.glob("*/*.json"))
DATASOURCE_UIDS = {d["uid"] for d in CONFIG["grafana_datasources"]}


def load(path):
    return json.loads(path.read_text())


def test_there_is_a_dashboard():
    assert DASHBOARDS


def test_one_default_data_source_for_victoriametrics():
    defaults = [d for d in CONFIG["grafana_datasources"] if d.get("isDefault")]
    assert [d["uid"] for d in defaults] == ["victoriametrics"]
    assert defaults[0]["type"] == "prometheus"
    assert defaults[0]["url"] == "http://victoriametrics:8428"


def test_uids_are_unique():
    for kind in ("grafana_datasources", "grafana_folders"):
        uids = [item["uid"] for item in CONFIG[kind]]
        assert len(uids) == len(set(uids)), kind
    uids = [load(p)["uid"] for p in DASHBOARDS]
    assert len(uids) == len(set(uids))


def test_every_dashboard_directory_is_a_folder_of_the_config():
    folders = {f["uid"] for f in CONFIG["grafana_folders"]}
    assert {p.name for p in DASHBOARD_DIR.iterdir() if p.is_dir()} <= folders


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_dashboard_uid_is_its_file_name(path):
    dashboard = load(path)
    assert dashboard["uid"] == path.stem
    # Grafana assigns these; from the repo they would clash with another instance's
    assert "id" not in dashboard and "version" not in dashboard


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_every_query_uses_a_data_source_of_the_config(path):
    dashboard = load(path)
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] in DATASOURCE_UIDS, panel["title"]
        assert panel["targets"], panel["title"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] in DATASOURCE_UIDS, panel["title"]
            assert target["expr"].strip(), panel["title"]
    for variable in dashboard.get("templating", {}).get("list", []):
        if variable["type"] == "query":
            assert variable["datasource"]["uid"] in DATASOURCE_UIDS, variable["name"]


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_panels_have_unique_ids(path):
    ids = [panel["id"] for panel in load(path)["panels"]]
    assert len(ids) == len(set(ids))
