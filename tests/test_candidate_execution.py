from __future__ import annotations

import sqlite3

import pytest

from orbita_agent.gateway import APPROVAL_PHRASE
from orbita_mvp.execution_dispatch import ExecutionCapabilityLimit, content_hash


def _execution_spec() -> dict:
    return {
        "name": "Frozen research operator fixture",
        "image": "python@sha256:" + "a" * 64,
        "command": ["python", "/code/check.py"],
        "code_files": [
            {
                "target": "check.py",
                "text": "import json\njson.dump({'survived': True}, open('/output/verdict.json','w'))\n",
                "media_type": "text/x-python",
            }
        ],
        "outputs": [
            {
                "path": "verdict.json",
                "media_type": "application/json",
                "json_schema": {
                    "type": "object",
                    "required": ["survived"],
                    "properties": {"survived": {"type": "boolean"}},
                    "additionalProperties": False,
                },
            }
        ],
        "network": False,
    }


def _research_plan(file_record: dict, *, include_contract: bool = True) -> dict:
    candidate = {
        "id": "operator-1",
        "statement": "Test one exact structured research operator without tabular reinterpretation.",
        "kind": "research_operator",
    }
    if include_contract:
        candidate["execution_contract"] = {
            "scientific_question": "Does this frozen operator survive its explicitly bounded executable challenge?",
            "claim_scope": {
                "domain": "fixture rows",
                "quantifiers": "the selected frozen fixture only",
                "finite_or_infinite": "finite",
            },
            "execution_spec": _execution_spec(),
            "verdict_schema": {"support": "survived=true", "refute": "survived=false"},
            "independent_verifier": {"required": True, "identity": "separate-fixture-verifier"},
            "falsification_coverage": {
                "tested_domains": ["fixture rows"],
                "known_uncovered_regions": ["all external domains"],
            },
            "anti_rescue_rules": ["Do not reinterpret the operator as a correlation."],
            "network_policy": "disabled",
        }
    return {
        "schema_version": "orbita-research-plan/0.1",
        "selected_dataset": {"file_id": file_record["id"]},
        "thresholds": {},
        "candidates": [candidate],
    }


def test_compiled_statistical_plan_freezes_exact_executor_contract(gateway, sample_csv):
    case = gateway.create_case(name="Bound table plan", goal="Find bounded associations")
    gateway.add_inline_file(case_id=case["id"], filename="data.csv", content=sample_csv)
    plan = gateway.compile_plan(case["id"])
    binding = plan["plan"]["execution_binding"]

    assert binding["executor_id"] == "tabular-statistical/1"
    assert binding["candidate_kinds"]
    assert binding["binding_hash"] == content_hash({key: value for key, value in binding.items() if key != "binding_hash"})
    registry = gateway.executor_registry_status()
    exact = next(item for item in registry["contracts"] if item["executor_id"] == binding["executor_id"])
    assert exact["executor_contract_hash"] == binding["executor_contract_hash"]
    assert registry["coercion_enabled"] is False


def test_research_operator_without_executable_realization_fails_before_approval(gateway, sample_csv, monkeypatch):
    case = gateway.create_case(name="Ungrounded operator", goal="Do not fake a table score")
    file_record = gateway.add_inline_file(case_id=case["id"], filename="data.csv", content=sample_csv)
    table_called = False

    def forbidden_table(*_args, **_kwargs):
        nonlocal table_called
        table_called = True
        raise AssertionError("research_operator must never reach the legacy table runner")

    monkeypatch.setattr(gateway.service, "run_case", forbidden_table)
    with pytest.raises(ExecutionCapabilityLimit, match="ENGINE_CAPABILITY_LIMIT") as failure:
        gateway.submit_plan(case["id"], plan=_research_plan(file_record, include_contract=False))
    assert failure.value.required_executor == "structured-research-operator/1"
    assert failure.value.available_executor is None
    assert table_called is False
    assert gateway.service.store.get_case(case["id"])["plans"] == []


