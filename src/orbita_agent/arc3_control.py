"""Deterministic ARC-AGI-3-style control for Hodgeform's adaptive loop.

This is an offline acceptance environment, not an ARC benchmark score.  It
exercises the production semantic-evolution primitives with prediction-before-
action receipts, executable hypotheses, counterexamples, and a prospective
language refinement.  No LLM prose is treated as an executable world model.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from .semantic_evolution import (
    audit_representation,
    build_language_snapshot,
    evaluate_missing_primitive,
    propose_missing_primitive,
)

SCHEMA = "hodgeform-arc3-control/1"
RECEIPT_SCHEMA = "hodgeform-arc3-receipt-chain/1"
ACTIONS = ("LEFT", "RIGHT")


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("ARC control artifacts must contain finite JSON values") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _append(chain: list[dict[str, Any]], kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = {
        "sequence": len(chain),
        "kind": kind,
        "payload": copy.deepcopy(payload),
        "previous_receipt_hash": chain[-1]["receipt_hash"] if chain else None,
    }
    receipt = body | {"receipt_hash": content_hash(body)}
    chain.append(receipt)
    return receipt


def verify_receipt_chain(artifact: dict[str, Any]) -> dict[str, Any]:
    if artifact.get("schema") != SCHEMA:
        raise ValueError("unsupported ARC control artifact schema")
    chain = artifact.get("receipts")
    if not isinstance(chain, list) or not chain:
        raise ValueError("ARC control artifact requires a nonempty receipt chain")
    previous = None
    prediction_by_action: dict[str, dict[str, Any]] = {}
    for sequence, receipt in enumerate(chain):
        body = {key: value for key, value in receipt.items() if key != "receipt_hash"}
        if receipt.get("sequence") != sequence:
            raise ValueError("receipt sequence is not contiguous")
        if receipt.get("previous_receipt_hash") != previous:
            raise ValueError("receipt chain predecessor mismatch")
        if receipt.get("receipt_hash") != content_hash(body):
            raise ValueError("receipt hash does not match its contents")
        kind, payload = receipt.get("kind"), receipt.get("payload", {})
        if kind == "prediction_frozen":
            prediction_by_action[payload["action_id"]] = payload
        if kind == "action_observed":
            prediction = prediction_by_action.get(payload.get("action_id"))
            if prediction is None or prediction["sequence_before_action"] >= sequence:
                raise ValueError("every action must have an earlier frozen prediction")
        previous = receipt["receipt_hash"]
    body = {key: value for key, value in artifact.items() if key not in {"artifact_hash", "artifact_path"}}
    if artifact.get("artifact_hash") != content_hash(body):
        raise ValueError("ARC control artifact hash does not match its contents")
    return {
        "valid": True,
        "receipt_count": len(chain),
        "latest_receipt_hash": previous,
        "artifact_hash": artifact["artifact_hash"],
    }


class _LineWorld:
    """Three one-dimensional levels whose shared appearance hides direction."""

    starts = ((0, 1), (2, 1), (0, 1))

    def __init__(self) -> None:
        self.level = 0
        self.agent_x, self.goal_x = self.starts[0]

    @property
    def complete(self) -> bool:
        return self.agent_x == self.goal_x

    def observation(self) -> dict[str, Any]:
        relation = "left_of" if self.agent_x < self.goal_x else "right_of" if self.agent_x > self.goal_x else "at"
        return {
            "frame": {"agent_color": "blue", "goal_color": "gold"},
            "raw_state": {"relative_position": relation},
            "legal_actions": list(ACTIONS),
            "level": self.level + 1,
        }

    def step(self, action: str) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError("unsupported synthetic ARC action")
        before = abs(self.agent_x - self.goal_x)
        self.agent_x = max(0, self.agent_x - 1) if action == "LEFT" else min(2, self.agent_x + 1)
        after = abs(self.agent_x - self.goal_x)
        return {"progress": int(after < before), "completed": self.complete, "distance": after}

    def next_level(self) -> bool:
        if not self.complete:
            raise RuntimeError("cannot advance an incomplete level")
        self.level += 1
        if self.level >= len(self.starts):
            return False
        self.agent_x, self.goal_x = self.starts[self.level]
        return True


def _snapshot() -> dict[str, Any]:
    return build_language_snapshot(
        {
            "name": "arc3-visible-color-action-language",
            "version": "control-1",
            "primitives": [],
            "observables": ["agent_color", "goal_color", "action"],
            "refusal_conditions": ["no executable prediction is available"],
            "unknown_conditions": ["identical visible state-action pairs have different outcomes"],
            "read_permissions": ["visible_frame", "legal_actions"],
            "write_permissions": ["frozen_prediction", "environment_action"],
            "grounding_rules": ["outcome means observed reduction in goal distance"],
            "invariants": ["prediction is frozen before action", "benchmark score is unavailable"],
        }
    )


def _case(world_id: str, observation: dict[str, Any], action: str, outcome: int) -> dict[str, Any]:
    return {
        "world_id": world_id,
        "language_view": observation["frame"] | {"action": action},
        "state": observation["raw_state"],
        "outcome": outcome,
    }


def _predict(action: str, relation: str | None, refined: bool) -> int:
    if refined:
        return int((relation == "left_of" and action == "RIGHT") or (relation == "right_of" and action == "LEFT"))
    return int(action == "RIGHT")


def run_synthetic_arc3_control(output_dir: str | Path | None = None) -> dict[str, Any]:
    """Run the frozen three-level control and return one replayable artifact."""

    world = _LineWorld()
    snapshot = _snapshot()
    receipts: list[dict[str, Any]] = []
    discovery_cases: list[dict[str, Any]] = []
    evaluation_cases: list[dict[str, Any]] = []
    failed_hypotheses: list[str] = []
    actions_taken = 0
    proposal = None
    audit = None
    evaluation = None
    refined = False

    _append(receipts, "control_started", {"snapshot_hash": snapshot["snapshot_hash"], "score_accessed": False})
    _append(
        receipts,
        "hypothesis_frozen",
        {
            "hypothesis_id": "constant-right-progress",
            "executable_rule": {"kind": "action_lookup", "progress_action": "RIGHT"},
            "llm_prose_used_as_model": False,
        },
    )

    while True:
        observation = world.observation()
        observation_receipt = _append(
            receipts,
            "observation_recorded",
            {"observation": observation, "observation_hash": content_hash(observation)},
        )
        relation = observation["raw_state"]["relative_position"] if refined else None

        # Level 3 deliberately freezes one prospective non-progress prediction for
        # the same LEFT action seen on level 2.  This produces a clean post-freeze
        # evaluation pair before the controller takes the progress action.
        if refined and world.level == 2 and evaluation is None:
            action = "LEFT"
        else:
            action = "RIGHT" if not refined or relation == "left_of" else "LEFT"
        predicted = _predict(action, relation, refined)
        action_id = f"level-{world.level + 1}-action-{actions_taken + 1}"
        _append(
            receipts,
            "prediction_frozen",
            {
                "action_id": action_id,
                "observation_hash": observation_receipt["payload"]["observation_hash"],
                "action": action,
                "predicted_progress": predicted,
                "model": "relative-position-rule" if refined else "constant-right-progress",
                "sequence_before_action": len(receipts) - 1,
            },
        )
        result = world.step(action)
        actions_taken += 1
        transition = _case(action_id, observation, action, result["progress"])
        _append(
            receipts,
            "action_observed",
            {"action_id": action_id, "action": action, "result": result, "transition_hash": content_hash(transition)},
        )

        if predicted != result["progress"]:
            counterexample = {
                "hypothesis_id": "relative-position-rule" if refined else "constant-right-progress",
                "prediction": predicted,
                "actual": result["progress"],
                "transition_hash": content_hash(transition),
            }
            failed_hypotheses.append(counterexample["hypothesis_id"])
            _append(receipts, "counterexample_retained", counterexample)

        if proposal is None:
            discovery_cases.append(transition)
            if len(discovery_cases) >= 2:
                audit = audit_representation(snapshot, discovery_cases)
                if audit["verdict"] == "LANGUAGE_LIMIT_WITNESS":
                    _append(receipts, "representation_collision_frozen", audit)
                    proposal = propose_missing_primitive(snapshot, discovery_cases)
                    _append(receipts, "language_refinement_frozen", proposal)
                    refined = True
                    _append(
                        receipts,
                        "hypothesis_frozen",
                        {
                            "hypothesis_id": "relative-position-rule",
                            "executable_rule": {
                                "kind": "state_action_rule",
                                "primitive": proposal["primitive"]["source_field"],
                                "mapping": {"left_of": "RIGHT", "right_of": "LEFT"},
                            },
                            "llm_prose_used_as_model": False,
                        },
                    )
        else:
            evaluation_cases.append(transition)
            if len(evaluation_cases) >= 2 and evaluation is None:
                evaluation = evaluate_missing_primitive(snapshot, proposal, evaluation_cases)
                _append(receipts, "prospective_refinement_evaluated", evaluation)

        if result["completed"]:
            _append(receipts, "level_completed", {"level": world.level + 1, "actions_taken": actions_taken})
            if not world.next_level():
                break

    if audit is None or proposal is None or evaluation is None:
        raise RuntimeError("synthetic ARC control did not reach its required language-repair stages")
    if evaluation["strict_improvement"] is not True:
        raise RuntimeError("frozen representation repair failed its prospective control")

    summary = {
        "levels_completed": len(_LineWorld.starts),
        "actions_taken": actions_taken,
        "failed_hypotheses": failed_hypotheses,
        "counterexample_count": sum(item["kind"] == "counterexample_retained" for item in receipts),
        "prediction_count": sum(item["kind"] == "prediction_frozen" for item in receipts),
        "language_limit_detected": True,
        "proposed_primitive": proposal["primitive"]["source_field"],
        "prospective_refinement_survived": True,
        "score_accessed": False,
        "llm_prose_used_as_proof": False,
        "scope": "offline_synthetic_three_level_control_only",
    }
    _append(receipts, "control_completed", summary)
    body = {
        "schema": SCHEMA,
        "receipt_schema": RECEIPT_SCHEMA,
        "environment": "synthetic-hidden-direction-line-world/1",
        "snapshot": snapshot,
        "representation_audit": audit,
        "refinement_proposal": proposal,
        "refinement_evaluation": evaluation,
        "summary": summary,
        "receipts": receipts,
    }
    artifact = body | {"artifact_hash": content_hash(body)}
    verify_receipt_chain(artifact)
    if output_dir is not None:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / f"arc3-control-{artifact['artifact_hash']}.json"
        serialized = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != serialized:
            raise RuntimeError("existing ARC control artifact differs from its deterministic hash path")
        if not path.exists():
            path.write_text(serialized, encoding="utf-8")
        artifact = artifact | {"artifact_path": str(path.resolve())}
    return artifact
