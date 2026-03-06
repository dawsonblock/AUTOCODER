from __future__ import annotations

import json
import time

from control_plane.contracts import VerificationPack
from control_plane.redis_keys import (
    RECENT_RUNS,
    STATS_EVAL_TIMES,
    STATS_TOTAL_EVALS,
    STREAM_RESULTS,
    root_node_id,
    tree_done_key,
)
from control_plane.runtime import build_parser, ensure_stream_group, load_runtime


class GlobalMemory:
    def __init__(self, profile: str) -> None:
        self.config, self.redis = load_runtime(profile)
        ensure_stream_group(self.redis, STREAM_RESULTS, "memory_group")

    def compute_reward(self, pack: VerificationPack, parent_data: dict[str, str] | None) -> float:
        tests_failed = pack.tests_failed
        parent_failed = int(parent_data.get("tests_failed", 999)) if parent_data else 999
        reward = (100 * pack.tests_passed) - (200 * tests_failed) - (0.01 * pack.runtime)
        if tests_failed < parent_failed:
            reward += 500 * (parent_failed - tests_failed)
        return reward

    def run(self) -> None:
        while True:
            entries = self.redis.xreadgroup(
                "memory_group",
                "mem_1",
                {STREAM_RESULTS: ">"},
                count=50,
                block=2000,
            )
            if not entries:
                continue

            for _, messages in entries:
                pipe = self.redis.pipeline()
                now = time.time()
                for message_id, message in messages:
                    try:
                        pack = VerificationPack.from_json(message["payload"])
                        node_data = self.redis.hgetall(pack.capsule_id)
                        if not node_data:
                            continue
                        parent_id = node_data.get("parent", "")
                        parent_data = self.redis.hgetall(parent_id) if parent_id else None
                        reward = self.compute_reward(pack, parent_data)
                        task_id = node_data["tree_id"]
                        root_id = root_node_id(task_id)
                        status = "FORMAL PROOF" if pack.success and pack.tests_failed == 0 else "Failed"
                        live_run = {
                            "id": pack.capsule_id[-8:],
                            "runtime": f"{pack.runtime:.3f}",
                            "status": status,
                            "reward": f"{reward:+.0f}",
                        }

                        pipe.incr(STATS_TOTAL_EVALS)
                        pipe.zadd(STATS_EVAL_TIMES, {f"{now}:{pack.capsule_id}": now})
                        pipe.zremrangebyscore(STATS_EVAL_TIMES, 0, now - 60)
                        pipe.lpush(RECENT_RUNS, json.dumps(live_run))
                        pipe.ltrim(RECENT_RUNS, 0, 49)

                        current = pack.capsule_id
                        while current:
                            current_data = self.redis.hgetall(current)
                            pipe.hincrby(current, "visits", 1)
                            pipe.hincrbyfloat(current, "value_sum", reward)
                            if current == pack.capsule_id:
                                pipe.hset(
                                    current,
                                    mapping={
                                        "tests_failed": pack.tests_failed,
                                        "tests_passed": pack.tests_passed,
                                        "artifact_uri": pack.artifact_uri or "",
                                    },
                                )
                            parent = current_data.get("parent") if current_data else ""
                            current = parent

                        best_reward = float(self.redis.hget(root_id, "best_reward") or "0")
                        if reward > best_reward:
                            pipe.hset(root_id, "best_reward", reward)
                        max_depth_seen = int(self.redis.hget(root_id, "max_depth_seen") or "0")
                        node_depth = int(node_data.get("depth", "0"))
                        if node_depth > max_depth_seen:
                            pipe.hset(root_id, "max_depth_seen", node_depth)

                        if pack.success and pack.tests_failed == 0:
                            pipe.set(tree_done_key(task_id), pack.capsule_id)
                            pipe.hset(pack.capsule_id, "is_terminal", "True")
                    finally:
                        pipe.xack(STREAM_RESULTS, "memory_group", message_id)
                pipe.execute()


def main() -> None:
    parser = build_parser("Run the Kernel Omega global memory daemon.")
    args = parser.parse_args()
    GlobalMemory(profile=args.profile).run()


if __name__ == "__main__":
    main()

