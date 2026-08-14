"""Immutable, source-aware evidence receipts and decision eligibility.

Normalization preserves route-specific provenance.  It does not turn one source
kind into another and never grants admission, activation, promotion, or deploy
authority.  Eligibility is derived from a fixed policy rather than caller input.
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

SCHEMA = "orbita-evidence-receipt/1"
ELIGIBILITY_SCHEMA = "orbita-evidence-eligibility/1"

SOURCE_KINDS = frozenset(
    {
        "DISCOVERY_RUN",
        "GENOME_TOURNAMENT",
        "EXTERNAL_EXPERIMENT",
        "PROOF_ASSISTANT",
        "INDEPENDENT_VERIFIER",
    }
)
INDEPENDENCE_LEVELS = (
    "same_execution",
    "same_case",
    "same_family",
    "cross_domain",
    "external",
)
DECISION_KINDS = frozenset(
    {
        "SCIENTIFIC_CLAIM_REVIEW",
        "RESEARCH_POLICY_REVIEW",
        "DISCOVERY_OPERATOR_REVIEW",
        "LANGUAGE_LIMIT_CERTIFICATE_REVIEW",
        "REPAIR_CANDIDATE_REVIEW",
        "VERIFICATION_REVIEW",
        "SEMANTIC_ADMISSION",
        "SEMANTIC_ACTIVATION",
        "POLICY_PROMOTION",
        "CODE_DEPLOYMENT",
        "ARCHITECTURE_ACTIVATION",
    }
)
NEVER_EVIDENCE_ONLY = frozenset(
    {
        "SEMANTIC_ADMISSION",
        "SEMANTIC_ACTIVATION",
        "POLICY_PROMOTION",
        "CODE_DEPLOYMENT",
        "ARCHITECTURE_ACTIVATION",
    }
)
SOURCE_DECISIONS = {
    "DISCOVERY_RUN": frozenset({"SCIENTIFIC_CLAIM_REVIEW", "RESEARCH_POLICY_REVIEW"}),
    "GENOME_TOURNAMENT": frozenset({"DISCOVERY_OPERATOR_REVIEW"}),
    "EXTERNAL_EXPERIMENT": frozenset({"SCIENTIFIC_CLAIM_REVIEW", "REPAIR_CANDIDATE_REVIEW"}),
    "PROOF_ASSISTANT": frozenset({"LANGUAGE_LIMIT_CERTIFICATE_REVIEW"}),
    "INDEPENDENT_VERIFIER": frozenset({"VERIFICATION_REVIEW"}),
}

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS normalized_evidence_receipts (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(source_kind, source_id)
);
CREATE TRIGGER IF NOT EXISTS normalized_evidence_receipts_no_update
BEFORE UPDATE ON normalized_evidence_receipts BEGIN SELECT RAISE(ABORT, 'evidence receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS normalized_evidence_receipts_no_delete
BEFORE DELETE ON normalized_evidence_receipts BEGIN SELECT RAISE(ABORT, 'evidence receipts are append-only'); END;
"""


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence receipts must contain finite JSON values") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _text(name: str, value: Any, *, maximum: int = 4_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{name} must be a nonblank string of at most {maximum} characters")
    return value.strip()


def _hash(name: str, value: Any) -> str:
    value = _text(name, value, maximum=64).lower()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a 64-character SHA-256 digest")
    return value


