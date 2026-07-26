#!/usr/bin/env python3
"""Compare a saved model run with Orbita's zero-token local adjudicator."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

from orbita.evaluation import ComparativeEvaluationRuntime, default_adversarial_suite
from orbita.ledger import EpistemicLedger
from orbita_agent.adjudication import adjudicate_epistemic_task


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=Path, required=True, help="Saved response JSON to use as the control.")
    parser.add_argument("--out", type=Path, required=True, help="New output directory; must not already exist.")
    return parser.parse_args()


def _local_result(task: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    adjudicated = adjudicate_epistemic_task(task)
    latency_ms = (time.perf_counter() - started) * 1000
    return {
        "task_id": adjudicated["task_id"],
        "final_answer": adjudicated["final_answer"],
        "claim_judgments": adjudicated["claim_judgments"],
        "action_judgments": adjudicated["action_judgments"],
        "discovery_judgments": adjudicated["discovery_judgments"],
        "audit_trace": adjudicated["audit_trace"],
        "latency_ms": latency_ms,
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
        "metadata": {
            "task_hash": adjudicated["task_hash"],
            "decision_basis": adjudicated["decision_basis"],
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    control_path = args.control.resolve()
    out = args.out.resolve()
    if not control_path.is_file():
        raise RuntimeError(f"Control response does not exist: {control_path}")
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite output directory: {out}")
    out.mkdir(parents=True)

    control = json.loads(control_path.read_text(encoding="utf-8"))
    if control.get("schema_version") != "1.0":
        raise RuntimeError("Unsupported control response schema")
    if not isinstance(control.get("results"), list):
        raise RuntimeError("Control response has no results list")

    db_path = out / "benchmark.sqlite"
    with EpistemicLedger(db_path) as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, out / "workspace")
        suite = runtime.create_suite(default_adversarial_suite())
        public_bundle = runtime.export_public_suite(suite["id"])
        public_tasks = public_bundle["tasks"]
        expected_ids = {task["id"] for task in public_tasks}
        control_ids = {result.get("task_id") for result in control["results"]}
        if control_ids != expected_ids:
            raise RuntimeError(
                f"Control task IDs do not match the suite; missing={sorted(expected_ids - control_ids)}, "
                f"extra={sorted(control_ids - expected_ids)}"
            )

        local_payload = {
            "schema_version": "1.0",
            "system": {
                "kind": "orbita",
                "name": "Orbita deterministic adjudicator",
                "version": "1.0",
                "provider": "local",
                "evaluation_mode": "empirical",
                "config": {
                    "condition": "local-adjudicator",
                    "full_orbita_runtime": True,
                    "model_calls": 0,
                    "network_calls": 0,
                },
            },
            "results": [_local_result(task) for task in public_tasks],
            "metadata": {
                "gold_visible_to_adjudicator": False,
                "control_reused": True,
            },
        }

        control_scored = runtime.import_run(suite["id"], control)
        local_scored = runtime.import_run(suite["id"], local_payload)
        report = runtime.compile_report(suite["id"])
        verification = {
            "suite": runtime.verify_suite(suite["id"]),
            "runs": {
                control_scored["id"]: runtime.verify_run(control_scored["id"]),
                local_scored["id"]: runtime.verify_run(local_scored["id"]),
            },
            "report": runtime.verify_report(suite["id"]),
        }

        shutil.copy2(control_path, out / "saved-control.response.json")
        _write_json(out / "public_tasks.json", public_bundle)
        _write_json(out / "orbita-local-adjudicator.response.json", local_payload)
        _write_json(out / "report.json", report["report"])
        manifest = {
            "suite_id": suite["id"],
            "suite_hash": suite["suite_hash"],
            "report_hash": report["report_hash"],
            "control": {
                "source": str(control_path),
                "run_id": control_scored["id"],
                "response_hash": control_scored["response_hash"],
            },
            "orbita": {
                "run_id": local_scored["id"],
                "response_hash": local_scored["response_hash"],
                "model_calls": 0,
                "network_calls": 0,
            },
            "verification": verification,
            "interpretation_boundary": (
                "Zero-token comparison using saved GPT-5.6 responses and fresh deterministic Orbita "
                "adjudications over the same public tasks. This reuses the control and is not a fresh model run."
            ),
        }
        _write_json(out / "release_manifest.json", manifest)
        print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
