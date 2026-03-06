from __future__ import annotations

import base64
import hashlib
import json

import nacl.exceptions
import nacl.signing

from control_plane.contracts import VerificationPack


def canonical_signature_bytes(pack: VerificationPack) -> bytes:
    core = {
        "capsule_id": pack.capsule_id,
        "error_signature": pack.error_signature,
        "hardware_isolated": pack.hardware_isolated,
        "inputs_digest": pack.inputs_digest,
        "success": pack.success,
        "tests_failed": pack.tests_failed,
        "tests_passed": pack.tests_passed,
    }
    return json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_pack(pack: VerificationPack, signing_key: nacl.signing.SigningKey) -> VerificationPack:
    digest = hashlib.sha256(canonical_signature_bytes(pack)).digest()
    signature = signing_key.sign(digest).signature
    pack.attestation = base64.b64encode(signature).decode("ascii")
    return pack


def verify_pack(pack: VerificationPack, public_key_hex: str) -> bool:
    if not pack.attestation:
        return False
    digest = hashlib.sha256(canonical_signature_bytes(pack)).digest()
    verifier = nacl.signing.VerifyKey(bytes.fromhex(public_key_hex))
    try:
        verifier.verify(digest, base64.b64decode(pack.attestation))
    except nacl.exceptions.BadSignatureError:
        return False
    return True
