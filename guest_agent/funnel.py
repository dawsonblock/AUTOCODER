#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence


def _tool(name: str) -> str:
    candidate = Path(sys.executable).resolve().with_name(name)
    return str(candidate) if candidate.exists() else name


def run_cmd(cmd: Sequence[str], cwd: str | None = None, timeout: int = 30) -> tuple[bool, str]:
    env = os.environ.copy()
    if cwd:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = cwd if not existing else f"{cwd}{os.pathsep}{existing}"
    try:
        result = subprocess.run(
            list(cmd),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    return result.returncode == 0, combined


def parse_pytest_counts(output: str) -> tuple[int, int]:
    passed = 0
    failed = 0
    for count, label in re.findall(r"(\d+)\s+(passed|failed|error|errors)", output):
        value = int(count)
        if label == "passed":
            passed += value
        else:
            failed += value
    return passed, failed


def execute_funnel(target_file: str, target_test: str, cwd: str | None) -> dict[str, object]:
    started = time.perf_counter()
    stages: dict[str, float] = {}

    def fail(
        *,
        error: str,
        tests_passed: int,
        tests_failed: int,
        failed_stage: str,
    ) -> dict[str, object]:
        return {
            "success": False,
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "error": error,
            "runtime": time.perf_counter() - started,
            "meta": {
                "stages": stages,
                "failed_stage": failed_stage,
                "full_suite_attempted": failed_stage == "full_suite",
            },
        }

    lint_started = time.perf_counter()
    lint_ok, lint_output = run_cmd(
        [_tool("ruff"), "check", target_file, "--select", "E,F"],
        cwd=cwd,
        timeout=20,
    )
    stages["lint_ms"] = (time.perf_counter() - lint_started) * 1000.0
    if not lint_ok:
        return fail(
            error=lint_output or "LintError",
            tests_passed=0,
            tests_failed=999,
            failed_stage="lint",
        )

    type_started = time.perf_counter()
    type_ok, type_output = run_cmd(
        [_tool("mypy"), target_file, "--ignore-missing-imports"],
        cwd=cwd,
        timeout=20,
    )
    stages["typecheck_ms"] = (time.perf_counter() - type_started) * 1000.0
    if not type_ok:
        return fail(
            error=type_output or "TypeError",
            tests_passed=0,
            tests_failed=999,
            failed_stage="typecheck",
        )

    targeted_started = time.perf_counter()
    targeted_ok, targeted_output = run_cmd(
        [_tool("pytest"), target_test, "-q", "-x", "--disable-warnings"],
        cwd=cwd,
        timeout=30,
    )
    stages["targeted_pytest_ms"] = (time.perf_counter() - targeted_started) * 1000.0
    targeted_passed, targeted_failed = parse_pytest_counts(targeted_output)
    if not targeted_ok:
        return fail(
            error=targeted_output or "TargetTestFailed",
            tests_passed=targeted_passed,
            tests_failed=targeted_failed or 1,
            failed_stage="targeted_pytest",
        )

    full_suite_cmd = [_tool("pytest"), "-q", "--disable-warnings"]
    if not cwd:
        full_suite_cmd.append(target_test)
    full_started = time.perf_counter()
    full_ok, full_output = run_cmd(
        full_suite_cmd,
        cwd=cwd,
        timeout=60,
    )
    stages["full_suite_ms"] = (time.perf_counter() - full_started) * 1000.0
    full_passed, full_failed = parse_pytest_counts(full_output)
    return {
        "success": full_ok,
        "tests_passed": full_passed if full_passed else targeted_passed,
        "tests_failed": full_failed if full_failed else (0 if full_ok else 1),
        "error": None if full_ok else (full_output or "RegressionError"),
        "runtime": time.perf_counter() - started,
        "meta": {
            "stages": stages,
            "failed_stage": None if full_ok else "full_suite",
            "full_suite_attempted": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Kernel Omega verification funnel")
    parser.add_argument("target_file")
    parser.add_argument("target_test")
    parser.add_argument("--cwd", default=None)
    args = parser.parse_args()
    print(json.dumps(execute_funnel(args.target_file, args.target_test, args.cwd)))


if __name__ == "__main__":
    main()
