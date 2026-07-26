#!/usr/bin/env python3
"""Generate and execute a private zero-token Orbita adjudication holdout."""

from __future__ import annotations

import argparse
import json
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from suite import build_hidden_suite, sanitize_public_bundle

from orbita.evaluation import ComparativeEvaluationRuntime
from orbita.ledger import EpistemicLedger
from orbita_agent.adjudication import adjudicate_epistemic_task


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--tasks-per-category", type=int, default=20)
    return parser.parse_args()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _result(task: dict[str, Any]) -> dict[str, Any]:
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
            "task_hash": result["task_hash"],
            "decision_basis": result["decision_basis"],
        },
    }


def main() -> None:
    args = _parse_args()
    out = args.out.resolve()
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite output directory: {out}")
    out.mkdir(parents=True)
    seed = args.seed if args.seed is not None else secrets.randbits(63)
    spec = build_hidden_suite(seed, tasks_per_category=args.tasks_per_category)

    with EpistemicLedger(out / "benchmark.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, out / "workspace")
        suite = runtime.create_suite(spec)
        public_bundle = sanitize_public_bundle(runtime.export_public_suite(suite["id"]))
        payload = {
            "schema_version": "1.0",
            "system": {
                "kind": "orbita",
                "name": "Orbita deterministic adjudicator",
                "version": "1.0",
                "provider": "local",
                "evaluation_mode": "empirical",
                "config": {
                    "condition": "private-seeded-holdout",
                    "model_calls": 0,
                    "network_calls": 0,
                },
            },
            "results": [_result(task) for task in public_bundle["tasks"]],
            "metadata": {"gold_visible_to_adjudicator": False},
        }
        scored = runtime.import_run(suite["id"], payload)
        report = runtime.compile_report(suite["id"])
        verification = {
            "suite": runtime.verify_suite(suite["id"]),
            "run": runtime.verify_run(scored["id"]),
            "report": runtime.verify_report(suite["id"]),
        }

    private_suite = {
        "name": spec.name,
        "version": spec.version,
        "seed": spec.seed,
        "metadata": spec.metadata,
        "tasks": [asdict(task) for task in spec.tasks],
    }
    _write_json(out / "private_suite.json", private_suite)
    _write_json(out / "public_tasks.json", public_bundle)
    _write_json(out / "orbita-local-adjudicator.response.json", payload)
    _write_json(out / "report.json", report["report"])
    _write_json(out / "verification.json", verification)

    metrics = scored["metrics"]
    summary = {
        "tasks": len(spec.tasks),
        "target_states": metrics["counts"]["target_state_opportunities"],
        "target_states_correct": metrics["counts"]["target_states_correct"],
        "adjudication_accuracy": metrics["rates"]["adjudication_accuracy"],
        "overall_score": metrics["overall_score"],
        "mean_task_score": metrics["mean_task_score"],
        "input_tokens": 0,
        "output_tokens": 0,
        "model_calls": 0,
        "network_calls": 0,
        "latency_ms": sum(float(item["latency_ms"]) for item in payload["results"]),
        "verification": verification,
    }
    _write_json(out / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
