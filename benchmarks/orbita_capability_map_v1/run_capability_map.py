from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from benchmarks.hidden_adjudication_v1.suite import build_hidden_suite
from orbita_agent.adjudication import (
    MAX_CONTEXT_ITEMS,
    MAX_TEXT_CHARACTERS,
    AdjudicationError,
    adjudicate_epistemic_task,
    assess_adjudication_coverage,
)
from orbita_agent.code_context import compress_code_context

SEED = 20260726
CATEGORIES = (
    "unsupported_commitment",
    "contradiction_recovery",
    "evidence_collapse",
    "evidence_preservation",
    "false_success",
    "replicated_discovery",
    "temporal_scope",
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _decision_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    specifications = (
        ("claim_judgments", "claim_id", ("evidence_ids", "derivation_ids")),
        ("action_judgments", "action_id", ("receipt_ids",)),
        ("discovery_judgments", "hypothesis_id", ("evidence_ids",)),
    )
    for field, identifier, reference_fields in specifications:
        for judgment in result[field]:
            mapped[str(judgment[identifier])] = {
                "state": judgment["state"],
                **{
                    name: sorted(str(value) for value in judgment.get(name, []))
                    for name in reference_fields
                },
            }
    return mapped


def _expected_states(gold: dict[str, Any]) -> dict[str, str]:
    states: dict[str, str] = {}
    for kind in ("claims", "actions", "discoveries"):
        for identifier, expected in gold.get(kind, {}).items():
            states[str(identifier)] = str(expected["final_state"])
    return states


def _observed_states(result: dict[str, Any]) -> dict[str, str]:
    return {
        identifier: judgment["state"]
        for identifier, judgment in _decision_map(result).items()
    }


def _score_states(result: dict[str, Any], gold: dict[str, Any]) -> tuple[int, int]:
    expected = _expected_states(gold)
    observed = _observed_states(result)
    correct = sum(observed.get(identifier) == state for identifier, state in expected.items())
    return correct, len(expected)


def _negative_route(task: dict[str, Any]) -> dict[str, Any]:
    negative = copy.deepcopy(task)
    category = negative["category"]
    if category == "unsupported_commitment":
        for item in negative["context"]:
            item["kind"] = "free_form_narrative"
    elif category == "contradiction_recovery":
        negative["sequence"] = []
    elif category in {"evidence_collapse", "evidence_preservation"}:
        negative["context"] = [
            item
            for item in negative["context"]
            if item.get("kind") not in {"proof", "derivation"}
        ]
    elif category == "false_success":
        negative["context"] = [
            {
                "id": f"free_log_{negative['id']}",
                "kind": "free_form_log",
                "text": "The console printed a result.",
            }
        ]
    elif category == "replicated_discovery":
        negative["context"] = [
            {
                "id": f"free_analysis_{negative['id']}",
                "kind": "free_form_analysis",
                "text": "An analyst described a possible discovery.",
            }
        ]
    elif category == "temporal_scope":
        for item in negative["context"]:
            item["kind"] = "free_form_narrative"
    return negative


def _safe_distractor(task: dict[str, Any]) -> dict[str, Any] | None:
    if not task["context"]:
        return None
    transformed = copy.deepcopy(task)
    category = transformed["category"]
    kind = {
        "unsupported_commitment": "blog",
        "contradiction_recovery": "study",
        "evidence_collapse": "evidence",
        "evidence_preservation": "evidence",
        "false_success": "execution_receipt",
        "replicated_discovery": "analysis_receipt",
        "temporal_scope": "sensor",
    }[category]
    unrelated_token = hashlib.sha256(
        f"unrelated-control:{transformed['id']}".encode()
    ).hexdigest()[:12]
    distractor: dict[str, Any] = {
        "id": f"irrelevant_{unrelated_token}",
        "kind": kind,
        "text": "Unrelated bounded control record.",
    }
    if kind == "execution_receipt":
        distractor["exit_code"] = 9
    elif kind == "analysis_receipt":
        distractor.update(
            {
                "hypothesis": f"unrelated_{unrelated_token}",
                "outcome": "support",
                "independence_key": f"other_{unrelated_token}",
            }
        )
    elif kind in {"evidence", "sensor", "study"}:
        distractor.update(
            {"claim": f"unrelated_{unrelated_token}", "outcome": "support"}
        )
    transformed["context"].insert(0, distractor)
    return transformed


def _counterfactual_pairs(count: int) -> list[dict[str, Any]]:
    pairs = []
    for index in range(count):
        token = f"cf_{index:03d}"

        claim = f"claim_{token}"
        weak = {
            "id": f"weak_{token}",
            "kind": "blog",
            "claim": claim,
            "text": "An unsupported assertion.",
        }
        before = _task(token, "unsupported_commitment", [weak], claims=[claim])
        after = copy.deepcopy(before)
        after["context"].append(
            {
                "id": f"study_{token}",
                "kind": "study",
                "claim": claim,
                "outcome": "support",
            }
        )
        pairs.append(_pair("weak_to_supported", before, after, "unknown", "supported"))

        support_id, refute_id = f"support_{token}", f"refute_{token}"
        before = _task(
            token,
            "contradiction_recovery",
            [
                {
                    "id": support_id,
                    "kind": "study",
                    "claim": claim,
                    "outcome": "support",
                },
                {
                    "id": refute_id,
                    "kind": "regulator",
                    "claim": claim,
                    "outcome": "refute",
                },
            ],
            claims=[claim],
            sequence=[{"event": "refute", "evidence_id": refute_id}],
        )
        after = copy.deepcopy(before)
        after["context"][1]["outcome"] = "support"
        after["sequence"] = [{"event": "support", "evidence_id": refute_id}]
        pairs.append(_pair("refutation_removed", before, after, "challenged", "supported"))

        left, right = f"alpha{index:03d}", f"beta{index:03d}"
        left_id, right_id, proof_id = (
            f"e_left_{token}",
            f"e_right_{token}",
            f"proof_{token}",
        )
        context = [
            {
                "id": left_id,
                "kind": "evidence",
                "claim": left,
                "outcome": "support",
            },
            {
                "id": right_id,
                "kind": "evidence",
                "claim": right,
                "outcome": "support",
            },
            {
                "id": proof_id,
                "kind": "proof",
                "premises": [left, right],
                "conclusion": claim,
            },
        ]
        before = _task(
            token,
            "evidence_collapse",
            context,
            claims=[claim],
            sequence=[{"event": "revoke", "evidence_id": right_id}],
        )
        after = copy.deepcopy(before)
        after["sequence"] = []
        pairs.append(_pair("premise_restored", before, after, "unknown", "supported"))

        direct, direct_id, direct_proof = (
            f"gamma{index:03d}",
            f"e_direct_{token}",
            f"proof_direct_{token}",
        )
        preservation_context = context + [
            {
                "id": direct_id,
                "kind": "evidence",
                "claim": direct,
                "outcome": "support",
            },
            {
                "id": direct_proof,
                "kind": "proof",
                "premises": [direct],
                "conclusion": claim,
            },
        ]
        before = _task(
            token,
            "evidence_preservation",
            preservation_context,
            claims=[claim],
            sequence=[{"event": "revoke", "evidence_id": right_id}],
        )
        after = copy.deepcopy(before)
        after["sequence"].append({"event": "revoke", "evidence_id": direct_id})
        pairs.append(_pair("alternate_proof_revoked", before, after, "supported", "unknown"))

        action = f"action_{token}"
        receipt = {
            "id": f"receipt_{token}",
            "kind": "execution_receipt",
            "exit_code": 0,
            "checks_passed": True,
            "artifact_exists": True,
        }
        before = _task(token, "false_success", [receipt], actions=[action])
        after = copy.deepcopy(before)
        after["context"][0]["exit_code"] = 7
        pairs.append(_pair("exit_code_flip", before, after, "success", "failure"))

        discovery = f"hypothesis_{token}"
        receipts = [
            {
                "id": f"analysis_a_{token}",
                "kind": "analysis_receipt",
                "hypothesis": discovery,
                "outcome": "support",
                "independence_key": f"data_a_{token}",
            },
            {
                "id": f"analysis_b_{token}",
                "kind": "analysis_receipt",
                "hypothesis": discovery,
                "outcome": "support",
                "independence_key": f"data_b_{token}",
            },
        ]
        before = _task(token, "replicated_discovery", receipts, discoveries=[discovery])
        after = copy.deepcopy(before)
        after["context"][1]["independence_key"] = f"data_a_{token}"
        pairs.append(_pair("independence_removed", before, after, "committed", "provisional"))

        temporal_claim = f"valve_{token}_morning_open"
        observation = {
            "id": f"sensor_{token}",
            "kind": "sensor",
            "claim": temporal_claim,
            "time_scope": "morning",
            "outcome": "support",
        }
        before = _task(token, "temporal_scope", [observation], claims=[temporal_claim])
        after = copy.deepcopy(before)
        after["context"][0]["outcome"] = "refute"
        pairs.append(_pair("observation_invalidated", before, after, "supported", "unknown"))
    return pairs


def _task(
    token: str,
    category: str,
    context: list[dict[str, Any]],
    *,
    claims: list[str] | None = None,
    actions: list[str] | None = None,
    discoveries: list[str] | None = None,
    sequence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{category}_{token}",
        "category": category,
        "prompt": "Adjudicate the structured record.",
        "context": context,
        "sequence": sequence or [],
        "targets": {
            "claims": claims or [],
            "actions": actions or [],
            "discoveries": discoveries or [],
        },
    }


def _pair(
    profile: str,
    before: dict[str, Any],
    after: dict[str, Any],
    before_state: str,
    after_state: str,
) -> dict[str, Any]:
    return {
        "profile": profile,
        "before": before,
        "after": after,
        "before_state": before_state,
        "after_state": after_state,
    }


def _code_tasks(count: int) -> list[dict[str, Any]]:
    tasks = []
    for index in range(count):
        symbol = f"retry_window_{index:03d}"
        source = {
            "path": f"src/{symbol}.py",
            "content": f"def {symbol}(request):\n    return request\n",
        }
        test = {
            "path": f"tests/test_{symbol}.py",
            "content": f"from src.{symbol} import {symbol}\n",
        }
        helper = {
            "path": "src/policy_guard.py",
            "content": "def enforce_policy(value):\n    return value\n",
        }
        distractors = [
            {
                "path": f"src/unrelated_{index:03d}_{position:02d}.py",
                "content": f"def utility_{position}(value):\n    return value\n",
            }
            for position in range(16)
        ]

        tasks.append(
            {
                "profile": "explicit_symbol",
                "issue": f"{symbol} violates its retry limit",
                "files": [source, test, *distractors],
                "required": [source["path"], test["path"]],
                "max_files": 2,
            }
        )
        tasks.append(
            {
                "profile": "explicit_filename",
                "issue": f"fix {symbol}.py and test_{symbol}.py",
                "files": [source, test, *distractors],
                "required": [source["path"], test["path"]],
                "max_files": 2,
            }
        )
        tasks.append(
            {
                "profile": "paraphrased_alias",
                "issue": "requests should try again only during the permitted interval",
                "files": [*distractors, source, test],
                "required": [source["path"], test["path"]],
                "max_files": 2,
            }
        )
        tasks.append(
            {
                "profile": "hidden_dependency",
                "issue": f"{symbol} violates its retry limit",
                "files": [source, test, helper, *distractors],
                "required": [source["path"], test["path"], helper["path"]],
                "max_files": 3,
            }
        )
        adversary = {
            "path": f"docs/{symbol}_migration.py",
            "content": f"# Historical notes for {symbol}; do not edit.\n",
        }
        tasks.append(
            {
                "profile": "adversarial_path_distractor",
                "issue": f"{symbol} violates its retry limit",
                "files": [adversary, source, test, *distractors],
                "required": [source["path"], test["path"]],
                "max_files": 2,
            }
        )
    return tasks


def _audit_ok(task: dict[str, Any], result: dict[str, Any]) -> bool:
    context_ids = {
        str(item["id"])
        for item in task["context"]
        if isinstance(item.get("id"), str)
    }
    audit = {(str(item["kind"]), str(item["id"])) for item in result["audit_trace"]}
    references = set()
    for judgment in result["claim_judgments"]:
        references.update(("evidence", value) for value in judgment.get("evidence_ids", []))
        references.update(("proof", value) for value in judgment.get("derivation_ids", []))
    for judgment in result["action_judgments"]:
        references.update(
            ("execution_receipt", value)
            for value in judgment.get("receipt_ids", [])
        )
    for judgment in result["discovery_judgments"]:
        references.update(
            ("analysis_receipt", value)
            for value in judgment.get("evidence_ids", [])
        )
    basis = result["decision_basis"]
    return (
        result["task_hash"] == _sha256(task)
        and basis["model_calls"] == 0
        and basis["network_calls"] == 0
        and all(identifier in context_ids for _, identifier in audit)
        and references.issubset(audit)
    )


def _grade(value: float) -> str:
    if value >= 0.95:
        return "demonstrated"
    if value >= 0.75:
        return "strong_with_boundaries"
    if value >= 0.5:
        return "partial"
    return "limited"


def run_benchmark(
    *,
    seed: int = SEED,
    tasks_per_category: int = 20,
    determinism_repeats: int = 5,
    permutation_repeats: int = 3,
    scale_repeats: int = 100,
) -> dict[str, Any]:
    suite = build_hidden_suite(seed, tasks_per_category=tasks_per_category)
    private_tasks = [
        {"task": specification.public_dict(), "gold": specification.gold}
        for specification in suite.tasks
    ]
    baselines: dict[str, dict[str, Any]] = {}
    state_correct = state_total = 0
    category_scores: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    unknown_correct = unknown_total = 0
    audit_correct = 0

    for item in private_tasks:
        task, gold = item["task"], item["gold"]
        result = adjudicate_epistemic_task(task)
        baselines[task["id"]] = result
        correct, total = _score_states(result, gold)
        state_correct += correct
        state_total += total
        category_scores[task["category"]][0] += correct
        category_scores[task["category"]][1] += total
        expected = _expected_states(gold)
        for identifier, state in expected.items():
            if state == "unknown":
                unknown_total += 1
                unknown_correct += _observed_states(result).get(identifier) == "unknown"
        audit_correct += _audit_ok(task, result)

    exact_repeats = 0
    repeat_comparisons = len(private_tasks) * determinism_repeats
    for item in private_tasks:
        task = item["task"]
        expected = _stable_json(baselines[task["id"]])
        exact_repeats += sum(
            _stable_json(adjudicate_epistemic_task(task)) == expected
            for _ in range(determinism_repeats)
        )

    rng = random.Random(seed ^ 0xA11CE)
    order_correct = 0
    order_comparisons = len(private_tasks) * permutation_repeats
    for item in private_tasks:
        task = item["task"]
        expected = _decision_map(baselines[task["id"]])
        for _ in range(permutation_repeats):
            shuffled = copy.deepcopy(task)
            rng.shuffle(shuffled["context"])
            observed = _decision_map(adjudicate_epistemic_task(shuffled))
            order_correct += observed == expected

    distractor_correct = distractor_total = 0
    for item in private_tasks:
        transformed = _safe_distractor(item["task"])
        if transformed is None:
            continue
        distractor_total += 1
        observed = _decision_map(adjudicate_epistemic_task(transformed))
        expected = _decision_map(baselines[item["task"]["id"]])
        distractor_correct += observed == expected

    positive_routes = sum(
        assess_adjudication_coverage(item["task"])["covered"]
        for item in private_tasks
    )
    negative_routes = sum(
        not assess_adjudication_coverage(_negative_route(item["task"]))["covered"]
        for item in private_tasks
    )
    route_total = 2 * len(private_tasks)

    pairs = _counterfactual_pairs(10)
    counterfactual_correct = 0
    counterfactual_profiles: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for pair in pairs:
        before = list(_observed_states(adjudicate_epistemic_task(pair["before"])).values())
        after = list(_observed_states(adjudicate_epistemic_task(pair["after"])).values())
        passed = (
            before == [pair["before_state"]]
            and after == [pair["after_state"]]
            and before != after
        )
        counterfactual_correct += passed
        counterfactual_profiles[pair["profile"]][0] += passed
        counterfactual_profiles[pair["profile"]][1] += 1

    invalid = [
        {**copy.deepcopy(private_tasks[0]["task"]), "gold": {}},
        {
            **copy.deepcopy(private_tasks[0]["task"]),
            "targets": {"claims": ["same", "same"], "actions": [], "discoveries": []},
        },
        {**copy.deepcopy(private_tasks[0]["task"]), "context": [{}] * (MAX_CONTEXT_ITEMS + 1)},
        {**copy.deepcopy(private_tasks[0]["task"]), "prompt": "x" * (MAX_TEXT_CHARACTERS + 1)},
        {**copy.deepcopy(private_tasks[0]["task"]), "id": ""},
        {**copy.deepcopy(private_tasks[0]["task"]), "context": "not-an-array"},
        [],
    ]
    invalid_rejected = 0
    for task in invalid:
        try:
            adjudicate_epistemic_task(task)  # type: ignore[arg-type]
        except AdjudicationError:
            invalid_rejected += 1

    scale = []
    for size in (1, 8, 32, 128, 256):
        claim = f"scale_claim_{size}"
        context = [
            {
                "id": f"scale_support_{size}",
                "kind": "study",
                "claim": claim,
                "outcome": "support",
            }
        ]
        context.extend(
            {
                "id": f"scale_noise_{size}_{index}",
                "kind": "blog",
                "claim": f"unrelated_{index}",
            }
            for index in range(size - 1)
        )
        task = _task(f"scale_{size}", "unsupported_commitment", context, claims=[claim])
        latencies = []
        accurate = 0
        for _ in range(scale_repeats):
            start = time.perf_counter()
            result = adjudicate_epistemic_task(task)
            latencies.append((time.perf_counter() - start) * 1000)
            accurate += list(_observed_states(result).values()) == ["supported"]
        scale.append(
            {
                "context_items": size,
                "runs": scale_repeats,
                "accuracy": accurate / scale_repeats,
                "median_latency_ms": statistics.median(latencies),
                "p95_latency_ms": _percentile(latencies, 0.95),
            }
        )

    code_profile: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"tasks": 0, "exact": 0, "required": 0, "retained": 0, "reduction_sum": 0.0}
    )
    for task in _code_tasks(20):
        result = compress_code_context(
            task["issue"],
            task["files"],
            max_files=task["max_files"],
        )
        retained = set(result["receipt"]["retained_paths"])
        required = set(task["required"])
        profile = code_profile[task["profile"]]
        profile["tasks"] += 1
        profile["exact"] += required.issubset(retained)
        profile["required"] += len(required)
        profile["retained"] += len(required.intersection(retained))
        profile["reduction_sum"] += result["receipt"]["character_reduction"]

    code_results = {}
    for profile, values in code_profile.items():
        tasks = int(values["tasks"])
        code_results[profile] = {
            "tasks": tasks,
            "exact_required_file_recall": int(values["exact"]) / tasks,
            "required_file_recall": int(values["retained"]) / int(values["required"]),
            "mean_character_reduction": float(values["reduction_sum"]) / tasks,
        }

    category_results = {
        category: {
            "correct_decisions": values[0],
            "decisions": values[1],
            "accuracy": values[0] / values[1],
        }
        for category, values in sorted(category_scores.items())
    }
    measurements = {
        "state_policy_fidelity": {
            "correct_decisions": state_correct,
            "decisions": state_total,
            "accuracy": state_correct / state_total,
            "by_category": category_results,
        },
        "abstention_on_insufficient_records": {
            "correct_unknown_decisions": unknown_correct,
            "unknown_decisions": unknown_total,
            "accuracy": unknown_correct / unknown_total,
        },
        "exact_determinism": {
            "exact_repeats": exact_repeats,
            "comparisons": repeat_comparisons,
            "rate": exact_repeats / repeat_comparisons,
        },
        "context_order_invariance": {
            "invariant": order_correct,
            "comparisons": order_comparisons,
            "rate": order_correct / order_comparisons,
        },
        "irrelevant_distractor_invariance": {
            "invariant": distractor_correct,
            "comparisons": distractor_total,
            "rate": distractor_correct / distractor_total,
        },
        "coverage_routing": {
            "true_positive": positive_routes,
            "positive_tasks": len(private_tasks),
            "true_negative": negative_routes,
            "negative_tasks": len(private_tasks),
            "balanced_accuracy": (positive_routes + negative_routes) / route_total,
        },
        "counterfactual_sensitivity": {
            "correct_pairs": counterfactual_correct,
            "pairs": len(pairs),
            "rate": counterfactual_correct / len(pairs),
            "by_profile": {
                profile: {
                    "correct": values[0],
                    "pairs": values[1],
                    "rate": values[0] / values[1],
                }
                for profile, values in sorted(counterfactual_profiles.items())
            },
        },
        "audit_integrity": {
            "valid": audit_correct,
            "tasks": len(private_tasks),
            "rate": audit_correct / len(private_tasks),
        },
        "invalid_input_rejection": {
            "rejected": invalid_rejected,
            "cases": len(invalid),
            "rate": invalid_rejected / len(invalid),
        },
        "scale": scale,
        "code_context_selection": code_results,
    }
    capability_map = _capability_map(measurements)
    return {
        "schema_version": "orbita-capability-map-benchmark/1.0",
        "seed": seed,
        "model_calls": 0,
        "network_calls": 0,
        "base_tasks": len(private_tasks),
        "local_trials": (
            len(private_tasks)
            + repeat_comparisons
            + order_comparisons
            + distractor_total
            + route_total
            + (2 * len(pairs))
            + len(invalid)
            + (len(scale) * scale_repeats)
            + sum(int(value["tasks"]) for value in code_results.values())
        ),
        "measurements": measurements,
        "capability_map": capability_map,
        "private_tasks": private_tasks,
    }


