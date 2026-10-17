""".env files are never committed, and the git hooks catch secrets and commits to main before they leave the machine."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def test_no_env_file_is_tracked():
    tracked = git("ls-files").stdout.splitlines()
    env_files = [f for f in tracked if Path(f).name == ".env" or f.endswith(".env")]
    assert env_files == []


def test_env_files_are_ignored():
    for path in ("setup/.env", "setup/.outbound.env", ".env"):
        assert git("check-ignore", "--no-index", "-q", path).returncode == 0, f"{path} is not git-ignored"


def pre_commit_hook_ids():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    return {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}


def test_pre_commit_scans_for_secrets():
    hooks = pre_commit_hook_ids()
    assert "gitleaks" in hooks
    assert "detect-private-key" in hooks


def test_pre_commit_refuses_commits_on_main():
    hook = pre_commit_hook_ids().get("no-commit-to-branch")
    assert hook is not None
    assert hook.get("args") == ["--branch", "main"]


def test_pre_commit_is_installed_with_the_tooling():
    requirements = (ROOT / "requirements.txt").read_text().splitlines()
    assert any(line.startswith("pre-commit==") for line in requirements)
