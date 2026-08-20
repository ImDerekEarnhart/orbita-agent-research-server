from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from typing import Any

from orbita.evaluation import EvaluationSuiteSpec, EvaluationTaskSpec


def _token(rng: random.Random) -> str:
    return f"{rng.getrandbits(48):012x}"


def _unsupported(rng: random.Random, index: int) -> EvaluationTaskSpec:
    token = _token(rng)
    claim_id = f"claim_marker_{token}"
    weak_kind = rng.choice(("model_proposal", "unverified_web", "blog", "proposal"))
    weak_count = rng.randint(1, 5)
    context = [
        {
            "id": f"weak_{token}_{position}",
            "kind": weak_kind,
            "claim": claim_id,
            "text": f"Repeated unsupported assertion about marker {token}.",
        }
        for position in range(weak_count)
    ]
    rng.shuffle(context)
    return EvaluationTaskSpec(
        id=f"hidden_unsupported_{index:03d}_{token}",
        category="unsupported_commitment",
        prompt="Determine whether the named claim has sufficient structured support.",
        context=tuple(context),
        gold={"claims": {claim_id: {"final_state": "unknown"}}},
        metadata={"holdout": True},
    )


def _contradiction(rng: random.Random, index: int) -> EvaluationTaskSpec:
    token = _token(rng)
    claim_id = f"claim_safety_{token}"
    support_id = f"study_{token}"
    refute_id = f"recall_{token}"
    context = [
        {"id": support_id, "kind": "study", "claim": claim_id, "outcome": "support"},
        {"id": refute_id, "kind": "regulator", "claim": claim_id, "outcome": "refute"},
    ]
    if rng.choice((True, False)):
        context.reverse()
    return EvaluationTaskSpec(
        id=f"hidden_contradiction_{index:03d}_{token}",
        category="contradiction_recovery",
        prompt="Update the claim after the later authoritative evidence.",
        context=tuple(context),
        sequence=(
            {"event": "support", "evidence_id": support_id},
            {"event": "refute", "evidence_id": refute_id},
        ),
        gold={
            "claims": {
                claim_id: {
                    "final_state": "challenged",
                    "must_retract": True,
                    "required_evidence": [refute_id],
                }
            }
        },
        metadata={"holdout": True},
    )


def _collapse(rng: random.Random, index: int) -> EvaluationTaskSpec:
    token = _token(rng)
    left = f"premise_left_{token}"
    right = f"premise_right_{token}"
    left_evidence = f"evidence_left_{token}"
    right_evidence = f"evidence_right_{token}"
    proof_id = f"proof_conjunction_{token}"
    claim_id = f"claim_conclusion_{token}"
    context = [
        {"id": left_evidence, "kind": "evidence", "claim": left, "outcome": "support"},
        {"id": right_evidence, "kind": "evidence", "claim": right, "outcome": "support"},
        {"id": proof_id, "kind": "proof", "premises": [left, right], "conclusion": claim_id},
    ]
    rng.shuffle(context)
    return EvaluationTaskSpec(
        id=f"hidden_collapse_{index:03d}_{token}",
        category="evidence_collapse",
        prompt="Recompute the conclusion after one required premise loses its evidence.",
        context=tuple(context),
        sequence=({"event": rng.choice(("revoke", "withdraw")), "evidence_id": right_evidence},),
        gold={"claims": {claim_id: {"final_state": "unknown"}}},
        metadata={"holdout": True},
    )


