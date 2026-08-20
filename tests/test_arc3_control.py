from __future__ import annotations

from copy import deepcopy

import pytest

from orbita_agent.arc3_control import run_synthetic_arc3_control, verify_receipt_chain


def test_arc3_control_discovers_and_prospectively_tests_missing_primitive(tmp_path):
    artifact = run_synthetic_arc3_control(tmp_path)

    assert artifact["summary"]["levels_completed"] == 3
    assert artifact["summary"]["counterexample_count"] >= 1
    assert artifact["summary"]["proposed_primitive"] == "relative_position"
    assert artifact["summary"]["prospective_refinement_survived"] is True
    assert artifact["refinement_evaluation"]["delta_L1"] == {"numerator": 0, "denominator": 1}
    assert artifact["summary"]["score_accessed"] is False
    assert artifact["summary"]["llm_prose_used_as_proof"] is False
    assert verify_receipt_chain(artifact)["valid"] is True
    assert artifact["artifact_path"]


def test_every_environment_action_has_a_prior_frozen_prediction():
    artifact = run_synthetic_arc3_control()
    receipts = artifact["receipts"]
    predictions = {
        item["payload"]["action_id"]: item["sequence"]
        for item in receipts
        if item["kind"] == "prediction_frozen"
    }

    observed = [item for item in receipts if item["kind"] == "action_observed"]
    assert observed
    assert all(predictions[item["payload"]["action_id"]] < item["sequence"] for item in observed)


def test_mutated_receipt_fails_closed():
    artifact = run_synthetic_arc3_control()
    mutated = deepcopy(artifact)
    prediction = next(item for item in mutated["receipts"] if item["kind"] == "prediction_frozen")
    prediction["payload"]["predicted_progress"] = 99

    with pytest.raises(ValueError, match="receipt hash"):
        verify_receipt_chain(mutated)


def test_control_is_deterministic():
    left = run_synthetic_arc3_control()
    right = run_synthetic_arc3_control()

    assert left["artifact_hash"] == right["artifact_hash"]
    assert left["receipts"] == right["receipts"]


def test_gateway_persists_control_idempotently(gateway):
    first = gateway.run_arc3_synthetic_control()
    second = gateway.run_arc3_synthetic_control()

    assert first["artifact_hash"] == second["artifact_hash"]
    assert first["artifact_path"] == second["artifact_path"]
    verification = gateway.verify_arc3_control(first)
    assert verification["valid"] is True
    assert verification["server_bound"] is True
    assert verification["verification_mode"] == "SERVER_BOUND_PERSISTED_SYNTHETIC_ARTIFACT"
    assert verification["official_arc_execution_proved"] is False


def test_gateway_rejects_self_consistent_but_unpersisted_artifact(gateway):
    artifact = run_synthetic_arc3_control(control_variant="no-language-hole")

    assert verify_receipt_chain(artifact)["valid"] is True
    with pytest.raises(ValueError, match="not bound to this tenant"):
        gateway.verify_arc3_control(artifact)


def test_negative_control_preserves_refusal_artifact(tmp_path):
    artifact = run_synthetic_arc3_control(tmp_path, control_variant="no-language-hole")

    assert artifact["summary"]["status"] == "CONTROL_REFUSED"
    assert artifact["summary"]["language_limit_detected"] is False
    assert artifact["summary"]["prospective_refinement_survived"] is False
    assert artifact["summary"]["refusal_reasons"]
    assert artifact["receipts"][-1]["kind"] == "control_refused"
    assert verify_receipt_chain(artifact)["valid"] is True
    assert artifact["artifact_path"]


def test_unknown_control_variant_fails_closed():
    with pytest.raises(ValueError, match="unsupported synthetic ARC control variant"):
        run_synthetic_arc3_control(control_variant="made-up")
