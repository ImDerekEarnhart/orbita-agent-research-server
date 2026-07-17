from __future__ import annotations

import pytest

from orbita_agent.gateway import APPROVAL_PHRASE
from orbita_agent.improvement import PROMOTION_PHRASE, ROLLBACK_PHRASE


def _completed_case(gateway, sample_csv):
    case = gateway.create_case(name="Improvement benchmark", goal="Find stable associations between x and y")
    gateway.add_inline_file(case_id=case["id"], filename="benchmark.csv", content=sample_csv)
    plan = gateway.compile_plan(case["id"])
    gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="benchmark-owner",
        confirmation=APPROVAL_PHRASE,
    )
    gateway.run_discovery(case["id"], plan_id=plan["id"])
    return case


def test_improvement_is_allowlisted_and_hash_gated(gateway, sample_csv):
    case = _completed_case(gateway, sample_csv)
    initial = gateway.improvement_status()["active_policy"]
    assert initial["version"] == 1

    with pytest.raises(ValueError, match="forbidden"):
        gateway.propose_improvement(
            name="Unsafe rewrite",
            rationale="Attempt to add an arbitrary execution capability.",
            patch={"shell_command": "curl example.test"},
        )

    candidate = gateway.propose_improvement(
        name="Increase replication stress",
        rationale="Use two additional deterministic resamples to stress candidate stability.",
        patch={"cross_seed_count": 11},
    )
    assert candidate["status"] == "proposed"
    evaluation = gateway.evaluate_improvement(candidate["id"], case_ids=[case["id"]])
    assert evaluation["verdict"] == "eligible_for_review"
    assert evaluation["benchmarks"][0]["plan_hash"]

    with pytest.raises(ValueError, match="Candidate hash mismatch"):
        gateway.promote_improvement(
            candidate["id"],
            expected_candidate_hash="0" * 64,
            expected_evaluation_hash=evaluation["evaluation_hash"],
            reviewer="policy-owner",
            confirmation=PROMOTION_PHRASE,
        )
    with pytest.raises(ValueError, match="confirmation"):
        gateway.promote_improvement(
            candidate["id"],
            expected_candidate_hash=candidate["candidate_hash"],
            expected_evaluation_hash=evaluation["evaluation_hash"],
            reviewer="policy-owner",
            confirmation="approve",
        )

    promoted = gateway.promote_improvement(
        candidate["id"],
        expected_candidate_hash=candidate["candidate_hash"],
        expected_evaluation_hash=evaluation["evaluation_hash"],
        reviewer="policy-owner",
        confirmation=PROMOTION_PHRASE,
    )
    active = promoted["active_policy"]
    assert active["version"] == 2
    assert active["policy"]["cross_seed_count"] == 11

    plan = gateway.compile_plan(case["id"])
    assert plan["plan"]["thresholds"]["cross_seed_count"] == 11
    assert plan["plan"]["improvement_policy"]["policy_hash"] == active["policy_hash"]

    with pytest.raises(ValueError, match="hash mismatch"):
        gateway.rollback_improvement(
            initial["id"],
            expected_active_policy_hash="0" * 64,
            reviewer="policy-owner",
            confirmation=ROLLBACK_PHRASE,
        )
    rolled_back = gateway.rollback_improvement(
        initial["id"],
        expected_active_policy_hash=active["policy_hash"],
        reviewer="policy-owner",
        confirmation=ROLLBACK_PHRASE,
    )
    assert rolled_back["active_policy"]["id"] == initial["id"]


def test_history_suggester_needs_real_runs(gateway):
    suggestion = gateway.suggest_improvement()
    assert suggestion["status"] == "needs_benchmarks"
    assert suggestion["history"]["completed_case_count"] == 0


def test_compiled_plan_records_active_policy_and_budget_override(gateway, sample_csv):
    case = gateway.create_case(name="Policy receipt")
    gateway.add_inline_file(case_id=case["id"], filename="data.csv", content=sample_csv)
    plan = gateway.compile_plan(case["id"], max_candidates=3)
    receipt = plan["plan"]["improvement_policy"]
    assert receipt["policy_hash"]
    assert receipt["max_candidates_overridden"] is True
    assert plan["plan"]["candidate_generation"]["candidate_budget"] == 3
