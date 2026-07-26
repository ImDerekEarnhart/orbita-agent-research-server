#!/usr/bin/env python3
"""Compare GPT on every task with an Orbita-first GPT fallback router."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from suite import sanitize_public_bundle

from orbita.evaluation import (
    ComparativeEvaluationRuntime,
    EvaluationSuiteSpec,
    EvaluationTaskSpec,
)
from orbita.ledger import EpistemicLedger
from orbita_agent.adjudication import (
    adjudicate_epistemic_task,
    assess_adjudication_coverage,
)

_EMPIRICAL_RUNNER = (
    Path(__file__).resolve().parents[1]
    / "adversarial-epistemic-v1"
    / "run_empirical.py"
)
_SPEC = importlib.util.spec_from_file_location("orbita_empirical_runner", _EMPIRICAL_RUNNER)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load empirical runner: {_EMPIRICAL_RUNNER}")
_EMPIRICAL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EMPIRICAL)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-suite", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--tasks-per-category", type=int, default=2)
    parser.add_argument("--workers", type=int, default=2)
    return parser.parse_args()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _select_stratified(
    spec: EvaluationSuiteSpec,
    tasks_per_category: int,
) -> EvaluationSuiteSpec:
    if tasks_per_category < 1:
        raise ValueError("tasks_per_category must be positive")
    selected: list[EvaluationTaskSpec] = []
    counts: Counter[str] = Counter()
    for task in spec.tasks:
        if counts[task.category] < tasks_per_category:
            selected.append(task)
            counts[task.category] += 1
    if len(counts) != 7 or set(counts.values()) != {tasks_per_category}:
        raise RuntimeError(f"Private suite lacks the requested category coverage: {dict(counts)}")
    return EvaluationSuiteSpec(
        name=f"{spec.name} — GPT router pilot",
        version=f"{spec.version}-router-pilot",
        tasks=tuple(selected),
        seed=spec.seed,
        metadata={
            **spec.metadata,
            "pilot": True,
            "tasks_per_category": tasks_per_category,
        },
    )


def _orbita_result(task: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    result = adjudicate_epistemic_task(task)
    return {
        "task_id": result["task_id"],
        "final_answer": result["final_answer"],
        "claim_judgments": result["claim_judgments"],
        "action_judgments": result["action_judgments"],
        "discovery_judgments": result["discovery_judgments"],
        "audit_trace": result["audit_trace"],
        "latency_ms": (time.perf_counter() - started) * 1000,
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "metadata": {
            "route": "orbita-local",
            "task_hash": result["task_hash"],
            "decision_basis": result["decision_basis"],
        },
    }


def _gpt_fallback_result(task: dict[str, Any], model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    result, receipt = _EMPIRICAL._openai_task(
        task,
        model,
        _EMPIRICAL.DIRECT_INSTRUCTIONS,
    )
    return (
        {
            "task_id": task["id"],
            **result,
            "latency_ms": (time.perf_counter() - started) * 1000,
            "token_usage": receipt["usage"],
            "metadata": {
                "route": "gpt-fallback",
                "provider_response_id": receipt["response_id"],
            },
        },
        receipt,
    )


def _run_routed(
    tasks: list[dict[str, Any]],
    model: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    results = []
    fallback_receipts = []
    for task in tasks:
        coverage = assess_adjudication_coverage(task)
        if coverage["covered"]:
            results.append(_orbita_result(task))
        else:
            result, receipt = _gpt_fallback_result(task, model)
            result["metadata"]["coverage"] = coverage
            results.append(result)
            fallback_receipts.append({"task_id": task["id"], **receipt})
    payload = {
        "schema_version": "1.0",
        "system": {
            "kind": "orbita",
            "name": f"Orbita-first router with {model} fallback",
            "version": "1.0",
            "provider": "local + OpenAI fallback",
            "evaluation_mode": "empirical",
            "config": {
                "condition": "orbita-first-gpt-fallback",
                "fallback_model": model,
                "gold_visible_to_router": False,
            },
        },
        "results": results,
        "metadata": {
            "routing_policy": (
                "Use deterministic Orbita adjudication for valid structured tasks; "
                "call GPT only when Orbita rejects the task as outside its accepted schema."
            ),
            "gpt_fallback_calls": len(fallback_receipts),
        },
    }
    return payload, fallback_receipts


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    totals = Counter()
    for result in payload["results"]:
        usage = result.get("token_usage", {})
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            totals[key] += int(usage.get(key) or 0)
    return dict(totals)


def _cumulative_latency_ms(payload: dict[str, Any]) -> float:
    return sum(float(result.get("latency_ms") or 0) for result in payload["results"])


def main() -> None:
    args = _parse_args()
    out = args.out.resolve()
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite output directory: {out}")
    out.mkdir(parents=True)

    private_value = json.loads(args.private_suite.resolve().read_text(encoding="utf-8"))
    full_spec = EvaluationSuiteSpec.from_dict(private_value)
    spec = _select_stratified(full_spec, args.tasks_per_category)

    with EpistemicLedger(out / "benchmark.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, out / "workspace")
        suite = runtime.create_suite(spec)
        public = sanitize_public_bundle(runtime.export_public_suite(suite["id"]))

        direct_payload, direct_receipts = _EMPIRICAL._run_system(
            public["tasks"],
            "openai",
            args.model,
            "direct",
            max(1, args.workers),
        )
        routed_payload, fallback_receipts = _run_routed(public["tasks"], args.model)

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
        "tasks": len(spec.tasks),
        "categories": dict(Counter(task.category for task in spec.tasks)),
        "model": args.model,
        "direct": {
            "gpt_calls": len(direct_receipts),
            "usage": direct_usage,
            "cumulative_latency_ms": _cumulative_latency_ms(direct_payload),
            "overall_score": direct_scored["metrics"]["overall_score"],
            "mean_task_score": direct_scored["metrics"]["mean_task_score"],
            "adjudication_accuracy": direct_scored["metrics"]["rates"]["adjudication_accuracy"],
        },
        "orbita_routed": {
            "gpt_calls": len(fallback_receipts),
            "usage": routed_usage,
            "cumulative_latency_ms": _cumulative_latency_ms(routed_payload),
            "overall_score": routed_scored["metrics"]["overall_score"],
            "mean_task_score": routed_scored["metrics"]["mean_task_score"],
            "adjudication_accuracy": routed_scored["metrics"]["rates"]["adjudication_accuracy"],
        },
        "token_reduction": (
            1.0 - (routed_total / direct_total)
            if direct_total
            else None
        ),
        "verification": verification,
        "interpretation_boundary": (
            "Stratified private-suite pilot. Gold was used only by the scorer. "
            "The routed arm measures deterministic coverage on Orbita's accepted structured schema, "
            "not arbitrary natural-language task coverage."
        ),
    }

    _write_json(out / "public_tasks.json", public)
    _write_json(out / "gpt-direct.response.json", direct_payload)
    _write_json(out / "gpt-direct.receipts.json", direct_receipts)
    _write_json(out / "orbita-routed.response.json", routed_payload)
    _write_json(out / "orbita-routed-gpt-fallback.receipts.json", fallback_receipts)
    _write_json(out / "report.json", report["report"])
    _write_json(out / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
