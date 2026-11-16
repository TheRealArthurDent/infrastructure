"""The repository settings in .github/repo-settings.json are applied by scripts/github-settings.sh.

The file is sent as is to `PATCH /repos/{owner}/{repo}`, so it may only hold fields that endpoint accepts.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SETTINGS = ROOT / ".github" / "repo-settings.json"
SCRIPT = ROOT / "scripts" / "github-settings.sh"

# Fields of PATCH /repos/{owner}/{repo} this repo manages.
KNOWN_FIELDS = {
    "allow_auto_merge",
    "delete_branch_on_merge",
    "allow_merge_commit",
    "allow_squash_merge",
    "allow_rebase_merge",
    "allow_update_branch",
    "has_issues",
    "has_projects",
    "has_wiki",
    "security_and_analysis",
}
SECURITY_FIELDS = {
    "secret_scanning",
    "secret_scanning_push_protection",
    "secret_scanning_non_provider_patterns",
    "secret_scanning_validity_checks",
    "dependabot_security_updates",
}


def settings():
    return json.loads(SETTINGS.read_text())


def test_only_known_fields():
    assert set(settings()) <= KNOWN_FIELDS
    assert set(settings().get("security_and_analysis", {})) <= SECURITY_FIELDS


def test_auto_merge_and_branch_cleanup():
    assert settings()["allow_auto_merge"] is True
    assert settings()["delete_branch_on_merge"] is True


def test_secret_scanning_with_push_protection():
    security = settings()["security_and_analysis"]
    assert security["secret_scanning"] == {"status": "enabled"}
    assert security["secret_scanning_push_protection"] == {"status": "enabled"}


def test_script_targets_this_repo():
    assert 'REPO="${REPO:-TheRealArthurDent/infrastructure}"' in SCRIPT.read_text()


def test_one_script_for_all_github_settings():
    """Rulesets and repository settings share scripts/github-settings.sh; no separate scripts next to it."""
    assert sorted(p.name for p in (ROOT / "scripts").glob("*.sh")) == ["github-settings.sh"]