def normalize_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("evidence scope must be an object")
    allowed = {"domain", "quantifier", "boundary", "assumptions"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unknown evidence scope fields: " + ", ".join(unknown))
    boundary = value.get("boundary")
    if not isinstance(boundary, dict) or not boundary:
        raise ValueError("evidence scope requires a nonempty boundary")
    assumptions = value.get("assumptions", [])
    if not isinstance(assumptions, list) or any(not isinstance(item, str) or not item.strip() for item in assumptions):
        raise ValueError("evidence scope assumptions must be nonblank strings")
    scope = {
        "domain": _text("scope.domain", value.get("domain"), maximum=200),
        "quantifier": _text("scope.quantifier", value.get("quantifier"), maximum=80),
        "boundary": json.loads(_stable(boundary)),
        "assumptions": list(dict.fromkeys(item.strip() for item in assumptions)),
    }
    return scope | {"scope_hash": content_hash(scope)}


def normalize_independence(value: Any, *, source_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("evidence independence must be an object")
    allowed = {"level", "subject_id", "evaluator_id", "rationale", "verifier_receipt_hash"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unknown evidence independence fields: " + ", ".join(unknown))
    level = _text("independence.level", value.get("level"), maximum=40)
    if level not in INDEPENDENCE_LEVELS:
        raise ValueError("evidence independence level is invalid")
    subject_id = _text("independence.subject_id", value.get("subject_id") or source_id, maximum=300)
    evaluator_id = _text("independence.evaluator_id", value.get("evaluator_id"), maximum=300)
    if level == "external" and evaluator_id == subject_id:
        raise ValueError("external independence requires a different evaluator and subject")
    verifier_hash = value.get("verifier_receipt_hash")
    if verifier_hash is not None:
        verifier_hash = _hash("independence.verifier_receipt_hash", verifier_hash)
    independence = {
        "level": level,
        "subject_id": subject_id,
        "evaluator_id": evaluator_id,
        "rationale": _text("independence.rationale", value.get("rationale"), maximum=2_000),
        "verifier_receipt_hash": verifier_hash,
    }
    return independence | {"independence_hash": content_hash(independence)}


def evidence_eligibility(receipt: dict[str, Any]) -> dict[str, Any]:
    source_kind = receipt["source_kind"]
    allowed = set(SOURCE_DECISIONS[source_kind])
    reasons: list[str] = []
    result = receipt["result"]
    if result.get("status") in {"UNVERIFIED", "PENDING", "NOT_SUBMITTED"}:
        allowed.clear()
        reasons.append("source result has not completed its declared verification path")
    if source_kind in {"GENOME_TOURNAMENT", "EXTERNAL_EXPERIMENT"} and not receipt["frozen_before_reveal"]:
        allowed.discard("DISCOVERY_OPERATOR_REVIEW")
        allowed.discard("REPAIR_CANDIDATE_REVIEW")
        reasons.append("prospective decision support requires freeze before reveal")
    if source_kind == "EXTERNAL_EXPERIMENT" and receipt["independence"]["level"] in {
        "same_execution",
        "same_case",
    }:
        allowed.discard("REPAIR_CANDIDATE_REVIEW")
        reasons.append("repair review requires at least same-family-independent evaluation")
    return {
        "schema": ELIGIBILITY_SCHEMA,
        "allowed_decisions": sorted(allowed),
        "prohibited_decisions": sorted(DECISION_KINDS - allowed),
        "evidence_only_authority": False,
        "reasons": reasons,
    }


class EvidenceReceiptLedger:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DB_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def record(
        self,
        *,
        source_kind: str,
        source_id: str,
        source_hashes: dict[str, str],
        frozen_before_reveal: bool,
        scope: dict[str, Any],
        result: dict[str, Any],
        evaluator: dict[str, Any],
        independence: dict[str, Any],
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        if source_kind not in SOURCE_KINDS:
            raise ValueError("unsupported evidence source kind")
        source_id = _text("source_id", source_id, maximum=300)
        if not isinstance(frozen_before_reveal, bool):
            raise ValueError("frozen_before_reveal must be boolean")
        if not isinstance(source_hashes, dict) or not source_hashes:
            raise ValueError("source_hashes must be a nonempty object")
        exact_hashes = {
            _text("source hash name", key, maximum=100): _hash(f"source_hashes.{key}", value)
            for key, value in sorted(source_hashes.items())
        }
        if not isinstance(result, dict) or not result:
            raise ValueError("evidence result must be a nonempty object")
        if not isinstance(evaluator, dict):
            raise ValueError("evaluator must be an object")
        normalized_evaluator = {
            "id": _text("evaluator.id", evaluator.get("id"), maximum=300),
            "kind": _text("evaluator.kind", evaluator.get("kind"), maximum=100),
            "receipt_hash": (
                _hash("evaluator.receipt_hash", evaluator["receipt_hash"])
                if evaluator.get("receipt_hash") is not None
                else None
            ),
        }
        if not isinstance(provenance, dict) or not provenance:
            raise ValueError("provenance must be a nonempty object")
        created_at = datetime.now(UTC).isoformat()
        core = {
            "schema": SCHEMA,
            "source_kind": source_kind,
            "source_id": source_id,
            "source_hashes": exact_hashes,
            "frozen_before_reveal": frozen_before_reveal,
            "scope": normalize_scope(scope),
            "result": json.loads(_stable(result)),
            "evaluator": normalized_evaluator,
            "independence": normalize_independence(independence, source_id=source_id),
            "provenance": json.loads(_stable(provenance)),
        }
        body = core | {"normalization_hash": content_hash(core), "created_at": created_at}
        body["eligibility"] = evidence_eligibility(body)
        receipt_hash = content_hash(body)
        receipt_id = f"evidence_{uuid.uuid4().hex[:16]}"
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO normalized_evidence_receipts
                       (id, source_kind, source_id, receipt_json, receipt_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (receipt_id, source_kind, source_id, _stable(body), receipt_hash, created_at),
                )
            except sqlite3.IntegrityError as exc:
                existing = self.conn.execute(
                    "SELECT id, receipt_hash FROM normalized_evidence_receipts WHERE source_kind = ? AND source_id = ?",
                    (source_kind, source_id),
                ).fetchone()
                if existing:
                    persisted = self.get(existing["id"])
                    if persisted.get("normalization_hash") == body["normalization_hash"]:
                        return persisted
                raise ValueError("evidence source was already normalized with different contents") from exc
            self.conn.commit()
        return self.get(receipt_id)

    def get(self, receipt_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM normalized_evidence_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown normalized evidence receipt: {receipt_id}")
        body = json.loads(row["receipt_json"])
        return {"id": row["id"], **body, "receipt_hash": row["receipt_hash"]}

    def list(self, *, limit: int = 25) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        rows = self.conn.execute(
            "SELECT id FROM normalized_evidence_receipts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def verify(self, receipt_id: str) -> dict[str, Any]:
        item = self.get(receipt_id)
        body = {key: value for key, value in item.items() if key not in {"id", "receipt_hash"}}
        failures: list[str] = []
        if content_hash(body) != item["receipt_hash"]:
            failures.append("receipt_hash_mismatch")
        core = {
            key: value
            for key, value in item.items()
            if key not in {"id", "receipt_hash", "normalization_hash", "created_at", "eligibility"}
        }
        if content_hash(core) != item["normalization_hash"]:
            failures.append("normalization_hash_mismatch")
        scope = {key: value for key, value in item["scope"].items() if key != "scope_hash"}
        if content_hash(scope) != item["scope"]["scope_hash"]:
            failures.append("scope_hash_mismatch")
        independence = {
            key: value for key, value in item["independence"].items() if key != "independence_hash"
        }
        if content_hash(independence) != item["independence"]["independence_hash"]:
            failures.append("independence_hash_mismatch")
        if item["eligibility"] != evidence_eligibility(body):
            failures.append("eligibility_policy_mismatch")
        return {"receipt_id": receipt_id, "valid": not failures, "failures": failures}

    def check_decision(self, receipt_id: str, decision_kind: str) -> dict[str, Any]:
        if decision_kind not in DECISION_KINDS:
            raise ValueError("unsupported evidence decision kind")
        receipt = self.get(receipt_id)
        eligible = decision_kind in receipt["eligibility"]["allowed_decisions"]
        return {
            "receipt_id": receipt_id,
            "decision_kind": decision_kind,
            "eligible": eligible,
            "requires_separate_authority": decision_kind in NEVER_EVIDENCE_ONLY,
            "receipt_hash": receipt["receipt_hash"],
        }

    def status(self) -> dict[str, Any]:
        count = self.conn.execute("SELECT COUNT(*) FROM normalized_evidence_receipts").fetchone()[0]
        return {
            "schema": "orbita-evidence-normalization-status/1",
            "receipt_count": count,
            "source_kinds": sorted(SOURCE_KINDS),
            "decision_kinds": sorted(DECISION_KINDS),
            "append_only": True,
            "activation_authority": False,
            "policy": {key: sorted(value) for key, value in sorted(SOURCE_DECISIONS.items())},
        }


def discovery_run_receipt_input(case: dict[str, Any], run: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if run.get("status") != "completed":
        raise ValueError("only a completed discovery run can be normalized")
    if run.get("case_id") != case.get("id") or run.get("plan_id") != plan.get("id"):
        raise ValueError("discovery run, case, and plan bindings do not match")
    return {
        "source_kind": "DISCOVERY_RUN",
        "source_id": run["id"],
        "source_hashes": {
            "plan_hash": plan["plan_hash"],
            "result_hash": content_hash(run.get("result", {})),
        },
        "frozen_before_reveal": False,
        "scope": {
            "domain": "orbita_discovery_case",
            "quantifier": "finite_run",
            "boundary": {"case_id": case["id"], "plan_id": plan["id"], "run_id": run["id"]},
            "assumptions": ["finite run survival is not universal proof"],
        },
        "result": {"status": "COMPLETED", "summary": run.get("result", {}).get("summary", {})},
        "evaluator": {"id": "orbita-discovery-engine", "kind": "deterministic_pipeline"},
        "independence": {
            "level": "same_case",
            "subject_id": run["id"],
            "evaluator_id": "orbita-discovery-engine",
            "rationale": "The governed discovery engine evaluated one frozen case plan.",
        },
        "provenance": {"case_id": case["id"], "plan_id": plan["id"], "run_id": run["id"]},
    }


def genome_tournament_receipt_input(payload: dict[str, Any], entry_id: str) -> dict[str, Any]:
    tournament = payload.get("tournament") if isinstance(payload, dict) else None
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    if not isinstance(tournament, dict) or tournament.get("status") != "frozen":
        raise ValueError("only a frozen Discovery Genome tournament can be normalized")
    entry = next((item for item in entries if isinstance(item, dict) and item.get("id") == entry_id), None)
    if entry is None or not entry.get("verdict") or not isinstance(entry.get("result_json"), dict):
        raise ValueError("Genome tournament entry must have a recorded result")
    result_body = {
        "schema": "orbita.discovery-tournament-result.v1",
        "tournament_id": tournament["id"],
        "entry_id": entry_id,
        "verdict": entry["verdict"],
        "result": entry["result_json"],
    }
    if entry.get("result_hash") != content_hash(result_body):
        raise ValueError("Genome tournament result hash does not match its recorded contents")
    if isinstance(entry.get("prediction_json"), dict) and entry.get("prediction_hash") != content_hash(
        entry["prediction_json"]
    ):
        raise ValueError("Genome prediction hash does not match its frozen contents")
    hashes = {
        "manifest_hash": tournament.get("manifest_hash"),
        "prediction_hash": entry.get("prediction_hash"),
        "result_hash": entry.get("result_hash"),
    }
    if entry.get("operator_contract_hash"):
        hashes["operator_contract_hash"] = entry["operator_contract_hash"]
    target = tournament.get("target_json") or tournament.get("target") or {}
    independence = entry.get("independence_level") or entry.get("operator_independence_level") or "same_family"
    if independence not in INDEPENDENCE_LEVELS:
        independence = "same_family"
    return {
        "source_kind": "GENOME_TOURNAMENT",
        "source_id": f"{tournament['id']}:{entry_id}",
        "source_hashes": hashes,
        "frozen_before_reveal": True,
        "scope": {
            "domain": "discovery_genome_target",
            "quantifier": "frozen_target",
            "boundary": {"tournament_id": tournament["id"], "entry_id": entry_id, "target": target},
            "assumptions": ["tournament survival supports only the tested operator and target scope"],
        },
        "result": {"status": "COMPLETED", "verdict": entry["verdict"], "result": entry["result_json"]},
        "evaluator": {"id": "orbita-discovery-genome", "kind": "blind_tournament"},
        "independence": {
            "level": independence,
            "subject_id": str(entry.get("operator_id") or entry_id),
            "evaluator_id": "orbita-discovery-genome",
            "rationale": "Prediction and operator hashes were frozen in the tournament manifest before result recording.",
        },
        "provenance": {"tournament_id": tournament["id"], "entry_id": entry_id},
    }


def external_experiment_receipt_input(experiment: dict[str, Any]) -> dict[str, Any]:
    execution = experiment.get("execution")
    if not isinstance(execution, dict) or execution.get("status") != "succeeded":
        raise ValueError("only a succeeded external experiment execution can be normalized")
    if experiment.get("integrity_status") != "VERIFIED":
        raise ValueError("external experiment integrity must be verified")
    verification = experiment.get("verification")
    verified = isinstance(verification, dict)
    independence = "same_execution"
    evaluator_id = "orbita-external-experiment-runtime"
    verifier_hash = None
    if verified:
        verifier = verification.get("verifier", {})
        evaluator_id = str(verifier.get("id") or verifier.get("name") or "declared-independent-verifier")
        declared_level = experiment.get("independent_verifier", {}).get("independence_level")
        independence = declared_level if declared_level in INDEPENDENCE_LEVELS else "same_family"
        verifier_hash = verification.get("verifier_receipt_hash") or verification.get("verification_hash")
    claim_scope = experiment["claim_scope"]
    normalized_scope = {
        "domain": claim_scope.get("domain"),
        "quantifier": claim_scope.get("quantifier") or claim_scope.get("quantifiers"),
        "boundary": {
            key: value
            for key, value in claim_scope.items()
            if key not in {"domain", "quantifier", "quantifiers", "assumptions"}
        }
        or {"experiment_id": experiment["id"]},
        "assumptions": claim_scope.get("assumptions", []),
    }
    return {
        "source_kind": "EXTERNAL_EXPERIMENT",
        "source_id": experiment["id"],
        "source_hashes": {
            "experiment_hash": experiment["experiment_hash"],
            "manifest_hash": execution["manifest_hash"],
            "execution_receipt_hash": execution["receipt_hash"],
        },
        "frozen_before_reveal": True,
        "scope": normalized_scope,
        "result": {
            "status": "COMPLETED" if verified else "UNVERIFIED",
            "epistemic_status": experiment.get("epistemic_status"),
            "execution_classification": experiment.get("execution_classification"),
        },
        "evaluator": {
            "id": evaluator_id,
            "kind": "independent_verifier" if verified else "deterministic_runtime",
            "receipt_hash": verifier_hash,
        },
        "independence": {
            "level": independence,
            "subject_id": experiment["id"],
            "evaluator_id": evaluator_id,
            "rationale": (
                "A separately declared verifier recorded a conclusion."
                if verified
                else "Execution integrity is verified, but scientific verification has not occurred."
            ),
            "verifier_receipt_hash": verifier_hash,
        },
        "provenance": {
            "experiment_id": experiment["id"],
            "execution_run_id": execution.get("id") or execution.get("run_id"),
        },
    }
