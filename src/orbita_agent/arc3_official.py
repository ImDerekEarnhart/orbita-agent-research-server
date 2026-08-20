"""Governed bridge from Hodgeform receipts to the official ARC-AGI-3 API.

This module deliberately does not create a scorecard or call a model.  It
freezes equal-budget comparison conditions and mediates a previously frozen
prediction to ``env.step`` using the public ``arc_agi``/``arcengine`` API.
"""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any

from .arc3_control import append_receipt, content_hash

ADAPTER_SCHEMA = "hodgeform-arc3-official-adapter/1"
PROTOCOL_SCHEMA = "hodgeform-arc3-comparison-protocol/1"
OFFICIAL_ACTIONS = tuple(["RESET"] + [f"ACTION{i}" for i in range(1, 8)])
ALLOWED_CONDITION_DIFFERENCES = {"condition_id", "hodgeform_enabled", "controller"}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    if hasattr(value, "name"):
        return str(value.name)
    raise ValueError(f"unsupported ARC observation value: {type(value).__name__}")


def canonicalize_observation(observation: Any, legal_actions: list[str] | tuple[str, ...]) -> dict[str, Any]:
    actions = [str(getattr(action, "name", action)) for action in legal_actions]
    if not actions or any(action not in OFFICIAL_ACTIONS for action in actions):
        raise ValueError("official ARC legal actions contain an unsupported action")
    visible = _jsonable(observation)
    return {
        "schema": ADAPTER_SCHEMA,
        "visible_observation": visible,
        "visible_observation_hash": content_hash(visible),
        "legal_actions": actions,
    }


def freeze_official_prediction(
    receipts: list[dict[str, Any]],
    *,
    observation: Any,
    legal_actions: list[str] | tuple[str, ...],
    action: str,
    action_data: dict[str, int] | None,
    prediction: dict[str, Any],
) -> dict[str, Any]:
    canonical = canonicalize_observation(observation, legal_actions)
    if action not in canonical["legal_actions"]:
        raise ValueError("requested action is not legal for this observation")
    data = copy.deepcopy(action_data or {})
    if action == "ACTION6":
        if set(data) != {"x", "y"} or any(not isinstance(data[key], int) or not 0 <= data[key] <= 63 for key in data):
            raise ValueError("ACTION6 requires integer x/y coordinates in the inclusive range 0..63")
    elif data:
        raise ValueError("action data is permitted only for ACTION6")
    action_id = f"official-action-{sum(item['kind'] == 'official_action_observed' for item in receipts) + 1}"
    return append_receipt(
        receipts,
        "official_prediction_frozen",
        {
            "action_id": action_id,
            "observation_hash": canonical["visible_observation_hash"],
            "legal_actions": canonical["legal_actions"],
            "action": action,
            "action_data": data,
            "prediction": copy.deepcopy(prediction),
            "score_accessed": False,
            "sequence_before_action": len(receipts) - 1,
        },
    )


def execute_frozen_official_action(
    env: Any,
    receipts: list[dict[str, Any]],
    frozen_prediction: dict[str, Any],
) -> dict[str, Any]:
    """Invoke only the official environment step for an already-frozen action."""
    if frozen_prediction not in receipts or frozen_prediction.get("kind") != "official_prediction_frozen":
        raise ValueError("action execution requires a receipt from the active receipt chain")
    payload = frozen_prediction["payload"]
    if any(item["kind"] == "official_action_observed" and item["payload"]["action_id"] == payload["action_id"] for item in receipts):
        raise ValueError("a frozen action may be executed only once")
    try:
        game_action = getattr(importlib.import_module("arcengine").GameAction, payload["action"])
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("the pinned official arc-agi toolkit and arcengine runtime are required") from exc
    try:
        observation = env.step(game_action, data=payload["action_data"] or None)
    except Exception as exc:
        append_receipt(receipts, "official_action_failed", {"action_id": payload["action_id"], "error_type": type(exc).__name__})
        raise RuntimeError("official ARC environment step failed closed") from exc
    visible = _jsonable(observation)
    return append_receipt(
        receipts,
        "official_action_observed",
        {
            "action_id": payload["action_id"],
            "result": visible,
            "result_hash": content_hash(visible),
            "score_accessed": False,
        },
    )


def _condition_scientific_surface(condition: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in condition.items() if key not in ALLOWED_CONDITION_DIFFERENCES}


