from __future__ import annotations

import json
import math
import time
import uuid
import hashlib

from control_plane.contracts import ExecutionRequest, TaskCapsule
from control_plane.metadata_store import ExecutionQueued, MetadataStore
from control_plane.planning import RepairPlanner, TaskPlan
from control_plane.redis_keys import (
    STREAM_DISPATCH,
    TREE_INDEX,
    node_children_key,
    node_lock_key,
    root_node_id,
    task_budget_key,
    tree_budget_exhausted_key,
    tree_done_key,
    tree_node_id,
)
from control_plane.runtime import build_parser, load_runtime, sha256_text, update_worker_heartbeat
from control_plane.smt_oracle import Z3EquivalenceChecker
from control_plane.tree_sitter_engine import TargetedTreeSitterPatcher


class ForestWorker:
    def __init__(self, profile: str, task_id: str | None = None) -> None:
        self.config, self.redis = load_runtime(profile)
        self.metadata = MetadataStore(self.config)
        self.worker_id = f"worker_{uuid.uuid4().hex[:8]}"
        self.task_id = task_id
        self.patcher = TargetedTreeSitterPatcher()
        self.smt_oracle = Z3EquivalenceChecker()

    def _task_ids(self) -> list[str]:
        if self.task_id:
            return [self.task_id]
        return sorted(self.redis.smembers(TREE_INDEX))

    def distributed_select(self, task_id: str) -> str:
        root_data = self.redis.hgetall(root_node_id(task_id))
        task_plan = self._task_plan(root_data)
        current = root_node_id(task_id)
        while True:
            children = sorted(self.redis.smembers(node_children_key(current)))
            if not children:
                return current
            best_node = None
            best_ucb = -float("inf")
            parent_visits = int(self.redis.hget(current, "visits") or 1)
            for child_id in children:
                child_data = self.redis.hgetall(child_id)
                visits = int(child_data.get("visits", 0))
                if visits == 0:
                    return child_id
                reward = float(child_data.get("value_sum", 0.0)) / visits
                ucb = reward + task_plan.ucb_exploration * math.sqrt(math.log(max(parent_visits, 1)) / visits)
                if ucb > best_ucb:
                    best_ucb = ucb
                    best_node = child_id
            if best_node is None:
                return current
            current = best_node

    def _select_executor_core(self, candidate_id: str) -> int:
        core_hash = hashlib.md5(candidate_id.encode("utf-8")).hexdigest()
        return int(core_hash[:8], 16) % self.config.omega.executor_cores

    def _queue_execution(self, request: ExecutionRequest) -> None:
        executor_port = self.config.omega.executor_base_port + request.executor_core
        self.metadata.record_execution_queued(
            ExecutionQueued(
                run_id=request.run_id,
                candidate_id=request.candidate_id,
                task_id=request.task_id,
                executor_core=request.executor_core,
                executor_host=self.config.omega.executor_host,
                executor_port=executor_port,
                meta={"worker_id": self.worker_id},
            )
        )
        self.metadata.update_candidate_status(request.candidate_id, "queued")
        self.redis.xadd(STREAM_DISPATCH, {"payload": request.to_json()})

    def _candidate_summary(self, source_relpath: str, node_depth: int) -> str:
        return f"comparison operator mutation in {source_relpath} at depth {node_depth}"

    def _task_plan(self, root_data: dict[str, str]) -> TaskPlan:
        plan_json = root_data.get("plan_json", "")
        if plan_json:
            return TaskPlan.from_json(plan_json)
        suspicious_lines = json.loads(root_data.get("suspicious_lines", "[]"))
        return RepairPlanner.legacy_task_plan(suspicious_lines, self.config.budgets)

    def _reserve_candidate_budget(self, task_id: str, task_plan: TaskPlan) -> bool:
        generated = int(self.redis.incr(task_budget_key(task_id)))
        if generated > task_plan.max_capsules:
            self.redis.decr(task_budget_key(task_id))
            self.redis.set(tree_budget_exhausted_key(task_id), "1")
            self.redis.hset(root_node_id(task_id), mapping={"planning_status": "budget_exhausted"})
            self.metadata.update_task_status(task_id, "budget_exhausted")
            return False
        self.redis.hset(root_node_id(task_id), mapping={"generated_capsules": generated})
        return True

    def _dispatch_capsule(self, capsule: TaskCapsule) -> None:
        executor_core = self._select_executor_core(capsule.id)
        request = ExecutionRequest(
            run_id=capsule.run_id,
            candidate_id=capsule.id,
            task_id=capsule.tree_id,
            executor_core=executor_core,
            capsule=capsule,
            created_at=time.time(),
            preferred_executor_core=executor_core,
        )
        self._queue_execution(request)

    def expand(self, task_id: str, node_id: str) -> None:
        node = self.redis.hgetall(node_id)
        if not node or node.get("is_terminal") == "True" or node.get("expanded") == "True":
            return

        root_data = self.redis.hgetall(root_node_id(task_id))
        task_plan = self._task_plan(root_data)
        node_depth = int(node.get("depth", 0))
        if node_depth >= task_plan.max_depth:
            self.redis.hset(node_id, mapping={"is_terminal": "True", "expanded": "True"})
            return
        generated_capsules = int(self.redis.get(task_budget_key(task_id)) or root_data.get("generated_capsules", 0) or 0)
        expansion_plan = task_plan.expansion_plan(depth=node_depth, generated_capsules=generated_capsules)
        if expansion_plan.max_candidates == 0:
            self.redis.set(tree_budget_exhausted_key(task_id), "1")
            self.redis.hset(root_node_id(task_id), mapping={"planning_status": "budget_exhausted"})
            self.metadata.update_task_status(task_id, "budget_exhausted")
            self.redis.hset(node_id, mapping={"is_terminal": "True", "expanded": "True"})
            return

        generation_started = time.perf_counter()
        candidates = self.patcher.generate_targeted_patches(
            node["code"],
            expansion_plan.target_lines,
            max_candidates=expansion_plan.max_candidates,
        )
        if not candidates and expansion_plan.allow_comparison_fallback:
            candidates = self.patcher.generate_targeted_patches(
                node["code"],
                self.patcher.comparison_lines(node["code"]),
                max_candidates=expansion_plan.max_candidates,
            )
        repo_snapshot = json.loads(root_data.get("repo_snapshot", "{}"))
        repo_snapshot_digest = root_data.get("repo_snapshot_digest", "")
        repo_snapshot_bytes = int(root_data.get("repo_snapshot_bytes", "0") or 0)
        repo_snapshot_files = int(root_data.get("repo_snapshot_files", "0") or 0)
        generation_seconds = time.perf_counter() - generation_started

        pipe = self.redis.pipeline()
        generated = 0
        smt_pruned = 0
        for candidate in candidates:
            if self.smt_oracle.is_semantically_equivalent(node["code"], candidate):
                smt_pruned += 1
                continue
            if not self._reserve_candidate_budget(task_id, task_plan):
                break
            digest = sha256_text(candidate + node_id)
            child_id = tree_node_id(task_id, digest)
            pipe.hset(
                child_id,
                mapping={
                    "tree_id": task_id,
                    "code": candidate,
                    "test_code": node["test_code"],
                    "source_path": node["source_path"],
                    "test_path": node["test_path"],
                    "source_relpath": node.get("source_relpath", ""),
                    "test_relpath": node.get("test_relpath", ""),
                    "repo_path": node["repo_path"],
                    "parent": node_id,
                    "visits": 0,
                    "value_sum": 0.0,
                    "tests_failed": node.get("tests_failed", 999),
                    "tests_passed": node.get("tests_passed", 0),
                    "is_terminal": "False",
                    "expanded": "False",
                    "depth": node_depth + 1,
                    "suspicious_lines": json.dumps(expansion_plan.target_lines),
                },
            )
            pipe.sadd(node_children_key(node_id), child_id)
            generated += 1
            pipe.execute()
            pipe = self.redis.pipeline()
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            child_depth = node_depth + 1
            source_relpath = node.get("source_relpath", "")
            self.metadata.record_candidate(
                candidate_id=child_id,
                task_id=task_id,
                parent_id=node_id,
                patch_kind="comparison_swap",
                status="generated",
                source_relpath=source_relpath,
                files_touched=[source_relpath] if source_relpath else [],
                diff_summary=self._candidate_summary(source_relpath or node["source_path"], child_depth),
                static_score=1.0,
                metadata={
                    "suspicious_lines": expansion_plan.target_lines,
                    "planning_strategy": task_plan.strategy,
                    "generation_seconds": generation_seconds,
                    "smt_pruned_before_dispatch": smt_pruned,
                    "target_lines_considered": expansion_plan.target_lines,
                },
            )
            self.metadata.update_task_status(task_id, "running")
            self._dispatch_capsule(
                TaskCapsule(
                    id=child_id,
                    tree_id=task_id,
                    source_path=node["source_path"],
                    test_path=node["test_path"],
                    code=candidate,
                    test_code=node["test_code"],
                    run_id=run_id,
                    source_relpath=node.get("source_relpath", ""),
                    test_relpath=node.get("test_relpath", ""),
                    repo_snapshot=repo_snapshot,
                    repo_snapshot_digest=repo_snapshot_digest,
                    repo_snapshot_bytes=repo_snapshot_bytes,
                    repo_snapshot_files=repo_snapshot_files,
                )
            )

        self.metadata.merge_task_metadata(
            task_id,
            {
                "latest_expansion": {
                    "node_id": node_id,
                    "strategy": task_plan.strategy,
                    "generation_seconds": generation_seconds,
                    "generated_candidates": generated,
                    "smt_pruned": smt_pruned,
                }
            },
        )
        pipe.hset(node_id, "expanded", "True")
        if generated == 0:
            pipe.hset(node_id, "is_terminal", "True")
        pipe.execute()

    def run(self) -> None:
        while True:
            update_worker_heartbeat(
                self.redis,
                self.worker_id,
                ttl_seconds=self.config.omega.worker_heartbeat_ttl_seconds,
            )
            for task_id in self._task_ids():
                if self.redis.exists(tree_done_key(task_id)) or self.redis.exists(tree_budget_exhausted_key(task_id)):
                    continue
                node_id = self.distributed_select(task_id)
                lock_key = node_lock_key(node_id)
                if self.redis.set(lock_key, self.worker_id, nx=True, ex=10):
                    try:
                        self.expand(task_id, node_id)
                    finally:
                        if self.redis.get(lock_key) == self.worker_id:
                            self.redis.delete(lock_key)
            time.sleep(0.1)


def main() -> None:
    parser = build_parser("Run a Kernel Omega forest worker.")
    parser.add_argument("--task-id", default=None)
    args = parser.parse_args()
    ForestWorker(profile=args.profile, task_id=args.task_id).run()


if __name__ == "__main__":
    main()
