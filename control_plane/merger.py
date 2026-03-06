from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from control_plane.metadata_store import MetadataStore
from control_plane.redis_keys import STREAM_ACCEPTED, tree_accepted_key
from control_plane.runtime import build_parser, ensure_stream_group, load_runtime


class MergerService:
    def __init__(self, profile: str) -> None:
        self.config, self.redis = load_runtime(profile)
        self.metadata = MetadataStore(self.config)
        ensure_stream_group(self.redis, STREAM_ACCEPTED, "merger_group")

    def _git(self, repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repo_path,
            text=True,
            capture_output=True,
            check=False,
        )

    def _git_checked(self, repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        result = self._git(repo_path, *args)
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise RuntimeError(f"git {' '.join(args)}: {stderr}")
        return result

    def _ensure_repo(self, repo_path: Path) -> str:
        repo_path.mkdir(parents=True, exist_ok=True)
        if not (repo_path / ".git").exists():
            self._git_checked(repo_path, "init", "-b", "main")
        if not self._git(repo_path, "config", "user.email").stdout.strip():
            self._git_checked(repo_path, "config", "user.email", "omega@example.local")
        if not self._git(repo_path, "config", "user.name").stdout.strip():
            self._git_checked(repo_path, "config", "user.name", "Kernel Omega")

        head = self._git(repo_path, "rev-parse", "--verify", "HEAD")
        if head.returncode != 0:
            self._git_checked(repo_path, "add", ".")
            self._git_checked(repo_path, "commit", "--allow-empty", "-m", "Initial repository state")

        current_branch = self._git(repo_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
        return current_branch

    def apply_patch(self, capsule_id: str, code: str, source_relpath: str, repo_path: str) -> None:
        repo = Path(repo_path).resolve()
        base_branch = self._ensure_repo(repo)
        branch = f"omega_repair_{capsule_id[-8:]}"
        self._git_checked(repo, "checkout", "-B", branch, base_branch)

        target = (repo / source_relpath).resolve()
        if repo not in target.parents and target != repo:
            raise ValueError(f"unsafe source_relpath outside repo: {source_relpath}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        self._git_checked(repo, "add", str(target))
        self._git_checked(repo, "commit", "-m", f"Kernel Omega repair for {capsule_id[-8:]}")
        self._git_checked(repo, "checkout", base_branch)

    def run(self) -> None:
        while True:
            entries = self.redis.xreadgroup(
                "merger_group",
                "merger_1",
                {STREAM_ACCEPTED: ">"},
                count=10,
                block=5000,
            )
            if not entries:
                continue

            pipe = self.redis.pipeline()
            for _, messages in entries:
                for message_id, message in messages:
                    merge_started = time.perf_counter()
                    try:
                        payload = json.loads(message["payload"])
                        accepted_key = tree_accepted_key(payload["tree_id"])
                        winning_capsule = self.redis.get(accepted_key)
                        if winning_capsule and winning_capsule != payload["capsule_id"]:
                            continue
                        if not winning_capsule and not self.redis.set(accepted_key, payload["capsule_id"], nx=True):
                            if self.redis.get(accepted_key) != payload["capsule_id"]:
                                continue
                        try:
                            self.apply_patch(
                                capsule_id=payload["capsule_id"],
                                code=payload["code"],
                                source_relpath=payload["source_relpath"],
                                repo_path=payload["repo_path"],
                            )
                            self.metadata.update_candidate_status(payload["capsule_id"], "merged")
                            self.metadata.update_task_status(payload["tree_id"], "merged")
                            self.metadata.merge_candidate_metadata(
                                payload["capsule_id"],
                                {"merge": {"duration_ms": (time.perf_counter() - merge_started) * 1000.0}},
                            )
                        except Exception as exc:
                            self.redis.delete(accepted_key)
                            self.metadata.update_candidate_status(payload["capsule_id"], "merge_failed")
                            self.metadata.merge_candidate_metadata(
                                payload["capsule_id"],
                                {
                                    "merge": {
                                        "duration_ms": (time.perf_counter() - merge_started) * 1000.0,
                                        "error": str(exc),
                                    }
                                },
                            )
                            print(f"[merge_failed] {payload['capsule_id']}: {exc}")
                    finally:
                        pipe.xack(STREAM_ACCEPTED, "merger_group", message_id)
            pipe.execute()


def main() -> None:
    parser = build_parser("Run the Kernel Omega merger daemon.")
    args = parser.parse_args()
    MergerService(profile=args.profile).run()


if __name__ == "__main__":
    main()