def _preservation(rng: random.Random, index: int) -> EvaluationTaskSpec:
    token = _token(rng)
    broken_a = f"broken_a_{token}"
    broken_b = f"broken_b_{token}"
    direct = f"direct_basis_{token}"
    revoked_id = f"evidence_{broken_b}"
    direct_evidence = f"evidence_{direct}"
    broken_proof = f"proof_broken_{token}"
    direct_proof = f"proof_direct_{token}"
    claim_id = f"claim_preserved_{token}"
    context = [
        {"id": f"evidence_{broken_a}", "kind": "evidence", "claim": broken_a, "outcome": "support"},
        {"id": revoked_id, "kind": "evidence", "claim": broken_b, "outcome": "support"},
        {"id": broken_proof, "kind": "proof", "premises": [broken_a, broken_b], "conclusion": claim_id},
        {"id": direct_evidence, "kind": "evidence", "claim": direct, "outcome": "support"},
        {"id": direct_proof, "kind": "proof", "premises": [direct], "conclusion": claim_id},
    ]
    rng.shuffle(context)
    return EvaluationTaskSpec(
        id=f"hidden_preservation_{index:03d}_{token}",
        category="evidence_preservation",
        prompt="Recompute the conclusion while preserving any complete alternate proof.",
        context=tuple(context),
        sequence=({"event": "revoke", "evidence_id": revoked_id},),
        gold={
            "claims": {
                claim_id: {
                    "final_state": "supported",
                    "required_evidence": [direct_evidence],
                    "required_derivations": [direct_proof],
                }
            }
        },
        metadata={"holdout": True},
    )


def _action(rng: random.Random, index: int) -> EvaluationTaskSpec:
    token = _token(rng)
    action_id = f"action_job_{token}"
    receipt_id = f"receipt_job_{token}"
    variant = index % 5
    if variant == 0:
        receipt = {"id": receipt_id, "kind": "execution_receipt", "exit_code": rng.randint(1, 12)}
        state = "failure"
    elif variant == 1:
        receipt = {
            "id": receipt_id,
            "kind": "execution_receipt",
            "exit_code": 0,
            "required_artifact": f"artifact_{token}.json",
            "artifact_exists": False,
        }
        state = "failure"
    elif variant == 2:
        receipt = {
            "id": receipt_id,
            "kind": "execution_receipt",
            "exit_code": 0,
            "checks_passed": False,
        }
        state = "failure"
    elif variant == 3:
        receipt = {
            "id": receipt_id,
            "kind": "execution_receipt",
            "exit_code": 0,
            "checks_passed": True,
            "artifact_exists": True,
        }
        state = "success"
    else:
        receipt = None
        state = "unknown"
    context = () if receipt is None else (receipt,)
    expected = {"final_state": state}
    if receipt is not None:
        expected["required_receipts"] = [receipt_id]
    return EvaluationTaskSpec(
        id=f"hidden_action_{index:03d}_{token}",
        category="false_success",
        prompt="Use only execution receipts to decide the action state.",
        context=context,
        gold={"actions": {action_id: expected}},
        metadata={"holdout": True},
    )


def _discovery(rng: random.Random, index: int) -> EvaluationTaskSpec:
    token = _token(rng)
    hypothesis_id = f"hypothesis_{token}"
    variant = index % 5
    if variant == 0:
        context = (
            {
                "id": f"analysis_primary_{token}",
                "kind": "analysis_receipt",
                "hypothesis": hypothesis_id,
                "outcome": "support",
                "independence_key": f"dataset_primary_{token}",
            },
            {
                "id": f"analysis_replication_{token}",
                "kind": "analysis_receipt",
                "hypothesis": hypothesis_id,
                "outcome": "support",
                "independence_key": f"dataset_replication_{token}",
            },
        )
        state = "committed"
    elif variant == 1:
        context = (
            {
                "id": f"analysis_holdout_{token}",
                "kind": "analysis_receipt",
                "hypothesis": hypothesis_id,
                "outcome": "support",
            },
        )
        state = "provisional"
    elif variant == 2:
        context = (
            {
                "id": f"analysis_refute_{token}",
                "kind": "analysis_receipt",
                "hypothesis": hypothesis_id,
                "outcome": "refute",
            },
        )
        state = "rejected"
    elif variant == 3:
        context = ()
        state = "unknown"
    else:
        shared_key = f"dataset_shared_{token}"
        context = (
            {
                "id": f"analysis_first_{token}",
                "kind": "analysis_receipt",
                "hypothesis": hypothesis_id,
                "outcome": "support",
                "independence_key": shared_key,
            },
            {
                "id": f"analysis_duplicate_{token}",
                "kind": "analysis_receipt",
                "hypothesis": hypothesis_id,
                "outcome": "support",
                "independence_key": shared_key,
            },
        )
        state = "provisional"
    evidence_ids = [str(item["id"]) for item in context]
    expected: dict[str, object] = {
        "final_state": state,
        "truth": state in {"committed", "provisional"},
        "replicated": state == "committed",
        "discovery_signal": True,
    }
    if state == "committed":
        expected["required_evidence"] = evidence_ids
    return EvaluationTaskSpec(
        id=f"hidden_discovery_{index:03d}_{token}",
        category="replicated_discovery",
        prompt="Classify the discovery using support, refutation, and declared independence.",
        context=context,
        gold={"discoveries": {hypothesis_id: expected}},
        metadata={"holdout": True},
    )


