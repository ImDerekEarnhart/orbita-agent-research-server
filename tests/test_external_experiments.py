from __future__ import annotations

import json

import pytest

from orbita import EvidenceStatus
from orbita.execution import EngineResult
from orbita_agent.external_experiments import (
    COVERAGE_REEVALUATION_APPROVAL_PHRASE,
    EXTERNAL_EXPERIMENT_APPROVAL_PHRASE,
    REPRODUCTION_APPROVAL_PHRASE,
)
from orbita_agent.gateway import APPROVAL_PHRASE


class SuccessfulEngine:
    name = "fixture-oci"

    def available(self) -> bool:
        return True

    def run(self, manifest, run_root):
        output = run_root / "output" / "verdict.json"
        output.write_text(json.dumps({"bound_holds": True, "tested_n": 8}), encoding="utf-8")
        return EngineResult(
            engine=self.name,
            invoked_command=["fixture-oci", "run", manifest["run_id"]],
            exit_code=0,
            timed_out=False,
            stdout="completed",
            stderr="",
        )


class UnavailableEngine:
    name = "missing-fixture-oci"

    def available(self) -> bool:
        return False

    def run(self, manifest, run_root):  # pragma: no cover - availability blocks invocation
        raise AssertionError("unavailable engine must not run")


def _approved_plan(gateway, sample_csv):
    case = gateway.create_case(name="External theorem check", goal="Test a declared universal graph bound")
    gateway.add_inline_file(case_id=case["id"], filename="graphs.csv", content=sample_csv)
    plan = gateway.compile_plan(case["id"])
    plan = gateway.approve_plan(
        plan["id"],
        expected_plan_hash=plan["plan_hash"],
        reviewer="research-owner",
        confirmation=APPROVAL_PHRASE,
    )
    return case, plan