def test_research_operator_routes_to_governed_external_preparation_and_receipt(gateway, sample_csv, monkeypatch):
    case = gateway.create_case(name="Executable operator", goal="Freeze the correct execution route")
    file_record = gateway.add_inline_file(case_id=case["id"], filename="data.csv", content=sample_csv)
    plan = gateway.submit_plan(case["id"], plan=_research_plan(file_record))
    assert plan["plan"]["execution_binding"]["executor_id"] == "structured-research-operator/1"

    def forbidden_table(*_args, **_kwargs):
        raise AssertionError("research_operator must never reach the legacy table runner")

    monkeypatch.setattr(gateway.service, "run_case", forbidden_table)
    approved = gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="research-owner",
        confirmation=APPROVAL_PHRASE,
    )
    result = gateway.run_discovery(case["id"], plan_id=approved["id"])

    assert result["status"] == "frozen_external_experiment"
    assert result["experiment"]["execution"] is None
    assert result["scientific_claim_committed"] is False
    assert result["execution_dispatch"]["executor_id"] == "structured-research-operator/1"
    assert result["execution_dispatch"]["outcome"] == "prepared"
    assert gateway.list_candidate_execution_receipts()[0]["receipt_hash"] == result["execution_dispatch"]["receipt_hash"]
    assert gateway.verify_candidate_execution_receipt(result["execution_dispatch"]["id"])["valid"] is True

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        gateway.candidate_executions.conn.execute(
            "UPDATE candidate_execution_receipts SET outcome = 'completed' WHERE id = ?",
            (result["execution_dispatch"]["id"],),
        )


def test_mixed_semantics_and_known_unavailable_kinds_fail_closed_at_compilation(gateway, sample_csv):
    case = gateway.create_case(name="No coercion", goal="Reject ambiguous execution semantics")
    file_record = gateway.add_inline_file(case_id=case["id"], filename="data.csv", content=sample_csv)
    mixed = _research_plan(file_record)
    mixed["candidates"].append(
        {"id": "linear-1", "statement": "y tracks x", "kind": "linear_association", "predictor": "x", "outcome": "y"}
    )
    with pytest.raises(ExecutionCapabilityLimit, match="no fallback or semantic coercion"):
        gateway.submit_plan(case["id"], plan=mixed)

    unavailable = {
        "schema_version": "orbita-research-plan/0.1",
        "selected_dataset": {"file_id": file_record["id"]},
        "thresholds": {},
        "candidates": [{"id": "theorem-1", "statement": "Verify theorem", "kind": "formal_theorem"}],
    }
    with pytest.raises(ExecutionCapabilityLimit, match="ENGINE_CAPABILITY_LIMIT"):
        gateway.submit_plan(case["id"], plan=unavailable)

    malformed_statistical = {
        "schema_version": "orbita-research-plan/0.1",
        "selected_dataset": {"file_id": file_record["id"]},
        "thresholds": {},
        "candidates": [
            {"id": "linear-bad", "statement": "Missing executable columns", "kind": "linear_association"}
        ],
    }
    with pytest.raises(ExecutionCapabilityLimit, match="missing executor fields"):
        gateway.submit_plan(case["id"], plan=malformed_statistical)


def test_candidate_execution_receipts_are_tenant_scoped(gateway):
    first = gateway.for_tenant("execution-first")
    second = gateway.for_tenant("execution-second")
    binding = {
        "executor_id": "fixture/1",
        "executor_contract_hash": "a" * 64,
        "binding_hash": "b" * 64,
        "candidate_kinds": ["fixture"],
    }
    receipt = first.candidate_executions.record(
        case_id="case_fixture",
        plan_id="plan_fixture",
        plan_hash="c" * 64,
        binding=binding,
        outcome="prepared",
        result_reference={"status": "fixture"},
    )
    assert first.candidate_executions.verify(receipt["id"])["valid"] is True
    assert second.list_candidate_execution_receipts() == []
