from __future__ import annotations

import sqlite3

import pytest

from orbita_agent.improvement_registry import ImprovementRegistry, content_hash


def _candidate(registry: ImprovementRegistry, **overrides):
    values = {
        "candidate_kind": "code_patch",
        "limitation_kind": "ENGINE_CAPABILITY_LIMIT",
        "base_artifact": {"git_commit": "abc123", "component": "runner"},
        "candidate_artifact": {"diff_hash": "1" * 64, "summary": "Add a typed external experiment adapter"},
        "problem_statement": "The generic table runner cannot execute universal mathematical candidates.",
        "rationale": "Preserve the candidate semantics and route it to deterministic execution.",
        "expected_benefit": "Engine inability will no longer be mislabeled as scientific falsification.",
        "observed_failure_ids": ["p-vs-np-exp-001"],
        "known_risks": ["A weak verifier could accept an invalid external verdict."],
        "created_by": "codex-builder",
    }
    values.update(overrides)
    return registry.create_candidate(**values)


def _plan():
    return {
        "objective": "Compare the unchanged baseline with the candidate on frozen semantic-routing cases.",
        "benchmark_case_ids": ["p-vs-np-exp-001"],
        "positive_controls": ["valid tabular association still runs"],
        "negative_controls": ["universal claim is never converted to correlation"],
        "metrics": ["semantic_route_accuracy", "global_governance_regressions"],
        "acceptance_gates": {"semantic_route_accuracy": 1.0, "global_governance_regressions": 0},
        "anti_rescue_rules": ["Do not edit expected outputs after evaluation starts."],
        "global_safety_checks": ["network disabled", "no policy activation"],
        "execution_contract": {"network": "none", "runner": "DerekX-compatible"},
        "independent_verifier": {"required": True, "implementation": "separate exact checker"},
    }


def test_candidate_hash_is_semantic_and_changes_with_artifact(tmp_path):
    first = ImprovementRegistry(tmp_path / "first.db")
    second = ImprovementRegistry(tmp_path / "second.db")
    try:
        candidate_a = _candidate(first)
        candidate_b = _candidate(second)
        candidate_c = _candidate(
            second,
            candidate_artifact={"diff_hash": "2" * 64, "summary": "Add a typed external experiment adapter"},
        )
        assert candidate_a["candidate_hash"] == candidate_b["candidate_hash"]
        assert candidate_a["candidate_hash"] != candidate_c["candidate_hash"]
        assert candidate_a["base_hash"] == content_hash(candidate_a["base_artifact"])
    finally:
        first.close()
        second.close()


def test_freeze_and_evaluation_are_exact_hash_bound_and_immutable(tmp_path):
    registry = ImprovementRegistry(tmp_path / "registry.db")
    try:
        candidate = _candidate(registry)
        frozen = registry.freeze_evaluation(candidate["id"], evaluation_plan=_plan(), frozen_by="benchmark-owner")
        with pytest.raises(ValueError, match="already frozen"):
            registry.freeze_evaluation(candidate["id"], evaluation_plan=_plan(), frozen_by="candidate-author")
        with pytest.raises(ValueError, match="Candidate hash mismatch"):
            registry.record_evaluation(
                candidate["id"],
                expected_candidate_hash="0" * 64,
                expected_plan_hash=frozen["plan_hash"],
                result={"semantic_route_accuracy": 1.0},
                verdict="survived",
                evaluated_by="independent-checker",
            )
        evaluation = registry.record_evaluation(
            candidate["id"],
            expected_candidate_hash=candidate["candidate_hash"],
            expected_plan_hash=frozen["plan_hash"],
            result={"semantic_route_accuracy": 1.0, "global_governance_regressions": 0},
            verdict="survived",
            evaluated_by="independent-checker",
        )
        assert evaluation["evaluation_hash"]
        assert registry.get_candidate(candidate["id"])["state"] == "evaluation_survived"
        with pytest.raises(ValueError, match="already been recorded"):
            registry.record_evaluation(
                candidate["id"],
                expected_candidate_hash=candidate["candidate_hash"],
                expected_plan_hash=frozen["plan_hash"],
                result={"semantic_route_accuracy": 0.0},
                verdict="refuted",
                evaluated_by="candidate-author",
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry.conn.execute(
                "UPDATE improvement_registry_plans SET plan_json = '{}' WHERE id = ?", (frozen["id"],)
            )
    finally:
        registry.close()


def test_general_candidate_cannot_self_promote_or_change_active_policy(gateway):
    active_before = gateway.improvement_status()["active_policy"]
    candidate = gateway.register_improvement_candidate(
        candidate_kind="ui_workflow",
        limitation_kind="PRODUCT_FRICTION",
        base_artifact={"surface": "guided"},
        candidate_artifact={"proposal": "show frozen evaluation receipts in plain language"},
        problem_statement="Users cannot currently inspect generalized improvement receipts in Guided Orbita.",
        rationale="A read-only receipt view would make governance understandable without granting activation power.",
        expected_benefit="Users can review exact evidence and limitations before authorizing any future action.",
        created_by="guided-designer",
    )
    with pytest.raises(PermissionError, match="cannot self-promote"):
        gateway.improvement_registry.promote(candidate["id"], actor="guided-designer")
    assert gateway.improvement_status()["active_policy"] == active_before
    assert gateway.get_governed_improvement(candidate["id"])["activation_enabled"] is False


def test_language_limit_fails_closed_without_formal_certificate(tmp_path):
    registry = ImprovementRegistry(tmp_path / "registry.db")
    try:
        with pytest.raises(ValueError, match="machine-checkable"):
            _candidate(registry, limitation_kind="LANGUAGE_LIMIT", evidence={"programs_searched": 100_000})
        valid = _candidate(
            registry,
            limitation_kind="LANGUAGE_LIMIT",
            evidence={
                "language_limit_certificate": {
                    "proof_path": "finite_enumeration",
                    "grammar_hash": "a" * 64,
                    "proof_artifact_hash": "b" * 64,
                    "checker_receipt_hash": "c" * 64,
                }
            },
        )
        assert valid["limitation_kind"] == "LANGUAGE_LIMIT"
    finally:
        registry.close()
