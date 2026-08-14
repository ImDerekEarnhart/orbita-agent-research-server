"""Append-only governed state machine for arbitrary bounded problem solving.

The cognitive model may propose artifacts, but Orbita owns state transitions,
hash chaining, retry limits, and fail-closed epistemic gates. This service does
not execute tools, reveal hidden scoring data, or activate improvements.
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

STATES = (
    "GOAL", "REPRESENT", "PLAN", "ACT", "OBSERVE", "FALSIFY",
    "DIAGNOSE", "REPAIR_LEARN", "RETRY", "COMMIT_REFUSE", "COMPLETED",
)
TERMINAL_STATE = "COMPLETED"
FALSIFICATION_VERDICTS = frozenset({"survived", "refuted", "inconclusive"})
LIMITATION_KINDS = frozenset(
    {
        "SEARCH_FAILURE", "LANGUAGE_LIMIT", "MODEL_LIMIT", "EXECUTION_LIMIT",
        "ENGINE_CAPABILITY_LIMIT", "VERIFIER_LIMIT", "NON_IDENTIFIABILITY", "QUESTION_ILL_POSED",
    }
)
REPAIR_KINDS = frozenset({"search_strategy", "language_primitive", "model_prompt", "execution_adapter", "verifier"})
STAGE_CONTRACTS = {
    "REPRESENT": {
        "required": ["problem_representation", "unknowns"],
        "optional": ["assumptions", "language_snapshot_hash"],
    },
    "PLAN": {
        "required": ["steps", "falsifiers", "success_checks", "anti_rescue_rules"],
        "optional": ["executor_requirements"],
    },
    "ACT": {
        "required": ["executor", "action_receipts", "execution_status"],
        "optional": ["external_state_changed"],
        "execution_status": ["completed", "failed", "blocked", "not_executed"],
    },
    "OBSERVE": {
        "required": ["observations", "evidence_hashes"],
        "optional": ["missing_observations"],
    },
    "FALSIFY": {
        "required": ["verdict", "checks", "failed_checks", "scope"],
        "optional": ["counterexample_hashes"],
        "verdict": sorted(FALSIFICATION_VERDICTS),
    },
    "DIAGNOSE": {
        "required": ["limitation_kind", "rationale", "evidence_hashes"],
        "conditional": ["LANGUAGE_LIMIT also requires language_limit_certificate_hash"],
        "limitation_kind": sorted(LIMITATION_KINDS),
    },
    "REPAIR_LEARN": {
        "required": ["repair_kind", "candidate_hash", "prospective_predictions", "known_risks"],
        "repair_kind": sorted(REPAIR_KINDS),
        "activation_requested": False,
    },
    "RETRY": {
        "required": ["change_summary", "retained_falsifiers", "retry_authorized"],
        "retry_authorized": True,
    },
    "COMMIT_REFUSE": {
        "required": ["decision", "statement", "evidence_hashes", "limitations"],
        "decision": ["commit", "refuse"],
        "conditional": ["commit requires previously recorded evidence hashes"],
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS general_problem_loops (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    success_criteria_json TEXT NOT NULL,
    allowed_capabilities_json TEXT NOT NULL,
    max_cycles INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS general_problem_loop_events (
    id TEXT PRIMARY KEY,
    loop_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    state TEXT NOT NULL,
    next_state TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    artifact_json TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    previous_event_hash TEXT,
    event_hash TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(loop_id, sequence),
    FOREIGN KEY(loop_id) REFERENCES general_problem_loops(id)
);
CREATE TRIGGER IF NOT EXISTS general_problem_loops_no_update
BEFORE UPDATE ON general_problem_loops BEGIN SELECT RAISE(ABORT, 'general problem loops are immutable'); END;
CREATE TRIGGER IF NOT EXISTS general_problem_loops_no_delete
BEFORE DELETE ON general_problem_loops BEGIN SELECT RAISE(ABORT, 'general problem loops are append-only'); END;
CREATE TRIGGER IF NOT EXISTS general_problem_loop_events_no_update
BEFORE UPDATE ON general_problem_loop_events BEGIN SELECT RAISE(ABORT, 'general problem loop events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS general_problem_loop_events_no_delete
BEFORE DELETE ON general_problem_loop_events BEGIN SELECT RAISE(ABORT, 'general problem loop events are append-only'); END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("problem-loop artifacts must contain finite JSON values") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _text(name: str, value: Any, *, minimum: int = 1, maximum: int = 8_000) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{name} must contain between {minimum} and {maximum} characters")
    return value.strip()


def _strings(name: str, value: Any, *, required: bool = False, maximum: int = 256) -> list[str]:
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a list of at most {maximum} strings")
    result = []
    for item in value:
        normalized = _text(f"{name} entry", item, maximum=1_000)
        if normalized not in result:
            result.append(normalized)
    if required and not result:
        raise ValueError(f"{name} must contain at least one item")
    return result


def _object(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    _stable(value)
    return value


def _sha(name: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return value.lower()


def _unknown_fields(stage: str, artifact: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(artifact) - allowed)
    if unknown:
        raise ValueError(f"unknown {stage} artifact fields: " + ", ".join(unknown))


def _validate_artifact(state: str, artifact: dict[str, Any]) -> dict[str, Any]:
    artifact = dict(_object("artifact", artifact))
    if artifact.get("activation_requested") is True:
        raise PermissionError("the General Problem Loop cannot request or perform runtime activation")
    if state == "REPRESENT":
        _unknown_fields(state, artifact, {"problem_representation", "assumptions", "unknowns", "language_snapshot_hash", "activation_requested"})
        snapshot_hash = artifact.get("language_snapshot_hash")
        return {
            "problem_representation": _text("problem_representation", artifact.get("problem_representation"), minimum=12),
            "assumptions": _strings("assumptions", artifact.get("assumptions")),
            "unknowns": _strings("unknowns", artifact.get("unknowns"), required=True),
            "language_snapshot_hash": _sha("language_snapshot_hash", snapshot_hash) if snapshot_hash else None,
        }
    if state == "PLAN":
        _unknown_fields(state, artifact, {"steps", "falsifiers", "success_checks", "executor_requirements", "anti_rescue_rules"})
        return {
            "steps": _strings("steps", artifact.get("steps"), required=True),
            "falsifiers": _strings("falsifiers", artifact.get("falsifiers"), required=True),
            "success_checks": _strings("success_checks", artifact.get("success_checks"), required=True),
            "executor_requirements": _strings("executor_requirements", artifact.get("executor_requirements")),
            "anti_rescue_rules": _strings("anti_rescue_rules", artifact.get("anti_rescue_rules"), required=True),
        }
    if state == "ACT":
        _unknown_fields(state, artifact, {"executor", "action_receipts", "execution_status", "external_state_changed"})
        status = _text("execution_status", artifact.get("execution_status"), maximum=80)
        if status not in {"completed", "failed", "blocked", "not_executed"}:
            raise ValueError("execution_status must be completed, failed, blocked, or not_executed")
        receipts = artifact.get("action_receipts") or []
        if not isinstance(receipts, list) or len(receipts) > 256:
            raise ValueError("action_receipts must be a list")
        normalized_receipts = []
        for receipt in receipts:
            receipt = _object("action receipt", receipt)
            receipt_status = _text("receipt.status", receipt.get("status"), maximum=80)
            if receipt_status not in {"completed", "failed", "blocked"}:
                raise ValueError("receipt.status must be completed, failed, or blocked")
            normalized_receipts.append(
                {
                    "executor": _text("receipt.executor", receipt.get("executor"), maximum=160),
                    "receipt_hash": _sha("receipt_hash", receipt.get("receipt_hash")),
                    "status": receipt_status,
                }
            )
        if status == "completed" and not normalized_receipts:
            raise ValueError("completed execution requires at least one hash-bound action receipt")
        if status == "completed" and any(receipt["status"] != "completed" for receipt in normalized_receipts):
            raise ValueError("completed execution cannot contain a non-completed action receipt")
        return {
            "executor": _text("executor", artifact.get("executor"), maximum=160),
            "action_receipts": normalized_receipts,
            "execution_status": status,
            "external_state_changed": bool(artifact.get("external_state_changed", False)),
        }
    if state == "OBSERVE":
        _unknown_fields(state, artifact, {"observations", "evidence_hashes", "missing_observations"})
        return {
            "observations": _strings("observations", artifact.get("observations"), required=True),
            "evidence_hashes": [_sha("evidence_hash", item) for item in _strings("evidence_hashes", artifact.get("evidence_hashes"), required=True)],
            "missing_observations": _strings("missing_observations", artifact.get("missing_observations")),
        }
    if state == "FALSIFY":
        _unknown_fields(state, artifact, {"verdict", "checks", "failed_checks", "counterexample_hashes", "scope"})
        verdict = _text("verdict", artifact.get("verdict"), maximum=80)
        if verdict not in FALSIFICATION_VERDICTS:
            raise ValueError("verdict must be survived, refuted, or inconclusive")
        failed = _strings("failed_checks", artifact.get("failed_checks"))
        if verdict == "refuted" and not failed:
            raise ValueError("a refuted verdict requires at least one failed check")
        if verdict == "survived" and failed:
            raise ValueError("a survived verdict cannot contain failed checks")
        return {
            "verdict": verdict,
            "checks": _strings("checks", artifact.get("checks"), required=True),
            "failed_checks": failed,
            "counterexample_hashes": [_sha("counterexample_hash", item) for item in _strings("counterexample_hashes", artifact.get("counterexample_hashes"))],
            "scope": _text("scope", artifact.get("scope"), maximum=500),
        }
    if state == "DIAGNOSE":
        _unknown_fields(state, artifact, {"limitation_kind", "rationale", "evidence_hashes", "language_limit_certificate_hash"})
        kind = _text("limitation_kind", artifact.get("limitation_kind"), maximum=80)
        if kind not in LIMITATION_KINDS:
            raise ValueError("unsupported limitation_kind")
        certificate = artifact.get("language_limit_certificate_hash")
        if kind == "LANGUAGE_LIMIT" and not certificate:
            raise ValueError("LANGUAGE_LIMIT requires a language_limit_certificate_hash")
        return {
            "limitation_kind": kind,
            "rationale": _text("rationale", artifact.get("rationale"), minimum=12),
            "evidence_hashes": [_sha("evidence_hash", item) for item in _strings("evidence_hashes", artifact.get("evidence_hashes"), required=True)],
            "language_limit_certificate_hash": _sha("language_limit_certificate_hash", certificate) if certificate else None,
        }
    if state == "REPAIR_LEARN":
        _unknown_fields(state, artifact, {"repair_kind", "candidate_hash", "prospective_predictions", "known_risks", "activation_requested"})
        kind = _text("repair_kind", artifact.get("repair_kind"), maximum=80)
        if kind not in REPAIR_KINDS:
            raise ValueError("unsupported repair_kind")
        return {
            "repair_kind": kind,
            "candidate_hash": _sha("candidate_hash", artifact.get("candidate_hash")),
            "prospective_predictions": _strings("prospective_predictions", artifact.get("prospective_predictions"), required=True),
            "known_risks": _strings("known_risks", artifact.get("known_risks"), required=True),
            "activation_requested": False,
        }
    if state == "RETRY":
        _unknown_fields(state, artifact, {"change_summary", "retained_falsifiers", "retry_authorized"})
        if artifact.get("retry_authorized") is not True:
            raise PermissionError("retry_authorized must be true for this bounded retry")
        return {
            "change_summary": _text("change_summary", artifact.get("change_summary"), minimum=12),
            "retained_falsifiers": _strings("retained_falsifiers", artifact.get("retained_falsifiers"), required=True),
            "retry_authorized": True,
        }
    if state == "COMMIT_REFUSE":
        _unknown_fields(state, artifact, {"decision", "statement", "evidence_hashes", "limitations"})
        decision = _text("decision", artifact.get("decision"), maximum=40)
        if decision not in {"commit", "refuse"}:
            raise ValueError("decision must be commit or refuse")
        evidence = [_sha("evidence_hash", item) for item in _strings("evidence_hashes", artifact.get("evidence_hashes"))]
        if decision == "commit" and not evidence:
            raise ValueError("commit requires at least one exact evidence hash")
        return {
            "decision": decision,
            "statement": _text("statement", artifact.get("statement"), minimum=12),
            "evidence_hashes": evidence,
            "limitations": _strings("limitations", artifact.get("limitations"), required=True),
        }
    raise ValueError(f"no artifact can be submitted for state {state}")


def _next_state(state: str, artifact: dict[str, Any], *, cycle: int, max_cycles: int) -> tuple[str, int]:
    linear = {"REPRESENT": "PLAN", "PLAN": "ACT", "ACT": "OBSERVE", "OBSERVE": "FALSIFY"}
    if state in linear:
        return linear[state], cycle
    if state == "FALSIFY":
        return ("COMMIT_REFUSE" if artifact["verdict"] == "survived" else "DIAGNOSE"), cycle
    if state == "DIAGNOSE":
        return ("COMMIT_REFUSE" if artifact["limitation_kind"] in {"NON_IDENTIFIABILITY", "QUESTION_ILL_POSED"} else "REPAIR_LEARN"), cycle
    if state == "REPAIR_LEARN":
        return "RETRY", cycle
    if state == "RETRY":
        if cycle >= max_cycles:
            return "COMMIT_REFUSE", cycle
        return "REPRESENT", cycle + 1
    if state == "COMMIT_REFUSE":
        return TERMINAL_STATE, cycle
    raise ValueError(f"invalid transition from {state}")


class GeneralProblemLoopService:
    """Tenant-local append-only problem-loop ledger with deterministic transitions."""

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
        count = self.conn.execute("SELECT COUNT(*) FROM general_problem_loops").fetchone()[0]
        return {
            "mode": "governed_general_problem_loop", "loop_count": count, "states": list(STATES),
            "llm_role": "proposal_only", "orbita_role": "state_transition_and_evidence_governor",
            "tool_execution": "receipts_only", "runtime_activation": False,
            "stage_contracts": STAGE_CONTRACTS,
        }

    def create(self, *, goal: str, success_criteria: list[str], allowed_capabilities: list[str], max_cycles: int = 3, created_by: str = "user") -> dict[str, Any]:
        if not isinstance(max_cycles, int) or isinstance(max_cycles, bool) or not 0 <= max_cycles <= 20:
            raise ValueError("max_cycles must be an integer between 0 and 20")
        goal = _text("goal", goal, minimum=12)
        success = _strings("success_criteria", success_criteria, required=True)
        capabilities = _strings("allowed_capabilities", allowed_capabilities, required=True)
        actor = _text("created_by", created_by, maximum=160)
        loop_id, created_at = _id("problem_loop"), _now()
        with self._lock:
            self.conn.execute(
                """INSERT INTO general_problem_loops
                   (id, goal, success_criteria_json, allowed_capabilities_json, max_cycles, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (loop_id, goal, _stable(success), _stable(capabilities), max_cycles, actor, created_at),
            )
            artifact = {"goal": goal, "success_criteria": success, "allowed_capabilities": capabilities, "max_cycles": max_cycles}
            self._append_event(loop_id, "GOAL", "REPRESENT", 0, artifact, actor, None, 0)
            self.conn.commit()
        return self.get(loop_id)

    def list(self, *, limit: int = 25) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        rows = self.conn.execute("SELECT id FROM general_problem_loops ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self.get(row["id"], include_events=False) for row in rows]

    def get(self, loop_id: str, *, include_events: bool = True) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM general_problem_loops WHERE id = ?", (loop_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown general problem loop: {loop_id}")
        events = self.conn.execute("SELECT * FROM general_problem_loop_events WHERE loop_id = ? ORDER BY sequence", (loop_id,)).fetchall()
        if not events:
            raise RuntimeError("problem loop has no genesis event")
        latest = events[-1]
        item = dict(row)
        item["success_criteria"] = json.loads(item.pop("success_criteria_json"))
        item["allowed_capabilities"] = json.loads(item.pop("allowed_capabilities_json"))
        item |= {
            "current_state": latest["next_state"], "cycle": latest["cycle"], "event_count": len(events),
            "latest_event_hash": latest["event_hash"], "terminal": latest["next_state"] == TERMINAL_STATE,
            "activation_enabled": False,
        }
        item["required_artifact"] = STAGE_CONTRACTS.get(item["current_state"])
        if include_events:
            item["events"] = [self._event_row(event) for event in events]
        return item

    def submit(self, loop_id: str, *, expected_state: str, expected_previous_event_hash: str, artifact: dict[str, Any], actor: str) -> dict[str, Any]:
        actor = _text("actor", actor, maximum=160)
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                loop = self.get(loop_id)
                if loop["terminal"]:
                    raise ValueError("general problem loop is already completed")
                if expected_state != loop["current_state"]:
                    raise ValueError(f"state mismatch: expected {loop['current_state']}")
                if _sha("expected_previous_event_hash", expected_previous_event_hash) != loop["latest_event_hash"]:
                    raise ValueError("previous event hash mismatch")
                normalized = _validate_artifact(loop["current_state"], artifact)
                known_hashes = self._known_hashes(loop["events"])
                if loop["current_state"] == "PLAN":
                    unavailable = sorted(set(normalized["executor_requirements"]) - set(loop["allowed_capabilities"]))
                    if unavailable:
                        raise PermissionError("plan requests capabilities outside the frozen allowlist: " + ", ".join(unavailable))
                if loop["current_state"] == "ACT":
                    used = {normalized["executor"], *(item["executor"] for item in normalized["action_receipts"])}
                    unavailable = sorted(used - set(loop["allowed_capabilities"]))
                    if unavailable:
                        raise PermissionError("action uses executors outside the frozen allowlist: " + ", ".join(unavailable))
                if loop["current_state"] == "OBSERVE":
                    unknown = sorted(set(normalized["evidence_hashes"]) - known_hashes)
                    if unknown:
                        raise ValueError("observation cites evidence absent from prior action receipts")
                if loop["current_state"] == "FALSIFY":
                    unknown = sorted(set(normalized["counterexample_hashes"]) - known_hashes)
                    if unknown:
                        raise ValueError("falsification cites counterexamples absent from prior receipts or evidence")
                if loop["current_state"] == "DIAGNOSE":
                    unknown = sorted(set(normalized["evidence_hashes"]) - known_hashes)
                    if unknown:
                        raise ValueError("diagnosis cites evidence absent from the loop history")
                    certificate = normalized["language_limit_certificate_hash"]
                    if certificate and certificate not in normalized["evidence_hashes"]:
                        raise ValueError("language-limit certificate hash must also be cited as diagnosis evidence")
                if loop["current_state"] == "COMMIT_REFUSE" and normalized["decision"] == "commit":
                    unknown = sorted(set(normalized["evidence_hashes"]) - known_hashes)
                    if unknown:
                        raise ValueError("commit cites evidence absent from the loop history")
                next_state, next_cycle = _next_state(loop["current_state"], normalized, cycle=loop["cycle"], max_cycles=loop["max_cycles"])
                self._append_event(loop_id, loop["current_state"], next_state, next_cycle, normalized, actor, loop["latest_event_hash"], loop["event_count"])
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return self.get(loop_id)

    @staticmethod
    def _known_hashes(events: list[dict[str, Any]]) -> set[str]:
        known: set[str] = set()
        for event in events:
            artifact = event["artifact"]
            known.update(artifact.get("evidence_hashes") or [])
            known.update(artifact.get("counterexample_hashes") or [])
            for receipt in artifact.get("action_receipts") or []:
                known.add(receipt["receipt_hash"])
        return known

    def verify_chain(self, loop_id: str) -> dict[str, Any]:
        loop, previous, failures = self.get(loop_id), None, []
        for event in loop["events"]:
            body = {
                "loop_id": loop_id, "sequence": event["sequence"], "state": event["state"],
                "next_state": event["next_state"], "cycle": event["cycle"],
                "artifact_hash": event["artifact_hash"], "previous_event_hash": event["previous_event_hash"],
                "actor": event["actor"],
            }
            if event["artifact_hash"] != content_hash(event["artifact"]):
                failures.append({"sequence": event["sequence"], "reason": "artifact_hash_mismatch"})
            if event["previous_event_hash"] != previous:
                failures.append({"sequence": event["sequence"], "reason": "previous_event_hash_mismatch"})
            if event["event_hash"] != content_hash(body):
                failures.append({"sequence": event["sequence"], "reason": "event_hash_mismatch"})
            previous = event["event_hash"]
        return {"loop_id": loop_id, "valid": not failures, "event_count": len(loop["events"]), "failures": failures}

    def _append_event(self, loop_id: str, state: str, next_state: str, cycle: int, artifact: dict[str, Any], actor: str, previous_event_hash: str | None, sequence: int) -> None:
        artifact_hash = content_hash(artifact)
        body = {
            "loop_id": loop_id, "sequence": sequence, "state": state, "next_state": next_state, "cycle": cycle,
            "artifact_hash": artifact_hash, "previous_event_hash": previous_event_hash, "actor": actor,
        }
        self.conn.execute(
            """INSERT INTO general_problem_loop_events
               (id, loop_id, sequence, state, next_state, cycle, artifact_json, artifact_hash,
                previous_event_hash, event_hash, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_id("problem_event"), loop_id, sequence, state, next_state, cycle, _stable(artifact), artifact_hash,
             previous_event_hash, content_hash(body), actor, _now()),
        )

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["artifact"] = json.loads(item.pop("artifact_json"))
        return item
