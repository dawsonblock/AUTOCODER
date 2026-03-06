from __future__ import annotations

import json
import subprocess
from pathlib import Path

from control_plane.redis_keys import STREAM_ACCEPTED, tree_accepted_key
from control_plane.runtime import build_parser, ensure_stream_group, load_runtime


class MergerService:
    def __init__(self, profile: str) -> None:
        self.config, self.redis = load_runtime(profile)
        ensure_stream_group(self.redis, STREAM_ACCEPTED, "merger_group")

    def _git(self, repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repo_path,
            text=True,
            capture_output=True,
            check=False,
        )

    def _ensure_repo(self, repo_path: Path) -> str:
        repo_path.mkdir(parents=True, exist_ok=True)
        if not (repo_path / ".git").exists():
            self._git(repo_path, "init", "-b", "main")
        if not self._git(repo_path, "config", "user.email").stdout.strip():
            self._git(repo_path, "config", "user.email", "omega@example.local")
        if not self._git(repo_path, "config", "user.name").stdout.strip():
            self._git(repo_path, "config", "user.name", "Kernel Omega")

        head = self._git(repo_path, "rev-parse", "--verify", "HEAD")
        if head.returncode != 0:
            self._git(repo_path, "add", ".")
            self._git(repo_path, "commit", "--allow-empty", "-m", "Initial repository state")

        current_branch = self._git(repo_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
        return current_branch

    def apply_patch(self, capsule_id: str, code: str, source_relpath: str, repo_path: str) -> None:
        repo = Path(repo_path).resolve()
        base_branch = self._ensure_repo(repo)
        branch = f"omega_repair_{capsule_id[-8:]}"
        self._git(repo, "checkout", "-B", branch, base_branch)

        target = (repo / source_relpath).resolve()
        if repo not in target.parents and target != repo:
            raise ValueError(f"unsafe source_relpath outside repo: {source_relpath}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        self._git(repo, "add", str(target))
        self._git(repo, "commit", "-m", f"Kernel Omega repair for {capsule_id[-8:]}")
        self._git(repo, "checkout", base_branch)

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
                    try:
                        payload = json.loads(message["payload"])
                        accepted_key = tree_accepted_key(payload["tree_id"])
                        if not self.redis.set(accepted_key, payload["capsule_id"], nx=True):
                            continue
                        try:
                            self.apply_patch(
                                capsule_id=payload["capsule_id"],
                                code=payload["code"],
                                source_relpath=payload["source_relpath"],
                                repo_path=payload["repo_path"],
                            )
                        except Exception:
                            self.redis.delete(accepted_key)
                            raise
                    finally:
                        pipe.xack(STREAM_ACCEPTED, "merger_group", message_id)
            pipe.execute()


def main() -> None:
    parser = build_parser("Run the Kernel Omega merger daemon.")
    args = parser.parse_args()
    MergerService(profile=args.profile).run()


if __name__ == "__main__":
    main()
