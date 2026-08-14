from __future__ import annotations

import pytest

from orbita_agent.semantic_evolution import (
    TRANSITION_PHRASE,
    audit_representation,
    audit_temporal_unaskability,
    build_capability_component_graph,
    build_language_limit_certificate,
    build_language_snapshot,
    build_repair_candidate,
    content_hash,
    materialize_authorized_transition,
)


def _snapshot():
    return build_language_snapshot(
        {
            "name": "Temporal language",
            "version": "L0",
            "primitives": [
                {
                    "name": "instantaneous_value",
                    "kind": "observable",
                    "inputs": ["scalar"],
                    "output": "x_t",
                    "semantics": {"operator": "current_value"},
                    "dependencies": [],
                }
            ],
            "observables": ["x_t"],
            "refusal_conditions": ["history is required but unavailable"],
            "unknown_conditions": ["outcome is not identified"],
            "read_permissions": ["visible observations"],
            "write_permissions": [],
            "grounding_rules": ["every observable has executable semantics"],
            "invariants": ["UNKNOWN != FALSE", "candidate cannot approve itself"],
        }
    )


def _audit(snapshot):
    return audit_representation(
        snapshot,
        [
            {"world_id": "low-to-high", "language_view": {"x_t": 0.51}, "outcome": "low_regime"},
            {"world_id": "high-to-low", "language_view": {"x_t": 0.51}, "outcome": "high_regime"},
            {
                "world_id": "nuisance-a",
                "language_view": {"x_t": 0.1, "sensor": "a"},
                "outcome": "low_regime",
                "nuisance_class": "same-low-state",
            },
            {
                "world_id": "nuisance-b",
                "language_view": {"x_t": 0.1, "sensor": "b"},
                "outcome": "low_regime",
                "nuisance_class": "same-low-state",
            },
        ],
    )


def _candidate(snapshot, audit):
    certificate = build_language_limit_certificate(
        snapshot,
        audit,
        proof_path="finite_enumeration",
        proof_artifact_hash="a" * 64,
        checker_receipt_hash="b" * 64,
    )
    return build_repair_candidate(
        snapshot,
        certificate,
        primitive={
            "name": "persistent_state",
            "kind": "temporal_transform",
            "inputs": ["scalar_sequence"],
            "output": "persistent_state_t",
            "semantics": {"operator": "state_inertia", "alpha": 0.1, "eta": 0.2},
            "dependencies": ["instantaneous_value"],
        },
        predicted_resolved_collisions=["low-to-high vs high-to-low"],
        predicted_unchanged_cases=["static-low-control"],
        predicted_new_failures=["unstable numerical parameters"],
        minimality_claim="One temporal state is added; existing primitive semantics remain unchanged.",
    )


def test_snapshot_is_canonical_hash_bound_and_inactive():
    snapshot = _snapshot()
    assert snapshot["snapshot_hash"]
    assert snapshot["active"] is False
    reordered = _snapshot()
    assert reordered["snapshot_hash"] == snapshot["snapshot_hash"]


def test_audit_finds_exact_collision_and_candidate_overseparation():
    audit = _audit(_snapshot())
    assert audit["verdict"] == "LANGUAGE_LIMIT_WITNESS"
    assert audit["collisions"][0]["world_ids"] == ["low-to-high", "high-to-low"]
    assert audit["overseparations"][0]["nuisance_class"] == "same-low-state"
    assert audit["scope"] == "finite_cases_only"


def test_certificate_requires_a_real_collision_and_exact_snapshot_binding():
    snapshot = _snapshot()
    no_hole = audit_representation(
        snapshot,
        [{"world_id": "a", "language_view": {"x": 0}, "outcome": 0}, {"world_id": "b", "language_view": {"x": 1}, "outcome": 1}],
    )
    with pytest.raises(ValueError, match="collision witness"):
        build_language_limit_certificate(
            snapshot, no_hole, proof_path="finite_enumeration", proof_artifact_hash="a" * 64, checker_receipt_hash="b" * 64
        )
    audit = _audit(snapshot)
    changed = dict(snapshot, version="tampered")
    with pytest.raises(ValueError, match="snapshot hash"):
        build_language_limit_certificate(
            changed, audit, proof_path="finite_enumeration", proof_artifact_hash="a" * 64, checker_receipt_hash="b" * 64
        )


