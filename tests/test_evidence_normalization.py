from __future__ import annotations

import copy
import sqlite3

import pytest

from orbita_agent.evidence_normalization import (
    EvidenceReceiptLedger,
    content_hash,
    external_experiment_receipt_input,
    genome_tournament_receipt_input,
)
from orbita_agent.gateway import APPROVAL_PHRASE


def _genome_payload(*, result_hash: str | None = None) -> dict:
    result = {"score": 1.0, "scope": "six frozen worlds"}
    return {
        "tournament": {
            "id": "tournament-1",
            "status": "frozen",
            "manifest_hash": "a" * 64,
            "target": {"world_ids": ["w1", "w2"]},
        },
        "entries": [
            {
                "id": "entry-1",
                "operator_id": "operator-1",
                "operator_contract_hash": "b" * 64,
                "prediction_hash": "c" * 64,
                "result_hash": result_hash or content_hash(
                    {
                        "schema": "orbita.discovery-tournament-result.v1",
                        "tournament_id": "tournament-1",
                        "entry_id": "entry-1",
                        "verdict": "survived",
                        "result": result,
                    }
                ),
                "verdict": "survived",
                "result_json": result,
                "independence_level": "cross_domain",
            }
        ],
    }


def test_genome_evidence_is_operator_eligible_but_never_activation_or_deployment(tmp_path):
    ledger = EvidenceReceiptLedger(tmp_path / "evidence.db")
    try:
        receipt = ledger.record(**genome_tournament_receipt_input(_genome_payload(), "entry-1"))
        assert receipt["eligibility"]["allowed_decisions"] == ["DISCOVERY_OPERATOR_REVIEW"]
        assert ledger.check_decision(receipt["id"], "DISCOVERY_OPERATOR_REVIEW")["eligible"] is True
        for decision in (
            "SEMANTIC_ADMISSION",
            "SEMANTIC_ACTIVATION",
            "POLICY_PROMOTION",
            "CODE_DEPLOYMENT",
            "ARCHITECTURE_ACTIVATION",
        ):
            result = ledger.check_decision(receipt["id"], decision)
            assert result["eligible"] is False
            assert result["requires_separate_authority"] is True
        assert ledger.verify(receipt["id"])["valid"] is True
    finally:
        ledger.close()


def test_receipts_are_idempotent_for_exact_source_and_fail_closed_on_changed_hashes(tmp_path):
    ledger = EvidenceReceiptLedger(tmp_path / "evidence.db")
    try:
        exact = genome_tournament_receipt_input(_genome_payload(), "entry-1")
        first = ledger.record(**exact)
        assert ledger.record(**exact)["id"] == first["id"]

        changed = copy.deepcopy(exact)
        changed["source_hashes"]["result_hash"] = "d" * 64
        with pytest.raises(ValueError, match="already normalized with different contents"):
            ledger.record(**changed)

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            ledger.conn.execute(
                "UPDATE normalized_evidence_receipts SET source_id = 'changed' WHERE id = ?",
                (first["id"],),
            )
    finally:
        ledger.close()


def test_unverified_and_same_source_evidence_cannot_claim_stronger_eligibility(tmp_path):
    ledger = EvidenceReceiptLedger(tmp_path / "evidence.db")
    try:
        receipt = ledger.record(
            source_kind="EXTERNAL_EXPERIMENT",
            source_id="experiment-1",
            source_hashes={"experiment_hash": "a" * 64},
            frozen_before_reveal=True,
            scope={
                "domain": "finite fixtures",
                "quantifier": "declared fixtures",
                "boundary": {"n_max": 8},
                "assumptions": [],
            },
            result={"status": "UNVERIFIED"},
            evaluator={"id": "same-runtime", "kind": "deterministic_runtime"},
            independence={
                "level": "same_execution",
                "subject_id": "experiment-1",
                "evaluator_id": "same-runtime",
                "rationale": "Execution integrity is not independent scientific verification.",
            },
            provenance={"experiment_id": "experiment-1"},
        )
        assert receipt["eligibility"]["allowed_decisions"] == []
        assert ledger.check_decision(receipt["id"], "REPAIR_CANDIDATE_REVIEW")["eligible"] is False

        with pytest.raises(ValueError, match="different evaluator and subject"):
            ledger.record(
                source_kind="INDEPENDENT_VERIFIER",
                source_id="verifier-1",
                source_hashes={"receipt_hash": "b" * 64},
                frozen_before_reveal=False,
                scope={
                    "domain": "fixture",
                    "quantifier": "one verification",
                    "boundary": {"id": "v1"},
                    "assumptions": [],
                },
                result={"status": "COMPLETED"},
                evaluator={"id": "verifier-1", "kind": "verifier"},
                independence={
                    "level": "external",
                    "subject_id": "verifier-1",
                    "evaluator_id": "verifier-1",
                    "rationale": "Invalid self-declared external independence.",
                },
                provenance={"id": "v1"},
            )
    finally:
        ledger.close()


