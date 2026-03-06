from __future__ import annotations

import json
import math
import socket
import time
import uuid
import hashlib

from control_plane.contracts import TaskCapsule
from control_plane.redis_keys import (
    STATS_INFLIGHT,
    TREE_INDEX,
    node_children_key,
    node_lock_key,
    root_node_id,
    tree_done_key,
    tree_node_id,
)
from control_plane.runtime import build_parser, load_runtime, sha256_text, update_worker_heartbeat
from control_plane.smt_oracle import Z3EquivalenceChecker
from control_plane.tree_sitter_engine import TargetedTreeSitterPatcher


class ForestWorker:
    def __init__(self, profile: str, task_id: str | None = None) -> None:
        self.config, self.redis = load_runtime(profile)
        self.worker_id = f"worker_{uuid.uuid4().hex[:8]}"
        self.task_id = task_id
        self.patcher = TargetedTreeSitterPatcher()
        self.smt_oracle = Z3EquivalenceChecker()

    def _task_ids(self) -> list[str]:
        if self.task_id:
            return [self.task_id]
        return sorted(self.redis.smembers(TREE_INDEX))

    def distributed_select(self, task_id: str) -> str:
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
                ucb = reward + 1.414 * math.sqrt(math.log(max(parent_visits, 1)) / visits)
                if ucb > best_ucb:
                    best_ucb = ucb
                    best_node = child_id
            if best_node is None:
                return current
            current = best_node

    def _dispatch_capsule(self, capsule: TaskCapsule) -> None:
        core_hash = hashlib.md5(capsule.id.encode("utf-8")).hexdigest()
        core_index = int(core_hash[:8], 16) % self.config.omega.executor_cores
        port = self.config.omega.executor_base_port + core_index
        self.redis.incr(STATS_INFLIGHT)
        try:
            with socket.create_connection((self.config.omega.executor_host, port), timeout=5) as sock:
                sock.sendall(capsule.to_json().encode("utf-8"))
                sock.shutdown(socket.SHUT_WR)
        except OSError:
            self.redis.decr(STATS_INFLIGHT)
            raise

    def expand(self, task_id: str, node_id: str) -> None:
        node = self.redis.hgetall(node_id)
        if not node or node.get("is_terminal") == "True" or node.get("expanded") == "True":
            return

        if int(node.get("depth", 0)) >= self.config.budgets.max_depth:
            self.redis.hset(node_id, mapping={"is_terminal": "True", "expanded": "True"})
            return

        suspicious_lines = json.loads(node.get("suspicious_lines", "[]"))
        if not suspicious_lines:
            suspicious_lines = json.loads(self.redis.hget(root_node_id(task_id), "suspicious_lines") or "[]")

        candidates = self.patcher.generate_targeted_patches(
            node["code"],
            suspicious_lines,
            max_candidates=self.config.budgets.max_children,
        )
        if not candidates:
            candidates = self.patcher.generate_targeted_patches(
                node["code"],
                self.patcher.comparison_lines(node["code"]),
                max_candidates=self.config.budgets.max_children,
            )
        root_data = self.redis.hgetall(root_node_id(task_id))
        repo_snapshot = json.loads(root_data.get("repo_snapshot", "{}"))

        pipe = self.redis.pipeline()
        generated = 0
        for candidate in candidates:
            if self.smt_oracle.is_semantically_equivalent(node["code"], candidate):
                continue
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
                    "depth": int(node.get("depth", 0)) + 1,
                    "suspicious_lines": json.dumps(suspicious_lines),
                },
            )
            pipe.sadd(node_children_key(node_id), child_id)
            generated += 1
            pipe.execute()
            pipe = self.redis.pipeline()
            self._dispatch_capsule(
                TaskCapsule(
                    id=child_id,
                    tree_id=task_id,
                    source_path=node["source_path"],
                    test_path=node["test_path"],
                    code=candidate,
                    test_code=node["test_code"],
                    source_relpath=node.get("source_relpath", ""),
                    test_relpath=node.get("test_relpath", ""),
                    repo_snapshot=repo_snapshot,
                )
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
                if self.redis.exists(tree_done_key(task_id)):
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