def test_transition_requires_survived_hash_bound_evaluation_and_exact_human_phrase():
    snapshot = _snapshot()
    candidate = _candidate(snapshot, _audit(snapshot))
    evaluation_body = {"candidate_hash": candidate["candidate_hash"], "verdict": "survived", "held_out_accuracy": 1.0}
    evaluation = evaluation_body | {"evaluation_hash": content_hash(evaluation_body)}
    with pytest.raises(PermissionError, match="confirmation"):
        materialize_authorized_transition(
            snapshot,
            candidate,
            evaluation,
            expected_candidate_hash=candidate["candidate_hash"],
            expected_evaluation_hash=evaluation["evaluation_hash"],
            authorized_by="Derek",
            confirmation="approve",
            new_version="L1",
        )
    result = materialize_authorized_transition(
        snapshot,
        candidate,
        evaluation,
        expected_candidate_hash=candidate["candidate_hash"],
        expected_evaluation_hash=evaluation["evaluation_hash"],
        authorized_by="Derek",
        confirmation=TRANSITION_PHRASE,
        new_version="L1",
    )
    assert result["new_snapshot"]["parent_snapshot_hash"] == snapshot["snapshot_hash"]
    assert result["new_snapshot"]["active"] is False
    assert result["production_runtime_changed"] is False
    assert result["transition_receipt"]["runtime_activation"] == "disabled"


def test_component_graph_connects_outputs_and_failure_mode_needs_without_claiming_success():
    graph = build_capability_component_graph(
        [
            {
                "id": "unaskable",
                "type": "representation_expander",
                "inputs": ["hypotheses"],
                "outputs": ["new_observable"],
                "capabilities": ["detect_when_nonlinear_memory_is_warranted"],
                "needs": [],
                "failure_modes": ["cannot itself admit a primitive"],
                "assumptions": ["competing hypotheses exist"],
                "falsifiers": ["null control produces a hole"],
            },
            {
                "id": "state-inertia",
                "type": "temporal_transform",
                "inputs": ["new_observable"],
                "outputs": ["persistent_state"],
                "capabilities": ["history_sensitive_state"],
                "needs": ["detect_when_nonlinear_memory_is_warranted"],
                "failure_modes": ["may not beat linear memory"],
                "assumptions": ["ordered sequence"],
                "falsifiers": ["held-out baseline dominance"],
            },
        ]
    )
    kinds = {(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]}
    assert ("unaskable", "state-inertia", "dataflow") in kinds
    assert ("unaskable", "state-inertia", "limitation_resolution") in kinds
    assert graph["claim_scope"] == "interface_matches_only"


def test_temporal_unaskability_compares_state_inertia_with_ordinary_memory_baselines():
    result = audit_temporal_unaskability(
        [
            {"world_id": "low-history", "values": [-1, -1, -1, -1, 0.51], "outcome": "low"},
            {"world_id": "high-history", "values": [1, 1, 1, 1, 0.51], "outcome": "high"},
        ],
        [
            {"name": "present only", "operator": "current_value", "parameters": {}},
            {"name": "EWMA", "operator": "ewma", "parameters": {"alpha": 0.2}},
            {"name": "linear memory", "operator": "linear_recurrence", "parameters": {"decay": 0.8, "gain": 0.2}},
            {"name": "State-Inertia", "operator": "state_inertia", "parameters": {"alpha": 0.1, "eta": 0.2}},
        ],
    )
    by_name = {item["name"]: item for item in result["candidate_results"]}
    assert by_name["present only"]["unresolved_collision_count"] == 1
    assert by_name["EWMA"]["resolved_collision_count"] == 1
    assert by_name["linear memory"]["resolved_collision_count"] == 1
    assert by_name["State-Inertia"]["resolved_collision_count"] == 1
    assert result["admission_decision"] == "none"
    assert result["scope"] == "finite_fixed_parameter_diagnostic"


def test_temporal_unaskability_rejects_unknown_code_like_operators():
    with pytest.raises(ValueError, match="unsupported temporal operator"):
        audit_temporal_unaskability(
            [
                {"world_id": "a", "values": [0, 1], "outcome": "x"},
                {"world_id": "b", "values": [2, 1], "outcome": "y"},
            ],
            [{"name": "arbitrary", "operator": "python_eval", "parameters": {"code": "open('secret')"}}],
        )