def build_comparison_protocol(spec: dict[str, Any]) -> dict[str, Any]:
    required = {
        "official_toolkit_commit",
        "benchmarking_runner_commit",
        "operation_mode",
        "environment_manifest",
        "model",
        "api_request_parameters",
        "budgets",
        "repetitions",
        "seed_policy",
    }
    missing = sorted(required - set(spec))
    if missing:
        raise ValueError(f"ARC comparison protocol is missing required fields: {', '.join(missing)}")
    if spec["operation_mode"] not in {"OFFLINE", "ONLINE", "COMPETITION"}:
        raise ValueError("operation_mode must be an official ARC toolkit mode")
    if not isinstance(spec["repetitions"], int) or spec["repetitions"] < 1:
        raise ValueError("repetitions must be a positive integer")
    manifest = spec["environment_manifest"]
    if not isinstance(manifest, dict) or not manifest.get("selection") or not manifest.get("binding_rule"):
        raise ValueError("environment_manifest must freeze a selection and binding rule")
    common = {
        "official_toolkit_commit": spec["official_toolkit_commit"],
        "benchmarking_runner_commit": spec["benchmarking_runner_commit"],
        "operation_mode": spec["operation_mode"],
        "environment_manifest": copy.deepcopy(manifest),
        "model": copy.deepcopy(spec["model"]),
        "api_request_parameters": copy.deepcopy(spec["api_request_parameters"]),
        "budgets": copy.deepcopy(spec["budgets"]),
        "repetitions": spec["repetitions"],
        "seed_policy": copy.deepcopy(spec["seed_policy"]),
        "reset_semantics": "fresh model context and fresh Hodgeform case per environment and repetition; no cross-arm state",
        "score_policy": "no scorecard access or intermediate scores during execution; reveal only after both arms freeze",
        "execution_order": "precommitted balanced alternation; no adaptation from observed performance",
        "receipt_schema": "hodgeform-arc3-receipt-chain/1",
    }
    baseline = copy.deepcopy(common) | {
        "condition_id": "gpt-5.6-alone",
        "hodgeform_enabled": False,
        "controller": "official benchmarking wrapper only",
    }
    hybrid = copy.deepcopy(common) | {
        "condition_id": "gpt-5.6-plus-hodgeform",
        "hodgeform_enabled": True,
        "controller": "Hodgeform prediction/counterexample/language-refinement receipt loop",
    }
    if _condition_scientific_surface(baseline) != _condition_scientific_surface(hybrid):
        raise RuntimeError("comparison arms are not scientifically identical outside the intervention")
    body = {
        "schema": PROTOCOL_SCHEMA,
        "status": "FROZEN_NOT_EXECUTED",
        "hypothesis": "GPT-5.6 plus Hodgeform improves ARC-AGI-3 completion under identical model and environment-action budgets",
        "primary_metrics": ["official_score", "completion_rate"],
        "diagnostic_metrics": ["environment_actions", "model_tokens", "cost", "failed_hypotheses", "recovery_rate"],
        "conditions": [baseline, hybrid],
        "boundaries": {
            "official_task_content_accessed_during_freeze": False,
            "model_called_during_freeze": False,
            "scorecard_created_during_freeze": False,
            "provider_model_identifier_must_be_revalidated_before_execution": True,
        },
    }
    return body | {"protocol_hash": content_hash(body)}


def verify_comparison_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    if protocol.get("schema") != PROTOCOL_SCHEMA or protocol.get("status") != "FROZEN_NOT_EXECUTED":
        raise ValueError("unsupported or non-frozen ARC comparison protocol")
    body = {key: value for key, value in protocol.items() if key not in {"protocol_hash", "artifact_path"}}
    if protocol.get("protocol_hash") != content_hash(body):
        raise ValueError("ARC comparison protocol hash does not match its contents")
    conditions = protocol.get("conditions", [])
    if len(conditions) != 2 or _condition_scientific_surface(conditions[0]) != _condition_scientific_surface(conditions[1]):
        raise ValueError("comparison arms differ outside the registered Hodgeform intervention")
    if [item.get("condition_id") for item in conditions] != ["gpt-5.6-alone", "gpt-5.6-plus-hodgeform"]:
        raise ValueError("comparison condition identities are invalid")
    return {"valid": True, "status": protocol["status"], "protocol_hash": protocol["protocol_hash"]}


def freeze_comparison_protocol(spec: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    protocol = build_comparison_protocol(spec)
    verify_comparison_protocol(protocol)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"arc3-comparison-{protocol['protocol_hash']}.json"
    serialized = json.dumps(protocol, sort_keys=True, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != serialized:
        raise RuntimeError("existing ARC protocol differs from its immutable hash path")
    if not path.exists():
        path.write_text(serialized, encoding="utf-8")
    return protocol | {"artifact_path": str(path.resolve())}
