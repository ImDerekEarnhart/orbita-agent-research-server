from __future__ import annotations

import importlib.util
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from orbita.evaluation import ComparativeEvaluationRuntime
from orbita.ledger import EpistemicLedger
from orbita_agent.adjudication import (
    adjudicate_epistemic_task,
    assess_adjudication_coverage,
)

_SUITE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "hidden_adjudication_v1"
    / "suite.py"
)
_SPEC = importlib.util.spec_from_file_location("orbita_hidden_benchmark_suite", _SUITE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
build_hidden_suite = _MODULE.build_hidden_suite
sanitize_public_bundle = _MODULE.sanitize_public_bundle

_SEMANTIC_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "hidden_adjudication_v1"
    / "semantic_suite.py"
)
_SEMANTIC_SPEC = importlib.util.spec_from_file_location(
    "orbita_semantic_benchmark_suite",
    _SEMANTIC_PATH,
)
assert _SEMANTIC_SPEC and _SEMANTIC_SPEC.loader
_SEMANTIC_MODULE = importlib.util.module_from_spec(_SEMANTIC_SPEC)
_SEMANTIC_SPEC.loader.exec_module(_SEMANTIC_MODULE)
semantic_holdout_tasks = _SEMANTIC_MODULE.semantic_holdout_tasks


def _response_result(task: dict) -> dict:
    result = adjudicate_epistemic_task(task)
    return {
        "task_id": result["task_id"],
        "final_answer": result["final_answer"],
        "claim_judgments": result["claim_judgments"],
        "action_judgments": result["action_judgments"],
        "discovery_judgments": result["discovery_judgments"],
        "audit_trace": result["audit_trace"],
        "latency_ms": 0,
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "metadata": {"decision_basis": result["decision_basis"]},
    }


def test_hidden_suite_is_deterministic_balanced_and_seed_sensitive() -> None:
    first = build_hidden_suite(123456, tasks_per_category=3)
    repeated = build_hidden_suite(123456, tasks_per_category=3)
    different = build_hidden_suite(654321, tasks_per_category=3)

    assert asdict(first) == asdict(repeated)
    assert [task.id for task in first.tasks] != [task.id for task in different.tasks]
    assert len(first.tasks) == 21
    assert len({task.id for task in first.tasks}) == 21
    assert set(Counter(task.category for task in first.tasks).values()) == {3}


def test_sanitized_public_holdout_leaks_neither_gold_seed_nor_private_hash(tmp_path) -> None:
    seed = 987654321
    spec = build_hidden_suite(seed, tasks_per_category=1)
    with EpistemicLedger(tmp_path / "ledger.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, tmp_path / "workspace")
        suite = runtime.create_suite(spec)
        public = sanitize_public_bundle(runtime.export_public_suite(suite["id"]))

    serialized = repr(public)
    assert "gold" not in serialized
    assert "final_state" not in serialized
    assert str(seed) not in serialized
    assert "suite_hash" not in public["suite"]
    assert "seed" not in public["suite"]
    assert len(public["tasks"]) == 7


def test_hidden_suite_scores_through_gold_free_local_path(tmp_path) -> None:
    spec = build_hidden_suite(1122334455, tasks_per_category=3)
    with EpistemicLedger(tmp_path / "ledger.sqlite") as ledger:
        runtime = ComparativeEvaluationRuntime(ledger, tmp_path / "workspace")
        suite = runtime.create_suite(spec)
        public = sanitize_public_bundle(runtime.export_public_suite(suite["id"]))
        payload = {
            "schema_version": "1.0",
            "system": {
                "kind": "orbita",
                "name": "Orbita deterministic adjudicator",
                "evaluation_mode": "empirical",
                "config": {"model_calls": 0, "network_calls": 0},
            },
            "results": [_response_result(task) for task in public["tasks"]],
            "metadata": {"gold_visible_to_adjudicator": False},
        }
        scored = runtime.import_run(suite["id"], payload)

    assert scored["integrity_valid"] is True
    assert scored["metrics"]["counts"]["target_state_opportunities"] == 24
    assert scored["metrics"]["counts"]["target_states_correct"] == 24
    assert scored["metrics"]["rates"]["adjudication_accuracy"] == 1.0
    assert scored["metrics"]["overall_score"] == 1.0


def test_semantic_holdout_is_gold_free_and_routes_to_gpt() -> None:
    tasks = semantic_holdout_tasks()
    public = [task.public_dict() for task in tasks]
    target_count = sum(
        len(task["targets"][kind])
        for task in public
        for kind in ("claims", "actions", "discoveries")
    )

    assert len(tasks) == 7
    assert target_count == 8
    assert "gold" not in repr(public)
    assert "final_state" not in repr(public)
    assert all(
        assess_adjudication_coverage(task)["covered"] is False
        for task in public
    )
