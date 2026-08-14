from __future__ import annotations

import pytest

from orbita_agent.blind_calibration import REVEAL_APPROVAL_PHRASE
from orbita_agent.gateway import APPROVAL_PHRASE
from orbita_discovery.core import CandidateNotScorable


def _blind_tsv(rows: int = 10, *, include_gold: bool = False) -> str:
    header = ["blind_event_id", "shape", "motion", "sensor_count"]
    if include_gold:
        header.append("gold_label")
    lines = ["\t".join(header)]
    for index in range(1, rows + 1):
        values = [
            f"UAP-{index:03d}",
            "point" if index % 2 else "elongated",
            "steady" if index % 3 else "erratic",
            str(1 + index % 3),
        ]
        if include_gold:
            values.append("PROSAIC_LIKELY")
        lines.append("\t".join(values))
    return "\n".join(lines) + "\n"


def _plan(file_record: dict, *, expected_rows: int = 10) -> dict:
    return {
        "schema_version": "orbita-research-plan/0.1",
        "status": "ready_for_review",
        "selected_dataset": {
            "file_id": file_record["id"],
            "name": file_record["original_name"],
            "sha256": file_record["sha256"],
            "rows": expected_rows,
            "columns": 4,
        },
        "thresholds": {},
        "candidates": [
            {
                "id": "uap-blind-001",
                "statement": "Classify each sanitized event before the resolution key is revealed.",
                "kind": "prospective_blind_calibration",
                "blind_event_id_field": "blind_event_id",
                "visible_fields": ["blind_event_id", "shape", "motion", "sensor_count"],
                "scoring_key_fields": [
                    "gold_label",
                    "gold_hypotheses",
                    "resolution_notes",
                    "unresolved_holdout",
                ],
                "allowed_hypotheses": [
                    "ORDINARY_AIRCRAFT",
                    "SENSOR_ARTIFACT",
                    "INSUFFICIENT_DATA",
                ],
                "allowed_epistemic_labels": ["PROSAIC_LIKELY", "UNRESOLVED"],
                "allowed_evidence_classes": ["RADAR", "MULTI_SENSOR", "METADATA"],
                "forbidden_outputs": [
                    "NHI",
                    "EXOTIC",
                    "ANOMALOUS_PHYSICS",
                    "REPRODUCIBLE_RESIDUAL",
                ],
                "expected_row_count": expected_rows,
                "prediction_provider": {"kind": "external_submission"},
                "scoring_schema": {
                    "gold_event_id_field": "blind_event_id",
                    "gold_primary_label_field": "gold_label",
                    "gold_acceptable_hypotheses_field": "gold_hypotheses",
                    "hypothesis_delimiter": "|",
                },
            }
        ],
    }


def _prepare(gateway, *, content: str | None = None, expected_rows: int = 10):
    case = gateway.create_case(name="Prospective calibration", goal="Freeze before reveal")
    file_record = gateway.add_inline_file(
        case_id=case["id"],
        filename="blind-events.tsv",
        content=content or _blind_tsv(expected_rows),
    )
    plan = gateway.submit_plan(case["id"], plan=_plan(file_record, expected_rows=expected_rows))
    approved = gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="clean-room-owner",
        confirmation=APPROVAL_PHRASE,
    )
    protocol = gateway.run_discovery(case["id"], plan_id=approved["id"])
    return case, file_record, approved, protocol


def _predictions(rows: list[dict]) -> list[dict]:
    return [
        {
            "blind_event_id": row["blind_event_id"],
            "surviving_hypotheses": ["ORDINARY_AIRCRAFT", "SENSOR_ARTIFACT"],
            "primary_epistemic_label": "PROSAIC_LIKELY",
            "requested_evidence_classes": ["RADAR"],
            "confidence": 0.7,
            "justification": f"Visible shape={row['shape']} and motion={row['motion']} remain compatible with ordinary causes.",
        }
        for row in rows
    ]


def _gold(rows: int = 10) -> str:
    lines = ["blind_event_id,gold_label,gold_hypotheses"]
    for index in range(1, rows + 1):
        label = "PROSAIC_LIKELY" if index <= rows - 2 else "UNRESOLVED"
        hypothesis = "ORDINARY_AIRCRAFT|SENSOR_ARTIFACT" if index <= rows - 2 else "INSUFFICIENT_DATA"
        lines.append(f"UAP-{index:03d},{label},{hypothesis}")
    return "\n".join(lines) + "\n"


