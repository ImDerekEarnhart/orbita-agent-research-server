from __future__ import annotations

import asyncio
import copy
import os
import shutil
from pathlib import Path

import pytest

from orbita_agent.config import AgentConfig
from orbita_agent.gateway import AgentGateway
from orbita_agent.mcp_server import build_mcp_server
from orbita_agent.semantic_evolution import (
    audit_representation,
    build_language_snapshot,
    content_hash,
    freeze_language_limit_certificate,
    render_frozen_language_limit_lean_source,
)

KERNEL = Path("C:/LeanBuilds/orbita_language_limit_lean_v0_1/orbita_language_limit_lean")


def _spec() -> dict:
    return {
        "name": "Four-state parity L0",
        "version": "L0",
        "primitives": [
            {
                "name": "observe_a",
                "kind": "observable",
                "inputs": ["state"],
                "output": "a",
                "semantics": {"field": "a"},
                "dependencies": [],
            }
        ],
        "observables": ["a"],
        "refusal_conditions": [],
        "unknown_conditions": [],
        "read_permissions": ["finite supplied states"],
        "write_permissions": [],
        "grounding_rules": ["exact finite lookup"],
        "invariants": ["finite scope only"],
    }


def _cases() -> list[dict]:
    return [
        {
            "world_id": f"{a}{b}",
            "state": {"a": a, "b": b},
            "language_view": {"a": a},
            "outcome": (a + b) % 2,
        }
        for a, b in [(0, 0), (0, 1), (1, 0), (1, 1)]
    ]


def test_frozen_envelope_and_translation_are_deterministic_and_finite():
    snapshot = build_language_snapshot(_spec())
    cases = _cases()
    audit = audit_representation(snapshot, cases)
    certificate = freeze_language_limit_certificate(
        snapshot,
        audit,
        cases,
        case_id="case_test",
        claim_id="clm_test",
        arithmetic_semantics={"distance": "absolute", "domain": "rational", "unit": "unitless"},
        provenance_hashes={"dataset": "a" * 64},
    )
    assert certificate["witness"]["left_world_id"] != certificate["witness"]["right_world_id"]
    assert certificate["witness"]["left_outcome"] != certificate["witness"]["right_outcome"]
    assert certificate["scope"]["universal_promotion_allowed"] is False
    first = render_frozen_language_limit_lean_source(certificate)
    second = render_frozen_language_limit_lean_source(certificate)
    assert first["generated_lean_hash"] == second["generated_lean_hash"]
    assert first["lean_source"] == second["lean_source"]
    assert "llm" not in first["lean_source"].lower()


def test_case_lifecycle_lean_accepts_and_mutations_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(home=tmp_path / "home", lean_kernel_root=KERNEL)
    with AgentGateway(config) as gateway:
        def accepted(rendered, certificate):
            body = {
                "schema": "orbita-language-limit-lean-verification-receipt/1",
                "source_json_hash": certificate["certificate_hash"],
                "generated_lean_hash": rendered["generated_lean_hash"],
                "theorem_kernel_version": "0.2.0-rc1",
                "kernel_manifest_sha256": certificate["kernel"]["manifest_sha256"],
                "lean_version": "Lean 4.32.1",
                "mathlib_version": "v4.32.1",
                "build_result": "accepted",
                "exit_code": 0,
                "stdout_sha256": "0" * 64,
                "stderr_sha256": "0" * 64,
                "scope_verified": "exactly_the_frozen_finite_worlds",
                "claim_status": "LEAN_VERIFIED_FINITE",
                "universal_theorem_claimed": False,
                "llm_prose_used_as_proof": False,
            }
            return body | {"receipt_hash": content_hash(body)}

        monkeypatch.setattr(gateway.language_limits, "_run_lean", accepted)
        case = gateway.create_case(name="Parity acceptance", goal="Find a finite representational hole")
        frozen = gateway.discover_and_freeze_language_limit(
            case_id=case["id"], snapshot_spec=_spec(), cases=_cases(), provenance_hashes={"input": "b" * 64}
        )
        assert frozen["status"] == "FROZEN_PENDING_LEAN"
        verified = gateway.verify_language_limit(certificate_hash=frozen["certificate"]["certificate_hash"])
        assert verified["status"] == "LEAN_VERIFIED_FINITE"
        assert verified["verification_receipt"]["build_result"] == "accepted"
        assert verified["verification_receipt"]["universal_theorem_claimed"] is False
        assert verified["verification_receipt"]["llm_prose_used_as_proof"] is False
        assert gateway.claim_history(verified["claim_id"])["current_epistemic_contract"]["evidence_status"] == (
            "EXHAUSTIVELY_VERIFIED_FINITE_DOMAIN"
        )

        other_case = gateway.create_case(name="Mutation", goal="Reject a changed certificate")
        other = gateway.discover_and_freeze_language_limit(
            case_id=other_case["id"], snapshot_spec=_spec(), cases=_cases()
        )
        changed = copy.deepcopy(other["certificate"])
        changed["witness"]["left_outcome"] = 99
        rejected = gateway.verify_language_limit(
            certificate_hash=other["certificate"]["certificate_hash"], certificate=changed
        )
        assert rejected["status"] == "LEAN_REJECTED"
        assert "differs from the frozen" in rejected["verification_receipt"]["error"]

        provenance_case = gateway.create_case(name="Provenance", goal="Reject changed provenance")
        provenance = gateway.discover_and_freeze_language_limit(
            case_id=provenance_case["id"], snapshot_spec=_spec(), cases=_cases()
        )
        rejected_provenance = gateway.verify_language_limit(
            certificate_hash=provenance["certificate"]["certificate_hash"],
            provenance_hashes={"source_cases_sha256": "0" * 64},
        )
        assert rejected_provenance["status"] == "LEAN_REJECTED"
        assert "provenance differs" in rejected_provenance["verification_receipt"]["error"]

        changed_case = gateway.create_case(name="Changed case files", goal="Reject post-freeze provenance changes")
        changed_files = gateway.discover_and_freeze_language_limit(
            case_id=changed_case["id"], snapshot_spec=_spec(), cases=_cases()
        )
        gateway.add_inline_file(case_id=changed_case["id"], filename="new-source.txt", content="changed after freeze")
        rejected_files = gateway.verify_language_limit(
            certificate_hash=changed_files["certificate"]["certificate_hash"]
        )
        assert rejected_files["status"] == "LEAN_REJECTED"
        assert "case file provenance changed" in rejected_files["verification_receipt"]["error"]


