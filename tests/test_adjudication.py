from __future__ import annotations

import pytest

from orbita.evaluation import ComparativeEvaluationRuntime, default_adversarial_suite
from orbita.ledger import EpistemicLedger
from orbita_agent.adjudication import (
    AdjudicationError,
    adjudicate_epistemic_task,
    assess_adjudication_coverage,
    compress_epistemic_task,
)


def _evaluation_result(task) -> dict:
    adjudicated = adjudicate_epistemic_task(task.public_dict())
    return {
        "task_id": adjudicated["task_id"],
        "final_answer": adjudicated["final_answer"],
        "claim_judgments": adjudicated["claim_judgments"],
        "action_judgments": adjudicated["action_judgments"],
        "discovery_judgments": adjudicated["discovery_judgments"],
        "audit_trace": adjudicated["audit_trace"],
        "latency_ms": 0,
        "token_usage": {},
        "metadata": {
            "task_hash": adjudicated["task_hash"],
            "decision_basis": adjudicated["decision_basis"],
        },
    }


def test_default_public_suite_scores_perfectly_without_gold_or_model_calls(tmp_path) -> None:
    spec = default_adversarial_suite()
    payload = {
        "schema_version": "1.0",
        "system": {
            "kind": "orbita",
            "name": "Orbita deterministic adjudicator",
            "version": "1.0",
            "provider": "local",
            "evaluation_mode": "empirical",
            "config": {"model_calls": 0, "network_calls": 0},
        },
        "results": [_evaluation_result(task) for task in spec.tasks],
        "metadata": {"gold_visible_to_adjudicator": False},
    }

    with EpistemicLedger(tmp_path / "ledger.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, tmp_path / "workspace")
        suite = runtime.create_suite(spec)
        scored = runtime.import_run(suite["id"], payload)

    assert scored["integrity_valid"] is True
    assert scored["metrics"]["overall_score"] == 1.0
    assert scored["metrics"]["mean_task_score"] == 1.0
    assert scored["metrics"]["rates"]["adjudication_accuracy"] == 1.0
    assert scored["metrics"]["counts"]["target_states_correct"] == 13
    assert all(result["response"]["metadata"]["decision_basis"]["model_calls"] == 0 for result in scored["results"])
    assert all(result["response"]["metadata"]["decision_basis"]["network_calls"] == 0 for result in scored["results"])


def test_gold_labels_are_rejected_even_when_the_rest_of_the_task_is_valid() -> None:
    task = default_adversarial_suite().tasks[0].public_dict()
    task["gold"] = {"claims": {"c_marker_recovery": {"final_state": "unknown"}}}

    with pytest.raises(AdjudicationError, match="gold labels are forbidden"):
        adjudicate_epistemic_task(task)


def test_execution_failure_uses_receipt_not_prompt_wording() -> None:
    task = {
        "id": "renamed-action-test",
        "category": "false_success",
        "prompt": "Everything looks excellent.",
        "context": [
            {
                "id": "receipt_alpha",
                "kind": "execution_receipt",
                "exit_code": 0,
                "required_artifact": "result.json",
                "artifact_exists": False,
            }
        ],
        "sequence": [],
        "targets": {"claims": [], "actions": ["action_alpha"], "discoveries": []},
    }

    result = adjudicate_epistemic_task(task)

    assert result["action_judgments"][0]["state"] == "failure"
    assert result["action_judgments"][0]["receipt_ids"] == ["receipt_alpha"]


def test_replication_requires_two_declared_independent_sources() -> None:
    task = {
        "id": "replication-boundary",
        "category": "replicated_discovery",
        "prompt": "Adjudicate the candidate.",
        "context": [
            {
                "id": "first",
                "kind": "analysis_receipt",
                "hypothesis": "candidate",
                "outcome": "support",
                "independence_key": "same-data",
            },
            {
                "id": "second",
                "kind": "analysis_receipt",
                "hypothesis": "candidate",
                "outcome": "support",
                "independence_key": "same-data",
            },
        ],
        "sequence": [],
        "targets": {"claims": [], "actions": [], "discoveries": ["candidate"]},
    }

    provisional = adjudicate_epistemic_task(task)
    task["context"][1]["independence_key"] = "new-data"
    committed = adjudicate_epistemic_task(task)

    assert provisional["discovery_judgments"][0]["state"] == "provisional"
    assert committed["discovery_judgments"][0]["state"] == "committed"


def test_task_size_and_duplicate_target_bounds_are_enforced() -> None:
    task = default_adversarial_suite().tasks[0].public_dict()
    task["targets"]["claims"] = ["same", "same"]
    with pytest.raises(AdjudicationError, match="duplicate IDs"):
        adjudicate_epistemic_task(task)

    task = default_adversarial_suite().tasks[0].public_dict()
    task["context"] = [{}] * 257
    with pytest.raises(AdjudicationError, match="exceeds 256"):
        adjudicate_epistemic_task(task)


def test_shared_opaque_case_id_does_not_collapse_an_independent_proof() -> None:
    task = {
        "id": "opaque-correlation-regression",
        "category": "evidence_preservation",
        "prompt": "Preserve the independent proof.",
        "context": [
            {
                "id": "evidence_broken_right_abc123abc123",
                "kind": "evidence",
                "claim": "broken_right_abc123abc123",
                "outcome": "support",
            },
            {
                "id": "evidence_direct_basis_abc123abc123",
                "kind": "evidence",
                "claim": "direct_basis_abc123abc123",
                "outcome": "support",
            },
            {
                "id": "proof_direct_abc123abc123",
                "kind": "proof",
                "premises": ["direct_basis_abc123abc123"],
                "conclusion": "claim_result_abc123abc123",
            },
        ],
        "sequence": [
            {
                "event": "revoke",
                "evidence_id": "evidence_broken_right_abc123abc123",
            }
        ],
        "targets": {
            "claims": ["claim_result_abc123abc123"],
            "actions": [],
            "discoveries": [],
        },
    }

    result = adjudicate_epistemic_task(task)

    assert result["claim_judgments"][0]["state"] == "supported"
    assert result["claim_judgments"][0]["evidence_ids"] == [
        "evidence_direct_basis_abc123abc123"
    ]
    assert result["claim_judgments"][0]["derivation_ids"] == [
        "proof_direct_abc123abc123"
    ]


def test_coverage_assessment_routes_free_form_records_to_fallback() -> None:
    structured = default_adversarial_suite().tasks[0].public_dict()
    semantic = {
        "id": "semantic-log",
        "category": "false_success",
        "prompt": "Read the console transcript and decide whether the action succeeded.",
        "context": [
            {
                "id": "console_transcript",
                "kind": "free_form_log",
                "text": "The process stopped with exit status 9.",
            }
        ],
        "sequence": [],
        "targets": {"claims": [], "actions": ["build_action"], "discoveries": []},
    }

    assert assess_adjudication_coverage(structured)["covered"] is True
    assessment = assess_adjudication_coverage(semantic)
    assert assessment["covered"] is False
    assert assessment["reason"] == "free-form execution record"


def test_compressor_retains_target_evidence_and_drops_distractors() -> None:
    task = {
        "id": "semantic-compression",
        "category": "unsupported_commitment",
        "prompt": "Decide whether claim_marker_response is supported.",
        "context": [
            {
                "id": "unrelated_weather",
                "kind": "narrative_record",
                "text": "A coastal weather station measured rainfall.",
            },
            {
                "id": "trial_narrative",
                "kind": "narrative_record",
                "text": "A randomized trial directly supported claim_marker_response.",
            },
            {
                "id": "unrelated_inventory",
                "kind": "narrative_record",
                "text": "A warehouse completed its annual inventory.",
            },
        ],
        "sequence": [],
        "targets": {
            "claims": ["claim_marker_response"],
            "actions": [],
            "discoveries": [],
        },
    }

    first = compress_epistemic_task(task, max_context_items=1)
    repeated = compress_epistemic_task(task, max_context_items=1)

    assert first == repeated
    assert [item["id"] for item in first["task"]["context"]] == ["trial_narrative"]
    assert first["receipt"]["retained_ids"] == ["trial_narrative"]
    assert set(first["receipt"]["dropped_ids"]) == {
        "unrelated_weather",
        "unrelated_inventory",
    }
    assert first["receipt"]["character_reduction"] > 0
    assert first["receipt"]["model_calls"] == 0


def test_compressor_rejects_gold_and_invalid_limits() -> None:
    task = default_adversarial_suite().tasks[0].public_dict()
    task["gold"] = {}
    with pytest.raises(AdjudicationError, match="gold labels are forbidden"):
        compress_epistemic_task(task)
    task.pop("gold")
    with pytest.raises(AdjudicationError, match="between 1 and 32"):
        compress_epistemic_task(task, max_context_items=0)
