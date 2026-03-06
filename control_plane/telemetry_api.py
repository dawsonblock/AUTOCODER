from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from control_plane.redis_keys import (
    RECENT_RUNS,
    STATS_EVAL_TIMES,
    STATS_INFLIGHT,
    STATS_TOTAL_EVALS,
    TREE_INDEX,
    WORKER_HEARTBEATS,
    root_node_id,
)
from control_plane.runtime import build_parser, load_runtime


class TelemetryAPI:
    def __init__(self, profile: str) -> None:
        self.config, self.redis = load_runtime(profile)

    def dashboard_payload(self) -> dict[str, object]:
        now = time.time()
        ttl = self.config.omega.worker_heartbeat_ttl_seconds
        self.redis.zremrangebyscore(WORKER_HEARTBEATS, 0, now - ttl)

        live_runs = [json.loads(item) for item in self.redis.lrange(RECENT_RUNS, 0, 7)]
        forest_state = []
        for task_id in sorted(self.redis.smembers(TREE_INDEX)):
            root = self.redis.hgetall(root_node_id(task_id))
            if not root:
                continue
            forest_state.append(
                {
                    "id": task_id,
                    "strategy": "TREE_SITTER + Z3_SMT",
                    "depth": int(root.get("max_depth_seen", 0)),
                    "best_reward": float(root.get("best_reward", 0.0)),
                }
            )

        return {
            "metrics": {
                "activeWorkers": int(self.redis.zcount(WORKER_HEARTBEATS, now - ttl, "+inf")),
                "queueDepth": int(self.redis.get(STATS_INFLIGHT) or 0),
                "totalEvals": int(self.redis.get(STATS_TOTAL_EVALS) or 0),
                "evalsPerSec": float(self.redis.zcount(STATS_EVAL_TIMES, now - 5, "+inf")) / 5.0,
            },
            "liveRuns": live_runs,
            "forestState": forest_state,
        }

    def serve(self) -> None:
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in {"/api/dashboard", "/healthz"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = {"ok": True} if self.path == "/healthz" else api.dashboard_payload()
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

        server = ThreadingHTTPServer(
            (self.config.omega.telemetry_host, self.config.omega.telemetry_port),
            Handler,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()


def main() -> None:
    parser = build_parser("Run the Kernel Omega telemetry API.")
    args = parser.parse_args()
    TelemetryAPI(profile=args.profile).serve()


if __name__ == "__main__":
    main()
