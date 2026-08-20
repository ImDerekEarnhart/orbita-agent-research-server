"""Tenant-scoped bridge from approved Orbita plans to deterministic execution.

Execution integrity and scientific validity are deliberately separate. A valid
receipt says the frozen bytes ran as declared. It does not, by itself, prove the
claim, establish novelty, or justify a universal conclusion.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orbita.execution import ContainerExecutionSpec, OCIEngine
from orbita.ledger import EpistemicLedger
from orbita.models import ActorRole

EXTERNAL_EXPERIMENT_APPROVAL_PHRASE = "I reviewed this exact external experiment"
REPRODUCTION_APPROVAL_PHRASE = "I reviewed this exact external reproduction"
VERIFICATION_CONCLUSIONS = frozenset({"supports", "refutes", "inconclusive"})
COVERAGE_BUG_EFFECTS = frozenset({"refutes_claim", "challenges_coverage"})
RESOLUTION_DISPOSITIONS = frozenset({"refuted", "challenged", "unchanged", "superseded"})
COVERAGE_REEVALUATION_APPROVAL_PHRASE = "I reviewed this exact coverage reevaluation"

SCHEMA = """
CREATE TABLE IF NOT EXISTS external_experiments (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    scientific_question TEXT NOT NULL,
    claim_scope_json TEXT NOT NULL,
    execution_spec_json TEXT NOT NULL,
    verdict_schema_json TEXT NOT NULL,
    independent_verifier_json TEXT NOT NULL,
    falsification_coverage_json TEXT NOT NULL,
    anti_rescue_rules_json TEXT NOT NULL,
    experiment_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_experiment_runs (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL UNIQUE,
    experiment_hash TEXT NOT NULL,
    execution_run_id TEXT NOT NULL UNIQUE,
    manifest_hash TEXT NOT NULL UNIQUE,
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES external_experiments(id)
);

CREATE TABLE IF NOT EXISTS external_experiment_verifications (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL UNIQUE,
    experiment_hash TEXT NOT NULL,
    execution_receipt_hash TEXT NOT NULL,
    verifier_json TEXT NOT NULL,
    verifier_receipt_json TEXT NOT NULL,
    verifier_receipt_hash TEXT NOT NULL UNIQUE,
    conclusion TEXT NOT NULL,
    verified_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES external_experiments(id)
);

CREATE TABLE IF NOT EXISTS external_experiment_failures (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    failure_classification TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES external_experiments(id)
);

CREATE TABLE IF NOT EXISTS external_experiment_reproductions (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL UNIQUE,
    experiment_hash TEXT NOT NULL,
    original_run_id TEXT NOT NULL,
    original_receipt_hash TEXT NOT NULL,
    reproduction_run_id TEXT NOT NULL UNIQUE,
    reproduction_manifest_hash TEXT NOT NULL UNIQUE,
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES external_experiments(id)
);

CREATE TABLE IF NOT EXISTS coverage_bugs (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    experiment_hash TEXT NOT NULL,
    protocol_version INTEGER NOT NULL,
    claim_effect TEXT NOT NULL,
    missed_counterexample_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    fix_json TEXT NOT NULL,
    old_results_impacted_json TEXT NOT NULL,
    replacement_protocol_json TEXT NOT NULL,
    replacement_protocol_hash TEXT NOT NULL UNIQUE,
    reevaluation_status TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES external_experiments(id),
    UNIQUE(experiment_id, protocol_version)
);

CREATE TABLE IF NOT EXISTS coverage_reevaluations (
    id TEXT PRIMARY KEY,
    coverage_bug_id TEXT NOT NULL UNIQUE,
    replacement_protocol_hash TEXT NOT NULL,
    execution_spec_json TEXT NOT NULL,
    execution_spec_hash TEXT NOT NULL,
    resolution_targets_json TEXT NOT NULL,
    reevaluation_hash TEXT NOT NULL UNIQUE,
    execution_run_id TEXT NOT NULL UNIQUE,
    execution_manifest_hash TEXT NOT NULL UNIQUE,
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(coverage_bug_id) REFERENCES coverage_bugs(id)
);

CREATE TABLE IF NOT EXISTS coverage_resolution_receipts (
    id TEXT PRIMARY KEY,
    coverage_bug_id TEXT NOT NULL UNIQUE,
    reevaluation_hash TEXT NOT NULL,
    execution_receipt_hash TEXT NOT NULL,
    resolutions_json TEXT NOT NULL,
    resolutions_hash TEXT NOT NULL UNIQUE,
    recorded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(coverage_bug_id) REFERENCES coverage_bugs(id)
);

CREATE TABLE IF NOT EXISTS coverage_bug_claim_bindings (
    id TEXT PRIMARY KEY,
    coverage_bug_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(coverage_bug_id) REFERENCES coverage_bugs(id),
    UNIQUE(coverage_bug_id, claim_id)
);

CREATE TRIGGER IF NOT EXISTS external_experiments_no_update
BEFORE UPDATE ON external_experiments BEGIN SELECT RAISE(ABORT, 'external experiments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS external_experiments_no_delete
BEFORE DELETE ON external_experiments BEGIN SELECT RAISE(ABORT, 'external experiments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS external_experiment_runs_no_update
BEFORE UPDATE ON external_experiment_runs BEGIN SELECT RAISE(ABORT, 'external experiment run links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS external_experiment_runs_no_delete
BEFORE DELETE ON external_experiment_runs BEGIN SELECT RAISE(ABORT, 'external experiment run links are append-only'); END;
CREATE TRIGGER IF NOT EXISTS external_experiment_verifications_no_update
BEFORE UPDATE ON external_experiment_verifications BEGIN SELECT RAISE(ABORT, 'external verifications are immutable'); END;
CREATE TRIGGER IF NOT EXISTS external_experiment_verifications_no_delete
BEFORE DELETE ON external_experiment_verifications BEGIN SELECT RAISE(ABORT, 'external verifications are append-only'); END;
CREATE TRIGGER IF NOT EXISTS external_experiment_failures_no_update
BEFORE UPDATE ON external_experiment_failures BEGIN SELECT RAISE(ABORT, 'external failures are immutable'); END;
CREATE TRIGGER IF NOT EXISTS external_experiment_failures_no_delete
BEFORE DELETE ON external_experiment_failures BEGIN SELECT RAISE(ABORT, 'external failures are append-only'); END;
CREATE TRIGGER IF NOT EXISTS external_experiment_reproductions_no_update
BEFORE UPDATE ON external_experiment_reproductions BEGIN SELECT RAISE(ABORT, 'external reproductions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS external_experiment_reproductions_no_delete
BEFORE DELETE ON external_experiment_reproductions BEGIN SELECT RAISE(ABORT, 'external reproductions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS coverage_bugs_no_update
BEFORE UPDATE ON coverage_bugs BEGIN SELECT RAISE(ABORT, 'coverage bugs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS coverage_bugs_no_delete
BEFORE DELETE ON coverage_bugs BEGIN SELECT RAISE(ABORT, 'coverage bugs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS coverage_reevaluations_no_update
BEFORE UPDATE ON coverage_reevaluations BEGIN SELECT RAISE(ABORT, 'coverage reevaluations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS coverage_reevaluations_no_delete
BEFORE DELETE ON coverage_reevaluations BEGIN SELECT RAISE(ABORT, 'coverage reevaluations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS coverage_resolution_receipts_no_update
BEFORE UPDATE ON coverage_resolution_receipts BEGIN SELECT RAISE(ABORT, 'coverage resolution receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS coverage_resolution_receipts_no_delete
BEFORE DELETE ON coverage_resolution_receipts BEGIN SELECT RAISE(ABORT, 'coverage resolution receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS coverage_bug_claim_bindings_no_update
BEFORE UPDATE ON coverage_bug_claim_bindings BEGIN SELECT RAISE(ABORT, 'coverage bug claim bindings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS coverage_bug_claim_bindings_no_delete
BEFORE DELETE ON coverage_bug_claim_bindings BEGIN SELECT RAISE(ABORT, 'coverage bug claim bindings are append-only'); END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("external experiment artifacts must be finite JSON values") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _text(name: str, value: Any, *, minimum: int = 1, maximum: int = 8_000) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{name} must contain between {minimum} and {maximum} characters")
    return value.strip()


def _object(name: str, value: Any, *, required: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict) or (required and not value):
        raise ValueError(f"{name} must be a nonempty JSON object")
    _stable(value)
    return value


def _rules(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ValueError("anti_rescue_rules must contain between 1 and 100 rules")
    result = []
    for rule in value:
        result.append(_text("anti_rescue_rule", rule, maximum=500))
    return result


def _inline_only_execution_spec(value: dict[str, Any]) -> tuple[dict[str, Any], ContainerExecutionSpec]:
    raw = dict(_object("execution_spec", value))
    for group in ("code_files", "input_files"):
        files = raw.get(group, [])
        if not isinstance(files, list):
            raise ValueError(f"{group} must be an array")
        for item in files:
            if not isinstance(item, dict) or item.get("text") is None or item.get("source") is not None:
                raise ValueError(
                    f"{group} accepts inline text only in the Agent/MCP v1 route; arbitrary server source paths are forbidden"
                )
    if raw.get("required_claims") or raw.get("claim_tests"):
        raise ValueError(
            "Agent/MCP external experiments v1 record scientific verification separately; required_claims and claim_tests are unavailable"
        )
    spec = ContainerExecutionSpec.from_dict(raw)
    if not spec.outputs:
        raise ValueError("execution_spec must declare at least one output obligation")
    # Round-trip through canonical JSON now, before the exact experiment hash is frozen.
    canonical = json.loads(_stable(raw))
    return canonical, spec


class ExternalExperimentService:
    """Freeze, approve, execute, and independently verify one exact experiment."""

    def __init__(
        self,
        db_path: str | Path,
        epistemic_db: str | Path,
        workspace: str | Path,
        research_service: Any,
    ):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.epistemic_db = Path(epistemic_db)
        self.workspace = Path(workspace)
        self.research_service = research_service
        self._lock = threading.RLock()
        self._ledger: EpistemicLedger | None = None
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        if self._ledger is not None:
            self._ledger.close()
        self.conn.close()

    def _runtime(self):
        if self._ledger is None:
            self._ledger = EpistemicLedger(self.epistemic_db)
            self._ledger.executions.workspace = self.workspace
            self.workspace.mkdir(parents=True, exist_ok=True)
        return self._ledger.executions

    def status(self) -> dict[str, Any]:
        engines = {name: shutil.which(name) for name in ("docker", "podman")}
        runtime = {
            "api_version": "1",
            "engines": {name: bool(path) for name, path in engines.items()},
            "engine_paths": engines,
            "network_policy": "disabled",
            "approval_policy": "human approval bound to exact experiment and manifest hashes",
            "workspace": str(self.workspace),
        }
        return {
            "mode": "frozen_deterministic_external_experiment",
            "runtime": runtime,
            "approval_phrase": EXTERNAL_EXPERIMENT_APPROVAL_PHRASE,
            "reproduction_approval_phrase": REPRODUCTION_APPROVAL_PHRASE,
            "coverage_reevaluation_approval_phrase": COVERAGE_REEVALUATION_APPROVAL_PHRASE,
            "staging_policy": "inline text only; arbitrary server file paths forbidden",
            "epistemic_boundary": (
                "A verified execution receipt establishes integrity and replay evidence, not scientific truth or novelty."
            ),
        }

    def freeze(
        self,
        *,
        case_id: str,
        plan_id: str,
        expected_plan_hash: str,
        scientific_question: str,
        claim_scope: dict[str, Any],
        execution_spec: dict[str, Any],
        verdict_schema: dict[str, Any],
        independent_verifier: dict[str, Any],
        falsification_coverage: dict[str, Any],
        anti_rescue_rules: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        case = self.research_service.store.get_case(case_id)
        plan = self.research_service.store.get_plan(plan_id)
        if plan["case_id"] != case["id"]:
            raise ValueError("The plan does not belong to the declared case")
        if plan["status"] != "approved":
            raise ValueError("The exact Orbita plan must be approved before freezing an external experiment")
        if plan["plan_hash"] != expected_plan_hash:
            raise ValueError("Plan hash mismatch; fetch and review the approved plan again")
        canonical_spec, _ = _inline_only_execution_spec(execution_spec)
        scope = _object("claim_scope", claim_scope)
        if not scope.get("domain") or not scope.get("quantifiers"):
            raise ValueError("claim_scope requires domain and quantifiers")
        coverage = _object("falsification_coverage", falsification_coverage)
        if "known_uncovered_regions" not in coverage:
            raise ValueError("falsification_coverage must explicitly declare known_uncovered_regions")
        verifier = _object("independent_verifier", independent_verifier)
        if verifier.get("required") is not True:
            raise ValueError("independent_verifier.required must be true")
        body = {
            "schema_version": "orbita-external-experiment/1",
            "case_id": case_id,
            "plan_id": plan_id,
            "plan_hash": plan["plan_hash"],
            "scientific_question": _text("scientific_question", scientific_question, minimum=12),
            "claim_scope": scope,
            "execution_spec": canonical_spec,
            "verdict_schema": _object("verdict_schema", verdict_schema),
            "independent_verifier": verifier,
            "falsification_coverage": coverage,
            "anti_rescue_rules": _rules(anti_rescue_rules),
            "created_by": _text("created_by", created_by, maximum=160),
        }
        experiment_id = _id("external_experiment")
        experiment_hash = _hash(body)
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO external_experiments
                       (id, case_id, plan_id, plan_hash, scientific_question, claim_scope_json,
                        execution_spec_json, verdict_schema_json, independent_verifier_json,
                        falsification_coverage_json, anti_rescue_rules_json, experiment_hash,
                        created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        experiment_id,
                        case_id,
                        plan_id,
                        plan["plan_hash"],
                        body["scientific_question"],
                        _stable(scope),
                        _stable(canonical_spec),
                        _stable(body["verdict_schema"]),
                        _stable(verifier),
                        _stable(coverage),
                        _stable(body["anti_rescue_rules"]),
                        experiment_hash,
                        body["created_by"],
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "experiment_hash" in str(exc):
                    raise ValueError("An identical frozen external experiment already exists") from exc
                raise
            self.conn.commit()
        return self.get(experiment_id)

    def submit(self, experiment_id: str, *, expected_experiment_hash: str, submitted_by: str) -> dict[str, Any]:
        experiment = self.get(experiment_id)
        if experiment["experiment_hash"] != expected_experiment_hash:
            raise ValueError("Experiment hash mismatch; fetch and review the frozen experiment again")
        if experiment["execution"] is not None:
            raise ValueError("This frozen experiment already has an execution; create a new lineage candidate to revise it")
        spec = ContainerExecutionSpec.from_dict(experiment["execution_spec"])
        submitted = self._runtime().submit(
            spec,
            actor=_text("submitted_by", submitted_by, maximum=160),
            actor_role=ActorRole.TOOL,
        )
        with self._lock:
            self.conn.execute(
                """INSERT INTO external_experiment_runs
                   (id, experiment_id, experiment_hash, execution_run_id, manifest_hash, submitted_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    _id("external_run_link"),
                    experiment_id,
                    experiment["experiment_hash"],
                    submitted["id"],
                    submitted["manifest_hash"],
                    submitted_by,
                    _now(),
                ),
            )
            self.conn.commit()
        return self.get(experiment_id)

    def approve(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_manifest_hash: str,
        reviewer: str,
        rationale: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != EXTERNAL_EXPERIMENT_APPROVAL_PHRASE:
            raise ValueError(f"confirmation must exactly equal: {EXTERNAL_EXPERIMENT_APPROVAL_PHRASE}")
        experiment, execution = self._exact_execution(
            experiment_id, expected_experiment_hash, expected_manifest_hash
        )
        self._runtime().approve(
            execution["id"],
            reviewer=_text("reviewer", reviewer, maximum=160),
            rationale=_text("rationale", rationale, minimum=12),
            actor_role=ActorRole.HUMAN,
        )
        return self.get(experiment["id"])

    def execute(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_manifest_hash: str,
        engine: OCIEngine | None = None,
    ) -> dict[str, Any]:
        experiment, execution = self._exact_execution(
            experiment_id, expected_experiment_hash, expected_manifest_hash
        )
        try:
            self._runtime().execute(execution["id"], engine=engine)
        except RuntimeError as exc:
            if "unavailable" not in str(exc).casefold():
                raise
            with self._lock:
                self.conn.execute(
                    """INSERT INTO external_experiment_failures
                       (id, experiment_id, failure_classification, detail, created_at)
                       VALUES (?, ?, 'EXECUTION_LIMIT', ?, ?)""",
                    (_id("external_failure"), experiment_id, str(exc), _now()),
                )
                self.conn.commit()
        return self.get(experiment["id"])

    def record_verification(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_execution_receipt_hash: str,
        verifier_receipt: dict[str, Any],
        conclusion: str,
        verified_by: str,
    ) -> dict[str, Any]:
        experiment = self.get(experiment_id)
        execution = experiment["execution"]
        if experiment["experiment_hash"] != expected_experiment_hash:
            raise ValueError("Experiment hash mismatch; fetch and review the frozen experiment again")
        if execution is None or execution["status"] != "succeeded":
            raise ValueError("Only a successful deterministic execution can receive independent verification")
        if execution["receipt_hash"] != expected_execution_receipt_hash:
            raise ValueError("Execution receipt hash mismatch; fetch and verify the execution again")
        if self._runtime().verify_receipt(execution["id"]) is not True:
            raise ValueError("Execution receipt integrity verification failed")
        if conclusion not in VERIFICATION_CONCLUSIONS:
            raise ValueError("conclusion must be supports, refutes, or inconclusive")
        receipt = _object("verifier_receipt", verifier_receipt)
        declared = experiment["independent_verifier"]
        declared_identity = declared.get("identity")
        if declared_identity and receipt.get("verifier_identity") != declared_identity:
            raise ValueError("Verifier identity does not match the frozen independent-verifier contract")
        receipt_hash = _hash(receipt)
        actor = _text("verified_by", verified_by, maximum=160)
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO external_experiment_verifications
                       (id, experiment_id, experiment_hash, execution_receipt_hash, verifier_json,
                        verifier_receipt_json, verifier_receipt_hash, conclusion, verified_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _id("external_verification"),
                        experiment_id,
                        experiment["experiment_hash"],
                        execution["receipt_hash"],
                        _stable(declared),
                        _stable(receipt),
                        receipt_hash,
                        conclusion,
                        actor,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Independent verification is immutable and already recorded") from exc
            self.conn.commit()
        return self.get(experiment_id)

    def prepare_reproduction(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_execution_receipt_hash: str,
        submitted_by: str,
    ) -> dict[str, Any]:
        experiment = self.get(experiment_id)
        original = experiment["execution"]
        if experiment["experiment_hash"] != expected_experiment_hash:
            raise ValueError("Experiment hash mismatch; fetch and review the frozen experiment again")
        if original is None or original["status"] != "succeeded":
            raise ValueError("Only a successful deterministic execution can be reproduced")
        if original["receipt_hash"] != expected_execution_receipt_hash:
            raise ValueError("Execution receipt hash mismatch; fetch and verify the execution again")
        if self._runtime().verify_receipt(original["id"]) is not True:
            raise ValueError("Original execution receipt integrity verification failed")
        if experiment["reproduction"] is not None:
            raise ValueError("This experiment already has a frozen reproduction")
        actor = _text("submitted_by", submitted_by, maximum=160)
        reproduction = self._runtime().prepare_reproduction(
            original["id"], actor=actor, actor_role=ActorRole.TOOL
        )
        with self._lock:
            self.conn.execute(
                """INSERT INTO external_experiment_reproductions
                   (id, experiment_id, experiment_hash, original_run_id, original_receipt_hash,
                    reproduction_run_id, reproduction_manifest_hash, submitted_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _id("external_reproduction"),
                    experiment_id,
                    experiment["experiment_hash"],
                    original["id"],
                    original["receipt_hash"],
                    reproduction["id"],
                    reproduction["manifest_hash"],
                    actor,
                    _now(),
                ),
            )
            self.conn.commit()
        return self.get(experiment_id)

    def approve_reproduction(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_original_receipt_hash: str,
        expected_reproduction_manifest_hash: str,
        reviewer: str,
        rationale: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != REPRODUCTION_APPROVAL_PHRASE:
            raise ValueError(f"confirmation must exactly equal: {REPRODUCTION_APPROVAL_PHRASE}")
        experiment, reproduction = self._exact_reproduction(
            experiment_id,
            expected_experiment_hash,
            expected_original_receipt_hash,
            expected_reproduction_manifest_hash,
        )
        self._runtime().approve(
            reproduction["id"],
            reviewer=_text("reviewer", reviewer, maximum=160),
            rationale=_text("rationale", rationale, minimum=12),
            actor_role=ActorRole.HUMAN,
        )
        return self.get(experiment["id"])

    def execute_reproduction(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_original_receipt_hash: str,
        expected_reproduction_manifest_hash: str,
        engine: OCIEngine | None = None,
    ) -> dict[str, Any]:
        experiment, reproduction = self._exact_reproduction(
            experiment_id,
            expected_experiment_hash,
            expected_original_receipt_hash,
            expected_reproduction_manifest_hash,
        )
        try:
            self._runtime().execute(reproduction["id"], engine=engine)
        except RuntimeError as exc:
            if "unavailable" not in str(exc).casefold():
                raise
            with self._lock:
                self.conn.execute(
                    """INSERT INTO external_experiment_failures
                       (id, experiment_id, failure_classification, detail, created_at)
                       VALUES (?, ?, 'REPRODUCTION_EXECUTION_LIMIT', ?, ?)""",
                    (_id("external_failure"), experiment_id, str(exc), _now()),
                )
                self.conn.commit()
        return self.get(experiment["id"])

    def record_coverage_bug(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        claim_effect: str,
        missed_counterexample: dict[str, Any],
        reason: str,
        fix: dict[str, Any],
        old_results_impacted: list[str],
        replacement_coverage: dict[str, Any],
        recorded_by: str,
        affected_claim_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        experiment = self.get(experiment_id)
        if experiment["experiment_hash"] != expected_experiment_hash:
            raise ValueError("Experiment hash mismatch; fetch and review the frozen experiment again")
        if claim_effect not in COVERAGE_BUG_EFFECTS:
            raise ValueError("claim_effect must be refutes_claim or challenges_coverage")
        counterexample = _object("missed_counterexample", missed_counterexample)
        if counterexample.get("validated") is not True:
            raise ValueError("missed_counterexample.validated must be true")
        receipt_hash = counterexample.get("validation_receipt_hash")
        if not isinstance(receipt_hash, str) or len(receipt_hash) != 64 or any(
            character not in "0123456789abcdef" for character in receipt_hash
        ):
            raise ValueError("missed_counterexample requires a lowercase 64-character validation_receipt_hash")
        _text("missed_counterexample.validated_by", counterexample.get("validated_by"), maximum=160)
        fix = _object("fix", fix)
        replacement_coverage = _object("replacement_coverage", replacement_coverage)
        if "known_uncovered_regions" not in replacement_coverage:
            raise ValueError("replacement_coverage must explicitly declare known_uncovered_regions")
        impacted = _rules(old_results_impacted)
        if len(set(impacted)) != len(impacted):
            raise ValueError("old_results_impacted must not contain duplicates")
        if affected_claim_ids is None:
            claim_ids: list[str] = []
        elif not isinstance(affected_claim_ids, list) or len(affected_claim_ids) > 100:
            raise ValueError("affected_claim_ids must be an array of at most 100 claim IDs")
        else:
            claim_ids = [
                _text("affected_claim_id", claim_id, maximum=160)
                for claim_id in affected_claim_ids
            ]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("affected_claim_ids must not contain duplicates")
        for claim_id in claim_ids:
            self.research_service.ledger._require_claim(claim_id)
        prior_rows = self.conn.execute(
            "SELECT COALESCE(MAX(protocol_version), 1) AS version FROM coverage_bugs WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        version = int(prior_rows["version"]) + 1
        replacement = {
            "schema_version": "orbita-coverage-protocol/1",
            "protocol_version": version,
            "supersedes": {
                "experiment_id": experiment_id,
                "experiment_hash": experiment["experiment_hash"],
                "protocol_version": version - 1,
            },
            "scientific_question": experiment["scientific_question"],
            "claim_scope": experiment["claim_scope"],
            "falsification_coverage": replacement_coverage,
            "execution_spec_hash": _hash(experiment["execution_spec"]),
            "anti_rescue_rules": experiment["anti_rescue_rules"],
            "change": fix,
        }
        replacement_hash = _hash(replacement)
        actor = _text("recorded_by", recorded_by, maximum=160)
        coverage_bug_id = _id("coverage_bug")
        with self._lock:
            self.conn.execute(
                """INSERT INTO coverage_bugs
                   (id, experiment_id, experiment_hash, protocol_version, claim_effect,
                    missed_counterexample_json, reason, fix_json, old_results_impacted_json,
                    replacement_protocol_json, replacement_protocol_hash, reevaluation_status,
                    recorded_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REQUIRED', ?, ?)""",
                (
                    coverage_bug_id,
                    experiment_id,
                    experiment["experiment_hash"],
                    version,
                    claim_effect,
                    _stable(counterexample),
                    _text("reason", reason, minimum=12),
                    _stable(fix),
                    _stable(impacted),
                    _stable(replacement),
                    replacement_hash,
                    actor,
                    _now(),
                ),
            )
            for claim_id in claim_ids:
                self.conn.execute(
                    """INSERT INTO coverage_bug_claim_bindings
                       (id, coverage_bug_id, claim_id, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (_id("coverage_claim_binding"), coverage_bug_id, claim_id, _now()),
                )
            self.conn.commit()
        result = self.get(experiment_id)
        if claim_ids:
            result["claim_propagation"] = self.propagate_coverage_bug_to_claims(
                coverage_bug_id,
                expected_replacement_protocol_hash=replacement_hash,
            )
        return result

    def propagate_coverage_bug_to_claims(
        self,
        coverage_bug_id: str,
        *,
        expected_replacement_protocol_hash: str,
    ) -> dict[str, Any]:
        """Idempotently project a validated external bug into Guided claim memory."""

        bug = self.get_coverage_bug(coverage_bug_id)
        if bug["replacement_protocol_hash"] != expected_replacement_protocol_hash:
            raise ValueError("Replacement protocol hash mismatch; fetch and review the coverage bug again")
        propagated = []
        for claim_id in bug["affected_claim_ids"]:
            propagated.append(
                self.research_service.memory.record_coverage_bug_impact(
                    claim_id,
                    coverage_bug_id=coverage_bug_id,
                    replacement_protocol_hash=bug["replacement_protocol_hash"],
                    claim_effect=bug["claim_effect"],
                    missed_counterexample=bug["missed_counterexample"],
                    replacement_coverage=bug["replacement_protocol"]["falsification_coverage"],
                    reason=bug["reason"],
                )
            )
        return {
            "coverage_bug_id": coverage_bug_id,
            "replacement_protocol_hash": bug["replacement_protocol_hash"],
            "affected_claim_ids": bug["affected_claim_ids"],
            "propagated": propagated,
            "idempotent": True,
        }

    def prepare_coverage_reevaluation(
        self,
        coverage_bug_id: str,
        *,
        expected_replacement_protocol_hash: str,
        execution_spec: dict[str, Any],
        resolution_targets: list[str],
        submitted_by: str,
    ) -> dict[str, Any]:
        bug = self.get_coverage_bug(coverage_bug_id)
        if bug["replacement_protocol_hash"] != expected_replacement_protocol_hash:
            raise ValueError("Replacement protocol hash mismatch; fetch and review the coverage bug again")
        if bug["reevaluation"] is not None:
            raise ValueError("This replacement protocol already has a frozen reevaluation")
        targets = _rules(resolution_targets)
        if len(set(targets)) != len(targets):
            raise ValueError("resolution_targets must not contain duplicates")
        if sorted(targets) != sorted(bug["old_results_impacted"]):
            raise ValueError("resolution_targets must exactly cover every old result listed as impacted")
        raw_spec = dict(_object("execution_spec", execution_spec))
        raw_spec["metadata"] = {
            **dict(raw_spec.get("metadata", {})),
            "coverage_bug_id": coverage_bug_id,
            "replacement_protocol_hash": bug["replacement_protocol_hash"],
        }
        canonical_spec, spec = _inline_only_execution_spec(raw_spec)
        spec_hash = _hash(canonical_spec)
        body = {
            "schema_version": "orbita-coverage-reevaluation/1",
            "coverage_bug_id": coverage_bug_id,
            "replacement_protocol_hash": bug["replacement_protocol_hash"],
            "execution_spec": canonical_spec,
            "execution_spec_hash": spec_hash,
            "resolution_targets": targets,
        }
        reevaluation_hash = _hash(body)
        actor = _text("submitted_by", submitted_by, maximum=160)
        submitted = self._runtime().submit(spec, actor=actor, actor_role=ActorRole.TOOL)
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO coverage_reevaluations
                       (id, coverage_bug_id, replacement_protocol_hash, execution_spec_json,
                        execution_spec_hash, resolution_targets_json, reevaluation_hash,
                        execution_run_id, execution_manifest_hash, submitted_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _id("coverage_reevaluation"),
                        coverage_bug_id,
                        bug["replacement_protocol_hash"],
                        _stable(canonical_spec),
                        spec_hash,
                        _stable(targets),
                        reevaluation_hash,
                        submitted["id"],
                        submitted["manifest_hash"],
                        actor,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This replacement protocol already has a frozen reevaluation") from exc
            self.conn.commit()
        return self.get_coverage_bug(coverage_bug_id)

    def approve_coverage_reevaluation(
        self,
        coverage_bug_id: str,
        *,
        expected_replacement_protocol_hash: str,
        expected_reevaluation_hash: str,
        expected_execution_manifest_hash: str,
        reviewer: str,
        rationale: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != COVERAGE_REEVALUATION_APPROVAL_PHRASE:
            raise ValueError(f"confirmation must exactly equal: {COVERAGE_REEVALUATION_APPROVAL_PHRASE}")
        bug, reevaluation = self._exact_coverage_reevaluation(
            coverage_bug_id,
            expected_replacement_protocol_hash,
            expected_reevaluation_hash,
            expected_execution_manifest_hash,
        )
        self._runtime().approve(
            reevaluation["execution"]["id"],
            reviewer=_text("reviewer", reviewer, maximum=160),
            rationale=_text("rationale", rationale, minimum=12),
            actor_role=ActorRole.HUMAN,
        )
        return self.get_coverage_bug(bug["id"])

    def execute_coverage_reevaluation(
        self,
        coverage_bug_id: str,
        *,
        expected_replacement_protocol_hash: str,
        expected_reevaluation_hash: str,
        expected_execution_manifest_hash: str,
        engine: OCIEngine | None = None,
    ) -> dict[str, Any]:
        bug, reevaluation = self._exact_coverage_reevaluation(
            coverage_bug_id,
            expected_replacement_protocol_hash,
            expected_reevaluation_hash,
            expected_execution_manifest_hash,
        )
        try:
            self._runtime().execute(reevaluation["execution"]["id"], engine=engine)
        except RuntimeError as exc:
            if "unavailable" not in str(exc).casefold():
                raise
            experiment_id = bug["experiment_id"]
            with self._lock:
                self.conn.execute(
                    """INSERT INTO external_experiment_failures
                       (id, experiment_id, failure_classification, detail, created_at)
                       VALUES (?, ?, 'REEVALUATION_EXECUTION_LIMIT', ?, ?)""",
                    (_id("external_failure"), experiment_id, str(exc), _now()),
                )
                self.conn.commit()
        return self.get_coverage_bug(bug["id"])

    def record_coverage_resolutions(
        self,
        coverage_bug_id: str,
        *,
        expected_reevaluation_hash: str,
        expected_execution_receipt_hash: str,
        resolutions: list[dict[str, Any]],
        recorded_by: str,
    ) -> dict[str, Any]:
        bug = self.get_coverage_bug(coverage_bug_id)
        reevaluation = bug["reevaluation"]
        if reevaluation is None or reevaluation["reevaluation_hash"] != expected_reevaluation_hash:
            raise ValueError("Reevaluation hash mismatch; fetch and review the frozen reevaluation again")
        execution = reevaluation["execution"]
        if execution["status"] != "succeeded":
            raise ValueError("Coverage resolutions require a successful replacement-protocol execution")
        if execution["receipt_hash"] != expected_execution_receipt_hash:
            raise ValueError("Reevaluation receipt hash mismatch; fetch and verify the execution again")
        if self._runtime().verify_receipt(execution["id"]) is not True:
            raise ValueError("Reevaluation execution receipt integrity verification failed")
        if not isinstance(resolutions, list) or not resolutions:
            raise ValueError("resolutions must be a nonempty list")
        normalized = []
        seen = set()
        for resolution in resolutions:
            item = _object("resolution", resolution)
            result_id = _text("affected_result_id", item.get("affected_result_id"), maximum=500)
            if result_id in seen:
                raise ValueError("Each affected result must appear exactly once in resolutions")
            seen.add(result_id)
            disposition = item.get("disposition")
            if disposition not in RESOLUTION_DISPOSITIONS:
                raise ValueError("resolution disposition must be refuted, challenged, unchanged, or superseded")
            evidence_hash = item.get("evidence_receipt_hash")
            if not isinstance(evidence_hash, str) or len(evidence_hash) != 64 or any(
                character not in "0123456789abcdef" for character in evidence_hash
            ):
                raise ValueError("Each resolution requires a lowercase 64-character evidence_receipt_hash")
            if evidence_hash != execution["receipt_hash"]:
                raise ValueError("Each resolution must reference the exact replacement execution receipt in v1")
            normalized.append(
                {
                    "affected_result_id": result_id,
                    "disposition": disposition,
                    "rationale": _text("resolution rationale", item.get("rationale"), minimum=12),
                    "evidence_receipt_hash": evidence_hash,
                }
            )
        if sorted(seen) != sorted(bug["old_results_impacted"]):
            raise ValueError("resolutions must account for every affected result exactly once")
        resolutions_hash = _hash(normalized)
        actor = _text("recorded_by", recorded_by, maximum=160)
        with self._lock:
            try:
                self.conn.execute(
                    """INSERT INTO coverage_resolution_receipts
                       (id, coverage_bug_id, reevaluation_hash, execution_receipt_hash,
                        resolutions_json, resolutions_hash, recorded_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _id("coverage_resolution"),
                        coverage_bug_id,
                        reevaluation["reevaluation_hash"],
                        execution["receipt_hash"],
                        _stable(normalized),
                        resolutions_hash,
                        actor,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Coverage resolution receipt is immutable and already recorded") from exc
            self.conn.commit()
        return self.get_coverage_bug(coverage_bug_id)

    def get_coverage_bug(self, coverage_bug_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM coverage_bugs WHERE id = ?", (coverage_bug_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown coverage bug: {coverage_bug_id}")
        return self._coverage_bug_row(row)

    def get(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM external_experiments WHERE id = ?", (experiment_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown external experiment: {experiment_id}")
            item = self._experiment_row(row)
            link = self.conn.execute(
                "SELECT * FROM external_experiment_runs WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
            verification = self.conn.execute(
                "SELECT * FROM external_experiment_verifications WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
            failures = [
                dict(failure)
                for failure in self.conn.execute(
                    "SELECT * FROM external_experiment_failures WHERE experiment_id = ? ORDER BY created_at, id",
                    (experiment_id,),
                ).fetchall()
            ]
            reproduction_link = self.conn.execute(
                "SELECT * FROM external_experiment_reproductions WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
            coverage_bugs = [
                self._coverage_bug_row(bug)
                for bug in self.conn.execute(
                    "SELECT * FROM coverage_bugs WHERE experiment_id = ? ORDER BY protocol_version",
                    (experiment_id,),
                ).fetchall()
            ]
        execution = self._runtime().get(link["execution_run_id"]) if link else None
        reproduction = self._runtime().get(reproduction_link["reproduction_run_id"]) if reproduction_link else None
        item["execution"] = execution
        item["reproduction"] = reproduction
        item["verification"] = self._verification_row(verification) if verification else None
        item["execution_failures"] = failures
        item["coverage_bugs"] = coverage_bugs
        item["reevaluation_required"] = any(
            bug["current_reevaluation_status"] != "RESOLVED" for bug in coverage_bugs
        )
        item.update(self._classify(execution, reproduction, item["verification"], failures, coverage_bugs))
        return item

    def list(self, *, limit: int = 25) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        rows = self.conn.execute(
            "SELECT id FROM external_experiments ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def _exact_execution(
        self, experiment_id: str, expected_experiment_hash: str, expected_manifest_hash: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        experiment = self.get(experiment_id)
        execution = experiment["execution"]
        if experiment["experiment_hash"] != expected_experiment_hash:
            raise ValueError("Experiment hash mismatch; fetch and review the frozen experiment again")
        if execution is None:
            raise ValueError("The frozen experiment has not been submitted for execution")
        if execution["manifest_hash"] != expected_manifest_hash:
            raise ValueError("Execution manifest hash mismatch; fetch and review the staged execution again")
        return experiment, execution

    def _exact_reproduction(
        self,
        experiment_id: str,
        expected_experiment_hash: str,
        expected_original_receipt_hash: str,
        expected_reproduction_manifest_hash: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        experiment = self.get(experiment_id)
        original = experiment["execution"]
        reproduction = experiment["reproduction"]
        if experiment["experiment_hash"] != expected_experiment_hash:
            raise ValueError("Experiment hash mismatch; fetch and review the frozen experiment again")
        if original is None or original["receipt_hash"] != expected_original_receipt_hash:
            raise ValueError("Original execution receipt hash mismatch; fetch and review the execution again")
        if reproduction is None:
            raise ValueError("No reproduction has been prepared")
        if reproduction["manifest_hash"] != expected_reproduction_manifest_hash:
            raise ValueError("Reproduction manifest hash mismatch; fetch and review the reproduction again")
        return experiment, reproduction

    def _exact_coverage_reevaluation(
        self,
        coverage_bug_id: str,
        expected_replacement_protocol_hash: str,
        expected_reevaluation_hash: str,
        expected_execution_manifest_hash: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        bug = self.get_coverage_bug(coverage_bug_id)
        reevaluation = bug["reevaluation"]
        if bug["replacement_protocol_hash"] != expected_replacement_protocol_hash:
            raise ValueError("Replacement protocol hash mismatch; fetch and review the coverage bug again")
        if reevaluation is None or reevaluation["reevaluation_hash"] != expected_reevaluation_hash:
            raise ValueError("Reevaluation hash mismatch; fetch and review the frozen reevaluation again")
        if reevaluation["execution_manifest_hash"] != expected_execution_manifest_hash:
            raise ValueError("Reevaluation execution manifest hash mismatch; review the staged execution again")
        return bug, reevaluation

    @staticmethod
    def _classify(
        execution: dict[str, Any] | None,
        reproduction: dict[str, Any] | None,
        verification: dict[str, Any] | None,
        failures: list[dict[str, Any]],
        coverage_bugs: list[dict[str, Any]],
    ) -> dict[str, str]:
        if execution is None:
            execution_class = "NOT_SUBMITTED"
            integrity = "NOT_TESTED"
        elif execution["status"] in {"waiting_approval", "approved", "running"}:
            execution_class = "PENDING_EXECUTION"
            integrity = "STAGED_VERIFIED" if execution["manifest_integrity_valid"] and execution["artifact_integrity_valid"] else "FAILED"
        elif execution["status"] == "succeeded":
            execution_class = "EXECUTED"
            integrity = "VERIFIED" if all(
                (
                    execution["manifest_integrity_valid"],
                    execution["artifact_integrity_valid"],
                    execution["receipt_integrity_valid"],
                )
            ) else "FAILED"
        elif execution.get("timed_out"):
            execution_class = "EXECUTION_TIMEOUT"
            integrity = "RECEIPT_VERIFIED" if execution["receipt_integrity_valid"] else "FAILED"
        else:
            execution_class = "EXECUTION_FAILURE"
            integrity = "RECEIPT_VERIFIED" if execution.get("receipt_integrity_valid") else "FAILED"
        if failures and (execution is None or execution["status"] not in {"succeeded", "failed"}):
            execution_class = failures[-1]["failure_classification"]

        if any(bug["claim_effect"] == "refutes_claim" for bug in coverage_bugs):
            epistemic = "REFUTED"
        elif coverage_bugs:
            epistemic = "CHALLENGED"
        elif verification is None:
            epistemic = "UNVERIFIED"
        elif verification["conclusion"] == "refutes":
            epistemic = "REFUTED"
        elif verification["conclusion"] == "supports":
            epistemic = "EMPIRICAL_SURVIVOR"
        else:
            epistemic = "UNRESOLVED"
        if reproduction is None:
            bitwise = "NOT_TESTED"
        elif reproduction["status"] == "succeeded":
            bitwise = "VERIFIED" if reproduction.get("comparison", {}).get("outputs_match") is True else "FAILED"
        elif reproduction["status"] == "failed":
            bitwise = "FAILED"
        else:
            bitwise = "PENDING"
        if bitwise == "VERIFIED":
            scientific_reproduction = "NOT_ESTABLISHED_BY_TECHNICAL_REPLAY"
        elif bitwise == "FAILED":
            scientific_reproduction = "TECHNICAL_REPRODUCTION_FAILED"
        else:
            scientific_reproduction = "NOT_TESTED"
        return {
            "execution_classification": execution_class,
            "integrity_status": integrity,
            "bitwise_reproducibility_status": bitwise,
            "scientific_reproducibility_status": scientific_reproduction,
            "epistemic_status": epistemic,
        }

    @staticmethod
    def _experiment_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("claim_scope_json", "claim_scope"),
            ("execution_spec_json", "execution_spec"),
            ("verdict_schema_json", "verdict_schema"),
            ("independent_verifier_json", "independent_verifier"),
            ("falsification_coverage_json", "falsification_coverage"),
            ("anti_rescue_rules_json", "anti_rescue_rules"),
        ):
            item[target] = json.loads(item.pop(source))
        return item

    @staticmethod
    def _verification_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["verifier"] = json.loads(item.pop("verifier_json"))
        item["verifier_receipt"] = json.loads(item.pop("verifier_receipt_json"))
        return item

    def _coverage_bug_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("missed_counterexample_json", "missed_counterexample"),
            ("fix_json", "fix"),
            ("old_results_impacted_json", "old_results_impacted"),
            ("replacement_protocol_json", "replacement_protocol"),
        ):
            item[target] = json.loads(item.pop(source))
        reevaluation_row = self.conn.execute(
            "SELECT * FROM coverage_reevaluations WHERE coverage_bug_id = ?", (item["id"],)
        ).fetchone()
        resolution_row = self.conn.execute(
            "SELECT * FROM coverage_resolution_receipts WHERE coverage_bug_id = ?", (item["id"],)
        ).fetchone()
        reevaluation = self._coverage_reevaluation_row(reevaluation_row) if reevaluation_row else None
        if reevaluation is not None:
            reevaluation["execution"] = self._runtime().get(reevaluation["execution_run_id"])
        resolution = self._coverage_resolution_row(resolution_row) if resolution_row else None
        binding_rows = self.conn.execute(
            """SELECT claim_id FROM coverage_bug_claim_bindings
               WHERE coverage_bug_id = ? ORDER BY created_at, id""",
            (item["id"],),
        ).fetchall()
        item["affected_claim_ids"] = [binding["claim_id"] for binding in binding_rows]
        item["reevaluation"] = reevaluation
        item["resolution_receipt"] = resolution
        if resolution is not None:
            current_status = "RESOLVED"
        elif reevaluation is None:
            current_status = "REQUIRED"
        elif reevaluation["execution"]["status"] == "succeeded":
            current_status = "AWAITING_RESOLUTION"
        elif reevaluation["execution"]["status"] == "failed":
            current_status = "FAILED"
        else:
            current_status = "IN_PROGRESS"
        item["current_reevaluation_status"] = current_status
        return item

    @staticmethod
    def _coverage_reevaluation_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["execution_spec"] = json.loads(item.pop("execution_spec_json"))
        item["resolution_targets"] = json.loads(item.pop("resolution_targets_json"))
        return item

    @staticmethod
    def _coverage_resolution_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["resolutions"] = json.loads(item.pop("resolutions_json"))
        return item
