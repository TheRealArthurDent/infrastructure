"""Every status check the main-branch ruleset requires must come from a job that reports on every pull request.

A required check whose workflow skips a PR (e.g. by a `paths` filter) stays "Expected" forever and blocks the merge.
A job skipped by its `if` reports "Skipped", which counts as passed, so required jobs may skip themselves, but only
through the `changes` job's path filter, and only fail-safe: when `changes` fails, they run.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
RULESETS = ROOT / ".github" / "rulesets"
WORKFLOWS = ROOT / ".github" / "workflows"
GITHUB_ACTIONS_APP_ID = 15368
# The only condition a required job may carry: skip when the `changes` job says the PR touches nothing relevant,
# run otherwise, including when `changes` failed and its output is empty.
FAIL_SAFE_CHANGES_IF = re.compile(r"^\$\{\{ !cancelled\(\) && needs\.changes\.outputs\.[a-z_]+ != 'false' \}\}$")


def required_checks():
    checks = []
    for file in sorted(RULESETS.glob("*.json")):
        for rule in json.loads(file.read_text())["rules"]:
            if rule["type"] == "required_status_checks":
                checks += rule["parameters"]["required_status_checks"]
    return checks


def workflows():
    for file in sorted(WORKFLOWS.glob("*.yaml")):
        yield file, yaml.safe_load(file.read_text())


def runs_on_every_pull_request(workflow):
    # PyYAML reads the bare key `on` as True.
    triggers = workflow.get("on", workflow.get(True))
    if isinstance(triggers, list):
        triggers = dict.fromkeys(triggers)
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        return False
    pull_request = triggers["pull_request"] or {}
    if "paths" in pull_request or "paths-ignore" in pull_request:
        return False
    return "main" in pull_request.get("branches", ["main"])


def reports_on_every_run(job, jobs):
    """True if the job either runs or is skipped by the fail-safe path filter, so it always reports a check."""
    if "if" not in job:
        return True
    needs = job.get("needs", [])
    needs = [needs] if isinstance(needs, str) else needs
    changes = jobs.get("changes")
    return (
        "changes" in needs
        and changes is not None
        and "if" not in changes
        and bool(FAIL_SAFE_CHANGES_IF.match(str(job["if"]).strip()))
    )


def pull_request_jobs():
    """Map check name -> workflow file for every job that reports on all pull requests into main."""
    jobs = {}
    for file, workflow in workflows():
        if not runs_on_every_pull_request(workflow):
            continue
        for job_id, job in workflow["jobs"].items():
            if reports_on_every_run(job, workflow["jobs"]):
                jobs[job.get("name", job_id)] = file.name
    return jobs


def test_rulesets_require_checks():
    assert required_checks(), "no required status checks found in .github/rulesets/"


@pytest.mark.parametrize("check", required_checks(), ids=lambda c: c["context"])
def test_required_check_runs_on_every_pull_request(check):
    assert check["context"] in pull_request_jobs(), (
        f"'{check['context']}' is required, but no job of that name reports on every pull request into main"
    )
    assert check["integration_id"] == GITHUB_ACTIONS_APP_ID


@pytest.mark.parametrize(
    "condition, expected",
    [
        ("${{ !cancelled() && needs.changes.outputs.molecule != 'false' }}", True),
        # Skipped when `changes` fails: a broken filter would let an untested PR through.
        ("needs.changes.outputs.molecule == 'true'", False),
        ("${{ needs.changes.outputs.molecule != 'false' }}", False),
        ("${{ github.event.pull_request.draft == false }}", False),
    ],
)
def test_only_the_fail_safe_path_filter_may_skip_a_job(condition, expected):
    jobs = {"changes": {"runs-on": "ubuntu-latest"}, "job": {"needs": "changes", "if": condition}}
    assert reports_on_every_run(jobs["job"], jobs) is expected


def test_path_filtered_jobs_need_an_unconditional_changes_job():
    job = {"needs": ["changes"], "if": "${{ !cancelled() && needs.changes.outputs.unit != 'false' }}"}
    assert not reports_on_every_run(job, {"job": job})
    assert not reports_on_every_run(job, {"job": job, "changes": {"if": "github.event_name == 'push'"}})


def test_changes_job_filters_cover_every_output():
    """Every output a job checks comes from a filter of the `changes` job, so no job silently compares against ''."""
    for file, workflow in workflows():
        changes = workflow["jobs"].get("changes")
        if changes is None:
            continue
        filters = next(s for s in changes["steps"] if s.get("id") == "filter")
        names = set(yaml.safe_load(filters["with"]["filters"]))
        assert set(changes["outputs"]) <= names, file.name
        for job in workflow["jobs"].values():
            for used in re.findall(r"needs\.changes\.outputs\.([a-z_]+)", str(job.get("if", ""))):
                assert used in changes["outputs"], f"{file.name}: unknown output {used}"


def jobs_by_name():
    return {job.get("name", job_id): job for _, workflow in workflows() for job_id, job in workflow["jobs"].items()}


@pytest.mark.parametrize("name", ["Lint Ansible Code", "Unit tests", "Molecule"])
def test_slow_jobs_skip_pull_requests_that_dont_touch_them(name):
    """These use most of the Actions minutes; a docs-only PR shouldn't start them."""
    job = jobs_by_name()[name]
    assert "changes" in job.get("needs", []), f"'{name}' doesn't wait for the path filter"
    assert FAIL_SAFE_CHANGES_IF.match(str(job.get("if", ""))), f"'{name}' isn't skipped by the path filter"


def test_pull_request_runs_cancel_superseded_runs():
    """A new push to a PR cancels the run for the previous push instead of letting both use up minutes."""
    for file, workflow in workflows():
        triggers = workflow.get("on", workflow.get(True))
        if not isinstance(triggers, dict) or "pull_request" not in triggers:
            continue
        concurrency = workflow.get("concurrency")
        assert concurrency, f"{file.name} has no concurrency group"
        assert "github.event.pull_request.number || github.ref" in concurrency["group"], file.name
        assert "github.event_name == 'pull_request'" in str(concurrency["cancel-in-progress"]), file.name


def test_ruleset_file_name_matches_ruleset_name():
    for file in RULESETS.glob("*.json"):
        assert json.loads(file.read_text())["name"] == file.stem
