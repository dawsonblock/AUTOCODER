from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json


@dataclass
class TaskCapsule:
    id: str
    tree_id: str
    source_path: str
    test_path: str
    code: str
    test_code: str
    run_id: str = ""
    source_relpath: str = ""
    test_relpath: str = ""
    repo_snapshot: dict[str, str] = field(default_factory=dict)
    repo_snapshot_digest: str = ""
    repo_snapshot_bytes: int = 0
    repo_snapshot_files: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, value: str | bytes) -> "TaskCapsule":
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return cls(**json.loads(value))


@dataclass
class VerificationPack:
    capsule_id: str
    run_id: str
    success: bool
    runtime: float
    node_id: str
    hardware_isolated: bool
    inputs_digest: str
    tests_passed: int
    tests_failed: int
    error_signature: str | None = None
    attestation: str | None = None
    artifact_uri: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, value: str | bytes) -> "VerificationPack":
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return cls(**json.loads(value))


@dataclass
class ExecutionRequest:
    run_id: str
    candidate_id: str
    task_id: str
    executor_core: int
    capsule: TaskCapsule
    created_at: float
    preferred_executor_core: int | None = None

    def to_json(self) -> str:
        payload = asdict(self)
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def from_json(cls, value: str | bytes) -> "ExecutionRequest":
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        payload = json.loads(value)
        payload["capsule"] = TaskCapsule(**payload["capsule"])
        return cls(**payload)
