from __future__ import annotations

import socket
import time

from control_plane.contracts import ExecutionRequest, VerificationPack
from control_plane.metadata_store import MetadataStore
from control_plane.redis_keys import STATS_INFLIGHT, STREAM_DISPATCH, STREAM_RESULTS
from control_plane.runtime import build_parser, ensure_stream_group, load_runtime, sha256_text


class DispatcherService:
    def __init__(self, profile: str) -> None:
        self.config, self.redis = load_runtime(profile)
        self.metadata = MetadataStore(self.config)
        ensure_stream_group(self.redis, STREAM_DISPATCH, "dispatch_group")

    def _dispatch(self, request: ExecutionRequest) -> None:
        port = self.config.omega.executor_base_port + request.executor_core
        self.redis.incr(STATS_INFLIGHT)
        try:
            with socket.create_connection((self.config.omega.executor_host, port), timeout=5) as sock:
                sock.sendall(request.capsule.to_json().encode("utf-8"))
                sock.shutdown(socket.SHUT_WR)
        except OSError as exc:
            self.redis.decr(STATS_INFLIGHT)
            self.metadata.update_candidate_status(request.candidate_id, "dispatch_failed")
            pack = VerificationPack(
                capsule_id=request.candidate_id,
                run_id=request.run_id,
                success=False,
                runtime=0.0,
                node_id="dispatcher",
                hardware_isolated=False,
                inputs_digest=sha256_text(request.capsule.code),
                tests_passed=0,
                tests_failed=999,
                error_signature=f"DispatchError: {exc}",
            )
            self.metadata.complete_execution(pack)
            self.redis.xadd(STREAM_RESULTS, {"payload": pack.to_json()})
            return

        queue_wait_ms = max(0.0, (time.time() - request.created_at) * 1000.0)
        self.metadata.mark_execution_started(request.run_id)
        self.metadata.merge_execution_meta(
            request.run_id,
            {
                "dispatch": {
                    "queue_wait_ms": queue_wait_ms,
                    "preferred_core": request.preferred_executor_core
                    if request.preferred_executor_core is not None
                    else request.executor_core,
                    "assigned_core": request.executor_core,
                }
            },
        )
        self.metadata.update_candidate_status(request.candidate_id, "running")

    def run(self) -> None:
        while True:
            entries = self.redis.xreadgroup(
                "dispatch_group",
                "dispatcher_1",
                {STREAM_DISPATCH: ">"},
                count=50,
                block=5000,
            )
            if not entries:
                continue

            pipe = self.redis.pipeline()
            for _, messages in entries:
                for message_id, message in messages:
                    try:
                        request = ExecutionRequest.from_json(message["payload"])
                        self._dispatch(request)
                    finally:
                        pipe.xack(STREAM_DISPATCH, "dispatch_group", message_id)
            pipe.execute()


def main() -> None:
    parser = build_parser("Run the Kernel Omega dispatcher.")
    args = parser.parse_args()
    DispatcherService(profile=args.profile).run()


if __name__ == "__main__":
    main()
