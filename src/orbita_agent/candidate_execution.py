"""Append-only receipts for the unified candidate execution dispatcher."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_execution_receipts (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    executor_id TEXT NOT NULL,
    executor_contract_hash TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    candidate_kinds_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    result_reference_json TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    receipt_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS candidate_execution_receipts_no_update
BEFORE UPDATE ON candidate_execution_receipts BEGIN SELECT RAISE(ABORT, 'candidate execution receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS candidate_execution_receipts_no_delete
BEFORE DELETE ON candidate_execution_receipts BEGIN SELECT RAISE(ABORT, 'candidate execution receipts are append-only'); END;
"""


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate execution receipts must contain finite JSON values") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


class CandidateExecutionLedger:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def status(self) -> dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM candidate_execution_receipts").fetchone()[0]
        return {
            "schema_version": "orbita-candidate-execution-ledger/1",
            "receipt_count": count,
            "append_only": True,
            "activation_authority": False,
        }

    def record(
        self,
        *,
        case_id: str,
        plan_id: str,
        plan_hash: str,
        binding: dict[str, Any],
        outcome: str,
        result_reference: dict[str, Any],
    ) -> dict[str, Any]:
        if outcome not in {"completed", "prepared", "failed"}:
            raise ValueError("candidate execution outcome must be completed, prepared, or failed")
        created_at = datetime.now(UTC).isoformat()
        result_hash = content_hash(result_reference)
        body = {
            "schema_version": "orbita-candidate-execution-receipt/1",
            "case_id": case_id,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "executor_id": binding["executor_id"],
            "executor_contract_hash": binding["executor_contract_hash"],
            "binding_hash": binding["binding_hash"],
            "candidate_kinds": binding["candidate_kinds"],
            "outcome": outcome,
            "result_hash": result_hash,
            "created_at": created_at,
        }
        receipt_hash = content_hash(body)
        receipt_id = f"candidate_execution_{uuid.uuid4().hex[:16]}"
        with self._lock:
            self.conn.execute(
                """INSERT INTO candidate_execution_receipts
                   (id, case_id, plan_id, plan_hash, executor_id, executor_contract_hash, binding_hash,
                    candidate_kinds_json, outcome, result_reference_json, result_hash, receipt_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    receipt_id, case_id, plan_id, plan_hash, body["executor_id"],
                    body["executor_contract_hash"], body["binding_hash"], _stable(body["candidate_kinds"]),
                    outcome, _stable(result_reference), result_hash, receipt_hash, created_at,
                ),
            )
            self.conn.commit()
        return self.get(receipt_id)

    def get(self, receipt_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM candidate_execution_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown candidate execution receipt: {receipt_id}")
        item = dict(row)
        item["candidate_kinds"] = json.loads(item.pop("candidate_kinds_json"))
        item["result_reference"] = json.loads(item.pop("result_reference_json"))
        return item

    def list(self, *, limit: int = 25) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        rows = self.conn.execute(
            "SELECT id FROM candidate_execution_receipts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def verify(self, receipt_id: str) -> dict[str, Any]:
        item = self.get(receipt_id)
        failures: list[str] = []
        if item["result_hash"] != content_hash(item["result_reference"]):
            failures.append("result_hash_mismatch")
        body = {
            "schema_version": "orbita-candidate-execution-receipt/1",
            "case_id": item["case_id"],
            "plan_id": item["plan_id"],
            "plan_hash": item["plan_hash"],
            "executor_id": item["executor_id"],
            "executor_contract_hash": item["executor_contract_hash"],
            "binding_hash": item["binding_hash"],
            "candidate_kinds": item["candidate_kinds"],
            "outcome": item["outcome"],
            "result_hash": item["result_hash"],
            "created_at": item["created_at"],
        }
        if item["receipt_hash"] != content_hash(body):
            failures.append("receipt_hash_mismatch")
        return {"receipt_id": receipt_id, "valid": not failures, "failures": failures}
