#!/usr/bin/env python3
"""Compare GPT on full noisy semantic context with Orbita-compressed context."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from noisy_semantic_suite import noisy_semantic_suite
from run_gpt_router_pilot import _EMPIRICAL, _cumulative_latency_ms, _usage
from suite import sanitize_public_bundle

from orbita.evaluation import ComparativeEvaluationRuntime
from orbita.ledger import EpistemicLedger
from orbita_agent.adjudication import compress_epistemic_task


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-context-items", type=int, default=3)
    return parser.parse_args()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _required_ids(gold: dict[str, Any]) -> set[str]:
    required = set()
    for kind in ("claims", "actions", "discoveries"):
        for expected in gold.get(kind, {}).values():
            for field in ("required_evidence", "required_receipts", "required_derivations"):
                required.update(str(value) for value in expected.get(field, []))
    return required


def main() -> None:
    args = _parse_args()
    out = args.out.resolve()
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite output directory: {out}")
    out.mkdir(parents=True)
    spec = noisy_semantic_suite()

    with EpistemicLedger(out / "benchmark.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, out / "workspace")
        suite = runtime.create_suite(spec)
        public = sanitize_public_bundle(runtime.export_public_suite(suite["id"]))
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
            "name": f"{args.model} — Orbita-compressed semantic context",
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

    required_by_task = {task.id: _required_ids(task.gold) for task in spec.tasks}
    retained_by_task = {
        item["receipt"]["task_id"]: set(item["receipt"]["retained_ids"])
        for item in compressed
    }
    required_total = sum(len(values) for values in required_by_task.values())
    required_retained = sum(
        len(required.intersection(retained_by_task[task_id]))
        for task_id, required in required_by_task.items()
    )
    full_usage = _usage(full_payload)
    compact_usage = _usage(compact_payload)
    full_input = full_usage.get("input_tokens", 0)
    compact_input = compact_usage.get("input_tokens", 0)
    full_total = full_usage.get("total_tokens", 0)
    compact_total = compact_usage.get("total_tokens", 0)
    summary = {
        "tasks": len(spec.tasks),
        "target_states": full_scored["metrics"]["counts"]["target_state_opportunities"],
        "model": args.model,
        "gpt_calls_executed": len(full_receipts) + len(compact_receipts),
        "compression": {
            "original_context_items": sum(
                item["receipt"]["original_context_items"] for item in compressed
            ),
            "retained_context_items": sum(
                item["receipt"]["retained_context_items"] for item in compressed
            ),
            "required_evidence_ids": required_total,
            "required_evidence_ids_retained": required_retained,
            "required_evidence_recall": (
                required_retained / required_total if required_total else None
            ),
            "mean_character_reduction": (
                sum(item["receipt"]["character_reduction"] for item in compressed)
                / len(compressed)
            ),
        },
        "full_context": {
            "usage": full_usage,
            "cumulative_latency_ms": _cumulative_latency_ms(full_payload),
            "overall_score": full_scored["metrics"]["overall_score"],
            "mean_task_score": full_scored["metrics"]["mean_task_score"],
            "adjudication_accuracy": full_scored["metrics"]["rates"]["adjudication_accuracy"],
        },
        "orbita_compact": {
            "usage": compact_usage,
            "cumulative_latency_ms": _cumulative_latency_ms(compact_payload),
            "overall_score": compact_scored["metrics"]["overall_score"],
            "mean_task_score": compact_scored["metrics"]["mean_task_score"],
            "adjudication_accuracy": compact_scored["metrics"]["rates"]["adjudication_accuracy"],
        },
        "input_token_reduction": (
            1.0 - (compact_input / full_input) if full_input else None
        ),
        "total_token_reduction": (
            1.0 - (compact_total / full_total) if full_total else None
        ),
        "verification": verification,
        "interpretation_boundary": (
            "Seven-task noisy semantic pilot with independent full and compact GPT calls. "
            "Gold was scorer-only and was not used by the compressor."
        ),
    }

    _write_json(out / "public_noisy_tasks.json", public)
    _write_json(out / "compact_tasks.json", {"tasks": compact_tasks})
    _write_json(out / "compression_receipts.json", [item["receipt"] for item in compressed])
    _write_json(out / "gpt-full.response.json", full_payload)
    _write_json(out / "gpt-full.receipts.json", full_receipts)
    _write_json(out / "gpt-compact.response.json", compact_payload)
    _write_json(out / "gpt-compact.receipts.json", compact_receipts)
    _write_json(out / "report.json", report["report"])
    _write_json(out / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
