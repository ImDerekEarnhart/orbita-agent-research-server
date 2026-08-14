"""Append-only registry for governed, inactive Orbita improvement candidates.
This is an admission ledger, not an activation system. It can record a proposed
improvement, freeze its evaluation contract, and preserve an exact evaluation.
It cannot merge code, deploy, alter the active research policy, or promote a
candidate. Activation remains the responsibility of a separately implemented,
exact-hash human approval adapter.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CANDIDATE_KINDS = frozenset(
    {
        "policy_patch",
        "code_patch",
        "execution_adapter",
        "research_operator",
        "language_primitive",
        "verifier",
        "retrieval_change",
        "ui_workflow",
        "performance_change",
        "safety_reliability_change",
    }
)

LIMITATION_KINDS = frozenset(
    {
        "MISSING_ANSWER",
        "MISSING_EXPERIMENT",
        "MISSING_DATA",
        "SEARCH_FAILURE",
        "LANGUAGE_LIMIT",
        "MODEL_LIMIT",
        "EXECUTION_LIMIT",
        "ENGINE_CAPABILITY_LIMIT",
        "POLICY_LIMIT",
        "VERIFIER_LIMIT",
        "NON_IDENTIFIABILITY",
        "PRODUCT_FRICTION",
        "PERFORMANCE_LIMIT",
        "SAFETY_BLOCK",
        "QUESTION_ILL_POSED",
    }
)

EVALUATION_VERDICTS = frozenset({"survived", "refuted", "inconclusive"})
PROOF_PATHS = frozenset({"finite_enumeration", "structural_induction", "smt_model_check", "theorem_verifier"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS improvement_registry_candidates (
    id TEXT PRIMARY KEY,
    candidate_kind TEXT NOT NULL,
    limitation_kind TEXT NOT NULL,
    base_artifact_json TEXT NOT NULL,
    base_hash TEXT NOT NULL,
    candidate_artifact_json TEXT NOT NULL,
    candidate_hash TEXT NOT NULL UNIQUE,
    problem_statement TEXT NOT NULL,
    observed_failure_ids_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    expected_benefit TEXT NOT NULL,
    known_risks_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    parent_candidate_id TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS improvement_registry_plans (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE,
    candidate_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL UNIQUE,
    frozen_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES improvement_registry_candidates(id)
);

CREATE TABLE IF NOT EXISTS improvement_registry_evaluations (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE,
    candidate_hash TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    verdict TEXT NOT NULL,
    evaluation_hash TEXT NOT NULL UNIQUE,
    evaluated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES improvement_registry_candidates(id),
    FOREIGN KEY(plan_id) REFERENCES improvement_registry_plans(id)
);

CREATE TABLE IF NOT EXISTS improvement_registry_events (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES improvement_registry_candidates(id)
);

CREATE TRIGGER IF NOT EXISTS improvement_registry_candidates_no_update
BEFORE UPDATE ON improvement_registry_candidates BEGIN SELECT RAISE(ABORT, 'improvement candidates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS improvement_registry_candidates_no_delete
BEFORE DELETE ON improvement_registry_candidates BEGIN SELECT RAISE(ABORT, 'improvement candidates are append-only'); END;
CREATE TRIGGER IF NOT EXISTS improvement_registry_plans_no_update
BEFORE UPDATE ON improvement_registry_plans BEGIN SELECT RAISE(ABORT, 'frozen evaluation plans are immutable'); END;
CREATE TRIGGER IF NOT EXISTS improvement_registry_plans_no_delete
BEFORE DELETE ON improvement_registry_plans BEGIN SELECT RAISE(ABORT, 'frozen evaluation plans are append-only'); END;
CREATE TRIGGER IF NOT EXISTS improvement_registry_evaluations_no_update
BEFORE UPDATE ON improvement_registry_evaluations BEGIN SELECT RAISE(ABORT, 'improvement evaluations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS improvement_registry_evaluations_no_delete
BEFORE DELETE ON improvement_registry_evaluations BEGIN SELECT RAISE(ABORT, 'improvement evaluations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS improvement_registry_events_no_update
BEFORE UPDATE ON improvement_registry_events BEGIN SELECT RAISE(ABORT, 'improvement events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS improvement_registry_events_no_delete
BEFORE DELETE ON improvement_registry_events BEGIN SELECT RAISE(ABORT, 'improvement events are append-only'); END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("improvement artifacts must be finite JSON values") from exc


def content_hash(value: Any) -> str:
    """Return the canonical SHA-256 used by candidate and evaluation receipts."""
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _text(name: str, value: Any, *, minimum: int = 1, maximum: int = 4_000) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{name} must contain between {minimum} and {maximum} characters")
    return value.strip()


def _string_list(name: str, value: Any, *, maximum: int = 100) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a list containing at most {maximum} strings")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 500:
            raise ValueError(f"{name} entries must be nonblank strings of at most 500 characters")
        result.append(item.strip())
    return result


def _object(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    _stable(value)
    return value


def _validate_language_limit_evidence(limitation_kind: str, evidence: dict[str, Any]) -> None:
    if limitation_kind != "LANGUAGE_LIMIT":
        return
    certificate = evidence.get("language_limit_certificate")
    if not isinstance(certificate, dict):
        raise ValueError("LANGUAGE_LIMIT requires a machine-checkable language_limit_certificate")
    proof_path = certificate.get("proof_path")
    if proof_path not in PROOF_PATHS:
        raise ValueError("LANGUAGE_LIMIT certificate proof_path is not accepted")
    for key in ("grammar_hash", "proof_artifact_hash", "checker_receipt_hash"):
        value = certificate.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"LANGUAGE_LIMIT certificate requires a 64-character {key}")


def _validate_evaluation_plan(plan: dict[str, Any]) -> dict[str, Any]:
    plan = dict(_object("evaluation_plan", plan))
    allowed = {
        "objective",
        "benchmark_case_ids",
        "positive_controls",
        "negative_controls",
        "metrics",
        "acceptance_gates",
        "anti_rescue_rules",
        "global_safety_checks",
        "execution_contract",
        "independent_verifier",
    }
    unknown = sorted(set(plan) - allowed)
    if unknown:
        raise ValueError("Unknown evaluation plan fields: " + ", ".join(unknown))
    result = {
        "objective": _text("objective", plan.get("objective"), minimum=12),
        "benchmark_case_ids": _string_list("benchmark_case_ids", plan.get("benchmark_case_ids")),
        "positive_controls": _string_list("positive_controls", plan.get("positive_controls")),
        "negative_controls": _string_list("negative_controls", plan.get("negative_controls")),
        "metrics": _string_list("metrics", plan.get("metrics")),
        "acceptance_gates": _object("acceptance_gates", plan.get("acceptance_gates", {})),
        "anti_rescue_rules": _string_list("anti_rescue_rules", plan.get("anti_rescue_rules")),
        "global_safety_checks": _string_list("global_safety_checks", plan.get("global_safety_checks")),
        "execution_contract": _object("execution_contract", plan.get("execution_contract", {})),
        "independent_verifier": _object("independent_verifier", plan.get("independent_verifier", {})),
    }
    if not result["metrics"] or not result["acceptance_gates"]:
        raise ValueError("evaluation_plan requires metrics and acceptance_gates")
    if not result["anti_rescue_rules"]:
        raise ValueError("evaluation_plan requires at least one anti_rescue_rule")
    return result


class ImprovementRegistry:
    """Tenant-scoped, append-only admission ledger with no activation authority."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def status(self) -> dict[str, Any]:
        counts = {
            row["event_type"]: row["n"]
            for row in self.conn.execute(
                "SELECT event_type, COUNT(*) AS n FROM improvement_registry_events GROUP BY event_type"
            ).fetchall()
        }
        return {
            "mode": "governed_inactive_candidate_registry",
            "candidate_kinds": sorted(CANDIDATE_KINDS),
            "limitation_kinds": sorted(LIMITATION_KINDS),
            "event_counts": counts,
            "activation_enabled": False,
            "guarantees": [
                "Candidate, plan, evaluation, and event rows are append-only.",
                "Evaluation plans are frozen before results can be recorded.",
                "Expected candidate and plan hashes are required when recording results.",
                "No generalized candidate can promote, merge, deploy, or change active policy here.",
                "LANGUAGE_LIMIT requires an accepted proof path and exact certificate hashes.",
            ],
        }

    def create_candidate(
        self,
        *,
        candidate_kind: str,
        limitation_kind: str,
        base_artifact: dict[str, Any],
        candidate_artifact: dict[str, Any],
        problem_statement: str,
        rationale: str,
        expected_benefit: str,
        observed_failure_ids: list[str] | None = None,
        known_risks: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        parent_candidate_id: str | None = None,
        created_by: str = "agent-proposal",
    ) -> dict[str, Any]:
        if candidate_kind not in CANDIDATE_KINDS:
            raise ValueError("Unknown candidate_kind")
        if limitation_kind not in LIMITATION_KINDS:
            raise ValueError("Unknown limitation_kind")
        base_artifact = _object("base_artifact", base_artifact)
        candidate_artifact = _object("candidate_artifact", candidate_artifact)
        if not candidate_artifact:
            raise ValueError("candidate_artifact cannot be empty")
        evidence = _object("evidence", evidence or {})
        _validate_language_limit_evidence(limitation_kind, evidence)
        if parent_candidate_id is not None:
            self.get_candidate(parent_candidate_id)
        body = {
            "schema_version": "orbita-improvement-candidate/1",
            "candidate_kind": candidate_kind,
            "limitation_kind": limitation_kind,
            "base_artifact": base_artifact,
            "base_hash": content_hash(base_artifact),
            "candidate_artifact": candidate_artifact,
            "problem_statement": _text("problem_statement", problem_statement, minimum=12),
            "observed_failure_ids": _string_list("observed_failure_ids", observed_failure_ids),
            "rationale": _text("rationale", rationale, minimum=12),
            "expected_benefit": _text("expected_benefit", expected_benefit, minimum=12),
            "known_risks": _string_list("known_risks", known_risks),
            "evidence": evidence,
            "parent_candidate_id": parent_candidate_id,
            "created_by": _text("created_by", created_by, maximum=160),
        }
        candidate_hash = content_hash(body)
        candidate_id = _id("improvement_candidate")
        now = _now()
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO improvement_registry_candidates
                       (id, candidate_kind, limitation_kind, base_artifact_json, base_hash,
                        candidate_artifact_json, candidate_hash, problem_statement,
                        observed_failure_ids_json, rationale, expected_benefit, known_risks_json,
                        evidence_json, parent_candidate_id, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        candidate_id,
                        candidate_kind,
                        limitation_kind,
                        _stable(base_artifact),
                        body["base_hash"],
                        _stable(candidate_artifact),
                        candidate_hash,
                        body["problem_statement"],
                        _stable(body["observed_failure_ids"]),
                        body["rationale"],
                        body["expected_benefit"],
                        _stable(body["known_risks"]),
                        _stable(evidence),
                        parent_candidate_id,
                        body["created_by"],
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "candidate_hash" in str(exc):
                    raise ValueError("An identical improvement candidate is already registered") from exc
                raise
            self._event(candidate_id, "candidate_draft", body["created_by"], {"candidate_hash": candidate_hash})
            self.conn.commit()
        return self.get_candidate(candidate_id)

    def list_candidates(self, *, limit: int = 25) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM improvement_registry_candidates ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._candidate_row(row) for row in rows]

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM improvement_registry_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown governed improvement candidate: {candidate_id}")
            return self._candidate_row(row)

    def freeze_evaluation(
        self, candidate_id: str, *, evaluation_plan: dict[str, Any], frozen_by: str
    ) -> dict[str, Any]:
        candidate = self.get_candidate(candidate_id)
        if candidate["evaluation_plan"] is not None:
            raise ValueError("Evaluation plan is already frozen and cannot be replaced")
        plan = _validate_evaluation_plan(evaluation_plan)
        body = {
            "schema_version": "orbita-improvement-evaluation-plan/1",
            "candidate_id": candidate_id,
            "candidate_hash": candidate["candidate_hash"],
            "plan": plan,
        }
        plan_id = _id("improvement_plan")
        plan_hash = content_hash(body)
        actor = _text("frozen_by", frozen_by, maximum=160)
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO improvement_registry_plans
                       (id, candidate_id, candidate_hash, plan_json, plan_hash, frozen_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (plan_id, candidate_id, candidate["candidate_hash"], _stable(plan), plan_hash, actor, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Evaluation plan is already frozen and cannot be replaced") from exc
            self._event(candidate_id, "evaluation_frozen", actor, {"plan_id": plan_id, "plan_hash": plan_hash})
            self.conn.commit()
        return self.get_candidate(candidate_id)["evaluation_plan"]

    def record_evaluation(
        self,
        candidate_id: str,
        *,
        expected_candidate_hash: str,
        expected_plan_hash: str,
        result: dict[str, Any],
        verdict: str,
        evaluated_by: str,
    ) -> dict[str, Any]:
        candidate = self.get_candidate(candidate_id)
        plan = candidate["evaluation_plan"]
        if plan is None:
            raise ValueError("Evaluation plan must be frozen before results are recorded")
        if candidate["evaluation"] is not None:
            raise ValueError("Evaluation is immutable and has already been recorded")
        if expected_candidate_hash != candidate["candidate_hash"]:
            raise ValueError("Candidate hash mismatch; fetch and review the candidate again")
        if expected_plan_hash != plan["plan_hash"]:
            raise ValueError("Evaluation plan hash mismatch; fetch and review the frozen plan again")
        if verdict not in EVALUATION_VERDICTS:
            raise ValueError("verdict must be survived, refuted, or inconclusive")
        result = _object("result", result)
        result_hash = content_hash(result)
        actor = _text("evaluated_by", evaluated_by, maximum=160)
        body = {
            "schema_version": "orbita-improvement-evaluation/1",
            "candidate_id": candidate_id,
            "candidate_hash": candidate["candidate_hash"],
            "plan_id": plan["id"],
            "plan_hash": plan["plan_hash"],
            "result": result,
            "result_hash": result_hash,
            "verdict": verdict,
            "evaluated_by": actor,
        }
        evaluation_id = _id("improvement_evaluation")
        evaluation_hash = content_hash(body)
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO improvement_registry_evaluations
                       (id, candidate_id, candidate_hash, plan_id, plan_hash, result_json,
                        result_hash, verdict, evaluation_hash, evaluated_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        evaluation_id,
                        candidate_id,
                        candidate["candidate_hash"],
                        plan["id"],
                        plan["plan_hash"],
                        _stable(result),
                        result_hash,
                        verdict,
                        evaluation_hash,
                        actor,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Evaluation is immutable and has already been recorded") from exc
            self._event(
                candidate_id,
                f"evaluation_{verdict}",
                actor,
                {"evaluation_id": evaluation_id, "evaluation_hash": evaluation_hash},
            )
            self.conn.commit()
        return self.get_candidate(candidate_id)["evaluation"]

    def promote(self, candidate_id: str, *, actor: str) -> None:
        """Fail closed: Phase 0 deliberately has no generalized activation adapter."""
        self.get_candidate(candidate_id)
        _text("actor", actor, maximum=160)
        raise PermissionError(
            "Generalized improvement activation is disabled; this registry cannot self-promote, merge, deploy, "
            "or change the active policy"
        )

    def _candidate_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("base_artifact_json", "base_artifact"),
            ("candidate_artifact_json", "candidate_artifact"),
            ("observed_failure_ids_json", "observed_failure_ids"),
            ("known_risks_json", "known_risks"),
            ("evidence_json", "evidence"),
        ):
            item[target] = json.loads(item.pop(source))
        plan_row = self.conn.execute(
            "SELECT * FROM improvement_registry_plans WHERE candidate_id = ?", (item["id"],)
        ).fetchone()
        evaluation_row = self.conn.execute(
            "SELECT * FROM improvement_registry_evaluations WHERE candidate_id = ?", (item["id"],)
        ).fetchone()
        event_rows = self.conn.execute(
            "SELECT * FROM improvement_registry_events WHERE candidate_id = ? ORDER BY created_at, id", (item["id"],)
        ).fetchall()
        item["evaluation_plan"] = self._plan_row(plan_row) if plan_row else None
        item["evaluation"] = self._evaluation_row(evaluation_row) if evaluation_row else None
        item["events"] = [self._event_row(event) for event in event_rows]
        item["state"] = item["events"][-1]["event_type"] if item["events"] else "candidate_draft"
        item["activation_enabled"] = False
        return item

    @staticmethod
    def _plan_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["plan"] = json.loads(item.pop("plan_json"))
        return item

    @staticmethod
    def _evaluation_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json"))
        return item

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["detail"] = json.loads(item.pop("detail_json"))
        return item

    def _event(self, candidate_id: str, event_type: str, actor: str, detail: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO improvement_registry_events
               (id, candidate_id, event_type, actor, detail_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_id("improvement_event"), candidate_id, event_type, actor, _stable(detail), _now()),
        )