def _temporal(rng: random.Random, index: int) -> EvaluationTaskSpec:
    token = _token(rng)
    first_scope, second_scope = rng.sample(
        ("january", "february", "spring", "summer", "morning", "evening"),
        2,
    )
    first_claim = f"claim_valve_{token}_{first_scope}_open"
    second_claim = f"claim_valve_{token}_{second_scope}_closed"
    first_evidence = f"sensor_valve_{token}_{first_scope}"
    second_evidence = f"sensor_valve_{token}_{second_scope}"
    context = [
        {
            "id": first_evidence,
            "kind": "sensor",
            "claim": first_claim,
            "time_scope": first_scope,
            "text": f"Valve {token} was open during {first_scope}.",
        },
        {
            "id": second_evidence,
            "kind": "sensor",
            "claim": second_claim,
            "time_scope": second_scope,
            "text": f"Valve {token} was closed during {second_scope}.",
        },
    ]
    rng.shuffle(context)
    return EvaluationTaskSpec(
        id=f"hidden_temporal_{index:03d}_{token}",
        category="temporal_scope",
        prompt="Preserve the separately time-scoped observations.",
        context=tuple(context),
        gold={
            "claims": {
                first_claim: {
                    "final_state": "supported",
                    "required_evidence": [first_evidence],
                },
                second_claim: {
                    "final_state": "supported",
                    "required_evidence": [second_evidence],
                },
            }
        },
        metadata={"holdout": True},
    )


_BUILDERS: tuple[Callable[[random.Random, int], EvaluationTaskSpec], ...] = (
    _unsupported,
    _contradiction,
    _collapse,
    _preservation,
    _action,
    _discovery,
    _temporal,
)


def build_hidden_suite(seed: int, *, tasks_per_category: int = 20) -> EvaluationSuiteSpec:
    if tasks_per_category < 1 or tasks_per_category > 100:
        raise ValueError("tasks_per_category must be between 1 and 100")
    rng = random.Random(seed)
    tasks = [
        builder(rng, index)
        for builder in _BUILDERS
        for index in range(tasks_per_category)
    ]
    rng.shuffle(tasks)
    return EvaluationSuiteSpec(
        name="Orbita Hidden Adjudication Benchmark",
        version="1.0",
        tasks=tuple(tasks),
        seed=seed,
        metadata={
            "holdout": True,
            "tasks_per_category": tasks_per_category,
            "publication_boundary": (
                "Private seeded holdout. Publish public tasks only after evaluation; "
                "do not expose the seed or private suite during a run."
            ),
        },
    )


def sanitize_public_bundle(exported: dict[str, Any]) -> dict[str, Any]:
    """Remove private-seed and private-gold hash material from a public export."""

    suite = dict(exported["suite"])
    public = {
        "api_version": exported["api_version"],
        "response_schema_version": exported["response_schema_version"],
        "suite": {
            key: suite[key]
            for key in ("id", "name", "version", "metadata")
            if key in suite
        },
        "tasks": list(exported["tasks"]),
        "response_schema": exported["response_schema"],
    }
    stable = json.dumps(public, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    public["export_hash"] = hashlib.sha256(stable.encode("utf-8")).hexdigest()
    return public
