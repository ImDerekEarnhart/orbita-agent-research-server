from __future__ import annotations

import pytest

from orbita import (
    EpistemicLedger,
    EvidenceStatus,
    discovery_evidence_status,
    guard_scope_escalation,
    warranted_status,
)
from orbita_mvp.memory import BeliefMemory


def _finite_scope(*, rows: int = 100) -> dict:
    return {
        "domain": "uploaded_table",
        "quantifier": "observed_rows",
        "boundary": {"rows": rows},
        "assumptions": ["rows are independent"],
        "computational_model": "held-out screen",
        "representation_language": "linear association",
    }


def test_supported_discovery_is_empirical_survivor_not_proof() -> None:
    assert discovery_evidence_status("supported") is EvidenceStatus.EMPIRICAL_SURVIVOR


def test_one_counterexample_is_refutation_even_after_many_survivals() -> None:
    statuses = [discovery_evidence_status("supported") for _ in range(17_000)]
    statuses.append(discovery_evidence_status("refuted"))
    assert statuses[-1] is EvidenceStatus.REFUTED
    assert EvidenceStatus.FORMALLY_PROVED not in statuses


def test_sample_evidence_cannot_escalate_to_universal_claim() -> None:
    decision = guard_scope_escalation(
        _finite_scope(),
        {
            "domain": "uploaded_table",
            "quantifier": "all",
            "boundary": {},
            "assumptions": ["rows are independent"],
        },
    )
    assert decision["allowed"] is False
    assert decision["decision"] == "REJECT_SCOPE_ESCALATION"
    assert "sample or bounded evidence cannot support a universal claim" in decision["reasons"]


def test_execution_receipt_does_not_authorize_formal_proof() -> None:
    with pytest.raises(ValueError, match="formal proof receipt"):
        warranted_status(
            EvidenceStatus.FORMALLY_PROVED,
            claim_scope=_finite_scope(),
            coverage={"execution_receipt": "sha256:ran-successfully"},
        )


def test_bounded_verification_requires_a_declared_boundary() -> None:
    with pytest.raises(ValueError, match="explicit boundary"):
        warranted_status(
            EvidenceStatus.BOUNDED_VERIFIED,
            claim_scope={"domain": "graphs", "quantifier": "bounded_all"},
            coverage={"exhaustive": True},
        )


def test_epistemic_contract_is_append_only_and_reconstructed(tmp_path) -> None:
    ledger = EpistemicLedger(tmp_path / "ledger.db")
    memory = BeliefMemory(ledger)
    claim_id, _ = memory.resolve_or_create_claim("x predicts y", scope=_finite_scope())
    event = memory.record_epistemic_contract(
        claim_id,
        evidence_status=EvidenceStatus.EMPIRICAL_SURVIVOR,
        claim_scope=_finite_scope(),
        falsification_coverage={
            "tested_domains": ["held-out rows"],
            "known_uncovered_regions": ["external datasets"],
            "execution_receipt": "run_1",
        },
        reason="Survived the declared finite attacks.",
        source_run_id="run_1",
    )
    history = memory.reconstruct_history(claim_id)
    assert history["current_epistemic_contract"]["evidence_status"] == "EMPIRICAL_SURVIVOR"
    assert history["current_epistemic_contract"]["falsification_coverage"][
        "known_uncovered_regions"
    ] == ["external datasets"]
    with pytest.raises(Exception, match="immutable"):
        ledger.db.conn.execute(
            "UPDATE claim_epistemic_events SET reason = 'rewritten' WHERE id = ?",
            (event["event_id"],),
        )


def test_legacy_unstructured_scope_cannot_authorize_escalation() -> None:
    decision = guard_scope_escalation(
        {"kind": "correlation", "predictor": "x", "outcome": "y"},
        {"domain": "graphs", "quantifier": "all"},
    )
    assert decision["allowed"] is False
    assert "scope is legacy or incomplete" in decision["reasons"]