def test_genome_adapter_rejects_unfrozen_or_unevaluated_records():
    draft = _genome_payload()
    draft["tournament"]["status"] = "draft"
    with pytest.raises(ValueError, match="frozen"):
        genome_tournament_receipt_input(draft, "entry-1")

    unevaluated = _genome_payload()
    unevaluated["entries"][0]["result_json"] = None
    with pytest.raises(ValueError, match="recorded result"):
        genome_tournament_receipt_input(unevaluated, "entry-1")

    tampered = _genome_payload()
    tampered["entries"][0]["result_hash"] = "d" * 64
    with pytest.raises(ValueError, match="result hash"):
        genome_tournament_receipt_input(tampered, "entry-1")


def test_verified_external_experiment_adapter_preserves_scoped_review_only(tmp_path):
    ledger = EvidenceReceiptLedger(tmp_path / "evidence.db")
    experiment = {
        "id": "experiment-verified",
        "experiment_hash": "a" * 64,
        "claim_scope": {
            "domain": "simple graphs",
            "quantifiers": "all declared finite fixtures",
            "bound": "n <= 8",
            "assumptions": ["simple undirected graphs"],
        },
        "execution": {
            "id": "run-1",
            "status": "succeeded",
            "manifest_hash": "b" * 64,
            "receipt_hash": "c" * 64,
        },
        "integrity_status": "VERIFIED",
        "execution_classification": "EXECUTED",
        "epistemic_status": "EMPIRICAL_SURVIVOR",
        "independent_verifier": {"required": True, "independence_level": "external"},
        "verification": {
            "verifier": {"id": "external-checker"},
            "verifier_receipt_hash": "d" * 64,
        },
    }
    try:
        receipt = ledger.record(**external_experiment_receipt_input(experiment))
        assert receipt["scope"]["boundary"] == {"bound": "n <= 8"}
        assert receipt["independence"]["level"] == "external"
        assert set(receipt["eligibility"]["allowed_decisions"]) == {
            "REPAIR_CANDIDATE_REVIEW",
            "SCIENTIFIC_CLAIM_REVIEW",
        }
        assert ledger.check_decision(receipt["id"], "SEMANTIC_ADMISSION")["eligible"] is False
    finally:
        ledger.close()


def test_actual_completed_discovery_run_normalizes_and_is_tenant_scoped(gateway, sample_csv):
    case = gateway.create_case(name="Normalized evidence", goal="Run one finite discovery")
    gateway.add_inline_file(case_id=case["id"], filename="data.csv", content=sample_csv)
    plan = gateway.compile_plan(case["id"])
    gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="evidence-owner",
        confirmation=APPROVAL_PHRASE,
    )
    run = gateway.run_discovery(case["id"], plan_id=plan["id"])
    receipt = gateway.normalize_discovery_run_evidence(case["id"], run["id"])

    assert receipt["source_kind"] == "DISCOVERY_RUN"
    assert receipt["source_id"] == run["id"]
    assert gateway.verify_normalized_evidence(receipt["id"])["valid"] is True
    assert gateway.check_evidence_eligibility(receipt["id"], "RESEARCH_POLICY_REVIEW")["eligible"] is True
    assert gateway.check_evidence_eligibility(receipt["id"], "CODE_DEPLOYMENT")["eligible"] is False

    stranger = gateway.for_tenant("evidence-stranger")
    assert stranger.list_normalized_evidence() == []
    with pytest.raises(KeyError):
        stranger.get_normalized_evidence(receipt["id"])
