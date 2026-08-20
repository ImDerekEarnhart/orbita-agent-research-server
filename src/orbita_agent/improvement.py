"""Bounded, replay-based improvement of Orbita's research policy.

This module deliberately improves declarative policy values only.  It cannot
write source code, execute commands, deploy releases, or promote a proposal
without an exact human approval receipt.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd

from orbita_discovery.core import Engine, Ledger, survivors
from orbita_discovery.falsifiers import BaselineFalsifier, CrossSeedFalsifier, HeldOutFalsifier
from orbita_discovery.judges import GatedJudge
from orbita_mvp.table_domain import UploadedTableDomain, generate_table_candidates

PROMOTION_PHRASE = "I reviewed this exact evaluated improvement"
ROLLBACK_PHRASE = "I reviewed this exact policy rollback"
POLICY_SCHEMA = "orbita-research-policy/1"
MAX_BENCHMARK_CASES = 25

DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": POLICY_SCHEMA,
    "max_candidates": 60,
    "scout_fraction": 0.6,
    "seed": 20260623,
    "commit_at": 0.25,
    "baseline_margin": 0.05,
    "held_out_min": 0.15,
    "cross_seed_count": 9,
    "cross_seed_min": 0.15,
    "cross_seed_max_spread": 0.65,
}

DEFAULT_ACCEPTANCE: dict[str, Any] = {
    "min_benchmark_cases": 1,
    "max_error_count": 0,
    "max_invariant_failures": 0,
    "min_survivor_rate_delta": -0.25,
    "max_survivor_rate_delta": 0.25,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS improvement_policies (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    parent_policy_id TEXT,
    status TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    activated_at TEXT,
    activated_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_policy
ON improvement_policies(status) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS improvement_candidates (
    id TEXT PRIMARY KEY,
    base_policy_id TEXT NOT NULL,
    name TEXT NOT NULL,
    rationale TEXT NOT NULL,
    patch_json TEXT NOT NULL,
    proposed_policy_json TEXT NOT NULL,
    acceptance_json TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    promoted_policy_id TEXT
);

CREATE TABLE IF NOT EXISTS improvement_evaluations (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    benchmark_json TEXT NOT NULL,
    baseline_metrics_json TEXT NOT NULL,
    candidate_metrics_json TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    checks_json TEXT NOT NULL,
    verdict TEXT NOT NULL,
    evaluation_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS improvement_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _finite_number(name: str, value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical policy and reject every unrecognized capability."""
    allowed = set(DEFAULT_POLICY)
    unknown = sorted(set(policy) - allowed)
    missing = sorted(allowed - set(policy))
    if unknown or missing:
        parts = []
        if unknown:
            parts.append(f"unknown fields: {', '.join(unknown)}")
        if missing:
            parts.append(f"missing fields: {', '.join(missing)}")
        raise ValueError("Invalid policy; " + "; ".join(parts))
    if policy["schema_version"] != POLICY_SCHEMA:
        raise ValueError(f"schema_version must equal {POLICY_SCHEMA}")

    max_candidates = policy["max_candidates"]
    seed = policy["seed"]
    cross_seed_count = policy["cross_seed_count"]
    for name, value, low, high in (
        ("max_candidates", max_candidates, 1, 200),
        ("seed", seed, 0, 2_147_483_647),
        ("cross_seed_count", cross_seed_count, 1, 31),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} must be an integer between {low} and {high}")

    result = {
        "schema_version": POLICY_SCHEMA,
        "max_candidates": max_candidates,
        "scout_fraction": _finite_number("scout_fraction", policy["scout_fraction"], 0.3, 0.8),
        "seed": seed,
        "commit_at": _finite_number("commit_at", policy["commit_at"], -1.0, 1.0),
        "baseline_margin": _finite_number("baseline_margin", policy["baseline_margin"], 0.0, 1.0),
        "held_out_min": _finite_number("held_out_min", policy["held_out_min"], -1.0, 1.0),
        "cross_seed_count": cross_seed_count,
        "cross_seed_min": _finite_number("cross_seed_min", policy["cross_seed_min"], -1.0, 1.0),
        "cross_seed_max_spread": None,
    }
    spread = policy["cross_seed_max_spread"]
    if spread is not None:
        result["cross_seed_max_spread"] = _finite_number("cross_seed_max_spread", spread, 0.0, 2.0)
    if result["commit_at"] < result["held_out_min"]:
        raise ValueError("commit_at cannot be lower than held_out_min")
    return result


