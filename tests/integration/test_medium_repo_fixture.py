from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "medium_repo"


def test_medium_repo_fixture_has_multiple_files_and_failing_cases() -> None:
    temp_repo = ROOT / "tmp" / "medium_repo_copy"
    if temp_repo.exists():
        shutil.rmtree(temp_repo)
    shutil.copytree(FIXTURE_REPO, temp_repo)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(temp_repo)
    collect = subprocess.run(
        ["pytest", "tests", "--collect-only", "-q"],
        cwd=temp_repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    result = subprocess.run(
        ["pytest", "-q"],
        cwd=temp_repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    shutil.rmtree(temp_repo, ignore_errors=True)

    collected = [line for line in collect.stdout.splitlines() if "::" in line]
    assert len(collected) >= 21
    assert result.returncode != 0
