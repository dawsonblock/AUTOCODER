from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass
class RepoConfig:
    language: str
    package_manager: str


@dataclass
class OmegaSettings:
    redis: str
    cas_endpoint: str
    cas_bucket: str
    cas_access_key: str
    cas_secret_key: str
    executor_host: str
    executor_base_port: int
    executor_cores: int
    telemetry_host: str
    telemetry_port: int
    worker_heartbeat_ttl_seconds: int


@dataclass
class BudgetSettings:
    max_capsules: int
    max_wall_seconds: int
    max_diff_lines: int
    max_touched_files: int
    max_depth: int
    max_children: int
    suspicious_line_count: int


@dataclass
class PolicySettings:
    deny_edit_tests: bool
    deny_disable_asserts: bool
    require_hardware_isolation: bool


@dataclass
class AppConfig:
    repo: RepoConfig
    omega: OmegaSettings
    budgets: BudgetSettings
    policy: PolicySettings
    profile: str


def load_config(profile: str = "omega") -> AppConfig:
    base_path = CONFIG_DIR / "omega.toml"
    profile_path = CONFIG_DIR / f"{profile}.toml"
    with base_path.open("rb") as handle:
        base_config = tomllib.load(handle)

    merged = base_config
    if profile != "omega" and profile_path.exists():
        with profile_path.open("rb") as handle:
            merged = _merge_dicts(base_config, tomllib.load(handle))

    omega = merged["omega"]
    env_overrides = {
        "redis": os.getenv("OMEGA_REDIS_URL"),
        "cas_endpoint": os.getenv("OMEGA_CAS_ENDPOINT"),
        "cas_bucket": os.getenv("OMEGA_CAS_BUCKET"),
        "cas_access_key": os.getenv("OMEGA_CAS_ACCESS_KEY"),
        "cas_secret_key": os.getenv("OMEGA_CAS_SECRET_KEY"),
        "executor_host": os.getenv("OMEGA_EXECUTOR_HOST"),
        "executor_base_port": os.getenv("OMEGA_EXECUTOR_BASE_PORT"),
        "executor_cores": os.getenv("OMEGA_EXECUTOR_CORES"),
        "telemetry_host": os.getenv("OMEGA_TELEMETRY_HOST"),
        "telemetry_port": os.getenv("OMEGA_TELEMETRY_PORT"),
    }
    for key, value in env_overrides.items():
        if value is not None:
            omega[key] = int(value) if key.endswith("_port") or key.endswith("_cores") else value

    return AppConfig(
        repo=RepoConfig(**merged["repo"]),
        omega=OmegaSettings(**omega),
        budgets=BudgetSettings(**merged["budgets"]),
        policy=PolicySettings(**merged["policy"]),
        profile=profile,
    )
