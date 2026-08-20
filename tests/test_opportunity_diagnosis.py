from __future__ import annotations

import pytest

from orbita_agent.opportunity_diagnosis import (
    audit_observation_only_instrument,
    diagnose_improvement_opportunity,
)


def _metrics(**overrides):
    base = {
        "prediction_count": 1,
        "candidate_count": 1,
        "evaluable_count": 1,
        "refuted_count": 0,
        "improving_count": 1,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (_metrics(prediction_count=0, candidate_count=0, evaluable_count=0, improving_count=0), "NO_PREDICTIONS"),
        (_metrics(candidate_count=0, evaluable_count=0, improving_count=0), "NO_CANDIDATES"),
        (_metrics(candidate_count=2, evaluable_count=0, improving_count=0), "ALL_UNEVALUABLE"),
        (_metrics(candidate_count=2, evaluable_count=2, refuted_count=2, improving_count=0), "ALL_REFUTED"),
        (_metrics(candidate_count=2, evaluable_count=2, refuted_count=1, improving_count=0), "NONE_IMPROVE"),
        (_metrics(candidate_count=2, evaluable_count=2, refuted_count=1, improving_count=1), "OK"),
    ],
)
def test_gap_classification(metrics, expected):
    result = diagnose_improvement_opportunity(metrics)
    assert result["classification"] == expected
    assert result["activation_authority"] is False
    assert result["diagnosis_hash"]


def test_gap_classification_preserves_instrument_identity():
    instrument = {"name": "coverage-diagnostic", "version": "2", "definition": "count prediction opportunities"}
    result = diagnose_improvement_opportunity(_metrics(instrument=instrument, context={"experiment": "r11l/vc33"}))
    assert result["instrument"] == instrument
    assert result["instrument_hash"]
    assert result["context"]["experiment"] == "r11l/vc33"


def test_gap_classification_rejects_inconsistent_counts():
    with pytest.raises(ValueError, match="evaluable_count"):
        diagnose_improvement_opportunity(_metrics(candidate_count=1, evaluable_count=2))
    with pytest.raises(ValueError, match="refuted_count \+ improving_count"):
        diagnose_improvement_opportunity(_metrics(evaluable_count=2, refuted_count=2, improving_count=1))


def test_observation_only_audit_passes_only_for_exact_behavioral_match():
    instrument = {"name": "typed-gap-classifier", "version": "1", "mode": "observation_only"}
    baseline = [{"action": "left", "reward": 0}, {"action": "right", "reward": 1}]
    result = audit_observation_only_instrument(
        instrument=instrument,
        baseline_behavior=baseline,
        instrumented_behavior=list(baseline),
        context={"prospective": True},
    )
    assert result["verdict"] == "NO_BEHAVIOR_CHANGE_OBSERVED"
    assert result["behavior_changed"] is False
    assert result["mismatches"] == []
    assert result["scope"] == "supplied_behavior_projection_only"


def test_observation_only_audit_detects_changed_behavior_and_length():
    instrument = {"name": "diagnostic-v2", "version": "2", "mode": "observation_only"}
    result = audit_observation_only_instrument(
        instrument=instrument,
        baseline_behavior=[{"action": "a"}, {"action": "b"}],
        instrumented_behavior=[{"action": "a"}],
    )
    assert result["verdict"] == "BEHAVIOR_CHANGED"
    assert result["behavior_changed"] is True
    assert result["mismatches"][-1]["index"] == "length"