def test_ten_row_blind_workflow_freezes_before_key_and_scores_after_reveal(gateway):
    _, _, _, protocol = _prepare(gateway)
    assert protocol["status"] == "awaiting_predictions"
    assert protocol["protocol"]["row_count"] == 10
    assert protocol["scoring_key_available_to_prediction_surface"] is False
    assert protocol["sealed_scoring_key"] is None

    batch = gateway.get_blind_prediction_batch(protocol["id"])
    assert len(batch["rows"]) == 10
    assert batch["scoring_key_available"] is False
    assert all("gold_label" not in row for row in batch["rows"])
    assert all("unresolved_holdout" not in row for row in batch["rows"])

    with pytest.raises(ValueError, match="freeze"):
        gateway.seal_blind_scoring_key(
            protocol["id"],
            expected_protocol_hash=protocol["protocol_hash"],
            expected_prediction_freeze_hash="0" * 64,
            filename="gold.csv",
            content=_gold(),
            sealed_by="clean-room-owner",
        )

    frozen = gateway.freeze_blind_predictions(
        protocol["id"],
        expected_protocol_hash=protocol["protocol_hash"],
        predictions=_predictions(batch["rows"]),
        provider={
            "kind": "external_submission",
            "provider": "test-llm",
            "model": "fixture-model",
            "response_id": "response-001",
        },
    )
    freeze_hash = frozen["prediction_freeze"]["prediction_freeze_hash"]
    assert len(frozen["prediction_freeze"]["predictions"]) == 10
    assert frozen["status"] == "predictions_frozen_awaiting_scoring_key"
    with pytest.raises(ValueError, match="already frozen"):
        gateway.get_blind_prediction_batch(protocol["id"])

    sealed = gateway.seal_blind_scoring_key(
        protocol["id"],
        expected_protocol_hash=protocol["protocol_hash"],
        expected_prediction_freeze_hash=freeze_hash,
        filename="gold.csv",
        content=_gold(),
        sealed_by="independent-key-custodian",
    )
    key_hash = sealed["sealed_scoring_key"]["scoring_key_hash"]
    assert sealed["sealed_scoring_key"]["contents_exposed"] is False
    assert "rows" not in sealed["sealed_scoring_key"]
    with pytest.raises(ValueError, match="reveal approval"):
        gateway.score_blind_calibration(
            protocol["id"],
            expected_prediction_freeze_hash=freeze_hash,
            expected_scoring_key_hash=key_hash,
        )

    approved = gateway.approve_blind_reveal(
        protocol["id"],
        expected_protocol_hash=protocol["protocol_hash"],
        expected_prediction_freeze_hash=freeze_hash,
        expected_scoring_key_hash=key_hash,
        reviewer="clean-room-owner",
        rationale="Predictions are frozen and the independent scoring-key receipt matches.",
        confirmation=REVEAL_APPROVAL_PHRASE,
    )
    assert approved["status"] == "reveal_approved"
    scored = gateway.score_blind_calibration(
        protocol["id"],
        expected_prediction_freeze_hash=freeze_hash,
        expected_scoring_key_hash=key_hash,
    )
    assert scored["status"] == "scored"
    assert scored["score"]["row_count"] == 10
    assert scored["score"]["primary_accuracy"] == pytest.approx(0.8)
    assert scored["score"]["hypothesis_hit_rate"] == pytest.approx(0.8)
    assert scored["score"]["score_hash"]
    event_types = [event["event_type"] for event in scored["access_events"]]
    assert event_types.index("PREDICTIONS_FROZEN") < event_types.index(
        "SCORING_KEY_SEALED_AFTER_FREEZE"
    )
    assert event_types.index("SCORING_KEY_SEALED_AFTER_FREEZE") < event_types.index(
        "REVEAL_APPROVED"
    )


def test_blind_input_containing_declared_gold_fields_is_rejected(gateway):
    case = gateway.create_case(name="Leaky blind input", goal="Refuse leakage")
    file_record = gateway.add_inline_file(
        case_id=case["id"],
        filename="leaky.tsv",
        content=_blind_tsv(include_gold=True),
    )
    plan = gateway.submit_plan(case["id"], plan=_plan(file_record))
    approved = gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="clean-room-owner",
        confirmation=APPROVAL_PHRASE,
    )
    with pytest.raises(ValueError, match="not sanitized"):
        gateway.run_discovery(case["id"], plan_id=approved["id"])


def test_missing_row_materialization_fails_clearly(gateway):
    case = gateway.create_case(name="Missing materialization", goal="Refuse missing rows")
    file_record = gateway.add_inline_file(
        case_id=case["id"], filename="blind.tsv", content=_blind_tsv()
    )
    gateway.service.ledger.db.conn.execute(
        "UPDATE case_files SET extracted_path = NULL WHERE id = ?", (file_record["id"],)
    )
    gateway.service.ledger.db.conn.commit()
    plan = gateway.submit_plan(case["id"], plan=_plan(file_record))
    approved = gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="clean-room-owner",
        confirmation=APPROVAL_PHRASE,
    )
    with pytest.raises(ValueError, match="row-level materialization is unavailable"):
        gateway.run_discovery(case["id"], plan_id=approved["id"])


def test_forbidden_semantic_output_cannot_be_frozen(gateway):
    _, _, _, protocol = _prepare(gateway)
    batch = gateway.get_blind_prediction_batch(protocol["id"])
    predictions = _predictions(batch["rows"])
    predictions[0]["justification"] = "This requires an NHI explanation."
    with pytest.raises(ValueError, match="forbidden output"):
        gateway.freeze_blind_predictions(
            protocol["id"],
            expected_protocol_hash=protocol["protocol_hash"],
            predictions=predictions,
            provider={"kind": "external_submission", "model": "fixture-model"},
        )


def test_ordinary_discovery_still_rejects_unknown_candidate_kind_clearly(gateway, sample_csv):
    case = gateway.create_case(name="Unknown executor", goal="Fail clearly")
    file_record = gateway.add_inline_file(
        case_id=case["id"], filename="data.csv", content=sample_csv
    )
    plan_body = {
        "schema_version": "orbita-research-plan/0.1",
        "selected_dataset": {"file_id": file_record["id"]},
        "thresholds": {},
        "candidates": [
            {"id": "unknown-1", "statement": "Unsupported task", "kind": "unknown_executor"}
        ],
    }
    plan = gateway.submit_plan(case["id"], plan=plan_body)
    approved = gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="research-owner",
        confirmation=APPROVAL_PHRASE,
    )
    with pytest.raises(CandidateNotScorable, match="cannot fit a candidate"):
        gateway.run_discovery(case["id"], plan_id=approved["id"])