def _execution_spec():
    return {
        "name": "Frozen universal-bound fixture",
        "image": "python@sha256:" + "a" * 64,
        "command": ["python", "/code/check.py"],
        "code_files": [
            {
                "target": "check.py",
                "text": "import json\njson.dump({'bound_holds': True, 'tested_n': 8}, open('/output/verdict.json','w'))\n",
                "media_type": "text/x-python",
            }
        ],
        "outputs": [
            {
                "path": "verdict.json",
                "media_type": "application/json",
                "json_schema": {
                    "type": "object",
                    "required": ["bound_holds", "tested_n"],
                    "properties": {
                        "bound_holds": {"type": "boolean"},
                        "tested_n": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
            }
        ],
        "network": False,
    }


def _freeze(gateway, sample_csv):
    case, plan = _approved_plan(gateway, sample_csv)
    experiment = gateway.freeze_external_experiment(
        case_id=case["id"],
        plan_id=plan["id"],
        expected_plan_hash=plan["plan_hash"],
        scientific_question="Does the declared graph inequality hold throughout the explicitly bounded domain?",
        claim_scope={
            "domain": "simple graphs",
            "quantifiers": "all graphs in the declared finite fixture",
            "finite_or_infinite": "finite",
            "bound": "n <= 8",
            "computational_model": "exact enumeration fixture",
        },
        execution_spec=_execution_spec(),
        verdict_schema={"support": "bound_holds=true", "refute": "counterexample emitted"},
        independent_verifier={"required": True, "identity": "separate-exact-checker", "kind": "exact"},
        falsification_coverage={
            "tested_domains": ["simple graphs"],
            "tested_sizes": [1, 2, 3, 4, 5, 6, 7, 8],
            "known_uncovered_regions": ["n > 8"],
        },
        anti_rescue_rules=["Do not remove a counterexample or narrow the claim after execution."],
        created_by="orbita-planner",
    )
    return experiment


def _complete(gateway, sample_csv):
    experiment = _freeze(gateway, sample_csv)
    staged = gateway.submit_external_experiment(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        submitted_by="orbita-executor",
    )
    execution = staged["execution"]
    gateway.approve_external_experiment(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        expected_manifest_hash=execution["manifest_hash"],
        reviewer="research-owner",
        rationale="I reviewed the frozen scope and deterministic execution contract.",
        confirmation=EXTERNAL_EXPERIMENT_APPROVAL_PHRASE,
    )
    return gateway.external_experiments.execute(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        expected_manifest_hash=execution["manifest_hash"],
        engine=SuccessfulEngine(),
    )


def test_external_experiment_preserves_semantics_and_requires_exact_approval(gateway, sample_csv):
    experiment = _freeze(gateway, sample_csv)
    assert experiment["claim_scope"]["bound"] == "n <= 8"
    assert experiment["execution_classification"] == "NOT_SUBMITTED"
    assert experiment["epistemic_status"] == "UNVERIFIED"

    staged = gateway.submit_external_experiment(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        submitted_by="orbita-executor",
    )
    execution = staged["execution"]
    assert execution["status"] == "waiting_approval"
    assert execution["manifest"]["security"]["network"] == "none"
    assert execution["manifest"]["security"]["read_only_root"] is True

    with pytest.raises(ValueError, match="manifest hash mismatch"):
        gateway.approve_external_experiment(
            experiment["id"],
            expected_experiment_hash=experiment["experiment_hash"],
            expected_manifest_hash="0" * 64,
            reviewer="research-owner",
            rationale="I reviewed the frozen scope and deterministic execution contract.",
            confirmation=EXTERNAL_EXPERIMENT_APPROVAL_PHRASE,
        )
    approved = gateway.approve_external_experiment(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        expected_manifest_hash=execution["manifest_hash"],
        reviewer="research-owner",
        rationale="I reviewed the frozen scope and deterministic execution contract.",
        confirmation=EXTERNAL_EXPERIMENT_APPROVAL_PHRASE,
    )
    assert approved["execution"]["status"] == "approved"


def test_integrity_does_not_become_scientific_validity(gateway, sample_csv):
    experiment = _freeze(gateway, sample_csv)
    staged = gateway.submit_external_experiment(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        submitted_by="orbita-executor",
    )
    execution = staged["execution"]
    gateway.approve_external_experiment(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        expected_manifest_hash=execution["manifest_hash"],
        reviewer="research-owner",
        rationale="I reviewed the frozen scope and deterministic execution contract.",
        confirmation=EXTERNAL_EXPERIMENT_APPROVAL_PHRASE,
    )
    completed = gateway.external_experiments.execute(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        expected_manifest_hash=execution["manifest_hash"],
        engine=SuccessfulEngine(),
    )
    assert completed["execution"]["status"] == "succeeded"
    assert completed["integrity_status"] == "VERIFIED"
    assert completed["epistemic_status"] == "UNVERIFIED"
    assert completed["falsification_coverage"]["known_uncovered_regions"] == ["n > 8"]

    verified = gateway.record_external_verification(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        expected_execution_receipt_hash=completed["execution"]["receipt_hash"],
        verifier_receipt={
            "verifier_identity": "separate-exact-checker",
            "receipt": "independent reproduction agreed inside n <= 8",
        },
        conclusion="supports",
        verified_by="independent-verifier-operator",
    )
    assert verified["integrity_status"] == "VERIFIED"
    assert verified["epistemic_status"] == "EMPIRICAL_SURVIVOR"
    assert verified["scientific_reproducibility_status"] == "NOT_TESTED"


def test_external_route_rejects_server_paths_network_and_missing_coverage(gateway, sample_csv, tmp_path):
    case, plan = _approved_plan(gateway, sample_csv)
    base = {
        "case_id": case["id"],
        "plan_id": plan["id"],
        "expected_plan_hash": plan["plan_hash"],
        "scientific_question": "Can this exact external experiment test the declared bounded claim?",
        "claim_scope": {"domain": "graphs", "quantifiers": "all declared fixtures"},
        "verdict_schema": {"support": True},
        "independent_verifier": {"required": True, "identity": "checker"},
        "falsification_coverage": {"known_uncovered_regions": []},
        "anti_rescue_rules": ["No post-result scope edits."],
        "created_by": "tester",
    }
    source_spec = _execution_spec()
    source_spec["code_files"] = [{"target": "check.py", "source": str(tmp_path / "secret.txt")}]
    with pytest.raises(ValueError, match="arbitrary server source paths are forbidden"):
        gateway.freeze_external_experiment(**base, execution_spec=source_spec)

    network_spec = _execution_spec()
    network_spec["network"] = True
    with pytest.raises(ValueError, match="Network access is disabled"):
        gateway.freeze_external_experiment(**base, execution_spec=network_spec)

    missing_coverage = dict(base)
    missing_coverage["falsification_coverage"] = {"tested_sizes": [4, 5, 6, 7, 8]}
    with pytest.raises(ValueError, match="known_uncovered_regions"):
        gateway.freeze_external_experiment(**missing_coverage, execution_spec=_execution_spec())


def test_unavailable_executor_is_engine_limit_not_scientific_refutation(gateway, sample_csv):
    experiment = _freeze(gateway, sample_csv)
    staged = gateway.submit_external_experiment(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        submitted_by="orbita-executor",
    )
    execution = staged["execution"]
    gateway.approve_external_experiment(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        expected_manifest_hash=execution["manifest_hash"],
        reviewer="research-owner",
        rationale="I reviewed the frozen scope and deterministic execution contract.",
        confirmation=EXTERNAL_EXPERIMENT_APPROVAL_PHRASE,
    )
    result = gateway.external_experiments.execute(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        expected_manifest_hash=execution["manifest_hash"],
        engine=UnavailableEngine(),
    )
    assert result["execution_classification"] == "EXECUTION_LIMIT"
    assert result["epistemic_status"] == "UNVERIFIED"
    assert result["execution"]["status"] == "approved"


def test_exact_reproduction_compares_outputs_without_claiming_scientific_replication(gateway, sample_csv):
    completed = _complete(gateway, sample_csv)
    original = completed["execution"]
    prepared = gateway.prepare_external_reproduction(
        completed["id"],
        expected_experiment_hash=completed["experiment_hash"],
        expected_execution_receipt_hash=original["receipt_hash"],
        submitted_by="reproduction-preparer",
    )
    reproduction = prepared["reproduction"]
    assert reproduction["status"] == "waiting_approval"
    assert reproduction["parent_run_id"] == original["id"]
    assert prepared["bitwise_reproducibility_status"] == "PENDING"

    with pytest.raises(ValueError, match="confirmation"):
        gateway.approve_external_reproduction(
            completed["id"],
            expected_experiment_hash=completed["experiment_hash"],
            expected_original_receipt_hash=original["receipt_hash"],
            expected_reproduction_manifest_hash=reproduction["manifest_hash"],
            reviewer="research-owner",
            rationale="I reviewed the exact original receipt and reproduction manifest.",
            confirmation="approve replay",
        )
    gateway.approve_external_reproduction(
        completed["id"],
        expected_experiment_hash=completed["experiment_hash"],
        expected_original_receipt_hash=original["receipt_hash"],
        expected_reproduction_manifest_hash=reproduction["manifest_hash"],
        reviewer="research-owner",
        rationale="I reviewed the exact original receipt and reproduction manifest.",
        confirmation=REPRODUCTION_APPROVAL_PHRASE,
    )
    replayed = gateway.external_experiments.execute_reproduction(
        completed["id"],
        expected_experiment_hash=completed["experiment_hash"],
        expected_original_receipt_hash=original["receipt_hash"],
        expected_reproduction_manifest_hash=reproduction["manifest_hash"],
        engine=SuccessfulEngine(),
    )
    assert replayed["reproduction"]["comparison"]["outputs_match"] is True
    assert replayed["bitwise_reproducibility_status"] == "VERIFIED"
    assert replayed["scientific_reproducibility_status"] == "NOT_ESTABLISHED_BY_TECHNICAL_REPLAY"
    assert replayed["epistemic_status"] == "UNVERIFIED"


def test_coverage_bug_preserves_original_and_versions_replacement_protocol(gateway, sample_csv):
    experiment = _freeze(gateway, sample_csv)
    original_hash = experiment["experiment_hash"]
    original_coverage = experiment["falsification_coverage"]

    corrected = gateway.record_external_coverage_bug(
        experiment["id"],
        expected_experiment_hash=original_hash,
        claim_effect="refutes_claim",
        missed_counterexample={
            "validated": True,
            "witness": "K2",
            "graph_size": 2,
            "validation_receipt_hash": "d" * 64,
            "validated_by": "independent-small-graph-checker",
        },
        reason="The original tournament began at n=4 and therefore omitted the decisive n=2 boundary case.",
        fix={"change_summary": "Start exhaustive graph enumeration at n=1 before heuristic search."},
        old_results_impacted=["original 5/5 survival ranking", "universal conjecture status"],
        replacement_coverage={
            "tested_domains": ["simple graphs"],
            "required_sizes": [1, 2, 3, 4, 5, 6, 7, 8],
            "known_uncovered_regions": ["n > 8"],
        },
        recorded_by="coverage-auditor",
    )
    assert corrected["experiment_hash"] == original_hash
    assert corrected["falsification_coverage"] == original_coverage
    assert corrected["epistemic_status"] == "REFUTED"
    assert corrected["reevaluation_required"] is True
    bug = corrected["coverage_bugs"][0]
    assert bug["protocol_version"] == 2
    assert bug["reevaluation_status"] == "REQUIRED"
    assert bug["replacement_protocol"]["supersedes"]["experiment_hash"] == original_hash
    assert bug["replacement_protocol_hash"]

    with pytest.raises(ValueError, match="validated must be true"):
        gateway.record_external_coverage_bug(
            experiment["id"],
            expected_experiment_hash=original_hash,
            claim_effect="challenges_coverage",
            missed_counterexample={"validated": False, "witness": "unconfirmed"},
            reason="This unconfirmed item must not be admitted as a coverage bug yet.",
            fix={"change_summary": "none"},
            old_results_impacted=["none"],
            replacement_coverage={"known_uncovered_regions": []},
            recorded_by="coverage-auditor",
        )


def test_validated_coverage_bug_challenges_guided_claims_and_queues_dependents(
    gateway, sample_csv
):
    scope = {
        "domain": "simple graphs",
        "quantifier": "bounded_all",
        "boundary": {"max_n": 8},
        "assumptions": ["simple undirected graphs"],
        "computational_model": "finite graph tournament",
        "representation_language": "graph invariants",
    }
    claim_id, _ = gateway.service.memory.resolve_or_create_claim(
        "The candidate inequality holds for every graph in the stated scope.",
        scope=scope,
    )
    gateway.service.memory.record_epistemic_contract(
        claim_id,
        evidence_status=EvidenceStatus.BOUNDED_VERIFIED,
        claim_scope=scope,
        falsification_coverage={
            "tested_domains": ["simple graphs"],
            "tested_sizes": [4, 5, 6, 7, 8],
            "known_uncovered_regions": ["n < 4", "n > 8"],
        },
        reason="Survived the original bounded tournament.",
        source_run_id="legacy-tournament",
    )
    dependent_id, _ = gateway.service.memory.resolve_or_create_claim(
        "A downstream summary relies on the candidate inequality.",
        scope=scope,
    )
    gateway.service.ledger.add_proof(
        dependent_id,
        [claim_id],
        rule="legacy summary derivation",
    )
    experiment = _freeze(gateway, sample_csv)
    corrected = gateway.record_external_coverage_bug(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        claim_effect="refutes_claim",
        missed_counterexample={
            "validated": True,
            "witness": "K2",
            "graph_size": 2,
            "validation_receipt_hash": "d" * 64,
            "validated_by": "independent-small-graph-checker",
        },
        reason="The original tournament began at n=4 and omitted the decisive n=2 boundary case.",
        fix={"change_summary": "Start exhaustive graph enumeration at n=1."},
        old_results_impacted=["legacy-tournament"],
        replacement_coverage={
            "tested_domains": ["simple graphs"],
            "required_sizes": [1, 2, 3, 4, 5, 6, 7, 8],
            "known_uncovered_regions": ["n > 8"],
        },
        recorded_by="coverage-auditor",
        affected_claim_ids=[claim_id],
    )
    bug = corrected["coverage_bugs"][0]
    assert bug["affected_claim_ids"] == [claim_id]
    assert corrected["claim_propagation"]["affected_claim_ids"] == [claim_id]

    history = gateway.claim_history(claim_id)
    assert history["current_claim"]["status"] == "rejected"
    assert history["current_epistemic_contract"]["evidence_status"] == "REFUTED"
    assert len(history["epistemic_history"][claim_id]) == 2
    assert history["coverage_bug_impacts"][claim_id][0]["coverage_bug_id"] == bug["id"]
    open_queue = gateway.service.memory.list_reexamination("open")
    queued_claims = {item["claim_id"] for item in open_queue}
    assert claim_id in queued_claims
    assert dependent_id in queued_claims

    retried = gateway.propagate_external_coverage_bug_to_claims(
        bug["id"],
        expected_replacement_protocol_hash=bug["replacement_protocol_hash"],
    )
    assert retried["affected_claim_ids"] == [claim_id]
    assert len(gateway.claim_history(claim_id)["epistemic_history"][claim_id]) == 2


def test_replacement_protocol_executes_and_resolves_every_affected_result(gateway, sample_csv):
    experiment = _freeze(gateway, sample_csv)
    corrected = gateway.record_external_coverage_bug(
        experiment["id"],
        expected_experiment_hash=experiment["experiment_hash"],
        claim_effect="refutes_claim",
        missed_counterexample={
            "validated": True,
            "witness": "K2",
            "graph_size": 2,
            "validation_receipt_hash": "d" * 64,
            "validated_by": "independent-small-graph-checker",
        },
        reason="The original graph gauntlet started at n=4 and skipped a decisive boundary counterexample.",
        fix={"change_summary": "Enumerate graph sizes from n=1 before heuristic search."},
        old_results_impacted=["leaderboard:G1", "claim:G1-universal"],
        replacement_coverage={
            "tested_domains": ["simple graphs"],
            "required_sizes": [1, 2, 3, 4, 5, 6, 7, 8],
            "known_uncovered_regions": ["n > 8"],
        },
        recorded_by="coverage-auditor",
    )
    bug = corrected["coverage_bugs"][0]

    with pytest.raises(ValueError, match="exactly cover every old result"):
        gateway.prepare_coverage_reevaluation(
            bug["id"],
            expected_replacement_protocol_hash=bug["replacement_protocol_hash"],
            execution_spec=_execution_spec(),
            resolution_targets=["claim:G1-universal"],
            submitted_by="replacement-protocol-runner",
        )

    prepared = gateway.prepare_coverage_reevaluation(
        bug["id"],
        expected_replacement_protocol_hash=bug["replacement_protocol_hash"],
        execution_spec=_execution_spec(),
        resolution_targets=["leaderboard:G1", "claim:G1-universal"],
        submitted_by="replacement-protocol-runner",
    )
    reevaluation = prepared["reevaluation"]
    assert prepared["current_reevaluation_status"] == "IN_PROGRESS"
    assert reevaluation["execution"]["status"] == "waiting_approval"
    assert reevaluation["resolution_targets"] == ["leaderboard:G1", "claim:G1-universal"]

    gateway.approve_coverage_reevaluation(
        bug["id"],
        expected_replacement_protocol_hash=bug["replacement_protocol_hash"],
        expected_reevaluation_hash=reevaluation["reevaluation_hash"],
        expected_execution_manifest_hash=reevaluation["execution_manifest_hash"],
        reviewer="research-owner",
        rationale="I reviewed the corrected coverage, affected results, and exact staged manifest.",
        confirmation=COVERAGE_REEVALUATION_APPROVAL_PHRASE,
    )
    executed = gateway.external_experiments.execute_coverage_reevaluation(
        bug["id"],
        expected_replacement_protocol_hash=bug["replacement_protocol_hash"],
        expected_reevaluation_hash=reevaluation["reevaluation_hash"],
        expected_execution_manifest_hash=reevaluation["execution_manifest_hash"],
        engine=SuccessfulEngine(),
    )
    receipt_hash = executed["reevaluation"]["execution"]["receipt_hash"]
    assert executed["current_reevaluation_status"] == "AWAITING_RESOLUTION"

    with pytest.raises(ValueError, match="every affected result"):
        gateway.record_coverage_resolutions(
            bug["id"],
            expected_reevaluation_hash=reevaluation["reevaluation_hash"],
            expected_execution_receipt_hash=receipt_hash,
            resolutions=[
                {
                    "affected_result_id": "claim:G1-universal",
                    "disposition": "refuted",
                    "rationale": "The validated K2 witness refutes the frozen universal statement.",
                    "evidence_receipt_hash": receipt_hash,
                }
            ],
            recorded_by="resolution-auditor",
        )

    resolved = gateway.record_coverage_resolutions(
        bug["id"],
        expected_reevaluation_hash=reevaluation["reevaluation_hash"],
        expected_execution_receipt_hash=receipt_hash,
        resolutions=[
            {
                "affected_result_id": "leaderboard:G1",
                "disposition": "superseded",
                "rationale": "The old ranking omitted the boundary region and is replaced by protocol version 2.",
                "evidence_receipt_hash": receipt_hash,
            },
            {
                "affected_result_id": "claim:G1-universal",
                "disposition": "refuted",
                "rationale": "The validated K2 witness refutes the frozen universal statement.",
                "evidence_receipt_hash": receipt_hash,
            },
        ],
        recorded_by="resolution-auditor",
    )
    assert resolved["current_reevaluation_status"] == "RESOLVED"
    assert resolved["resolution_receipt"]["resolutions_hash"]
    assert len(resolved["resolution_receipt"]["resolutions"]) == 2
    final_experiment = gateway.get_external_experiment(experiment["id"])
    assert final_experiment["reevaluation_required"] is False
    assert final_experiment["epistemic_status"] == "REFUTED"
    assert final_experiment["experiment_hash"] == experiment["experiment_hash"]
