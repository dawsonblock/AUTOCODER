from __future__ import annotations

import json
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import nacl.signing

from control_plane.cas_client import ArtifactStore
from control_plane.contracts import TaskCapsule, VerificationPack
from control_plane.redis_keys import STATS_INFLIGHT, STREAM_RESULTS
from control_plane.runtime import build_parser, load_runtime, sha256_text
from control_plane.signatures import sign_pack


FUNNEL_PATH = Path(__file__).resolve().parent.parent / "guest_agent" / "funnel.py"


def _safe_target(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe relative path: {relative_path}")
    return root / relative


def _materialize_snapshot(repo_root: Path, snapshot: dict[str, str]) -> None:
    for relative_path, content in snapshot.items():
        target = _safe_target(repo_root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


class ExecutorHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: "ThreadedExecutorServer" = self.server  # type: ignore[assignment]
        data = bytearray()
        while True:
            chunk = self.request.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
        if not data:
            return
        capsule = TaskCapsule.from_json(bytes(data))
        server.execute_capsule(capsule)


class ThreadedExecutorServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[ExecutorHandler],
        node_id: str,
        signing_key: nacl.signing.SigningKey,
        store: ArtifactStore,
        redis_client,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.node_id = node_id
        self.signing_key = signing_key
        self.store = store
        self.redis = redis_client

    def execute_capsule(self, capsule: TaskCapsule) -> None:
        started = time.perf_counter()
        result_json: dict[str, Any] = {
            "success": False,
            "tests_passed": 0,
            "tests_failed": 1,
            "error": "Unknown",
            "runtime": 0.0,
        }
        try:
            with tempfile.TemporaryDirectory(prefix="omega_sim_") as tmp_dir:
                tmp_repo = Path(tmp_dir) / "repo"
                tmp_repo.mkdir(parents=True, exist_ok=True)
                _materialize_snapshot(tmp_repo, capsule.repo_snapshot)
                target_source = _safe_target(tmp_repo, capsule.source_relpath or Path(capsule.source_path).name)
                target_test = _safe_target(tmp_repo, capsule.test_relpath or Path(capsule.test_path).name)
                target_source.parent.mkdir(parents=True, exist_ok=True)
                target_test.parent.mkdir(parents=True, exist_ok=True)
                target_source.write_text(capsule.code, encoding="utf-8")
                target_test.write_text(capsule.test_code, encoding="utf-8")
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(FUNNEL_PATH),
                        str(target_source),
                        str(target_test),
                        "--cwd",
                        str(tmp_repo),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.stdout.strip():
                    result_json = json.loads(proc.stdout.strip().splitlines()[-1])
                else:
                    result_json["error"] = proc.stderr.strip() or "NoOutput"
        except Exception as exc:
            result_json["error"] = str(exc)

        artifact_uri = self.store.put_bundle(
            "executions",
            {
                "capsule": json.loads(capsule.to_json()),
                "funnel_result": result_json,
                "executor": self.node_id,
            },
        )

        pack = VerificationPack(
            capsule_id=capsule.id,
            success=bool(result_json.get("success", False)),
            runtime=time.perf_counter() - started,
            node_id=self.node_id,
            hardware_isolated=False,
            inputs_digest=sha256_text(capsule.code),
            tests_passed=int(result_json.get("tests_passed", 0)),
            tests_failed=int(result_json.get("tests_failed", 1)),
            error_signature=result_json.get("error"),
            artifact_uri=artifact_uri,
        )
        sign_pack(pack, self.signing_key)
        self.redis.xadd(STREAM_RESULTS, {"payload": pack.to_json()})
        self.redis.decr(STATS_INFLIGHT)


def run_servers(profile: str) -> None:
    config, redis_client = load_runtime(profile)
    signing_key = nacl.signing.SigningKey.generate()
    store = ArtifactStore(config)
    public_key = signing_key.verify_key.encode().hex()
    print(f">>> KERNEL OMEGA SIMULATOR [PubKey: {public_key}] <<<")

    servers: list[ThreadedExecutorServer] = []
    threads: list[threading.Thread] = []
    try:
        for index in range(config.omega.executor_cores):
            server = ThreadedExecutorServer(
                (config.omega.executor_host, config.omega.executor_base_port + index),
                ExecutorHandler,
                node_id=f"sim-core-{index}",
                signing_key=signing_key,
                store=store,
                redis_client=redis_client,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)
    except OSError as exc:
        for server in servers:
            server.shutdown()
            server.server_close()
        port_end = config.omega.executor_base_port + config.omega.executor_cores - 1
        raise RuntimeError(
            f"Unable to bind simulator ports {config.omega.executor_host}:{config.omega.executor_base_port}-{port_end}. "
            "Set OMEGA_EXECUTOR_BASE_PORT to a free range."
        ) from exc

    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        for server in servers:
            server.shutdown()
            server.server_close()


def main() -> None:
    parser = build_parser("Run the Kernel Omega simulator executor.")
    args = parser.parse_args()
    run_servers(args.profile)


if __name__ == "__main__":
    main()