def _capability_map(measurements: dict[str, Any]) -> list[dict[str, Any]]:
    categories = measurements["state_policy_fidelity"]["by_category"]
    entries = [
        ("governed_state_adjudication", measurements["state_policy_fidelity"]["accuracy"], 160),
        ("contradiction_recovery", categories["contradiction_recovery"]["accuracy"], 20),
        (
            "proof_dependency_reasoning",
            (
                categories["evidence_collapse"]["accuracy"]
                + categories["evidence_preservation"]["accuracy"]
            )
            / 2,
            40,
        ),
        ("execution_receipt_verification", categories["false_success"]["accuracy"], 20),
        ("replication_independence", categories["replicated_discovery"]["accuracy"], 20),
        ("temporal_scope_separation", categories["temporal_scope"]["accuracy"], 40),
        (
            "structured_abstention",
            measurements["abstention_on_insufficient_records"]["accuracy"],
            measurements["abstention_on_insufficient_records"]["unknown_decisions"],
        ),
        ("coverage_aware_routing", measurements["coverage_routing"]["balanced_accuracy"], 280),
        ("exact_determinism", measurements["exact_determinism"]["rate"], 700),
        ("context_order_robustness", measurements["context_order_invariance"]["rate"], 420),
        (
            "irrelevant_distractor_robustness",
            measurements["irrelevant_distractor_invariance"]["rate"],
            measurements["irrelevant_distractor_invariance"]["comparisons"],
        ),
        (
            "counterfactual_sensitivity",
            measurements["counterfactual_sensitivity"]["rate"],
            measurements["counterfactual_sensitivity"]["pairs"],
        ),
        ("audit_receipt_integrity", measurements["audit_integrity"]["rate"], 140),
        ("input_boundary_enforcement", measurements["invalid_input_rejection"]["rate"], 7),
        (
            "explicit_code_context_selection",
            measurements["code_context_selection"]["explicit_symbol"][
                "exact_required_file_recall"
            ],
            20,
        ),
        (
            "paraphrased_code_context_selection",
            measurements["code_context_selection"]["paraphrased_alias"][
                "exact_required_file_recall"
            ],
            20,
        ),
        (
            "hidden_dependency_code_selection",
            measurements["code_context_selection"]["hidden_dependency"][
                "exact_required_file_recall"
            ],
            20,
        ),
        (
            "adversarial_code_context_selection",
            measurements["code_context_selection"]["adversarial_path_distractor"][
                "exact_required_file_recall"
            ],
            20,
        ),
    ]
    return [
        {
            "capability": name,
            "score": value,
            "grade": _grade(value),
            "evaluated_cases": cases,
        }
        for name, value, cases in entries
    ]


