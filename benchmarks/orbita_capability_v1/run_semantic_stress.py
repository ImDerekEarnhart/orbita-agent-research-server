#!/usr/bin/env python3
"""Run the multi-domain full-context versus Orbita-compressed comparison."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from semantic_stress_suite import private_suite_dict, semantic_stress_suite

from orbita.evaluation import ComparativeEvaluationRuntime
from orbita.ledger import EpistemicLedger
from orbita_agent.adjudication import compress_epistemic_task

_EMPIRICAL_PATH = (
    Path(__file__).resolve().parents[1]
    / "adversarial-epistemic-v1"
    / "run_empirical.py"
)
_EMPIRICAL_SPEC = importlib.util.spec_from_file_location(
    "orbita_capability_empirical",
    _EMPIRICAL_PATH,
)
if _EMPIRICAL_SPEC is None or _EMPIRICAL_SPEC.loader is None:
    raise RuntimeError(f"Could not load empirical runner: {_EMPIRICAL_PATH}")
_EMPIRICAL = importlib.util.module_from_spec(_EMPIRICAL_SPEC)
_EMPIRICAL_SPEC.loader.exec_module(_EMPIRICAL)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-context-items", type=int, default=3)
    return parser.parse_args()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _sanitize_public_bundle(exported: dict[str, Any]) -> dict[str, Any]:
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
    public["export_hash"] = hashlib.sha256(
        _stable_json(public).encode("utf-8")
    ).hexdigest()
    return public


def _required_ids(gold: dict[str, Any]) -> set[str]:
    required = set()
    for kind in ("claims", "actions", "discoveries"):
        for expected in gold.get(kind, {}).values():
            for field in ("required_evidence", "required_receipts", "required_derivations"):
                required.update(str(value) for value in expected.get(field, []))
    return required


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for result in payload["results"]:
        usage = result.get("token_usage", {})
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            totals[key] += int(usage.get(key) or 0)
    return dict(totals)


def _score_maps(scored: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["task_id"]): item["score"] for item in scored["results"]}


def _result_maps(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["task_id"]): item for item in payload["results"]}


def _slice_metrics(
    task_ids: list[str],
    score_map: dict[str, dict[str, Any]],
    result_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scores = [score_map[task_id] for task_id in task_ids]
    results = [result_map[task_id] for task_id in task_ids]
    opportunities = sum(
        int(score["counts"].get("target_state_opportunities", 0))
        for score in scores
    )
    correct = sum(
        int(score["counts"].get("target_states_correct", 0))
        for score in scores
    )
    return {
        "tasks": len(task_ids),
        "target_states": opportunities,
        "target_states_correct": correct,
        "adjudication_accuracy": correct / opportunities if opportunities else None,
        "mean_task_score": statistics.fmean(
            float(score["task_score"]) for score in scores
        ),
        "input_tokens": sum(
            int(result.get("token_usage", {}).get("input_tokens") or 0)
            for result in results
        ),
        "output_tokens": sum(
            int(result.get("token_usage", {}).get("output_tokens") or 0)
            for result in results
        ),
        "total_tokens": sum(
            int(result.get("token_usage", {}).get("total_tokens") or 0)
            for result in results
        ),
        "latency_ms": sum(float(result.get("latency_ms") or 0) for result in results),
    }


def _comparison(full: dict[str, Any], compact: dict[str, Any]) -> dict[str, Any]:
    full_tokens = int(full["total_tokens"])
    compact_tokens = int(compact["total_tokens"])
    return {
        "accuracy_delta": (
            float(compact["adjudication_accuracy"]) - float(full["adjudication_accuracy"])
        ),
        "mean_task_score_delta": (
            float(compact["mean_task_score"]) - float(full["mean_task_score"])
        ),
        "input_token_reduction": (
            1.0 - (int(compact["input_tokens"]) / int(full["input_tokens"]))
            if full["input_tokens"]
            else None
        ),
        "total_token_reduction": (
            1.0 - (compact_tokens / full_tokens) if full_tokens else None
        ),
    }


def main() -> None:
    args = _parse_args()
    out = args.out.resolve()
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite output directory: {out}")
    out.mkdir(parents=True)
    spec = semantic_stress_suite()

    with EpistemicLedger(out / "benchmark.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, out / "workspace")
        suite = runtime.create_suite(spec)
        public = _sanitize_public_bundle(runtime.export_public_suite(suite["id"]))
        compressed = [
            compress_epistemic_task(task, max_context_items=args.max_context_items)
            for task in public["tasks"]
        ]
        compact_tasks = [item["task"] for item in compressed]

        full_payload, full_receipts = _EMPIRICAL._run_system(
            public["tasks"],
            "openai",
            args.model,
            "direct",
            max(1, args.workers),
        )
        compact_payload, compact_receipts = _EMPIRICAL._run_system(
            compact_tasks,
            "openai",
            args.model,
            "direct",
            max(1, args.workers),
        )
        compact_payload["system"] = {
            **compact_payload["system"],
            "kind": "orbita",
            "name": f"{args.model} — Orbita capability compression",
            "config": {
                "condition": "orbita-compressed-context",
                "max_context_items": args.max_context_items,
                "compressor_model_calls": 0,
            },
        }
        compact_payload["metadata"] = {
            **compact_payload.get("metadata", {}),
            "gold_visible_to_compressor": False,
            "compression_receipts": [item["receipt"] for item in compressed],
        }

        full_scored = runtime.import_run(suite["id"], full_payload)
        compact_scored = runtime.import_run(suite["id"], compact_payload)
        report = runtime.compile_report(suite["id"])
        verification = {
            "suite": runtime.verify_suite(suite["id"]),
            "full_run": runtime.verify_run(full_scored["id"]),
            "compact_run": runtime.verify_run(compact_scored["id"]),
            "report": runtime.verify_report(suite["id"]),
        }

    full_scores = _score_maps(full_scored)
    compact_scores = _score_maps(compact_scored)
    full_results = _result_maps(full_payload)
    compact_results = _result_maps(compact_payload)
    receipt_map = {
        item["receipt"]["task_id"]: item["receipt"]
        for item in compressed
    }
    task_by_id = {task.id: task for task in spec.tasks}

    slices: dict[str, dict[str, list[str]]] = {
        "prompt_profile": defaultdict(list),
        "domain": defaultdict(list),
        "lexical_alignment": defaultdict(list),
        "noise": defaultdict(list),
        "evidence_topology": defaultdict(list),
        "adversarial": defaultdict(list),
    }
    for task in spec.tasks:
        for dimension in slices:
            slices[dimension][str(task.metadata[dimension])].append(task.id)

    slice_report: dict[str, Any] = {}
    for dimension, values in slices.items():
        slice_report[dimension] = {}
        for value, task_ids in sorted(values.items()):
            full = _slice_metrics(task_ids, full_scores, full_results)
            compact = _slice_metrics(task_ids, compact_scores, compact_results)
            required = sum(len(_required_ids(task_by_id[task_id].gold)) for task_id in task_ids)
            retained = sum(
                len(
                    _required_ids(task_by_id[task_id].gold).intersection(
                        receipt_map[task_id]["retained_ids"]
                    )
                )
                for task_id in task_ids
            )
            task_outcomes = Counter()
            for task_id in task_ids:
                delta = (
                    float(compact_scores[task_id]["task_score"])
                    - float(full_scores[task_id]["task_score"])
                )
                task_outcomes[
                    "better" if delta > 1e-12 else "worse" if delta < -1e-12 else "tie"
                ] += 1
            slice_report[dimension][value] = {
                "full": full,
                "compact": compact,
                "comparison": _comparison(full, compact),
                "required_evidence_recall": retained / required if required else None,
                "task_outcomes": dict(task_outcomes),
            }

    all_ids = [task.id for task in spec.tasks]
    full_overall = _slice_metrics(all_ids, full_scores, full_results)
    compact_overall = _slice_metrics(all_ids, compact_scores, compact_results)
    summary = {
        "tasks": len(spec.tasks),
        "domains": len({task.metadata["domain"] for task in spec.tasks}),
        "prompt_profiles": len(
            {task.metadata["prompt_profile"] for task in spec.tasks}
        ),
        "model": args.model,
        "gpt_calls_executed": len(full_receipts) + len(compact_receipts),
        "full_context": full_overall,
        "orbita_compact": compact_overall,
        "comparison": _comparison(full_overall, compact_overall),
        "usage": {
            "full": _usage(full_payload),
            "compact": _usage(compact_payload),
        },
        "slices": slice_report,
        "verification": verification,
        "interpretation_boundary": (
            "Thirty-six procedurally generated semantic tasks crossing six domains and six "
            "prompt profiles. Full and compact arms used independent GPT calls. "
            "Gold was scorer-only."
        ),
    }

    _write_json(out / "private_suite.json", private_suite_dict(spec))
    _write_json(out / "public_tasks.json", public)
    _write_json(out / "compact_tasks.json", {"tasks": compact_tasks})
    _write_json(out / "compression_receipts.json", [item["receipt"] for item in compressed])
    _write_json(out / "gpt-full.response.json", full_payload)
    _write_json(out / "gpt-full.receipts.json", full_receipts)
    _write_json(out / "gpt-compact.response.json", compact_payload)
    _write_json(out / "gpt-compact.receipts.json", compact_receipts)
    _write_json(out / "report.json", report["report"])
    _write_json(out / "summary.json", summary)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "tasks",
                    "domains",
                    "prompt_profiles",
                    "model",
                    "gpt_calls_executed",
                    "full_context",
                    "orbita_compact",
                    "comparison",
                    "verification",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
