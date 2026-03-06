from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FUNNEL_PATH = ROOT / "guest_agent" / "funnel.py"


def load_funnel_module():
    spec = importlib.util.spec_from_file_location("omega_funnel", FUNNEL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_pytest_counts_sums_failures_and_errors() -> None:
    funnel = load_funnel_module()

    passed, failed = funnel.parse_pytest_counts("2 passed, 1 failed, 3 errors in 0.10s")

    assert passed == 2
    assert failed == 4


def test_execute_funnel_runs_full_suite_after_targeted(monkeypatch) -> None:
    funnel = load_funnel_module()
    calls: list[list[str]] = []

    def fake_run_cmd(cmd, cwd=None, timeout=30):
        calls.append(list(cmd))
        if cmd[0] == "ruff":
            return True, ""
        if cmd[0] == "mypy":
            return True, ""
        if cmd == ["pytest", "tests/test_algo.py", "-q", "-x", "--disable-warnings"]:
            return True, "1 passed in 0.01s"
        if cmd == ["pytest", "-q", "--disable-warnings"]:
            return False, "3 passed, 1 failed in 0.03s"
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(funnel, "_tool", lambda name: name)
    monkeypatch.setattr(funnel, "run_cmd", fake_run_cmd)

    result = funnel.execute_funnel("src/algo.py", "tests/test_algo.py", cwd="/repo")

    assert calls[-1] == ["pytest", "-q", "--disable-warnings"]
    assert result["tests_passed"] == 3
    assert result["tests_failed"] == 1
    assert result["success"] is False


def test_execute_funnel_falls_back_to_one_failure_without_summary(monkeypatch) -> None:
    funnel = load_funnel_module()

    def fake_run_cmd(cmd, cwd=None, timeout=30):
        if cmd[0] in {"ruff", "mypy"}:
            return True, ""
        if cmd == ["pytest", "tests/test_algo.py", "-q", "-x", "--disable-warnings"]:
            return True, "1 passed in 0.01s"
        if cmd == ["pytest", "-q", "--disable-warnings"]:
            return False, "internal error"
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(funnel, "_tool", lambda name: name)
    monkeypatch.setattr(funnel, "run_cmd", fake_run_cmd)

    result = funnel.execute_funnel("src/algo.py", "tests/test_algo.py", cwd="/repo")

    assert result["tests_failed"] == 1
    assert result["success"] is False