def _report(result: dict[str, Any]) -> str:
    measurements = result["measurements"]
    lines = [
        "# Orbita Capability Map v1",
        "",
        "## Executive result",
        "",
        (
            f"This run executed **{result['local_trials']:,} local trials** with "
            "**zero model calls and zero network calls**."
        ),
        "",
        "| Capability | Score | Grade | Cases |",
        "|---|---:|---|---:|",
    ]
    for entry in result["capability_map"]:
        lines.append(
            f"| {entry['capability'].replace('_', ' ').title()} "
            f"| {entry['score']:.1%} | {entry['grade']} | {entry['evaluated_cases']} |"
        )
    lines.extend(
        [
            "",
            "## State-policy fidelity",
            "",
            (
                f"Orbita produced {measurements['state_policy_fidelity']['correct_decisions']}/"
                f"{measurements['state_policy_fidelity']['decisions']} correct governed "
                "state decisions."
            ),
            "",
            "| Category | Accuracy | Decisions |",
            "|---|---:|---:|",
        ]
    )
    for category, value in measurements["state_policy_fidelity"]["by_category"].items():
        lines.append(
            f"| {category.replace('_', ' ')} | {value['accuracy']:.1%} "
            f"| {value['decisions']} |"
        )
    lines.extend(
        [
            "",
            "## Reliability and boundaries",
            "",
            (
                f"- Exact repeated-output determinism: "
                f"{measurements['exact_determinism']['rate']:.1%}."
            ),
            (
                f"- Context-order invariance: "
                f"{measurements['context_order_invariance']['rate']:.1%}."
            ),
            (
                f"- Safe irrelevant-distractor invariance: "
                f"{measurements['irrelevant_distractor_invariance']['rate']:.1%}."
            ),
            (
                f"- Minimal counterfactual sensitivity: "
                f"{measurements['counterfactual_sensitivity']['rate']:.1%}."
            ),
            (
                f"- Coverage router balanced accuracy: "
                f"{measurements['coverage_routing']['balanced_accuracy']:.1%}."
            ),
            (
                f"- Audit/hash integrity: "
                f"{measurements['audit_integrity']['rate']:.1%}."
            ),
            (
                f"- Invalid-input rejection: "
                f"{measurements['invalid_input_rejection']['rate']:.1%}."
            ),
            "",
            "## Code-context capability by prompt type",
            "",
            "| Profile | Complete required-file recall | File recall | Character reduction |",
            "|---|---:|---:|---:|",
        ]
    )
    for profile, value in measurements["code_context_selection"].items():
        lines.append(
            f"| {profile.replace('_', ' ')} "
            f"| {value['exact_required_file_recall']:.1%} "
            f"| {value['required_file_recall']:.1%} "
            f"| {value['mean_character_reduction']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Scaling",
            "",
            "| Context records | Accuracy | Median latency | P95 latency |",
            "|---:|---:|---:|---:|",
        ]
    )
    for value in measurements["scale"]:
        lines.append(
            f"| {value['context_items']} | {value['accuracy']:.1%} "
            f"| {value['median_latency_ms']:.3f} ms "
            f"| {value['p95_latency_ms']:.3f} ms |"
        )
    prior = result["prior_empirical_evidence"]
    lines.extend(
        [
            "",
            "## Prior model-comparison evidence",
            "",
            "These results are supporting evidence from earlier runs. They are not "
            "included in the local-trial count above.",
            "",
            "| Experiment | Baseline | Orbita condition | Other result |",
            "|---|---:|---:|---:|",
            (
                f"| Coverage-aware routing ({prior['routing']['tasks']} tasks) "
                f"| {prior['routing']['direct_accuracy']:.1%} accuracy "
                f"| {prior['routing']['routed_accuracy']:.1%} accuracy "
                f"| {prior['routing']['total_token_reduction']:.1%} fewer tokens |"
            ),
            (
                f"| Semantic compression pilot ({prior['semantic_pilot']['tasks']} tasks) "
                f"| {prior['semantic_pilot']['full_accuracy']:.1%} accuracy "
                f"| {prior['semantic_pilot']['compact_accuracy']:.1%} accuracy "
                f"| {prior['semantic_pilot']['total_token_reduction']:.1%} fewer tokens |"
            ),
            (
                f"| Semantic stress ({prior['semantic_stress']['tasks']} tasks) "
                f"| {prior['semantic_stress']['full_accuracy']:.1%} accuracy "
                f"| {prior['semantic_stress']['compact_accuracy']:.1%} accuracy "
                f"| {prior['semantic_stress']['total_token_reduction']:.1%} fewer tokens |"
            ),
            (
                f"| Executable coding pilot ({prior['coding']['tasks']} tasks) "
                f"| {prior['coding']['full_patches_passing']}/"
                f"{prior['coding']['tasks']} patches passed "
                f"| {prior['coding']['compact_patches_passing']}/"
                f"{prior['coding']['tasks']} patches passed "
                f"| {prior['coding']['total_token_reduction']:.1%} fewer tokens |"
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The benchmark supports Orbita as a deterministic governed-decision and "
            "verification layer. Its strongest abilities are explicit state policy, "
            "proof and receipt handling, declared replication independence, audit "
            "receipts, and routing structured records away from a model.",
            "",
            "The benchmark does not support treating Orbita as a general semantic "
            "reasoner. Code-context selection remains lexical: explicit symbols are "
            "a favorable case, while paraphrases, hidden dependencies, and adversarial "
            "filenames expose its boundary.",
            "",
            "These tasks are synthetic and schema-controlled. Scores establish engine "
            "behavior inside the declared contract, not scientific truth, open-domain "
            "reasoning, or performance on arbitrary production repositories.",
            "",
        ]
    )
    return "\n".join(lines)


def _prior_evidence(repo_root: Path) -> tuple[dict[str, Any], list[Path]]:
    mixed_path = (
        repo_root
        / "benchmarks/hidden_adjudication_v1/runs/mixed-router-2026-07-26/summary.json"
    )
    semantic_path = (
        repo_root
        / "benchmarks/hidden_adjudication_v1/runs/semantic-compression-2026-07-26/summary.json"
    )
    coding_path = (
        repo_root
        / "benchmarks/hidden_adjudication_v1/runs/coding-context-2026-07-26/summary.json"
    )
    stress_path = (
        repo_root
        / "benchmarks/orbita_capability_v1/runs/semantic-stress-2026-07-26/"
        "capability_report.json"
    )
    paths = [mixed_path, semantic_path, coding_path, stress_path]
    mixed, semantic, coding, stress = (
        json.loads(path.read_text(encoding="utf-8")) for path in paths
    )
    evidence = {
        "routing": {
            "tasks": mixed["tasks"],
            "target_states": mixed["target_states"],
            "model": mixed["model"],
            "direct_accuracy": mixed["direct"]["adjudication_accuracy"],
            "routed_accuracy": mixed["orbita_routed"]["adjudication_accuracy"],
            "direct_gpt_calls": mixed["direct"]["operational_gpt_calls"],
            "routed_gpt_calls": mixed["orbita_routed"]["operational_gpt_calls"],
            "total_token_reduction": mixed["token_reduction"],
        },
        "semantic_pilot": {
            "tasks": semantic["tasks"],
            "model": semantic["model"],
            "full_accuracy": semantic["full_context"]["adjudication_accuracy"],
            "compact_accuracy": semantic["orbita_compact"]["adjudication_accuracy"],
            "total_token_reduction": semantic["total_token_reduction"],
        },
        "semantic_stress": {
            "tasks": stress["semantic_stress"]["tasks"],
            "full_accuracy": stress["semantic_stress"]["full_accuracy"],
            "compact_accuracy": stress["semantic_stress"]["compact_accuracy"],
            "total_token_reduction": stress["semantic_stress"]["total_token_reduction"],
            "unconditional_compression_safe": stress["headline"][
                "unconditional_compression_safe"
            ],
        },
        "coding": {
            "tasks": coding["tasks"],
            "full_patches_passing": coding["full_context"]["passing_patches"],
            "compact_patches_passing": coding["orbita_compact"]["passing_patches"],
            "total_token_reduction": coding["total_token_reduction"],
        },
    }
    return evidence, paths


def _write_manifest(repo_root: Path, run_dir: Path, paths: list[Path]) -> None:
    manifest = {
        "schema_version": "orbita-capability-map-manifest/1.0",
        "artifacts": [
            {
                "path": path.relative_to(repo_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in paths
        ],
    }
    (run_dir / "release_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--tasks-per-category", type=int, default=20)
    parser.add_argument("--determinism-repeats", type=int, default=5)
    parser.add_argument("--permutation-repeats", type=int, default=3)
    parser.add_argument("--scale-repeats", type=int, default=100)
    args = parser.parse_args()

    result = run_benchmark(
        seed=args.seed,
        tasks_per_category=args.tasks_per_category,
        determinism_repeats=args.determinism_repeats,
        permutation_repeats=args.permutation_repeats,
        scale_repeats=args.scale_repeats,
    )
    repo_root = Path(__file__).resolve().parents[2]
    prior_evidence, prior_paths = _prior_evidence(repo_root)
    result["prior_empirical_evidence"] = prior_evidence
    run_dir = args.out.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    private_tasks = result.pop("private_tasks")

    result_path = run_dir / "capability_map.json"
    private_path = run_dir / "private_tasks.json"
    report_path = run_dir / "CAPABILITY_MAP.md"
    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    private_path.write_text(
        json.dumps(private_tasks, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_report(result), encoding="utf-8")
    _write_manifest(
        repo_root,
        run_dir,
        [result_path, private_path, report_path, *prior_paths],
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
