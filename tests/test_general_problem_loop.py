from __future__ import annotations

import sqlite3

import pytest

from orbita_agent.general_problem_loop import GeneralProblemLoopService


def _create(service: GeneralProblemLoopService, *, max_cycles: int = 1):
    return service.create(
        goal="Determine whether the proposed rule survives an exact finite test.",
        success_criteria=["Every frozen falsifier passes", "Evidence receipts remain hash bound"],
        allowed_capabilities=["finite_checker", "representation_audit"],
        max_cycles=max_cycles,
        created_by="test-user",
    )


def _submit(service, loop, artifact, *, state=None):
    return service.submit(
        loop["id"],
        expected_state=state or loop["current_state"],
        expected_previous_event_hash=loop["latest_event_hash"],
        artifact=artifact,
        actor="test-model",
    )


def _through_observe(service, loop):
    loop = _submit(
        service,
        loop,
        {"problem_representation": "A finite universal rule over explicit worlds.", "assumptions": [], "unknowns": ["Whether a counterexample exists"]},
    )
    loop = _submit(
        service,
        loop,
        {
            "steps": ["Enumerate every world", "Run the exact checker"],
            "falsifiers": ["Any counterexample refutes the rule"],
            "success_checks": ["All worlds checked"],
            "executor_requirements": ["finite_checker"],
            "anti_rescue_rules": ["Do not change the rule after execution starts"],
        },
    )
    loop = _submit(
        service,
        loop,
        {
            "executor": "finite_checker",
            "action_receipts": [{"executor": "finite_checker", "receipt_hash": "a" * 64, "status": "completed"}],
            "execution_status": "completed",
            "external_state_changed": False,
        },
    )
    return _submit(
        service,
        loop,
        {"observations": ["All 16 worlds were checked"], "evidence_hashes": ["a" * 64], "missing_observations": []},
    )


def test_survived_path_commits_only_with_exact_evidence_and_chain(tmp_path):
    service = GeneralProblemLoopService(tmp_path / "loops.db")
    try:
        loop = _through_observe(service, _create(service))
        loop = _submit(
            service,
            loop,
            {"verdict": "survived", "checks": ["No counterexample found"], "failed_checks": [], "counterexample_hashes": [], "scope": "16 finite worlds"},
        )
        assert loop["current_state"] == "COMMIT_REFUSE"
        with pytest.raises(ValueError, match="exact evidence hash"):
            _submit(service, loop, {"decision": "commit", "statement": "The finite claim survived the frozen test.", "evidence_hashes": [], "limitations": ["Finite scope only"]})
        loop = _submit(
            service,
            loop,
            {"decision": "commit", "statement": "The finite claim survived the frozen test.", "evidence_hashes": ["a" * 64], "limitations": ["Finite scope only"]},
        )
        assert loop["terminal"] is True
        assert loop["current_state"] == "COMPLETED"
        assert service.verify_chain(loop["id"])["valid"] is True
    finally:
        service.close()


def test_refuted_language_failure_requires_certificate_and_cannot_activate(tmp_path):
    service = GeneralProblemLoopService(tmp_path / "loops.db")
    try:
        loop = _through_observe(service, _create(service))
        loop = _submit(
            service,
            loop,
            {"verdict": "refuted", "checks": ["collision test"], "failed_checks": ["two outcomes collide"], "counterexample_hashes": ["a" * 64], "scope": "finite worlds"},
        )
        with pytest.raises(ValueError, match="certificate"):
            _submit(
                service,
                loop,
                {"limitation_kind": "LANGUAGE_LIMIT", "rationale": "The current view collapses two distinct outcomes.", "evidence_hashes": ["a" * 64]},
            )
        loop = _submit(
            service,
            loop,
            {
                "limitation_kind": "LANGUAGE_LIMIT",
                "rationale": "The current view collapses two distinct outcomes.",
                "evidence_hashes": ["a" * 64],
                "language_limit_certificate_hash": "a" * 64,
            },
        )
        assert loop["current_state"] == "REPAIR_LEARN"
        with pytest.raises(PermissionError, match="cannot request"):
            _submit(
                service,
                loop,
                {
                    "repair_kind": "language_primitive", "candidate_hash": "e" * 64,
                    "prospective_predictions": ["collision resolves"], "known_risks": ["overseparation"],
                    "activation_requested": True,
                },
            )
    finally:
        service.close()


