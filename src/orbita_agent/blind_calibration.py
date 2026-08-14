"""Provider-neutral prospective blind calibration with prediction-before-reveal governance."""

from __future__ import annotations

import hashlib
import io
import json
import math
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

PREDICTION_KIND = "prospective_blind_calibration"
REVEAL_APPROVAL_PHRASE = "I authorize reveal for this exact frozen prediction set"

PREDICTION_SCHEMA = "orbita-blind-predictions/1"
PROTOCOL_SCHEMA = "orbita-blind-calibration/1"
SCORE_SCHEMA = "orbita-blind-score/1"

PREDICTION_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS blind_protocols (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    plan_id TEXT NOT NULL UNIQUE,
    plan_hash TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    blind_input_file_id TEXT NOT NULL,
    blind_input_hash TEXT NOT NULL,
    protocol_json TEXT NOT NULL,
    protocol_hash TEXT NOT NULL UNIQUE,
    visible_rows_json TEXT NOT NULL,
    visible_rows_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blind_prediction_freezes (
    id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL UNIQUE,
    protocol_hash TEXT NOT NULL,
    provider_json TEXT NOT NULL,
    predictions_json TEXT NOT NULL,
    prediction_freeze_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(protocol_id) REFERENCES blind_protocols(id)
);

CREATE TABLE IF NOT EXISTS blind_access_events (
    id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(protocol_id) REFERENCES blind_protocols(id)
);

CREATE TRIGGER IF NOT EXISTS blind_protocols_no_update
BEFORE UPDATE ON blind_protocols BEGIN SELECT RAISE(ABORT, 'blind protocols are immutable'); END;
CREATE TRIGGER IF NOT EXISTS blind_protocols_no_delete
BEFORE DELETE ON blind_protocols BEGIN SELECT RAISE(ABORT, 'blind protocols are append-only'); END;
CREATE TRIGGER IF NOT EXISTS blind_prediction_freezes_no_update
BEFORE UPDATE ON blind_prediction_freezes BEGIN SELECT RAISE(ABORT, 'blind prediction freezes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS blind_prediction_freezes_no_delete
BEFORE DELETE ON blind_prediction_freezes BEGIN SELECT RAISE(ABORT, 'blind prediction freezes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS blind_access_events_no_update
BEFORE UPDATE ON blind_access_events BEGIN SELECT RAISE(ABORT, 'blind access events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS blind_access_events_no_delete
BEFORE DELETE ON blind_access_events BEGIN SELECT RAISE(ABORT, 'blind access events are append-only'); END;
"""

SCORING_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS sealed_scoring_keys (
    id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL UNIQUE,
    protocol_hash TEXT NOT NULL,
    prediction_freeze_hash TEXT NOT NULL,
    scoring_key_json TEXT NOT NULL,
    scoring_key_hash TEXT NOT NULL UNIQUE,
    row_count INTEGER NOT NULL,
    sealed_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reveal_approvals (
    id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL UNIQUE,
    protocol_hash TEXT NOT NULL,
    prediction_freeze_hash TEXT NOT NULL,
    scoring_key_hash TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blind_score_receipts (
    id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL UNIQUE,
    prediction_freeze_hash TEXT NOT NULL,
    scoring_key_hash TEXT NOT NULL,
    score_json TEXT NOT NULL,
    score_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS sealed_scoring_keys_no_update
BEFORE UPDATE ON sealed_scoring_keys BEGIN SELECT RAISE(ABORT, 'sealed scoring keys are immutable'); END;
CREATE TRIGGER IF NOT EXISTS sealed_scoring_keys_no_delete
BEFORE DELETE ON sealed_scoring_keys BEGIN SELECT RAISE(ABORT, 'sealed scoring keys are append-only'); END;
CREATE TRIGGER IF NOT EXISTS reveal_approvals_no_update
BEFORE UPDATE ON reveal_approvals BEGIN SELECT RAISE(ABORT, 'reveal approvals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS reveal_approvals_no_delete
BEFORE DELETE ON reveal_approvals BEGIN SELECT RAISE(ABORT, 'reveal approvals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS blind_score_receipts_no_update
BEFORE UPDATE ON blind_score_receipts BEGIN SELECT RAISE(ABORT, 'blind score receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS blind_score_receipts_no_delete
BEFORE DELETE ON blind_score_receipts BEGIN SELECT RAISE(ABORT, 'blind score receipts are append-only'); END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _stable(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("blind-calibration artifacts must contain finite JSON values") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _text(name: str, value: Any, *, minimum: int = 1, maximum: int = 8_000) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{name} must contain between {minimum} and {maximum} characters")
    return value.strip()


def _string_list(
    name: str,
    value: Any,
    *,
    minimum: int = 1,
    maximum: int = 100,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{name} must contain between {minimum} and {maximum} items")
    result = [_text(name, item, maximum=500) for item in value]
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _candidate_value(candidate: dict[str, Any], plan: dict[str, Any], *names: str) -> Any:
    calibration = plan.get("blind_calibration", {})
    for name in names:
        if name in candidate:
            return candidate[name]
        if isinstance(calibration, dict) and name in calibration:
            return calibration[name]
    return None


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _read_table(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.is_file():
        raise ValueError("row-level materialization is unavailable for the selected blind input")
    suffix = path.suffix.casefold()
    try:
        if suffix in {".tsv", ".tab"}:
            frame = pd.read_csv(path, sep="\t")
        elif suffix in {".json", ".jsonl"}:
            frame = pd.read_json(path, lines=suffix == ".jsonl")
        else:
            frame = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001 - converted into a stable public boundary
        raise ValueError("row-level materialization failed for the selected blind input") from exc
    if frame.empty:
        raise ValueError("row-level materialization produced no blind input rows")
    return frame


def _read_inline_scoring_key(filename: str, content: str) -> pd.DataFrame:
    name = _text("filename", filename, maximum=160)
    raw = _text("scoring key content", content, maximum=8_000_000)
    suffix = Path(name).suffix.casefold()
    try:
        if suffix in {".tsv", ".tab"}:
            frame = pd.read_csv(io.StringIO(raw), sep="\t")
        elif suffix == ".jsonl":
            frame = pd.read_json(io.StringIO(raw), lines=True)
        elif suffix == ".json":
            frame = pd.read_json(io.StringIO(raw))
        else:
            frame = pd.read_csv(io.StringIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("sealed scoring key could not be parsed as CSV, TSV, JSON, or JSONL") from exc
    if frame.empty:
        raise ValueError("sealed scoring key contains no rows")
    return frame


class BlindCalibrationService:
    """Two-store workflow whose prediction surface has no scoring-key read method."""

    def __init__(
        self,
        prediction_db: str | Path,
        scoring_db: str | Path,
        research_service: Any,
    ):
        prediction_path = Path(prediction_db)
        scoring_path = Path(scoring_db)
        prediction_path.parent.mkdir(parents=True, exist_ok=True)
        scoring_path.parent.mkdir(parents=True, exist_ok=True)
        self.prediction_conn = sqlite3.connect(prediction_path, check_same_thread=False)
        self.prediction_conn.row_factory = sqlite3.Row
        self.scoring_conn = sqlite3.connect(scoring_path, check_same_thread=False)
        self.scoring_conn.row_factory = sqlite3.Row
        self.prediction_conn.executescript(PREDICTION_DB_SCHEMA)
        self.scoring_conn.executescript(SCORING_DB_SCHEMA)
        self.prediction_conn.commit()
        self.scoring_conn.commit()
        self.research_service = research_service
        self._lock = threading.RLock()

    def close(self) -> None:
        self.prediction_conn.close()
        self.scoring_conn.close()

    def status(self) -> dict[str, Any]:
        protocols = self.prediction_conn.execute("SELECT COUNT(*) AS n FROM blind_protocols").fetchone()["n"]
        freezes = self.prediction_conn.execute(
            "SELECT COUNT(*) AS n FROM blind_prediction_freezes"
        ).fetchone()["n"]
        scores = self.scoring_conn.execute("SELECT COUNT(*) AS n FROM blind_score_receipts").fetchone()["n"]
        return {
            "schema_version": PROTOCOL_SCHEMA,
            "candidate_kind": PREDICTION_KIND,
            "protocols": protocols,
            "prediction_freezes": freezes,
            "score_receipts": scores,
            "reveal_approval_phrase": REVEAL_APPROVAL_PHRASE,
            "boundary": (
                "Prediction batches expose sanitized visible rows only. A scoring key cannot be sealed until "
                "predictions are frozen, and scoring requires separate exact-hash reveal approval."
            ),
        }

    def prepare_from_approved_plan(self, case_id: str, plan_id: str) -> dict[str, Any]:
        case = self.research_service.store.get_case(case_id)
        plan_record = self.research_service.store.get_plan(plan_id)
        if plan_record["case_id"] != case["id"]:
            raise ValueError("The plan does not belong to the declared case")
        if plan_record["status"] != "approved":
            raise ValueError("The exact blind-calibration plan must be approved before preparation")
        existing = self.prediction_conn.execute(
            "SELECT id FROM blind_protocols WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if existing is not None:
            return self.get(existing["id"])
        plan = plan_record["plan"]
        candidates = plan.get("candidates", [])
        blind_candidates = [item for item in candidates if item.get("kind") == PREDICTION_KIND]
        if len(blind_candidates) != 1 or len(candidates) != 1:
            raise ValueError(
                "a prospective blind calibration plan must contain exactly one candidate and no mixed analysis kinds"
            )
        candidate = blind_candidates[0]
        selected = plan.get("selected_dataset", {})
        file_id = str(
            _candidate_value(candidate, plan, "blind_input_file_id")
            or selected.get("file_id")
            or ""
        )
        file_record = self.research_service.store.get_file(file_id)
        if file_record["case_id"] != case_id:
            raise ValueError("blind_input_file_id does not belong to this case")
        extracted = file_record.get("extracted_path")
        if not extracted:
            raise ValueError("row-level materialization is unavailable for the selected blind input")
        frame = _read_table(Path(extracted))

        event_id_field = str(
            _candidate_value(candidate, plan, "blind_event_id_field") or "blind_event_id"
        )
        scoring_key_fields = _string_list(
            "scoring_key_fields",
            _candidate_value(
                candidate,
                plan,
                "scoring_key_fields",
                "hidden_fields",
                "sealed_fields",
            ),
        )
        if any(field in frame.columns for field in scoring_key_fields):
            present = sorted(field for field in scoring_key_fields if field in frame.columns)
            raise ValueError(
                "blind input contains declared scoring-key fields and is not sanitized: "
                + ", ".join(present)
            )
        if event_id_field not in frame.columns:
            raise ValueError(f"blind input is missing event identifier field {event_id_field!r}")
        event_ids = [str(value).strip() for value in frame[event_id_field].tolist()]
        if any(not value for value in event_ids) or len(set(event_ids)) != len(event_ids):
            raise ValueError("blind event identifiers must be nonempty and unique")

        visible_fields_raw = _candidate_value(candidate, plan, "visible_fields")
        if visible_fields_raw is None:
            visible_fields = [str(column) for column in frame.columns]
        else:
            visible_fields = _string_list("visible_fields", visible_fields_raw)
        if event_id_field not in visible_fields:
            visible_fields = [event_id_field, *visible_fields]
        missing_visible = sorted(set(visible_fields) - set(frame.columns))
        if missing_visible:
            raise ValueError("visible_fields are absent from blind input: " + ", ".join(missing_visible))
        forbidden_overlap = sorted(set(visible_fields) & set(scoring_key_fields))
        if forbidden_overlap:
            raise ValueError("visible_fields overlap scoring_key_fields: " + ", ".join(forbidden_overlap))

        allowed_hypotheses = _string_list(
            "allowed_hypotheses",
            _candidate_value(candidate, plan, "allowed_hypotheses", "hypothesis_vocabulary"),
        )
        allowed_labels = _string_list(
            "allowed_epistemic_labels",
            _candidate_value(
                candidate,
                plan,
                "allowed_epistemic_labels",
                "epistemic_label_vocabulary",
            ),
        )
        allowed_evidence = _string_list(
            "allowed_evidence_classes",
            _candidate_value(
                candidate,
                plan,
                "allowed_evidence_classes",
                "evidence_class_vocabulary",
            ),
            minimum=0,
        )
        forbidden_outputs_raw = _candidate_value(candidate, plan, "forbidden_outputs") or []
        forbidden_outputs = _string_list(
            "forbidden_outputs", forbidden_outputs_raw, minimum=0
        )
        scoring_schema = _candidate_value(candidate, plan, "scoring_schema")
        if not isinstance(scoring_schema, dict) or not scoring_schema:
            raise ValueError("prospective blind calibration requires a nonempty scoring_schema")
        gold_id_field = str(scoring_schema.get("gold_event_id_field") or event_id_field)
        gold_primary_field = _text(
            "scoring_schema.gold_primary_label_field",
            scoring_schema.get("gold_primary_label_field"),
            maximum=160,
        )
        gold_hypotheses_field = scoring_schema.get("gold_acceptable_hypotheses_field")
        if gold_hypotheses_field is not None:
            gold_hypotheses_field = _text(
                "scoring_schema.gold_acceptable_hypotheses_field",
                gold_hypotheses_field,
                maximum=160,
            )
        expected_rows = _candidate_value(candidate, plan, "expected_row_count")
        if expected_rows is not None and int(expected_rows) != len(frame):
            raise ValueError(
                f"blind input contains {len(frame)} rows but the frozen plan requires {int(expected_rows)}"
            )
        visible_rows = [
            {field: _json_value(row[field]) for field in visible_fields}
            for _, row in frame[visible_fields].iterrows()
        ]
        provider_policy = _candidate_value(candidate, plan, "prediction_provider") or {
            "kind": "external_submission"
        }
        if not isinstance(provider_policy, dict) or provider_policy.get("kind") not in {
            "external_submission",
            "deterministic_rules",
        }:
            raise ValueError(
                "prediction_provider.kind must be external_submission or deterministic_rules"
            )
        protocol = {
            "schema_version": PROTOCOL_SCHEMA,
            "case_id": case_id,
            "plan_id": plan_id,
            "plan_hash": plan_record["plan_hash"],
            "candidate_id": candidate["id"],
            "candidate_statement": candidate["statement"],
            "blind_input_file_id": file_id,
            "blind_input_hash": file_record["sha256"],
            "event_id_field": event_id_field,
            "visible_fields": visible_fields,
            "scoring_key_fields": scoring_key_fields,
            "row_count": len(visible_rows),
            "event_ids": event_ids,
            "allowed_hypotheses": allowed_hypotheses,
            "allowed_epistemic_labels": allowed_labels,
            "allowed_evidence_classes": allowed_evidence,
            "forbidden_outputs": forbidden_outputs,
            "prediction_provider": provider_policy,
            "scoring_schema": {
                **scoring_schema,
                "gold_event_id_field": gold_id_field,
                "gold_primary_label_field": gold_primary_field,
                "gold_acceptable_hypotheses_field": gold_hypotheses_field,
            },
            "anti_leakage": {
                "scoring_key_not_present_during_prediction": True,
                "blind_input_has_no_declared_scoring_fields": True,
                "prediction_freeze_precedes_key_sealing": True,
            },
        }
        protocol_hash = _hash(protocol)
        rows_hash = _hash(visible_rows)
        protocol_id = _id("blind_protocol")
        with self._lock:
            self.prediction_conn.execute(
                """INSERT INTO blind_protocols
                   (id, case_id, plan_id, plan_hash, candidate_id, blind_input_file_id,
                    blind_input_hash, protocol_json, protocol_hash, visible_rows_json,
                    visible_rows_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    protocol_id,
                    case_id,
                    plan_id,
                    plan_record["plan_hash"],
                    candidate["id"],
                    file_id,
                    file_record["sha256"],
                    _stable(protocol),
                    protocol_hash,
                    _stable(visible_rows),
                    rows_hash,
                    _now(),
                ),
            )
            self._event(protocol_id, "PROTOCOL_FROZEN", {"protocol_hash": protocol_hash})
            self.prediction_conn.commit()
        return self.get(protocol_id)

    def prediction_batch(self, protocol_id: str) -> dict[str, Any]:
        protocol = self._protocol(protocol_id)
        frozen = self._freeze(protocol_id)
        if frozen is not None:
            raise ValueError("predictions are already frozen; the blind batch is closed")
        self._event(
            protocol_id,
            "BLIND_ROWS_ACCESSED",
            {"visible_rows_hash": protocol["visible_rows_hash"], "row_count": len(protocol["visible_rows"])},
        )
        self.prediction_conn.commit()
        return {
            "protocol_id": protocol_id,
            "protocol_hash": protocol["protocol_hash"],
            "candidate_statement": protocol["protocol"]["candidate_statement"],
            "event_id_field": protocol["protocol"]["event_id_field"],
            "visible_fields": protocol["protocol"]["visible_fields"],
            "rows": protocol["visible_rows"],
            "visible_rows_hash": protocol["visible_rows_hash"],
            "prediction_schema": {
                "required": [
                    "blind_event_id",
                    "surviving_hypotheses",
                    "primary_epistemic_label",
                    "requested_evidence_classes",
                    "confidence",
                    "justification",
                ],
                "hypotheses": protocol["protocol"]["allowed_hypotheses"],
                "epistemic_labels": protocol["protocol"]["allowed_epistemic_labels"],
                "evidence_classes": protocol["protocol"]["allowed_evidence_classes"],
                "forbidden_outputs": protocol["protocol"]["forbidden_outputs"],
            },
            "scoring_key_available": False,
        }

    def freeze_predictions(
        self,
        protocol_id: str,
        *,
        expected_protocol_hash: str,
        predictions: list[dict[str, Any]],
        provider: dict[str, Any],
    ) -> dict[str, Any]:
        protocol = self._protocol(protocol_id)
        if protocol["protocol_hash"] != expected_protocol_hash:
            raise ValueError("Protocol hash mismatch; fetch and review the blind protocol again")
        if self._freeze(protocol_id) is not None:
            raise ValueError("predictions are immutable and already frozen for this protocol")
        if not isinstance(provider, dict) or not provider:
            raise ValueError("provider must identify the model, ruleset, or human source")
        allowed_provider_kind = protocol["protocol"]["prediction_provider"]["kind"]
        if provider.get("kind") != allowed_provider_kind:
            raise ValueError(
                f"provider.kind must match the frozen prediction provider {allowed_provider_kind!r}"
            )
        _stable(provider)
        if not isinstance(predictions, list) or len(predictions) != protocol["protocol"]["row_count"]:
            raise ValueError(
                f"prediction payload must contain exactly {protocol['protocol']['row_count']} rows"
            )
        allowed_hypotheses = set(protocol["protocol"]["allowed_hypotheses"])
        allowed_labels = set(protocol["protocol"]["allowed_epistemic_labels"])
        allowed_evidence = set(protocol["protocol"]["allowed_evidence_classes"])
        forbidden = [item.casefold() for item in protocol["protocol"]["forbidden_outputs"]]
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        visible_by_id = {
            str(row[protocol["protocol"]["event_id_field"]]): row
            for row in protocol["visible_rows"]
        }
        for raw in predictions:
            if not isinstance(raw, dict):
                raise ValueError("every prediction must be an object")
            event_id = _text("blind_event_id", raw.get("blind_event_id"), maximum=500)
            if event_id in seen:
                raise ValueError("each blind_event_id must appear exactly once")
            if event_id not in visible_by_id:
                raise ValueError(f"prediction references unknown blind_event_id {event_id!r}")
            seen.add(event_id)
            hypotheses = _string_list(
                "surviving_hypotheses", raw.get("surviving_hypotheses"), minimum=1, maximum=3
            )
            if not set(hypotheses) <= allowed_hypotheses:
                raise ValueError("surviving_hypotheses contains a value outside the frozen vocabulary")
            label = _text(
                "primary_epistemic_label", raw.get("primary_epistemic_label"), maximum=500
            )
            if label not in allowed_labels:
                raise ValueError("primary_epistemic_label is outside the frozen vocabulary")
            evidence_classes = _string_list(
                "requested_evidence_classes",
                raw.get("requested_evidence_classes", []),
                minimum=0,
                maximum=3,
            )
            if not set(evidence_classes) <= allowed_evidence:
                raise ValueError("requested_evidence_classes contains a value outside the frozen vocabulary")
            confidence = raw.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("confidence must be a number from 0 to 1")
            confidence = float(confidence)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("confidence must be a finite number from 0 to 1")
            justification = _text("justification", raw.get("justification"), minimum=8)
            normalized_item = {
                "blind_event_id": event_id,
                "surviving_hypotheses": hypotheses,
                "primary_epistemic_label": label,
                "requested_evidence_classes": evidence_classes,
                "confidence": confidence,
                "justification": justification,
                "visible_row_hash": _hash(visible_by_id[event_id]),
            }
            serialized = _stable(normalized_item).casefold()
            matched = [token for token in forbidden if token and token in serialized]
            if matched:
                raise ValueError("prediction contains a forbidden output from the frozen protocol")
            normalized.append(normalized_item)
        expected_ids = set(protocol["protocol"]["event_ids"])
        if seen != expected_ids:
            raise ValueError("predictions must cover every frozen blind event exactly once")
        normalized.sort(key=lambda item: protocol["protocol"]["event_ids"].index(item["blind_event_id"]))
        body = {
            "schema_version": PREDICTION_SCHEMA,
            "protocol_id": protocol_id,
            "protocol_hash": protocol["protocol_hash"],
            "visible_rows_hash": protocol["visible_rows_hash"],
            "provider": provider,
            "predictions": normalized,
        }
        freeze_hash = _hash(body)
        freeze_id = _id("prediction_freeze")
        with self._lock:
            self.prediction_conn.execute(
                """INSERT INTO blind_prediction_freezes
                   (id, protocol_id, protocol_hash, provider_json, predictions_json,
                    prediction_freeze_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    freeze_id,
                    protocol_id,
                    protocol["protocol_hash"],
                    _stable(provider),
                    _stable(normalized),
                    freeze_hash,
                    _now(),
                ),
            )
            self._event(
                protocol_id,
                "PREDICTIONS_FROZEN",
                {"prediction_freeze_hash": freeze_hash, "row_count": len(normalized)},
            )
            self.prediction_conn.commit()
        return self.get(protocol_id)

    def seal_scoring_key(
        self,
        protocol_id: str,
        *,
        expected_protocol_hash: str,
        expected_prediction_freeze_hash: str,
        filename: str,
        content: str,
        sealed_by: str,
    ) -> dict[str, Any]:
        protocol = self._protocol(protocol_id)
        frozen = self._require_freeze(protocol_id, expected_prediction_freeze_hash)
        if protocol["protocol_hash"] != expected_protocol_hash:
            raise ValueError("Protocol hash mismatch; fetch and review the blind protocol again")
        if self.scoring_conn.execute(
            "SELECT 1 FROM sealed_scoring_keys WHERE protocol_id = ?", (protocol_id,)
        ).fetchone():
            raise ValueError("the scoring key is immutable and already sealed")
        frame = _read_inline_scoring_key(filename, content)
        schema = protocol["protocol"]["scoring_schema"]
        required = [schema["gold_event_id_field"], schema["gold_primary_label_field"]]
        if schema.get("gold_acceptable_hypotheses_field"):
            required.append(schema["gold_acceptable_hypotheses_field"])
        missing = sorted(set(required) - set(frame.columns))
        if missing:
            raise ValueError("scoring key is missing required fields: " + ", ".join(missing))
        rows = [
            {column: _json_value(row[column]) for column in required}
            for _, row in frame[required].iterrows()
        ]
        ids = [str(row[schema["gold_event_id_field"]]).strip() for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError("scoring-key event identifiers must be unique")
        if set(ids) != set(protocol["protocol"]["event_ids"]):
            raise ValueError("scoring key must cover exactly the frozen blind event IDs")
        allowed_labels = set(protocol["protocol"]["allowed_epistemic_labels"])
        for row in rows:
            if str(row[schema["gold_primary_label_field"]]) not in allowed_labels:
                raise ValueError("scoring key contains a primary label outside the frozen vocabulary")
        body = {
            "schema_version": "orbita-sealed-scoring-key/1",
            "protocol_id": protocol_id,
            "protocol_hash": protocol["protocol_hash"],
            "prediction_freeze_hash": frozen["prediction_freeze_hash"],
            "rows": rows,
        }
        key_hash = _hash(body)
        with self._lock:
            self.scoring_conn.execute(
                """INSERT INTO sealed_scoring_keys
                   (id, protocol_id, protocol_hash, prediction_freeze_hash,
                    scoring_key_json, scoring_key_hash, row_count, sealed_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _id("sealed_key"),
                    protocol_id,
                    protocol["protocol_hash"],
                    frozen["prediction_freeze_hash"],
                    _stable(rows),
                    key_hash,
                    len(rows),
                    _text("sealed_by", sealed_by, maximum=160),
                    _now(),
                ),
            )
            self.scoring_conn.commit()
        self._event(
            protocol_id,
            "SCORING_KEY_SEALED_AFTER_FREEZE",
            {"scoring_key_hash": key_hash, "prediction_freeze_hash": frozen["prediction_freeze_hash"]},
        )
        self.prediction_conn.commit()
        return self.get(protocol_id)

    def approve_reveal(
        self,
        protocol_id: str,
        *,
        expected_protocol_hash: str,
        expected_prediction_freeze_hash: str,
        expected_scoring_key_hash: str,
        reviewer: str,
        rationale: str,
        confirmation: str,
    ) -> dict[str, Any]:
        protocol = self._protocol(protocol_id)
        self._require_freeze(protocol_id, expected_prediction_freeze_hash)
        key = self._sealed_key(protocol_id)
        if protocol["protocol_hash"] != expected_protocol_hash:
            raise ValueError("Protocol hash mismatch; fetch and review the blind protocol again")
        if key is None or key["scoring_key_hash"] != expected_scoring_key_hash:
            raise ValueError("Scoring key hash mismatch; fetch and review the sealed-key receipt again")
        if confirmation != REVEAL_APPROVAL_PHRASE:
            raise ValueError(f"confirmation must exactly equal: {REVEAL_APPROVAL_PHRASE}")
        with self._lock:
            try:
                self.scoring_conn.execute(
                    """INSERT INTO reveal_approvals
                       (id, protocol_id, protocol_hash, prediction_freeze_hash,
                        scoring_key_hash, reviewer, rationale, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _id("reveal_approval"),
                        protocol_id,
                        protocol["protocol_hash"],
                        expected_prediction_freeze_hash,
                        expected_scoring_key_hash,
                        _text("reviewer", reviewer, maximum=160),
                        _text("rationale", rationale, minimum=12),
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("reveal is already approved for this protocol") from exc
            self.scoring_conn.commit()
        self._event(
            protocol_id,
            "REVEAL_APPROVED",
            {
                "prediction_freeze_hash": expected_prediction_freeze_hash,
                "scoring_key_hash": expected_scoring_key_hash,
            },
        )
        self.prediction_conn.commit()
        return self.get(protocol_id)

    def score(
        self,
        protocol_id: str,
        *,
        expected_prediction_freeze_hash: str,
        expected_scoring_key_hash: str,
    ) -> dict[str, Any]:
        protocol = self._protocol(protocol_id)
        frozen = self._require_freeze(protocol_id, expected_prediction_freeze_hash)
        key = self._sealed_key(protocol_id)
        if key is None or key["scoring_key_hash"] != expected_scoring_key_hash:
            raise ValueError("Scoring key hash mismatch; fetch and review the sealed-key receipt again")
        approval = self.scoring_conn.execute(
            "SELECT * FROM reveal_approvals WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        if approval is None:
            raise ValueError("scoring is unavailable until exact-hash reveal approval is recorded")
        existing = self.scoring_conn.execute(
            "SELECT * FROM blind_score_receipts WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        if existing is not None:
            return self.get(protocol_id)
        schema = protocol["protocol"]["scoring_schema"]
        id_field = schema["gold_event_id_field"]
        label_field = schema["gold_primary_label_field"]
        hypotheses_field = schema.get("gold_acceptable_hypotheses_field")
        delimiter = str(schema.get("hypothesis_delimiter") or "|")
        gold_by_id = {str(row[id_field]): row for row in key["rows"]}
        row_results = []
        for prediction in frozen["predictions"]:
            event_id = prediction["blind_event_id"]
            gold = gold_by_id[event_id]
            gold_label = str(gold[label_field])
            primary_correct = prediction["primary_epistemic_label"] == gold_label
            acceptable: list[str] = []
            if hypotheses_field:
                raw = gold.get(hypotheses_field)
                if isinstance(raw, list):
                    acceptable = [str(value) for value in raw]
                elif raw is not None:
                    acceptable = [item.strip() for item in str(raw).split(delimiter) if item.strip()]
            hypothesis_hit = (
                bool(set(prediction["surviving_hypotheses"]) & set(acceptable))
                if hypotheses_field
                else None
            )
            row_results.append(
                {
                    "blind_event_id": event_id,
                    "predicted_primary_epistemic_label": prediction["primary_epistemic_label"],
                    "gold_primary_epistemic_label": gold_label,
                    "primary_correct": primary_correct,
                    "confidence": prediction["confidence"],
                    "brier": (prediction["confidence"] - float(primary_correct)) ** 2,
                    "acceptable_hypotheses": acceptable if hypotheses_field else None,
                    "hypothesis_hit": hypothesis_hit,
                }
            )
        n = len(row_results)
        hypothesis_rows = [row for row in row_results if row["hypothesis_hit"] is not None]
        score = {
            "schema_version": SCORE_SCHEMA,
            "protocol_id": protocol_id,
            "protocol_hash": protocol["protocol_hash"],
            "prediction_freeze_hash": frozen["prediction_freeze_hash"],
            "scoring_key_hash": key["scoring_key_hash"],
            "row_count": n,
            "primary_accuracy": sum(row["primary_correct"] for row in row_results) / n,
            "mean_brier": sum(row["brier"] for row in row_results) / n,
            "hypothesis_hit_rate": (
                sum(row["hypothesis_hit"] for row in hypothesis_rows) / len(hypothesis_rows)
                if hypothesis_rows
                else None
            ),
            "rows": row_results,
        }
        score_hash = _hash(score)
        with self._lock:
            self.scoring_conn.execute(
                """INSERT INTO blind_score_receipts
                   (id, protocol_id, prediction_freeze_hash, scoring_key_hash,
                    score_json, score_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    _id("blind_score"),
                    protocol_id,
                    frozen["prediction_freeze_hash"],
                    key["scoring_key_hash"],
                    _stable(score),
                    score_hash,
                    _now(),
                ),
            )
            self.scoring_conn.commit()
        self._event(protocol_id, "SCORING_COMPLETED", {"score_hash": score_hash})
        self.prediction_conn.commit()
        return self.get(protocol_id)

    def get(self, protocol_id: str) -> dict[str, Any]:
        protocol = self._protocol(protocol_id)
        frozen = self._freeze(protocol_id)
        key = self._sealed_key(protocol_id)
        approval = self.scoring_conn.execute(
            "SELECT * FROM reveal_approvals WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        score_row = self.scoring_conn.execute(
            "SELECT * FROM blind_score_receipts WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        score = None
        if score_row is not None:
            score = json.loads(score_row["score_json"])
            score["score_hash"] = score_row["score_hash"]
        events = self.prediction_conn.execute(
            "SELECT * FROM blind_access_events WHERE protocol_id = ? ORDER BY created_at, id",
            (protocol_id,),
        ).fetchall()
        if score is not None:
            status = "scored"
        elif approval is not None:
            status = "reveal_approved"
        elif key is not None:
            status = "awaiting_reveal_approval"
        elif frozen is not None:
            status = "predictions_frozen_awaiting_scoring_key"
        else:
            status = "awaiting_predictions"
        return {
            "id": protocol_id,
            "case_id": protocol["case_id"],
            "plan_id": protocol["plan_id"],
            "status": status,
            "protocol": protocol["protocol"],
            "protocol_hash": protocol["protocol_hash"],
            "visible_rows_hash": protocol["visible_rows_hash"],
            "prediction_freeze": frozen,
            "sealed_scoring_key": (
                {
                    "scoring_key_hash": key["scoring_key_hash"],
                    "prediction_freeze_hash": key["prediction_freeze_hash"],
                    "row_count": key["row_count"],
                    "sealed_by": key["sealed_by"],
                    "created_at": key["created_at"],
                    "contents_exposed": False,
                }
                if key is not None
                else None
            ),
            "reveal_approval": (
                {
                    "reviewer": approval["reviewer"],
                    "rationale": approval["rationale"],
                    "created_at": approval["created_at"],
                }
                if approval is not None
                else None
            ),
            "score": score,
            "access_events": [
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "detail": json.loads(row["detail_json"]),
                    "created_at": row["created_at"],
                }
                for row in events
            ],
            "scoring_key_available_to_prediction_surface": False,
        }

    def _protocol(self, protocol_id: str) -> dict[str, Any]:
        row = self.prediction_conn.execute(
            "SELECT * FROM blind_protocols WHERE id = ?", (protocol_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown blind calibration protocol: {protocol_id}")
        item = dict(row)
        item["protocol"] = json.loads(item.pop("protocol_json"))
        item["visible_rows"] = json.loads(item.pop("visible_rows_json"))
        return item

    def _freeze(self, protocol_id: str) -> dict[str, Any] | None:
        row = self.prediction_conn.execute(
            "SELECT * FROM blind_prediction_freezes WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["provider"] = json.loads(item.pop("provider_json"))
        item["predictions"] = json.loads(item.pop("predictions_json"))
        return item

    def _require_freeze(self, protocol_id: str, expected_hash: str) -> dict[str, Any]:
        frozen = self._freeze(protocol_id)
        if frozen is None or frozen["prediction_freeze_hash"] != expected_hash:
            raise ValueError("Prediction freeze hash mismatch; freeze and review predictions first")
        return frozen

    def _sealed_key(self, protocol_id: str) -> dict[str, Any] | None:
        row = self.scoring_conn.execute(
            "SELECT * FROM sealed_scoring_keys WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["rows"] = json.loads(item.pop("scoring_key_json"))
        return item

    def _event(self, protocol_id: str, event_type: str, detail: dict[str, Any]) -> None:
        self.prediction_conn.execute(
            """INSERT INTO blind_access_events
               (id, protocol_id, event_type, detail_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (_id("blind_event"), protocol_id, event_type, _stable(detail), _now()),
        )
