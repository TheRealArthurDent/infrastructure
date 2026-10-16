"""Every status check the main-branch ruleset requires must come from a job that runs on every pull request.

A required check whose workflow skips a PR (e.g. by a `paths` filter) stays "Expected" forever and blocks the merge.
"""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
RULESETS = ROOT / ".github" / "rulesets"
WORKFLOWS = ROOT / ".github" / "workflows"
GITHUB_ACTIONS_APP_ID = 15368


def required_checks():
    checks = []
    for file in sorted(RULESETS.glob("*.json")):
        for rule in json.loads(file.read_text())["rules"]:
            if rule["type"] == "required_status_checks":
                checks += rule["parameters"]["required_status_checks"]
    return checks


def pull_request_jobs():
    """Map check name -> workflow file for every job that runs on all pull requests into main."""
    jobs = {}
    for file in sorted(WORKFLOWS.glob("*.yaml")):
        workflow = yaml.safe_load(file.read_text())
        # PyYAML reads the bare key `on` as True.
        triggers = workflow.get("on", workflow.get(True))
        if isinstance(triggers, list):
            triggers = dict.fromkeys(triggers)
        if not isinstance(triggers, dict) or "pull_request" not in triggers:
            continue
        pull_request = triggers["pull_request"] or {}
        if "paths" in pull_request or "paths-ignore" in pull_request:
            continue
        if "main" not in pull_request.get("branches", ["main"]):
            continue
        for job_id, job in workflow["jobs"].items():
            if "if" not in job:
                jobs[job.get("name", job_id)] = file.name
    return jobs


def test_rulesets_require_checks():
    assert required_checks(), "no required status checks found in .github/rulesets/"


@pytest.mark.parametrize("check", required_checks(), ids=lambda c: c["context"])
def test_required_check_runs_on_every_pull_request(check):
    assert check["context"] in pull_request_jobs(), (
        f"'{check['context']}' is required, but no job of that name runs on every pull request into main"
    )
    assert check["integration_id"] == GITHUB_ACTIONS_APP_ID


def test_ruleset_file_name_matches_ruleset_name():
    for file in RULESETS.glob("*.json"):
        assert json.loads(file.read_text())["name"] == file.stem
