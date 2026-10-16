"""deploy.sh (fallback until #238 removes it) must not deploy to production when called without a target.

ssh and scp are replaced by stubs that only log their arguments, so nothing leaves the test.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def stub_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("ssh", "scp"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\nexit 0\n')
        stub.chmod(0o755)
    return bin_dir, log


def run_deploy(stub_path, *args):
    bin_dir, log = stub_path
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", "deploy.sh", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    calls = log.read_text() if log.exists() else ""
    return result, calls


def test_refuses_to_run_without_target(stub_path):
    result, calls = run_deploy(stub_path)
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()
    assert calls == ""


def test_deploys_to_the_named_target(stub_path):
    result, calls = run_deploy(stub_path, "testbed", "4242", "testuser")
    assert result.returncode == 0, result.stderr
    assert "testuser@testbed" in calls
    assert "raspi" not in calls
