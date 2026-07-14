from __future__ import annotations

import pytest

from orbita_agent.gateway import APPROVAL_PHRASE


def test_governed_agent_flow(gateway, sample_csv):
    case = gateway.create_case(name="Agent test", goal="Find stable associations")
    uploaded = gateway.add_inline_file(case_id=case["id"], filename="data.csv", content=sample_csv)
    assert uploaded["artifact_kind"] == "table"
    assert uploaded["profile"]["rows"] == 40

    plan = gateway.compile_plan(case["id"], max_candidates=24)
    assert plan["status"] == "proposed"
    assert plan["plan_hash"]

    with pytest.raises(ValueError, match="hash mismatch"):
        gateway.approve_plan(
            plan["id"],
            expected_plan_hash="0" * 64,
            reviewer="tester",
            confirmation=APPROVAL_PHRASE,
        )
    with pytest.raises(ValueError, match="confirmation"):
        gateway.approve_plan(
            plan["id"],
            expected_plan_hash=plan["plan_hash"],
            reviewer="tester",
            confirmation="approve",
        )

    approved = gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="tester",
        confirmation=APPROVAL_PHRASE,
    )
    assert approved["status"] == "approved"
    run = gateway.run_discovery(case["id"], plan_id=plan["id"])
    assert run["status"] == "completed"
    assert run["summary"]["candidate_count"] > 0
    assert run["findings_page"]["total"] == run["summary"]["candidate_count"]
    assert gateway.case_claims(case["id"])
    report = gateway.report(case["id"], format="markdown")
    assert "Research Dossier" in report["content"]


def test_run_requires_matching_approved_plan(gateway, sample_csv):
    first = gateway.create_case(name="First")
    second = gateway.create_case(name="Second")
    gateway.add_inline_file(case_id=first["id"], filename="data.csv", content=sample_csv)
    plan = gateway.compile_plan(first["id"])
    with pytest.raises(ValueError, match="approved"):
        gateway.run_discovery(first["id"], plan_id=plan["id"])
    gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="tester",
        confirmation=APPROVAL_PHRASE,
    )
    with pytest.raises(ValueError, match="does not belong"):
        gateway.run_discovery(second["id"], plan_id=plan["id"])


def test_inline_upload_is_bounded_and_sanitized(gateway):
    case = gateway.create_case(name="Upload guard")
    uploaded = gateway.add_inline_file(case_id=case["id"], filename="../../safe.csv", content="x,y\n1,2\n")
    assert uploaded["original_name"] == "safe.csv"
    with pytest.raises(ValueError, match="not allowed"):
        gateway.add_inline_file(case_id=case["id"], filename="payload.exe", content="no")
