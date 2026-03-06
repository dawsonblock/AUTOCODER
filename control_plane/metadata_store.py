from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from control_plane.contracts import VerificationPack
from control_plane.settings import AppConfig


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    source_path TEXT NOT NULL,
    test_path TEXT NOT NULL,
    repo_revision TEXT NOT NULL,
    objective TEXT NOT NULL,
    trigger TEXT NOT NULL,
    profile TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    parent_id TEXT NULL,
    patch_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    source_relpath TEXT NOT NULL DEFAULT '',
    files_touched JSONB NOT NULL DEFAULT '[]'::jsonb,
    diff_summary TEXT NOT NULL DEFAULT '',
    static_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    rank_score DOUBLE PRECISION NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_candidates_task_id ON candidates(task_id);

CREATE TABLE IF NOT EXISTS executions (
    run_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    executor_core INTEGER NULL,
    executor_host TEXT NULL,
    executor_port INTEGER NULL,
    executor_id TEXT NULL,
    hardware_isolated BOOLEAN NULL,
    started_at TIMESTAMPTZ NULL,
    finished_at TIMESTAMPTZ NULL,
    success BOOLEAN NULL,
    runtime DOUBLE PRECISION NULL,
    tests_passed INTEGER NULL,
    tests_failed INTEGER NULL,
    artifact_uri TEXT NULL,
    error_signature TEXT NULL,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_executions_task_id ON executions(task_id);
CREATE INDEX IF NOT EXISTS idx_executions_candidate_id ON executions(candidate_id);

CREATE TABLE IF NOT EXISTS policy_decisions (
    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    accepted BOOLEAN NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    risk_score DOUBLE PRECISION NOT NULL,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    requires_human_review BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@dataclass
class ExecutionQueued:
    run_id: str
    candidate_id: str
    task_id: str
    executor_core: int
    executor_host: str
    executor_port: int
    meta: dict[str, Any]


def can_transition_task_status(current: str | None, new: str) -> bool:
    if not current or current == new:
        return True
    if current == "merged":
        return new == "merged"
    return True


def can_transition_candidate_status(current: str | None, new: str) -> bool:
    if not current or current == new:
        return True
    if current == "merged":
        return new == "merged"
    if current == "accepted":
        return new in {"accepted", "merged", "merge_failed"}
    if current == "rejected_policy":
        return new == "rejected_policy"
    if current == "verified":
        return new in {"verified", "accepted", "rejected_policy", "merged", "merge_failed"}
    if current in {"failed", "dispatch_failed", "merge_failed"}:
        return new == current
    return True


class MetadataStore:
    SCHEMA_LOCK_ID = 904511337173

    def __init__(self, config: AppConfig) -> None:
        self.url = config.metadata.postgres_url
        self.ensure_schema()

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.url, autocommit=True)

    def _fetch_status(self, table: str, id_column: str, row_id: str) -> str | None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(f"SELECT status FROM {table} WHERE {id_column} = %s", (row_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return row[0]

    def ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (self.SCHEMA_LOCK_ID,))
            try:
                cursor.execute(SCHEMA_SQL)
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (self.SCHEMA_LOCK_ID,))

    def record_task(
        self,
        *,
        task_id: str,
        repo_path: str,
        source_path: str,
        test_path: str,
        profile: str,
        objective: str,
        trigger: str,
        status: str,
        repo_revision: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = json.dumps(metadata or {}, sort_keys=True)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tasks (
                    task_id, repo_path, source_path, test_path, repo_revision, objective, trigger, profile, status, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (task_id) DO UPDATE SET
                    repo_path = EXCLUDED.repo_path,
                    source_path = EXCLUDED.source_path,
                    test_path = EXCLUDED.test_path,
                    repo_revision = EXCLUDED.repo_revision,
                    objective = EXCLUDED.objective,
                    trigger = EXCLUDED.trigger,
                    profile = EXCLUDED.profile,
                    status = EXCLUDED.status,
                    metadata = EXCLUDED.metadata
                """,
                (task_id, repo_path, source_path, test_path, repo_revision, objective, trigger, profile, status, payload),
            )

    def update_task_status(self, task_id: str, status: str) -> bool:
        current = self._fetch_status("tasks", "task_id", task_id)
        if not can_transition_task_status(current, status):
            return False
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute("UPDATE tasks SET status = %s WHERE task_id = %s", (status, task_id))
        return True

    def merge_task_metadata(self, task_id: str, metadata: dict[str, Any]) -> None:
        payload = json.dumps(metadata, sort_keys=True)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE tasks
                SET metadata = metadata || %s::jsonb
                WHERE task_id = %s
                """,
                (payload, task_id),
            )

    def record_candidate(
        self,
        *,
        candidate_id: str,
        task_id: str,
        parent_id: str | None,
        patch_kind: str,
        status: str,
        source_relpath: str,
        files_touched: list[str],
        diff_summary: str,
        static_score: float,
        rank_score: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        files_payload = json.dumps(files_touched, sort_keys=True)
        meta_payload = json.dumps(metadata or {}, sort_keys=True)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO candidates (
                    candidate_id, task_id, parent_id, patch_kind, status, source_relpath, files_touched,
                    diff_summary, static_score, rank_score, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (candidate_id) DO UPDATE SET
                    parent_id = EXCLUDED.parent_id,
                    patch_kind = EXCLUDED.patch_kind,
                    status = EXCLUDED.status,
                    source_relpath = EXCLUDED.source_relpath,
                    files_touched = EXCLUDED.files_touched,
                    diff_summary = EXCLUDED.diff_summary,
                    static_score = EXCLUDED.static_score,
                    rank_score = EXCLUDED.rank_score,
                    metadata = EXCLUDED.metadata
                """,
                (
                    candidate_id,
                    task_id,
                    parent_id,
                    patch_kind,
                    status,
                    source_relpath,
                    files_payload,
                    diff_summary,
                    static_score,
                    rank_score,
                    meta_payload,
                ),
            )

    def update_candidate_status(self, candidate_id: str, status: str, *, rank_score: float | None = None) -> bool:
        current = self._fetch_status("candidates", "candidate_id", candidate_id)
        if not can_transition_candidate_status(current, status):
            return False
        with self._connect() as conn, conn.cursor() as cursor:
            if rank_score is None:
                cursor.execute("UPDATE candidates SET status = %s WHERE candidate_id = %s", (status, candidate_id))
            else:
                cursor.execute(
                    "UPDATE candidates SET status = %s, rank_score = %s WHERE candidate_id = %s",
                    (status, rank_score, candidate_id),
                )
        return True

    def merge_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None:
        payload = json.dumps(metadata, sort_keys=True)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE candidates
                SET metadata = metadata || %s::jsonb
                WHERE candidate_id = %s
                """,
                (payload, candidate_id),
            )

    def record_execution_queued(self, queued: ExecutionQueued) -> None:
        meta_payload = json.dumps(queued.meta, sort_keys=True)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO executions (
                    run_id, candidate_id, task_id, status, executor_core, executor_host, executor_port, meta
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    executor_core = EXCLUDED.executor_core,
                    executor_host = EXCLUDED.executor_host,
                    executor_port = EXCLUDED.executor_port,
                    meta = EXCLUDED.meta
                """,
                (
                    queued.run_id,
                    queued.candidate_id,
                    queued.task_id,
                    "queued",
                    queued.executor_core,
                    queued.executor_host,
                    queued.executor_port,
                    meta_payload,
                ),
            )

    def merge_execution_meta(self, run_id: str, meta: dict[str, Any]) -> None:
        payload = json.dumps(meta, sort_keys=True)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE executions
                SET meta = meta || %s::jsonb
                WHERE run_id = %s
                """,
                (payload, run_id),
            )

    def mark_execution_started(self, run_id: str, *, executor_id: str | None = None) -> None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE executions
                SET status = 'running',
                    executor_id = COALESCE(%s, executor_id),
                    started_at = COALESCE(started_at, NOW())
                WHERE run_id = %s
                """,
                (executor_id, run_id),
            )

    def complete_execution(self, pack: VerificationPack) -> None:
        meta_payload = json.dumps(pack.meta, sort_keys=True)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE executions
                SET status = %s,
                    executor_id = %s,
                    hardware_isolated = %s,
                    finished_at = NOW(),
                    success = %s,
                    runtime = %s,
                    tests_passed = %s,
                    tests_failed = %s,
                    artifact_uri = %s,
                    error_signature = %s,
                    meta = meta || %s::jsonb
                WHERE run_id = %s
                """,
                (
                    "succeeded" if pack.success and pack.tests_failed == 0 else "failed",
                    pack.node_id,
                    pack.hardware_isolated,
                    pack.success,
                    pack.runtime,
                    pack.tests_passed,
                    pack.tests_failed,
                    pack.artifact_uri,
                    pack.error_signature,
                    meta_payload,
                    pack.run_id,
                ),
            )

    def record_policy_decision(
        self,
        *,
        candidate_id: str,
        accepted: bool,
        confidence: float,
        risk_score: float,
        reasons: list[str],
        requires_human_review: bool = False,
    ) -> None:
        reasons_payload = json.dumps(reasons, sort_keys=True)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO policy_decisions (
                    candidate_id, accepted, confidence, risk_score, reasons, requires_human_review
                ) VALUES (
                    %s, %s, %s, %s, %s::jsonb, %s
                )
                ON CONFLICT (candidate_id) DO UPDATE SET
                    accepted = EXCLUDED.accepted,
                    confidence = EXCLUDED.confidence,
                    risk_score = EXCLUDED.risk_score,
                    reasons = EXCLUDED.reasons,
                    requires_human_review = EXCLUDED.requires_human_review,
                    created_at = NOW()
                """,
                (candidate_id, accepted, confidence, risk_score, reasons_payload, requires_human_review),
            )

    def performance_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*)::int,
                    COALESCE(AVG((metadata->'timings'->>'seed_total_seconds')::double precision), 0),
                    COALESCE(AVG((metadata->'timings'->>'coverage_total_seconds')::double precision), 0),
                    COALESCE(AVG((metadata->'snapshot'->>'bytes')::double precision), 0)
                FROM tasks
                """
            )
            task_row = cursor.fetchone() or (0, 0.0, 0.0, 0.0)
            task_count, avg_seed_seconds, avg_coverage_seconds, avg_snapshot_bytes = task_row

            cursor.execute(
                """
                SELECT
                    COUNT(*)::int,
                    COALESCE(AVG(runtime), 0),
                    COALESCE(AVG((meta->'dispatch'->>'queue_wait_ms')::double precision), 0),
                    COALESCE(AVG((meta->'simulator'->>'materialize_ms')::double precision), 0),
                    COALESCE(AVG((meta->'simulator'->>'artifact_write_ms')::double precision), 0),
                    COALESCE(AVG((meta->'funnel'->'stages'->>'full_suite_ms')::double precision), 0)
                FROM executions
                """
            )
            execution_row = cursor.fetchone() or (0, 0.0, 0.0, 0.0, 0.0, 0.0)
            (
                execution_count,
                avg_runtime_seconds,
                avg_queue_wait_ms,
                avg_materialize_ms,
                avg_artifact_write_ms,
                avg_full_suite_ms,
            ) = execution_row

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE accepted)::int,
                    COUNT(*)::int
                FROM policy_decisions
                """
            )
            policy_row = cursor.fetchone() or (0, 0)
            accepted_count, policy_count = policy_row

            cursor.execute(
                """
                SELECT
                    COUNT(*)::int,
                    COALESCE(AVG((metadata->'generation_seconds')::double precision), 0)
                FROM candidates
                WHERE patch_kind <> 'seed'
                """
            )
            candidate_row = cursor.fetchone() or (0, 0.0)
            generated_candidates, avg_generation_seconds = candidate_row

        return {
            "tasks": int(task_count or 0),
            "executions": int(execution_count or 0),
            "accepted": int(accepted_count or 0),
            "policyDecisions": int(policy_count or 0),
            "generatedCandidates": int(generated_candidates or 0),
            "avgSeedMs": float(avg_seed_seconds or 0.0) * 1000.0,
            "avgCoverageMs": float(avg_coverage_seconds or 0.0) * 1000.0,
            "avgGenerationMs": float(avg_generation_seconds or 0.0) * 1000.0,
            "avgQueueWaitMs": float(avg_queue_wait_ms or 0.0),
            "avgExecutorMs": float(avg_runtime_seconds or 0.0) * 1000.0,
            "avgMaterializeMs": float(avg_materialize_ms or 0.0),
            "avgArtifactWriteMs": float(avg_artifact_write_ms or 0.0),
            "avgFullSuiteMs": float(avg_full_suite_ms or 0.0),
            "avgSnapshotBytes": float(avg_snapshot_bytes or 0.0),
        }
