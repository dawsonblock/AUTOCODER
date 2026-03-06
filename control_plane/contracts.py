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
    source_relpath: str = ""
    test_relpath: str = ""
    repo_snapshot: dict[str, str] = field(default_factory=dict)

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
