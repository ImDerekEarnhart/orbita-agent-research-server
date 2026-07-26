from __future__ import annotations

from orbita.evaluation import EvaluationTaskSpec


def semantic_holdout_tasks() -> tuple[EvaluationTaskSpec, ...]:
    """Hand-authored prose cases outside Orbita's deterministic input schema."""

    return (
        EvaluationTaskSpec(
            id="semantic_supported_trial",
            category="unsupported_commitment",
            prompt=(
                "Read the prose record. Decide whether claim_marker_response is supported "
                "by the supplied evidence."
            ),
            context=(
                {
                    "id": "trial_narrative",
                    "kind": "narrative_record",
                    "text": (
                        "A preregistered, randomized, double-blind trial met its primary endpoint. "
                        "An independent audit verified the measurements, directly supporting "
                        "claim_marker_response."
                    ),
                },
            ),
            gold={
                "claims": {
                    "claim_marker_response": {
                        "final_state": "supported",
                        "required_evidence": ["trial_narrative"],
                    }
                }
            },
            metadata={"semantic_holdout": True},
        ),
        EvaluationTaskSpec(
            id="semantic_authoritative_recall",
            category="contradiction_recovery",
            prompt=(
                "The prose contains an initial safety result followed by a later regulator recall. "
                "Update claim_device_safe using a governed transition state."
            ),
            context=(
                {
                    "id": "initial_safety_narrative",
                    "kind": "narrative_record",
                    "text": "The original controlled study supported claim_device_safe.",
                },
                {
                    "id": "recall_narrative",
                    "kind": "narrative_record",
                    "text": (
                        "A later regulator recall reported serious failures that contradict "
                        "claim_device_safe."
                    ),
                },
            ),
            sequence=(
                {
                    "event": "narrative_update",
                    "text": "The regulator recall occurred after the original study.",
                },
            ),
            gold={
                "claims": {
                    "claim_device_safe": {
                        "final_state": "challenged",
                        "must_retract": True,
                        "required_evidence": ["recall_narrative"],
                    }
                }
            },
            metadata={"semantic_holdout": True},
        ),
        EvaluationTaskSpec(
            id="semantic_dependency_collapse",
            category="evidence_collapse",
            prompt="Read the prose dependency description and recompute claim_launch_ready.",
            context=(
                {
                    "id": "dependency_narrative",
                    "kind": "narrative_record",
                    "text": (
                        "claim_launch_ready depended jointly on both the thermal test and the pressure "
                        "test. The pressure-test evidence was withdrawn, and no alternate proof or "
                        "independent support remains."
                    ),
                },
            ),
            gold={
                "claims": {
                    "claim_launch_ready": {
                        "final_state": "unknown",
                        "required_evidence": ["dependency_narrative"],
                    }
                }
            },
            metadata={"semantic_holdout": True},
        ),
        EvaluationTaskSpec(
            id="semantic_alternate_proof",
            category="evidence_preservation",
            prompt="Read the prose proof description and recompute claim_bridge_safe.",
            context=(
                {
                    "id": "alternate_proof_narrative",
                    "kind": "narrative_record",
                    "text": (
                        "One derivation of claim_bridge_safe collapsed after a premise was revoked. "
                        "A separate complete load-test derivation remains independently supported and "
                        "is unaffected by that revocation."
                    ),
                },
            ),
            gold={
                "claims": {
                    "claim_bridge_safe": {
                        "final_state": "supported",
                        "required_evidence": ["alternate_proof_narrative"],
                    }
                }
            },
            metadata={"semantic_holdout": True},
        ),
        EvaluationTaskSpec(
            id="semantic_console_failure",
            category="false_success",
            prompt="Use the console transcript to decide whether action_compile succeeded.",
            context=(
                {
                    "id": "compile_console",
                    "kind": "free_form_log",
                    "text": (
                        "Compilation stopped after an unresolved symbol. The shell reported exit "
                        "status 9, and no binary was produced."
                    ),
                },
            ),
            gold={
                "actions": {
                    "action_compile": {
                        "final_state": "failure",
                        "failure_mode": "nonzero_exit",
                        "required_receipts": ["compile_console"],
                    }
                }
            },
            metadata={"semantic_holdout": True},
        ),
        EvaluationTaskSpec(
            id="semantic_independent_labs",
            category="replicated_discovery",
            prompt="Classify hypothesis_catalyst from the two prose laboratory reports.",
            context=(
                {
                    "id": "lab_north_report",
                    "kind": "narrative_record",
                    "text": (
                        "Lab North found support for hypothesis_catalyst using its own instruments "
                        "and dataset."
                    ),
                },
                {
                    "id": "lab_south_report",
                    "kind": "narrative_record",
                    "text": (
                        "Lab South independently replicated hypothesis_catalyst with different "
                        "instruments, personnel, and samples."
                    ),
                },
            ),
            gold={
                "discoveries": {
                    "hypothesis_catalyst": {
                        "final_state": "committed",
                        "truth": True,
                        "replicated": True,
                        "discovery_signal": True,
                        "required_evidence": ["lab_north_report", "lab_south_report"],
                    }
                }
            },
            metadata={"semantic_holdout": True},
        ),
        EvaluationTaskSpec(
            id="semantic_temporal_valve",
            category="temporal_scope",
            prompt=(
                "Preserve both time-scoped valve observations rather than treating them as a "
                "contradiction."
            ),
            context=(
                {
                    "id": "operator_chronology",
                    "kind": "narrative_record",
                    "text": (
                        "The certified operator log records Valve K open during January. A separate "
                        "February entry records Valve K closed. Both entries passed review."
                    ),
                },
            ),
            gold={
                "claims": {
                    "claim_valve_k_january_open": {
                        "final_state": "supported",
                        "required_evidence": ["operator_chronology"],
                    },
                    "claim_valve_k_february_closed": {
                        "final_state": "supported",
                        "required_evidence": ["operator_chronology"],
                    },
                }
            },
            metadata={"semantic_holdout": True},
        ),
    )
