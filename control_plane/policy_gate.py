from __future__ import annotations

import ast
import hashlib
import json
import os
import time
from pathlib import Path

from control_plane.contracts import VerificationPack
from control_plane.metadata_store import MetadataStore
from control_plane.redis_keys import STREAM_ACCEPTED, STREAM_RESULTS, tree_accepted_key
from control_plane.runtime import build_parser, ensure_stream_group, load_runtime
from control_plane.signatures import verify_pack


class PolicyGate:
    def __init__(self, profile: str) -> None:
        trusted_pubkey = self._load_trusted_pubkey()
        if not trusted_pubkey:
            raise RuntimeError("OMEGA_TRUSTED_PUBKEY must be set before starting policy_gate.")
        self.trusted_pubkey = trusted_pubkey
        self.config, self.redis = load_runtime(profile)
        self.metadata = MetadataStore(self.config)
        ensure_stream_group(self.redis, STREAM_RESULTS, "policy_group")

    def _load_trusted_pubkey(self) -> str | None:
        inline_key = os.getenv("OMEGA_TRUSTED_PUBKEY")
        if inline_key:
            return inline_key.strip()

        key_file = os.getenv("OMEGA_TRUSTED_PUBKEY_FILE")
        if not key_file:
            return None
        candidate = Path(key_file).expanduser()
        if not candidate.exists():
            raise RuntimeError(f"OMEGA_TRUSTED_PUBKEY_FILE does not exist: {candidate}")
        return candidate.read_text(encoding="utf-8").strip()

    def enforce_semantic_policy(self, code: str) -> bool:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False

        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None:
                        return False
                    if isinstance(handler.type, ast.Name) and handler.type.id in {"Exception", "BaseException"}:
                        return False
            if isinstance(node, ast.Attribute) and node.attr in {"skip", "xfail"}:
                return False
        return True

    def inputs_digest_matches(self, pack: VerificationPack, code: str) -> bool:
        return hashlib.sha256(code.encode("utf-8")).hexdigest() == pack.inputs_digest

    def run(self) -> None:
        while True:
            entries = self.redis.xreadgroup(
                "policy_group",
                "gate_1",
                {STREAM_RESULTS: ">"},
                count=50,
                block=5000,
            )
            if not entries:
                continue

            for _, messages in entries:
                pipe = self.redis.pipeline()
                for message_id, message in messages:
                    evaluation_started = time.perf_counter()
                    try:
                        pack = VerificationPack.from_json(message["payload"])
                        node_data = self.redis.hgetall(pack.capsule_id)
                        accepted = True
                        reasons: list[str] = []
                        code = ""

                        if not node_data:
                            accepted = False
                            reasons.append("missing_candidate")
                        elif self.config.policy.require_hardware_isolation and not pack.hardware_isolated:
                            accepted = False
                            reasons.append("hardware_isolation_required")
                        elif not (pack.success and pack.tests_failed == 0):
                            accepted = False
                            reasons.append("verification_failed")
                        elif not verify_pack(pack, self.trusted_pubkey):
                            accepted = False
                            reasons.append("bad_attestation")
                        else:
                            code = node_data.get("code", "")
                            if not self.inputs_digest_matches(pack, code):
                                accepted = False
                                reasons.append("inputs_digest_mismatch")
                            elif not self.enforce_semantic_policy(code):
                                accepted = False
                                reasons.append("semantic_policy_rejected")

                        if accepted:
                            accepted_key = tree_accepted_key(node_data["tree_id"])
                            reserved = self.redis.set(accepted_key, pack.capsule_id, nx=True)
                            current_winner = self.redis.get(accepted_key)
                            if not reserved and current_winner != pack.capsule_id:
                                accepted = False
                                reasons.append("tree_already_accepted")

                        if accepted:
                            payload = {
                                "capsule_id": pack.capsule_id,
                                "code": code,
                                "source_relpath": node_data.get("source_relpath", Path(node_data["source_path"]).name),
                                "repo_path": node_data["repo_path"],
                                "tree_id": node_data["tree_id"],
                                "artifact_uri": pack.artifact_uri,
                            }
                            pipe.xadd(STREAM_ACCEPTED, {"payload": json.dumps(payload, sort_keys=True)})
                            self.metadata.update_candidate_status(pack.capsule_id, "accepted")
                        else:
                            self.metadata.update_candidate_status(pack.capsule_id, "rejected_policy")
                        if node_data:
                            elapsed_ms = (time.perf_counter() - evaluation_started) * 1000.0
                            self.metadata.merge_candidate_metadata(
                                pack.capsule_id,
                                {
                                    "policy": {
                                        "accepted": accepted,
                                        "reasons": reasons or ["accepted"],
                                        "evaluation_ms": elapsed_ms,
                                    }
                                },
                            )
                            self.metadata.record_policy_decision(
                                candidate_id=pack.capsule_id,
                                accepted=accepted,
                                confidence=1.0 if accepted else 0.0,
                                risk_score=0.0 if accepted else 1.0,
                                reasons=reasons or ["accepted"],
                                requires_human_review=not accepted,
                            )
                    finally:
                        pipe.xack(STREAM_RESULTS, "policy_group", message_id)
                pipe.execute()


def main() -> None:
    parser = build_parser("Run the Kernel Omega policy gate.")
    args = parser.parse_args()
    PolicyGate(profile=args.profile).run()


if __name__ == "__main__":
    main()
