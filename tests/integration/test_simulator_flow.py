import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("OMEGA_RUN_INTEGRATION") != "1",
    reason="Set OMEGA_RUN_INTEGRATION=1 to run the full simulator integration flow.",
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"


@pytest.mark.integration
def test_sample_repo_fixture_has_one_failing_case() -> None:
    temp_repo = ROOT / "tmp" / "sample_repo_copy"
    if temp_repo.exists():
        shutil.rmtree(temp_repo)
    shutil.copytree(FIXTURE_REPO, temp_repo)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(temp_repo)
    result = subprocess.run(
        ["pytest", "tests/test_algo.py", "-q"],
        cwd=temp_repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    shutil.rmtree(temp_repo, ignore_errors=True)
    assert result.returncode != 0
