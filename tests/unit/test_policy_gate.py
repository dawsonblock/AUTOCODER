import nacl.signing

from control_plane.contracts import VerificationPack
from control_plane.policy_gate import PolicyGate
from control_plane.signatures import sign_pack, verify_pack


def test_signature_round_trip() -> None:
    signing_key = nacl.signing.SigningKey.generate()
    pack = VerificationPack(
        capsule_id="omega:tree:task:deadbeef",
        success=True,
        runtime=0.02,
        node_id="sim-core-0",
        hardware_isolated=False,
        inputs_digest="abc123",
        tests_passed=1,
        tests_failed=0,
    )
    sign_pack(pack, signing_key)

    assert verify_pack(pack, signing_key.verify_key.encode().hex())


def test_signature_rejects_tampered_trust_fields() -> None:
    signing_key = nacl.signing.SigningKey.generate()
    pack = VerificationPack(
        capsule_id="omega:tree:task:deadbeef",
        success=True,
        runtime=0.02,
        node_id="sim-core-0",
        hardware_isolated=True,
        inputs_digest="abc123",
        tests_passed=3,
        tests_failed=0,
    )
    sign_pack(pack, signing_key)
    pack.tests_failed = 1

    assert not verify_pack(pack, signing_key.verify_key.encode().hex())


def test_policy_gate_rejects_broad_exception_handler() -> None:
    gate = PolicyGate.__new__(PolicyGate)

    assert not gate.enforce_semantic_policy(
        "try:\n    do_work()\nexcept Exception:\n    pass\n",
    )
    assert gate.enforce_semantic_policy("assert value == 1\n")


def test_policy_gate_rejects_digest_mismatch() -> None:
    gate = PolicyGate.__new__(PolicyGate)
    signing_key = nacl.signing.SigningKey.generate()
    pack = VerificationPack(
        capsule_id="omega:tree:task:deadbeef",
        success=True,
        runtime=0.02,
        node_id="sim-core-0",
        hardware_isolated=False,
        inputs_digest="abc123",
        tests_passed=3,
        tests_failed=0,
    )
    sign_pack(pack, signing_key)

    assert not gate.inputs_digest_matches(pack, "assert True\n")