def test_state_and_previous_hash_are_mandatory_and_events_are_immutable(tmp_path):
    service = GeneralProblemLoopService(tmp_path / "loops.db")
    try:
        loop = _create(service)
        artifact = {"problem_representation": "A bounded finite problem representation.", "assumptions": [], "unknowns": ["answer"]}
        with pytest.raises(ValueError, match="state mismatch"):
            _submit(service, loop, artifact, state="PLAN")
        with pytest.raises(ValueError, match="previous event hash mismatch"):
            service.submit(
                loop["id"], expected_state="REPRESENT", expected_previous_event_hash="0" * 64,
                artifact=artifact, actor="test-model",
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            service.conn.execute("UPDATE general_problem_loop_events SET state = 'PLAN' WHERE loop_id = ?", (loop["id"],))
    finally:
        service.close()


def test_retry_budget_routes_to_refusal_instead_of_unbounded_autonomy(tmp_path):
    service = GeneralProblemLoopService(tmp_path / "loops.db")
    try:
        loop = _through_observe(service, _create(service, max_cycles=0))
        loop = _submit(
            service,
            loop,
            {"verdict": "inconclusive", "checks": ["coverage"], "failed_checks": ["coverage incomplete"], "counterexample_hashes": [], "scope": "partial"},
        )
        loop = _submit(
            service,
            loop,
            {"limitation_kind": "SEARCH_FAILURE", "rationale": "The bounded search did not cover the required cases.", "evidence_hashes": ["a" * 64]},
        )
        loop = _submit(
            service,
            loop,
            {
                "repair_kind": "search_strategy", "candidate_hash": "1" * 64,
                "prospective_predictions": ["coverage increases"], "known_risks": ["runtime cost increases"],
                "activation_requested": False,
            },
        )
        loop = _submit(
            service,
            loop,
            {"change_summary": "Use a different bounded enumeration order.", "retained_falsifiers": ["coverage must be complete"], "retry_authorized": True},
        )
        assert loop["current_state"] == "COMMIT_REFUSE"
        assert loop["cycle"] == 0
        loop = _submit(
            service,
            loop,
            {"decision": "refuse", "statement": "The retry budget is exhausted without adequate evidence.", "evidence_hashes": [], "limitations": ["Coverage incomplete"]},
        )
        assert loop["terminal"] is True
    finally:
        service.close()


def test_gateway_is_tenant_scoped_and_exposes_no_activation(gateway):
    first = gateway.for_tenant("first")
    second = gateway.for_tenant("second")
    loop = first.create_general_problem_loop(
        goal="Determine a bounded answer using governed evidence.",
        success_criteria=["Exact evidence exists"],
        allowed_capabilities=["finite_checker"],
        max_cycles=1,
    )
    assert [item["id"] for item in first.list_general_problem_loops()] == [loop["id"]]
    assert second.list_general_problem_loops() == []
    assert first.general_problem_loops.status()["runtime_activation"] is False
    assert loop["required_artifact"]["required"] == ["problem_representation", "unknowns"]


def test_plan_and_action_cannot_escape_frozen_capability_allowlist(tmp_path):
    service = GeneralProblemLoopService(tmp_path / "loops.db")
    try:
        loop = _create(service)
        loop = _submit(
            service,
            loop,
            {"problem_representation": "A finite claim over explicit worlds.", "assumptions": [], "unknowns": ["answer"]},
        )
        with pytest.raises(PermissionError, match="outside the frozen allowlist"):
            _submit(
                service,
                loop,
                {
                    "steps": ["Browse the web"], "falsifiers": ["contradiction"], "success_checks": ["source found"],
                    "executor_requirements": ["unapproved_browser"], "anti_rescue_rules": ["do not change target"],
                },
            )
    finally:
        service.close()


def test_survived_verdict_and_commit_cannot_hide_failures_or_invent_evidence(tmp_path):
    service = GeneralProblemLoopService(tmp_path / "loops.db")
    try:
        loop = _through_observe(service, _create(service))
        with pytest.raises(ValueError, match="cannot contain failed checks"):
            _submit(
                service,
                loop,
                {"verdict": "survived", "checks": ["coverage"], "failed_checks": ["one miss"], "counterexample_hashes": [], "scope": "finite"},
            )
        loop = _submit(
            service,
            loop,
            {"verdict": "survived", "checks": ["coverage"], "failed_checks": [], "counterexample_hashes": [], "scope": "finite"},
        )
        with pytest.raises(ValueError, match="absent from the loop history"):
            _submit(
                service,
                loop,
                {"decision": "commit", "statement": "The finite claim survived its checks.", "evidence_hashes": ["9" * 64], "limitations": ["finite"]},
            )
    finally:
        service.close()


def test_observation_cannot_invent_evidence_or_accept_a_failed_completed_receipt(tmp_path):
    service = GeneralProblemLoopService(tmp_path / "loops.db")
    try:
        loop = _create(service)
        loop = _submit(
            service,
            loop,
            {"problem_representation": "A finite claim over explicit worlds.", "assumptions": [], "unknowns": ["answer"]},
        )
        loop = _submit(
            service,
            loop,
            {
                "steps": ["Run checker"], "falsifiers": ["counterexample"], "success_checks": ["complete"],
                "executor_requirements": ["finite_checker"], "anti_rescue_rules": ["freeze target"],
            },
        )
        with pytest.raises(ValueError, match="non-completed"):
            _submit(
                service,
                loop,
                {
                    "executor": "finite_checker", "execution_status": "completed",
                    "action_receipts": [{"executor": "finite_checker", "receipt_hash": "a" * 64, "status": "failed"}],
                },
            )
        loop = _submit(
            service,
            loop,
            {
                "executor": "finite_checker", "execution_status": "completed",
                "action_receipts": [{"executor": "finite_checker", "receipt_hash": "a" * 64, "status": "completed"}],
            },
        )
        with pytest.raises(ValueError, match="absent from prior action receipts"):
            _submit(
                service,
                loop,
                {"observations": ["claimed output"], "evidence_hashes": ["b" * 64], "missing_observations": []},
            )
    finally:
        service.close()
