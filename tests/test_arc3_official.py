from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from orbita_agent.arc3_official import (
    build_comparison_protocol,
    canonicalize_observation,
    execute_frozen_official_action,
    freeze_official_prediction,
    verify_comparison_protocol,
)


def _spec() -> dict:
    return {
        "official_toolkit_commit": "f12822c4d550121c35a275008d964afbbed47d2f",
        "benchmarking_runner_commit": "86d72170ce3155551712a9fafd290bab471d6eee",
        "operation_mode": "COMPETITION",
        "environment_manifest": {
            "selection": "all available competition environments",
            "binding_rule": "capture ordered IDs and SHA-256 before either condition sees the first frame",
        },
        "model": {
            "provider": "openai",
            "requested_family": "GPT-5.6 Sol",
            "api_identifier": "gpt-5.6-sol",
            "api": "responses",
            "endpoint": "/v1/responses",
        },
        "api_request_parameters": {"reasoning": {"effort": "max"}, "max_output_tokens": 128000},
        "budgets": {"environment_action_multiplier": 5.0, "max_context_tokens": 175000, "max_output_tokens": 128000},
        "repetitions": 3,
        "seed_policy": {"kind": "precommitted", "seeds": [1103, 2207, 3301]},
    }


def test_observation_canonicalization_is_order_independent() -> None:
    left = canonicalize_observation({"b": [2], "a": 1}, ["ACTION1", "ACTION6"])
    right = canonicalize_observation({"a": 1, "b": [2]}, ["ACTION1", "ACTION6"])
    assert left["visible_observation_hash"] == right["visible_observation_hash"]


def test_prediction_is_frozen_before_official_step(monkeypatch: pytest.MonkeyPatch) -> None:
    action_enum = SimpleNamespace(ACTION1="engine-action-1")
    monkeypatch.setitem(sys.modules, "arcengine", SimpleNamespace(GameAction=action_enum))
    events: list[str] = []

    class FakeEnvironment:
        def step(self, action, data=None):
            events.append("step")
            assert action == "engine-action-1"
            assert data is None
            return {"state": "PLAYING", "frame": [[1, 0]]}

        def get_scorecard(self):  # pragma: no cover - a call is a test failure
            raise AssertionError("scorecard must never be accessed")

    receipts: list[dict] = []
    frozen = freeze_official_prediction(
        receipts,
        observation={"frame": [[0, 1]]},
        legal_actions=["ACTION1"],
        action="ACTION1",
        action_data=None,
        prediction={"expected_change": "agent moves"},
    )
    assert frozen["kind"] == "official_prediction_frozen"
    observed = execute_frozen_official_action(FakeEnvironment(), receipts, frozen)
    assert events == ["step"]
    assert observed["payload"]["score_accessed"] is False
    assert receipts.index(frozen) < receipts.index(observed)


def test_action_syntax_and_budget_surface_fail_closed() -> None:
    with pytest.raises(ValueError, match="not legal"):
        freeze_official_prediction([], observation={}, legal_actions=["ACTION1"], action="ACTION2", action_data=None, prediction={})
    with pytest.raises(ValueError, match="coordinates"):
        freeze_official_prediction([], observation={}, legal_actions=["ACTION6"], action="ACTION6", action_data={"x": 64, "y": 0}, prediction={})
    with pytest.raises(ValueError, match="only for ACTION6"):
        freeze_official_prediction([], observation={}, legal_actions=["ACTION1"], action="ACTION1", action_data={"x": 1}, prediction={})


def test_comparison_protocol_freezes_only_hodgeform_intervention() -> None:
    protocol = build_comparison_protocol(_spec())
    result = verify_comparison_protocol(protocol)
    assert result["valid"] is True
    assert protocol["status"] == "FROZEN_NOT_EXECUTED"
    baseline, hybrid = protocol["conditions"]
    assert baseline["model"] == hybrid["model"]
    assert baseline["budgets"] == hybrid["budgets"]
    assert baseline["hodgeform_enabled"] is False
    assert hybrid["hodgeform_enabled"] is True


def test_protocol_mutation_or_arm_budget_drift_is_rejected() -> None:
    protocol = build_comparison_protocol(_spec())
    mutated = copy.deepcopy(protocol)
    mutated["conditions"][1]["budgets"]["max_output_tokens"] += 1
    with pytest.raises(ValueError, match="hash"):
        verify_comparison_protocol(mutated)
    from orbita_agent.arc3_control import content_hash

    mutated["protocol_hash"] = content_hash({key: value for key, value in mutated.items() if key != "protocol_hash"})
    with pytest.raises(ValueError, match="outside"):
        verify_comparison_protocol(mutated)


def test_committed_registration_rebuilds_to_the_frozen_hash() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "benchmarks/registrations/arc3_gpt56_hodgeform_spec.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (
            root
            / "benchmarks/registrations/frozen"
            / "arc3-comparison-2aac4c64c05a496124b12b5ed322ed4618d9ca6e19d6498fcc02a3f3e28ba68f.json"
        ).read_text(encoding="utf-8")
    )
    assert build_comparison_protocol(spec) == expected
    assert verify_comparison_protocol(expected)["valid"] is True
