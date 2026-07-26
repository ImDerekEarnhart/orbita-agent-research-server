#!/usr/bin/env python3
"""Run a replay-controlled structured plus semantic routing benchmark."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from run_gpt_router_pilot import (
    _EMPIRICAL,
    _cumulative_latency_ms,
    _orbita_result,
    _usage,
)
from semantic_suite import semantic_holdout_tasks
from suite import sanitize_public_bundle

from orbita.evaluation import (
    ComparativeEvaluationRuntime,
    EvaluationSuiteSpec,
)
from orbita.ledger import EpistemicLedger
from orbita_agent.adjudication import assess_adjudication_coverage


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-suite", type=Path, required=True)
    parser.add_argument("--prior-pilot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--workers", type=int, default=2)
    return parser.parse_args()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _result_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(result["task_id"]): dict(result) for result in payload["results"]}


def _receipt_map(receipts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(receipt["task_id"]): dict(receipt) for receipt in receipts}


def main() -> None:
    args = _parse_args()
    out = args.out.resolve()
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite output directory: {out}")
    out.mkdir(parents=True)

    prior = args.prior_pilot.resolve()
    prior_public = json.loads((prior / "public_tasks.json").read_text(encoding="utf-8"))
    prior_direct = json.loads((prior / "gpt-direct.response.json").read_text(encoding="utf-8"))
    prior_receipts = json.loads((prior / "gpt-direct.receipts.json").read_text(encoding="utf-8"))
    structured_ids = {str(task["id"]) for task in prior_public["tasks"]}

    full_private = EvaluationSuiteSpec.from_dict(
        json.loads(args.private_suite.resolve().read_text(encoding="utf-8"))
    )
    structured_specs = [task for task in full_private.tasks if task.id in structured_ids]
    if len(structured_specs) != len(structured_ids):
        raise RuntimeError("Prior pilot tasks do not match the private suite")
    semantic_specs = list(semantic_holdout_tasks())
    combined_spec = EvaluationSuiteSpec(
        name="Orbita Mixed Structured-Semantic Router Benchmark",
        version="1.0",
        tasks=tuple(structured_specs + semantic_specs),
        seed=full_private.seed,
        metadata={
            "structured_tasks": len(structured_specs),
            "semantic_tasks": len(semantic_specs),
            "gold_visible_to_systems": False,
            "replay_controlled": True,
        },
    )

    with EpistemicLedger(out / "benchmark.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, out / "workspace")
        suite = runtime.create_suite(combined_spec)
        public = sanitize_public_bundle(runtime.export_public_suite(suite["id"]))
        public_by_id = {str(task["id"]): task for task in public["tasks"]}
        semantic_public = [public_by_id[task.id] for task in semantic_specs]

        semantic_payload, semantic_receipts = _EMPIRICAL._run_system(
            semantic_public,
            "openai",
            args.model,
            "direct",
            max(1, args.workers),
        )
        direct_results = {**_result_map(prior_direct), **_result_map(semantic_payload)}
        direct_receipt_map = {
            **_receipt_map(prior_receipts),
            **_receipt_map(semantic_receipts),
        }
        ordered_direct_results = [direct_results[str(task["id"])] for task in public["tasks"]]
        ordered_direct_receipts = [
            direct_receipt_map[str(task["id"])] for task in public["tasks"]
        ]
        direct_payload = {
            "schema_version": "1.0",
            "system": {
                "kind": "base_llm",
                "name": f"{args.model} — direct mixed-domain replay",
                "version": args.model,
                "provider": "OpenAI",
                "evaluation_mode": "replay",
                "config": {
                    "condition": "direct",
                    "structured_responses_reused": True,
                    "semantic_responses_fresh": True,
                },
            },
            "results": ordered_direct_results,
            "metadata": {
                "gold_visible_to_system": False,
                "new_gpt_calls": len(semantic_receipts),
            },
        }

        semantic_result_map = _result_map(semantic_payload)
        semantic_receipt_map = _receipt_map(semantic_receipts)
        routed_results = []
        routed_receipts = []
        route_counts: Counter[str] = Counter()
        for task in public["tasks"]:
            task_id = str(task["id"])
            coverage = assess_adjudication_coverage(task)
            if coverage["covered"]:
                routed_results.append(_orbita_result(task))
                route_counts["orbita"] += 1
            else:
                replayed = dict(semantic_result_map[task_id])
                replayed["metadata"] = {
                    **replayed.get("metadata", {}),
                    "route": "gpt-fallback",
                    "coverage": coverage,
                    "shared_response_replay": True,
                }
                routed_results.append(replayed)
                routed_receipts.append(semantic_receipt_map[task_id])
                route_counts["gpt_fallback"] += 1
        routed_payload = {
            "schema_version": "1.0",
            "system": {
                "kind": "orbita",
                "name": f"Orbita coverage router + {args.model} fallback",
                "version": "1.0",
                "provider": "local + OpenAI",
                "evaluation_mode": "replay",
                "config": {
                    "condition": "coverage-routed",
                    "fallback_model": args.model,
                    "shared_semantic_responses": True,
                },
            },
            "results": routed_results,
            "metadata": {
                "gold_visible_to_system": False,
                "route_counts": dict(route_counts),
            },
        }

        direct_scored = runtime.import_run(suite["id"], direct_payload)
        routed_scored = runtime.import_run(suite["id"], routed_payload)
        report = runtime.compile_report(suite["id"])
        verification = {
            "suite": runtime.verify_suite(suite["id"]),
            "direct_run": runtime.verify_run(direct_scored["id"]),
            "routed_run": runtime.verify_run(routed_scored["id"]),
            "report": runtime.verify_report(suite["id"]),
        }

    direct_usage = _usage(direct_payload)
    routed_usage = _usage(routed_payload)
    direct_total = direct_usage.get("total_tokens", 0)
    routed_total = routed_usage.get("total_tokens", 0)
    summary = {
        "tasks": len(combined_spec.tasks),
        "target_states": direct_scored["metrics"]["counts"]["target_state_opportunities"],
        "model": args.model,
        "new_gpt_calls_executed": len(semantic_receipts),
        "direct": {
            "operational_gpt_calls": len(combined_spec.tasks),
            "usage": direct_usage,
            "cumulative_latency_ms": _cumulative_latency_ms(direct_payload),
            "overall_score": direct_scored["metrics"]["overall_score"],
            "mean_task_score": direct_scored["metrics"]["mean_task_score"],
            "adjudication_accuracy": direct_scored["metrics"]["rates"]["adjudication_accuracy"],
        },
        "orbita_routed": {
            "operational_gpt_calls": len(routed_receipts),
            "route_counts": dict(route_counts),
            "usage": routed_usage,
            "cumulative_latency_ms": _cumulative_latency_ms(routed_payload),
            "overall_score": routed_scored["metrics"]["overall_score"],
            "mean_task_score": routed_scored["metrics"]["mean_task_score"],
            "adjudication_accuracy": routed_scored["metrics"]["rates"]["adjudication_accuracy"],
        },
        "token_reduction": 1.0 - (routed_total / direct_total) if direct_total else None,
        "verification": verification,
        "interpretation_boundary": (
            "Replay-controlled routing comparison. The same seven fresh GPT semantic responses "
            "were used in both arms; fourteen prior GPT structured responses were reused only in "
            "the direct arm. Gold was scorer-only."
        ),
    }

    _write_json(out / "public_tasks.json", public)
    _write_json(out / "gpt-direct-mixed.response.json", direct_payload)
    _write_json(out / "gpt-direct-mixed.receipts.json", ordered_direct_receipts)
    _write_json(out / "orbita-routed-mixed.response.json", routed_payload)
    _write_json(out / "orbita-routed-mixed.receipts.json", routed_receipts)
    _write_json(out / "report.json", report["report"])
    _write_json(out / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