def test_missing_primitive_is_discovered_then_tested_once(tmp_path: Path):
    with AgentGateway(AgentConfig(home=tmp_path / "home")) as gateway:
        case = gateway.create_case(name="Parity repair", goal="Find P without naming P")
        proposal = gateway.propose_language_refinement(
            case_id=case["id"], snapshot_spec=_spec(), discovery_cases=_cases()
        )
        assert proposal["status"] == "FROZEN_PENDING_PROSPECTIVE_TEST"
        assert proposal["proposal"]["primitive"]["source_field"] == "b"
        result = gateway.evaluate_language_refinement(
            proposal_hash=proposal["proposal"]["proposal_hash"], evaluation_cases=_cases()
        )
        assert result["status"] == "PROSPECTIVE_REFINEMENT_SURVIVED"
        assert result["evaluation"]["delta_L0"] == {"numerator": 1, "denominator": 1}
        assert result["evaluation"]["delta_L1"] == {"numerator": 0, "denominator": 1}
        assert result["evaluation"]["universal_promotion_allowed"] is False
        with pytest.raises(ValueError, match="already been evaluated"):
            gateway.evaluate_language_refinement(
                proposal_hash=proposal["proposal"]["proposal_hash"], evaluation_cases=_cases()
            )


def test_complete_path_is_reachable_through_mcp_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with AgentGateway(AgentConfig(home=tmp_path / "home")) as gateway:
        case = gateway.create_case(name="MCP-only control", goal="Exercise the public tool path")

        def accepted(rendered, certificate):
            body = {
                "schema": "orbita-language-limit-lean-verification-receipt/1",
                "source_json_hash": certificate["certificate_hash"],
                "generated_lean_hash": rendered["generated_lean_hash"],
                "theorem_kernel_version": "0.2.0-rc1",
                "kernel_manifest_sha256": certificate["kernel"]["manifest_sha256"],
                "lean_version": "Lean 4.32.1",
                "mathlib_version": "v4.32.1",
                "build_result": "accepted",
                "exit_code": 0,
                "stdout_sha256": "0" * 64,
                "stderr_sha256": "0" * 64,
                "scope_verified": "exactly_the_frozen_finite_worlds",
                "claim_status": "LEAN_VERIFIED_FINITE",
                "universal_theorem_claimed": False,
                "llm_prose_used_as_proof": False,
            }
            return body | {"receipt_hash": content_hash(body)}

        monkeypatch.setattr(gateway.language_limits, "_run_lean", accepted)
        mcp, _ = build_mcp_server(gateway=gateway)
        _, frozen = asyncio.run(
            mcp.call_tool(
                "orbita_discover_and_freeze_language_limit",
                {"case_id": case["id"], "snapshot_spec": _spec(), "cases": _cases()},
            )
        )
        certificate_hash = frozen["certificate"]["certificate_hash"]
        _, verified = asyncio.run(
            mcp.call_tool("orbita_lean_verify_language_limit", {"certificate_hash": certificate_hash})
        )
        _, retrieved = asyncio.run(
            mcp.call_tool("orbita_get_language_limit_verification", {"certificate_hash": certificate_hash})
        )
        assert verified["status"] == "LEAN_VERIFIED_FINITE"
        assert retrieved["verification_receipt"]["receipt_hash"] == verified["verification_receipt"]["receipt_hash"]


@pytest.mark.skipif(
    os.getenv("ORBITA_RUN_LEAN_INTEGRATION") != "1" or not KERNEL.is_dir() or shutil.which("lake") is None,
    reason="set ORBITA_RUN_LEAN_INTEGRATION=1 with the local verified Lean kernel",
)
def test_real_lean_kernel_accepts_generated_certificate(tmp_path: Path):
    with AgentGateway(AgentConfig(home=tmp_path / "home", lean_kernel_root=KERNEL)) as gateway:
        case = gateway.create_case(name="Real Lean control", goal="Compile deterministic finite certificate")
        frozen = gateway.discover_and_freeze_language_limit(case_id=case["id"], snapshot_spec=_spec(), cases=_cases())
        verified = gateway.verify_language_limit(certificate_hash=frozen["certificate"]["certificate_hash"])
        assert verified["status"] == "LEAN_VERIFIED_FINITE"