def validate_acceptance(criteria: dict[str, Any] | None) -> dict[str, Any]:
    result = {**DEFAULT_ACCEPTANCE, **(criteria or {})}
    unknown = sorted(set(result) - set(DEFAULT_ACCEPTANCE))
    if unknown:
        raise ValueError(f"Unknown acceptance criteria: {', '.join(unknown)}")
    for key in ("min_benchmark_cases", "max_error_count", "max_invariant_failures"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    result["min_survivor_rate_delta"] = _finite_number(
        "min_survivor_rate_delta", result["min_survivor_rate_delta"], -1.0, 1.0
    )
    result["max_survivor_rate_delta"] = _finite_number(
        "max_survivor_rate_delta", result["max_survivor_rate_delta"], -1.0, 1.0
    )
    if result["min_survivor_rate_delta"] > result["max_survivor_rate_delta"]:
        raise ValueError("min_survivor_rate_delta cannot exceed max_survivor_rate_delta")
    return result


class ImprovementLab:
    """Version, evaluate, approve, activate, and roll back bounded policies."""

    def __init__(self, db_path: str | Path, service: Any):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.service = service
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._seed_default()

    def close(self) -> None:
        self.conn.close()

    def _seed_default(self) -> None:
        if self.conn.execute("SELECT 1 FROM improvement_policies LIMIT 1").fetchone():
            return
        policy = validate_policy(DEFAULT_POLICY)
        now = _now()
        self.conn.execute(
            """INSERT INTO improvement_policies
               (id, version, status, policy_json, policy_hash, rationale, created_at, activated_at, activated_by)
               VALUES (?, 1, 'active', ?, ?, ?, ?, ?, ?)""",
            (
                _id("policy"),
                _stable(policy),
                _hash(policy),
                "Factory governed research policy",
                now,
                now,
                "orbita-bootstrap",
            ),
        )
        self.conn.commit()

    @staticmethod
    def _policy_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["policy"] = json.loads(item.pop("policy_json"))
        return item

    def active_policy(self) -> dict[str, Any]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM improvement_policies WHERE status = 'active'").fetchone()
            if row is None:
                raise RuntimeError("Improvement registry has no active policy")
            return self._policy_row(row)

    def status(self) -> dict[str, Any]:
        active = self.active_policy()
        counts = {
            row["status"]: row["n"]
            for row in self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM improvement_candidates GROUP BY status"
            ).fetchall()
        }
        return {
            "mode": "bounded_policy_improvement",
            "active_policy": active,
            "candidate_counts": counts,
            "promotion_phrase": PROMOTION_PHRASE,
            "rollback_phrase": ROLLBACK_PHRASE,
            "guarantees": [
                "Only allowlisted numeric research-policy fields can change.",
                "Evaluation replays frozen historical case inputs deterministically.",
                "Evaluation eligibility is a stability check, not proof that a policy is scientifically better.",
                "No proposal can activate without exact candidate and evaluation hashes plus human confirmation.",
                "The lab cannot edit code, run shell commands, deploy, or silently promote itself.",
            ],
        }

    def history(self, limit: int = 25) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        policies = [
            self._policy_row(row)
            for row in self.conn.execute(
                "SELECT * FROM improvement_policies ORDER BY version DESC, created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        ]
        candidates = [self._candidate_row(row, include_policy=False) for row in self.conn.execute(
            "SELECT * FROM improvement_candidates ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()]
        return {"policies": policies, "candidates": candidates}

    def _candidate_row(self, row: sqlite3.Row, *, include_policy: bool = True) -> dict[str, Any]:
        item = dict(row)
        item["patch"] = json.loads(item.pop("patch_json"))
        item["acceptance_criteria"] = json.loads(item.pop("acceptance_json"))
        proposed = json.loads(item.pop("proposed_policy_json"))
        if include_policy:
            item["proposed_policy"] = proposed
        latest = self.conn.execute(
            "SELECT * FROM improvement_evaluations WHERE candidate_id = ? ORDER BY created_at DESC LIMIT 1",
            (item["id"],),
        ).fetchone()
        item["latest_evaluation"] = self._evaluation_row(latest) if latest else None
        return item

    @staticmethod
    def _evaluation_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("benchmark_json", "benchmarks"),
            ("baseline_metrics_json", "baseline_metrics"),
            ("candidate_metrics_json", "candidate_metrics"),
            ("diff_json", "diff"),
            ("checks_json", "checks"),
        ):
            item[target] = json.loads(item.pop(source))
        return item

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM improvement_candidates WHERE id = ?", (candidate_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown improvement candidate: {candidate_id}")
            return self._candidate_row(row)

    def propose(
        self,
        *,
        name: str,
        rationale: str,
        patch: dict[str, Any],
        acceptance_criteria: dict[str, Any] | None = None,
        actor: str = "agent-proposal",
    ) -> dict[str, Any]:
        if not name.strip() or len(name) > 160:
            raise ValueError("name must be a short nonblank label")
        if len(rationale.strip()) < 12 or len(rationale) > 4_000:
            raise ValueError("rationale must be specific and between 12 and 4,000 characters")
        if not isinstance(patch, dict) or not patch:
            raise ValueError("patch must change at least one policy field")
        if "schema_version" in patch:
            raise ValueError("schema_version cannot be changed by an improvement proposal")
        active = self.active_policy()
        unknown = sorted(set(patch) - (set(DEFAULT_POLICY) - {"schema_version"}))
        if unknown:
            raise ValueError(f"Policy patch contains forbidden fields: {', '.join(unknown)}")
        proposed = validate_policy({**active["policy"], **patch})
        normalized_patch = {key: proposed[key] for key in sorted(patch)}
        if all(proposed[key] == active["policy"][key] for key in normalized_patch):
            raise ValueError("patch does not change the active policy")
        acceptance = validate_acceptance(acceptance_criteria)
        body = {
            "base_policy_id": active["id"],
            "base_policy_hash": active["policy_hash"],
            "name": name.strip(),
            "rationale": rationale.strip(),
            "patch": normalized_patch,
            "proposed_policy": proposed,
            "acceptance_criteria": acceptance,
        }
        candidate_id = _id("improvement")
        with self._lock:
            self.conn.execute(
                """INSERT INTO improvement_candidates
                   (id, base_policy_id, name, rationale, patch_json, proposed_policy_json,
                    acceptance_json, candidate_hash, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)""",
                (
                    candidate_id,
                    active["id"],
                    body["name"],
                    body["rationale"],
                    _stable(normalized_patch),
                    _stable(proposed),
                    _stable(acceptance),
                    _hash(body),
                    _now(),
                ),
            )
            self._event("proposed", candidate_id, actor, body)
            self.conn.commit()
        return self.get_candidate(candidate_id)

    def historical_summary(self) -> dict[str, Any]:
        cases = self.service.store.list_cases()
        completed = []
        for case in cases:
            run = next((item for item in case.get("runs", []) if item.get("status") == "completed"), None)
            if run:
                completed.append(run)
        findings = [finding for run in completed for finding in run.get("result", {}).get("findings", [])]
        survivors_count = sum(
            1
            for finding in findings
            if finding.get("final_status") in {"supported", "challenged", "provisional"}
            and not any(item.get("killed") for item in finding.get("falsifications", []))
        )
        return {
            "completed_case_count": len(completed),
            "candidate_count": len(findings),
            "survivor_count": survivors_count,
            "survivor_rate": round(survivors_count / len(findings), 6) if findings else 0.0,
        }

    def suggest(self) -> dict[str, Any]:
        """Use run history to create one conservative, unapproved proposal."""
        summary = self.historical_summary()
        if summary["completed_case_count"] == 0 or summary["candidate_count"] == 0:
            return {
                "status": "needs_benchmarks",
                "history": summary,
                "next_step": "Complete at least one approved discovery run before requesting a suggestion.",
            }
        active = self.active_policy()["policy"]
        if summary["survivor_rate"] > 0.4:
            next_commit = round(min(1.0, active["commit_at"] + 0.05), 6)
            next_min = min(next_commit, round(active["held_out_min"] + 0.05, 6))
            next_cross = min(next_commit, round(active["cross_seed_min"] + 0.05, 6))
            patch = {
                "commit_at": next_commit,
                "held_out_min": next_min,
                "cross_seed_min": next_cross,
            }
            name = "Tighten replication thresholds"
            reason = (
                f"Historical survivor rate is {summary['survivor_rate']:.1%}; test whether stricter held-out "
                "and cross-seed minima preserve stable findings."
            )
        elif active["cross_seed_count"] < 25:
            patch = {"cross_seed_count": min(25, active["cross_seed_count"] + 2)}
            name = "Increase replication stress"
            reason = (
                f"Historical survivor rate is {summary['survivor_rate']:.1%}; test two more deterministic "
                "cross-seed resamples without changing the acceptance threshold."
            )
        else:
            patch = {"baseline_margin": round(min(1.0, active["baseline_margin"] + 0.01), 6)}
            name = "Increase baseline separation"
            reason = "Cross-seed stress is already high; test a slightly stronger margin over the null baseline."
        if all(active[key] == value for key, value in patch.items()):
            return {
                "status": "policy_at_safe_suggestion_ceiling",
                "history": summary,
                "next_step": "Ask a researcher to propose a domain-justified allowlisted patch.",
            }
        candidate = self.propose(name=name, rationale=reason, patch=patch, actor="orbita-history-suggester")
        return {"status": "proposed", "history": summary, "candidate": candidate}

    def _benchmarks(self, case_ids: list[str] | None) -> list[dict[str, Any]]:
        raw_ids = case_ids or []
        if any(not isinstance(case_id, str) or not case_id for case_id in raw_ids):
            raise ValueError("benchmark case IDs must be nonblank strings")
        requested = set(raw_ids)
        records = []
        for case in self.service.store.list_cases():
            if requested and case["id"] not in requested:
                continue
            run = next((item for item in case.get("runs", []) if item.get("status") == "completed"), None)
            if not run:
                continue
            plan = self.service.store.get_plan(run["plan_id"])
            selected = plan["plan"].get("selected_dataset", {})
            records.append(
                {
                    "case_id": case["id"],
                    "run_id": run["id"],
                    "plan_id": plan["id"],
                    "plan_hash": plan["plan_hash"],
                    "goal": plan["plan"].get("goal", ""),
                    "file_id": selected.get("file_id"),
                    "dataset_sha256": selected.get("sha256"),
                    "excluded_columns": plan["plan"].get("excluded_from_candidate_generation", []),
                    "group_column": plan["plan"].get("candidate_generation", {}).get("group_column"),
                }
            )
        found = {item["case_id"] for item in records}
        missing = sorted(requested - found)
        if missing:
            raise ValueError("No completed benchmark run for case(s): " + ", ".join(missing))
        if len(records) > MAX_BENCHMARK_CASES:
            raise ValueError(f"An evaluation can replay at most {MAX_BENCHMARK_CASES} completed cases")
        return sorted(records, key=lambda item: item["case_id"])

    def _replay_one(self, benchmark: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        selected_file = self.service.store.get_file(benchmark["file_id"])
        if selected_file["sha256"] != benchmark["dataset_sha256"]:
            raise ValueError("Frozen benchmark dataset hash no longer matches its receipt")
        df = pd.read_csv(selected_file["extracted_path"])
        candidates, _generation = generate_table_candidates(
            df,
            goal=benchmark["goal"],
            max_candidates=policy["max_candidates"],
            scout_fraction=policy["scout_fraction"],
            seed=policy["seed"],
            excluded_columns=benchmark.get("excluded_columns", []),
            group_column=benchmark.get("group_column"),
        )
        if not candidates:
            return {
                "candidate_count": 0,
                "survivor_count": 0,
                "refuted_count": 0,
                "supported_count": 0,
                "provisional_count": 0,
                "survivor_scores": [],
                "invariant_failures": 0,
            }
        domain = UploadedTableDomain(
            df,
            candidates,
            scout_fraction=policy["scout_fraction"],
            seed=policy["seed"],
            group_column=benchmark.get("group_column"),
        )
        engine = Engine(
            GatedJudge(commit_at=policy["commit_at"], baseline_margin=policy["baseline_margin"]),
            [
                BaselineFalsifier(margin=policy["baseline_margin"]),
                HeldOutFalsifier(min_score=policy["held_out_min"]),
                CrossSeedFalsifier(
                    seeds=policy["cross_seed_count"],
                    min_median=policy["cross_seed_min"],
                    max_spread=policy["cross_seed_max_spread"],
                ),
            ],
            Ledger(),
        )
        engine.run(domain)
        kept = survivors(engine.ledger)
        invariant_failures = 0
        ids = [item.candidate.id for item in engine.ledger.entries]
        invariant_failures += len(ids) - len(set(ids))
        for item in engine.ledger.entries:
            metrics = [item.verdict.score, *[attack.metric for attack in item.falsifications]]
            invariant_failures += sum(1 for value in metrics if not math.isfinite(float(value)))
            if item in kept and any(attack.killed for attack in item.falsifications):
                invariant_failures += 1
        return {
            "candidate_count": len(engine.ledger.entries),
            "survivor_count": len(kept),
            "refuted_count": sum(item.final_status == "refuted" for item in engine.ledger.entries),
            "supported_count": sum(item.final_status == "supported" for item in engine.ledger.entries),
            "provisional_count": sum(item.final_status == "provisional" for item in engine.ledger.entries),
            "survivor_scores": [item.verdict.score for item in kept],
            "invariant_failures": invariant_failures,
        }

    def _replay(self, benchmarks: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
        totals = {
            "benchmark_cases": len(benchmarks),
            "evaluated_cases": 0,
            "error_count": 0,
            "candidate_count": 0,
            "survivor_count": 0,
            "refuted_count": 0,
            "supported_count": 0,
            "provisional_count": 0,
            "invariant_failures": 0,
        }
        scores: list[float] = []
        errors = []
        for benchmark in benchmarks:
            try:
                result = self._replay_one(benchmark, policy)
                totals["evaluated_cases"] += 1
                scores.extend(result.pop("survivor_scores"))
                for key, value in result.items():
                    totals[key] += value
            except Exception as exc:  # persist a bounded diagnostic, then let acceptance criteria block promotion
                totals["error_count"] += 1
                errors.append({"case_id": benchmark["case_id"], "error_type": type(exc).__name__, "error": str(exc)})
        candidate_count = totals["candidate_count"]
        totals["survivor_rate"] = round(totals["survivor_count"] / candidate_count, 6) if candidate_count else 0.0
        totals["refutation_rate"] = round(totals["refuted_count"] / candidate_count, 6) if candidate_count else 0.0
        totals["mean_survivor_score"] = round(mean(scores), 6) if scores else None
        totals["errors"] = errors
        return totals

    def evaluate(self, candidate_id: str, *, case_ids: list[str] | None = None) -> dict[str, Any]:
        candidate = self.get_candidate(candidate_id)
        if candidate["status"] == "promoted":
            raise ValueError("A promoted candidate cannot be re-evaluated")
        active = self.active_policy()
        if candidate["base_policy_id"] != active["id"]:
            raise ValueError("Candidate is stale because its base policy is no longer active")
        benchmarks = self._benchmarks(case_ids)
        baseline = self._replay(benchmarks, active["policy"])
        proposed = self._replay(benchmarks, candidate["proposed_policy"])
        delta = {
            "survivor_rate": round(proposed["survivor_rate"] - baseline["survivor_rate"], 6),
            "refutation_rate": round(proposed["refutation_rate"] - baseline["refutation_rate"], 6),
            "candidate_count": proposed["candidate_count"] - baseline["candidate_count"],
            "mean_survivor_score": None,
        }
        if baseline["mean_survivor_score"] is not None and proposed["mean_survivor_score"] is not None:
            delta["mean_survivor_score"] = round(
                proposed["mean_survivor_score"] - baseline["mean_survivor_score"], 6
            )
        criteria = candidate["acceptance_criteria"]
        checks = {
            "minimum_benchmarks": proposed["evaluated_cases"] >= criteria["min_benchmark_cases"],
            "error_budget": max(baseline["error_count"], proposed["error_count"])
            <= criteria["max_error_count"],
            "baseline_invariants": baseline["invariant_failures"] == 0,
            "invariants": proposed["invariant_failures"] <= criteria["max_invariant_failures"],
            "minimum_survivor_delta": delta["survivor_rate"] >= criteria["min_survivor_rate_delta"],
            "maximum_survivor_delta": delta["survivor_rate"] <= criteria["max_survivor_rate_delta"],
        }
        verdict = "eligible_for_review" if all(checks.values()) else "blocked"
        body = {
            "candidate_id": candidate_id,
            "candidate_hash": candidate["candidate_hash"],
            "base_policy_id": active["id"],
            "base_policy_hash": active["policy_hash"],
            "benchmarks": benchmarks,
            "baseline_metrics": baseline,
            "candidate_metrics": proposed,
            "diff": delta,
            "checks": checks,
            "verdict": verdict,
        }
        evaluation_id = _id("evaluation")
        with self._lock:
            self.conn.execute(
                """INSERT INTO improvement_evaluations
                   (id, candidate_id, benchmark_json, baseline_metrics_json, candidate_metrics_json,
                    diff_json, checks_json, verdict, evaluation_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evaluation_id,
                    candidate_id,
                    _stable(benchmarks),
                    _stable(baseline),
                    _stable(proposed),
                    _stable(delta),
                    _stable(checks),
                    verdict,
                    _hash(body),
                    _now(),
                ),
            )
            self.conn.execute(
                "UPDATE improvement_candidates SET status = ? WHERE id = ?",
                ("evaluated" if verdict == "eligible_for_review" else "blocked", candidate_id),
            )
            self._event("evaluated", candidate_id, "orbita-replay-evaluator", body)
            self.conn.commit()
        return self.get_candidate(candidate_id)["latest_evaluation"]

    def promote(
        self,
        candidate_id: str,
        *,
        expected_candidate_hash: str,
        expected_evaluation_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != PROMOTION_PHRASE:
            raise ValueError(f"confirmation must exactly equal: {PROMOTION_PHRASE}")
        if not reviewer.strip() or len(reviewer) > 160:
            raise ValueError("reviewer must identify the approving person or authorized principal")
        candidate = self.get_candidate(candidate_id)
        evaluation = candidate["latest_evaluation"]
        active = self.active_policy()
        if candidate["base_policy_id"] != active["id"]:
            raise ValueError("Candidate is stale because its base policy is no longer active")
        if candidate["candidate_hash"] != expected_candidate_hash:
            raise ValueError("Candidate hash mismatch; fetch and review the proposal again")
        if not evaluation or evaluation["evaluation_hash"] != expected_evaluation_hash:
            raise ValueError("Evaluation hash mismatch; fetch and review the latest replay again")
        if evaluation["verdict"] != "eligible_for_review":
            raise ValueError("The latest evaluation is blocked and cannot be promoted")
        policy = validate_policy(candidate["proposed_policy"])
        with self._lock:
            version_row = self.conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM improvement_policies"
            ).fetchone()
            version = version_row["v"]
            policy_id = _id("policy")
            now = _now()
            self.conn.execute("UPDATE improvement_policies SET status = 'retired' WHERE id = ?", (active["id"],))
            self.conn.execute(
                """INSERT INTO improvement_policies
                   (id, version, parent_policy_id, status, policy_json, policy_hash, rationale,
                    created_at, activated_at, activated_by)
                   VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
                (
                    policy_id,
                    version,
                    active["id"],
                    _stable(policy),
                    _hash(policy),
                    candidate["rationale"],
                    now,
                    now,
                    reviewer.strip(),
                ),
            )
            self.conn.execute(
                "UPDATE improvement_candidates SET status = 'promoted', promoted_policy_id = ? WHERE id = ?",
                (policy_id, candidate_id),
            )
            self._event(
                "promoted",
                policy_id,
                reviewer.strip(),
                {
                    "candidate_id": candidate_id,
                    "candidate_hash": expected_candidate_hash,
                    "evaluation_hash": expected_evaluation_hash,
                    "prior_policy_id": active["id"],
                },
            )
            self.conn.commit()
        return {"status": "promoted", "active_policy": self.active_policy(), "candidate_id": candidate_id}

    def rollback(
        self,
        target_policy_id: str,
        *,
        expected_active_policy_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != ROLLBACK_PHRASE:
            raise ValueError(f"confirmation must exactly equal: {ROLLBACK_PHRASE}")
        if not reviewer.strip() or len(reviewer) > 160:
            raise ValueError("reviewer must identify the approving person or authorized principal")
        active = self.active_policy()
        if active["policy_hash"] != expected_active_policy_hash:
            raise ValueError("Active policy hash mismatch; fetch improvement status and review again")
        with self._lock:
            row = self.conn.execute("SELECT * FROM improvement_policies WHERE id = ?", (target_policy_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown policy: {target_policy_id}")
            target = self._policy_row(row)
            if target["id"] == active["id"]:
                raise ValueError("Target policy is already active")
            if not target.get("activated_at"):
                raise ValueError("Rollback target was never active")
            self.conn.execute("UPDATE improvement_policies SET status = 'rolled_back' WHERE id = ?", (active["id"],))
            self.conn.execute(
                "UPDATE improvement_policies SET status = 'active', activated_at = ?, activated_by = ? WHERE id = ?",
                (_now(), reviewer.strip(), target_policy_id),
            )
            self._event(
                "rolled_back",
                target_policy_id,
                reviewer.strip(),
                {"from_policy_id": active["id"], "from_policy_hash": active["policy_hash"]},
            )
            self.conn.commit()
        return {"status": "rolled_back", "active_policy": self.active_policy(), "replaced_policy_id": active["id"]}

    def _event(self, event_type: str, subject_id: str, actor: str, detail: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO improvement_events (id, event_type, subject_id, actor, detail_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_id("event"), event_type, subject_id, actor, _stable(detail), _now()),
        )
