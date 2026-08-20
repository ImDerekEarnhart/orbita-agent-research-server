"""Opportunity-aware diagnostics for governed recursive improvement.

The functions in this module are deliberately observational. They classify why an
improvement attempt failed (or succeeded) and audit whether a new diagnostic
instrument changed a supplied behavioral trace. They do not execute candidates,
modify active policy, deploy code, or grant activation authority.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

DIAGNOSIS_SCHEMA = "orbita-improvement-opportunity-diagnosis/1"
INSTRUMENT_AUDIT_SCHEMA = "orbita-observation-only-instrument-audit/1"

GAP_CLASSES = frozenset(
    {
        "NO_PREDICTIONS",
        "NO_CANDIDATES",
        "ALL_UNEVALUABLE",
        "ALL_REFUTED",
        "NONE_IMPROVE",
        "OK",
    }
)

LIKELY_REPAIR = {
    "NO_PREDICTIONS": "generalising_prediction_or_representation",
    "NO_CANDIDATES": "proposer_search_or_llm_proposer",
    "ALL_UNEVALUABLE": "executor_verifier_or_language_extension",
    "ALL_REFUTED": "revise_hypothesis_family",
    "NONE_IMPROVE": "goal_representation_or_proposer",
    "OK": "retain_evidence_and_recurse",
}


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("diagnostic artifacts must contain finite JSON values") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _count(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _instrument(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        raise ValueError("instrument must be a nonempty JSON object when supplied")
    _stable(value)
    return value


def diagnose_improvement_opportunity(metrics: dict[str, Any]) -> dict[str, Any]:
    """Classify where an improvement attempt ran out of opportunity.

    Classification precedence is intentional: if no prediction was produced, later
    stages never had a genuine opportunity to demonstrate value. Likewise, if no
    candidate was proposed, evaluator outcomes are not interpreted as proposer
    quality. The result is descriptive and does not authorize a repair.
    """
    if not isinstance(metrics, dict):
        raise ValueError("metrics must be a JSON object")
    allowed = {
        "prediction_count",
        "candidate_count",
        "evaluable_count",
        "refuted_count",
        "improving_count",
        "instrument",
        "context",
    }
    unknown = sorted(set(metrics) - allowed)
    if unknown:
        raise ValueError("unknown opportunity metric fields: " + ", ".join(unknown))

    prediction_count = _count("prediction_count", metrics.get("prediction_count"))
    candidate_count = _count("candidate_count", metrics.get("candidate_count"))
    evaluable_count = _count("evaluable_count", metrics.get("evaluable_count"))
    refuted_count = _count("refuted_count", metrics.get("refuted_count"))
    improving_count = _count("improving_count", metrics.get("improving_count"))

    if evaluable_count > candidate_count:
        raise ValueError("evaluable_count cannot exceed candidate_count")
    if refuted_count > evaluable_count:
        raise ValueError("refuted_count cannot exceed evaluable_count")
    if improving_count > evaluable_count:
        raise ValueError("improving_count cannot exceed evaluable_count")
    if refuted_count + improving_count > evaluable_count:
        raise ValueError("refuted_count + improving_count cannot exceed evaluable_count")

    instrument = _instrument(metrics.get("instrument"))
    context = metrics.get("context", {})
    if not isinstance(context, dict):
        raise ValueError("context must be a JSON object")
    _stable(context)

    if prediction_count == 0:
        classification = "NO_PREDICTIONS"
    elif candidate_count == 0:
        classification = "NO_CANDIDATES"
    elif evaluable_count == 0:
        classification = "ALL_UNEVALUABLE"
    elif refuted_count == evaluable_count:
        classification = "ALL_REFUTED"
    elif improving_count == 0:
        classification = "NONE_IMPROVE"
    else:
        classification = "OK"

    body = {
        "schema": DIAGNOSIS_SCHEMA,
        "classification": classification,
        "likely_repair": LIKELY_REPAIR[classification],
        "metrics": {
            "prediction_count": prediction_count,
            "candidate_count": candidate_count,
            "evaluable_count": evaluable_count,
            "refuted_count": refuted_count,
            "improving_count": improving_count,
        },
        "opportunity": {
            "prediction_stage_reached": prediction_count > 0,
            "proposal_stage_reached": candidate_count > 0,
            "evaluation_stage_reached": evaluable_count > 0,
            "improvement_observed": improving_count > 0,
        },
        "instrument": instrument,
        "instrument_hash": content_hash(instrument) if instrument is not None else None,
        "context": context,
        "scope": "supplied_attempt_metrics_only",
        "activation_authority": False,
    }
    return body | {"diagnosis_hash": content_hash(body)}


def audit_observation_only_instrument(
    *,
    instrument: dict[str, Any],
    baseline_behavior: list[Any],
    instrumented_behavior: list[Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prospectively test an 'observation-only' claim on supplied behavior traces.

    The caller decides which behavior projection matters before calling this function.
    Orbita compares those projections exactly and never treats construction intent as
    proof of neutrality. A passing audit is limited to the supplied traces.
    """
    instrument = _instrument(instrument)
    if instrument is None:  # defensive; _instrument(None) is permitted for diagnosis only
        raise ValueError("instrument must be supplied")
    if not isinstance(baseline_behavior, list) or not isinstance(instrumented_behavior, list):
        raise ValueError("baseline_behavior and instrumented_behavior must be lists")
    _stable(baseline_behavior)
    _stable(instrumented_behavior)
    context = context or {}
    if not isinstance(context, dict):
        raise ValueError("context must be a JSON object")
    _stable(context)

    same_length = len(baseline_behavior) == len(instrumented_behavior)
    mismatches: list[dict[str, Any]] = []
    for index, (before, after) in enumerate(zip(baseline_behavior, instrumented_behavior, strict=False)):
        if _stable(before) != _stable(after):
            mismatches.append(
                {
                    "index": index,
                    "baseline_hash": content_hash(before),
                    "instrumented_hash": content_hash(after),
                }
            )
    if not same_length:
        mismatches.append(
            {
                "index": "length",
                "baseline_length": len(baseline_behavior),
                "instrumented_length": len(instrumented_behavior),
            }
        )

    behavior_changed = bool(mismatches)
    body = {
        "schema": INSTRUMENT_AUDIT_SCHEMA,
        "instrument": instrument,
        "instrument_hash": content_hash(instrument),
        "baseline_behavior_hash": content_hash(baseline_behavior),
        "instrumented_behavior_hash": content_hash(instrumented_behavior),
        "behavior_changed": behavior_changed,
        "mismatches": mismatches,
        "verdict": "BEHAVIOR_CHANGED" if behavior_changed else "NO_BEHAVIOR_CHANGE_OBSERVED",
        "context": context,
        "scope": "supplied_behavior_projection_only",
        "claim_boundary": "no-behavior-change on these traces is not a universal neutrality proof",
        "activation_authority": False,
    }
    return body | {"audit_hash": content_hash(body)}
