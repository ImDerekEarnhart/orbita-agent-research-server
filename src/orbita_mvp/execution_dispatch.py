"""Frozen candidate-kind to executor compatibility contracts.

Plans are bound to an exact executor contract before they can be approved.
The registry never guesses, coerces one candidate kind into another, or grants
execution authority.  It only proves that the declared candidate semantics have
an installed execution path with a frozen contract.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orbita.execution import ContainerExecutionSpec
from orbita_discovery.core import CandidateNotScorable

BINDING_SCHEMA = "orbita-executor-binding/1"
CONTRACT_SCHEMA = "orbita-executor-contract/1"


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("executor contracts must contain finite JSON values") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _source_hash(*relative_paths: str) -> str:
    source_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative in sorted(relative_paths):
        path = source_root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class ExecutionCapabilityLimit(CandidateNotScorable):
    """Typed, pre-approval statement that no compatible executor is grounded."""

    def __init__(
        self,
        message: str,
        *,
        classification: str = "ENGINE_CAPABILITY_LIMIT",
        candidate_kinds: set[Any] | None = None,
        required_executor: str | None = None,
        available_executor: str | None = None,
    ):
        self.classification = classification
        self.candidate_kinds = sorted(str(item) for item in (candidate_kinds or set()))
        self.required_executor = required_executor
        self.available_executor = available_executor
        super().__init__(f"{classification}: {message}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "candidate_kinds": self.candidate_kinds,
            "required_executor": self.required_executor,
            "available_executor": self.available_executor,
            "message": str(self),
        }


@dataclass(frozen=True)
class ExecutorContract:
    executor_id: str
    primary_candidate_kinds: frozenset[str]
    companion_candidate_kinds: frozenset[str]
    supported_input_artifacts: frozenset[str]
    required_plan_fields: tuple[str, ...]
    supported_protocols: tuple[str, ...]
    output_schema: str
    verification_mode: str
    network_policy: str
    determinism: str
    implementation_hash: str
    verifier_hash: str
    resource_limits: dict[str, Any]

    def accepts(self, kinds: set[str]) -> bool:
        primary = kinds & self.primary_candidate_kinds
        return bool(primary) and kinds <= self.primary_candidate_kinds | self.companion_candidate_kinds

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": CONTRACT_SCHEMA,
            "executor_id": self.executor_id,
            "supported_candidate_kinds": sorted(self.primary_candidate_kinds),
            "companion_candidate_kinds": sorted(self.companion_candidate_kinds),
            "supported_input_artifacts": sorted(self.supported_input_artifacts),
            "required_plan_fields": list(self.required_plan_fields),
            "supported_protocols": list(self.supported_protocols),
            "output_schema": self.output_schema,
            "verification_mode": self.verification_mode,
            "network_policy": self.network_policy,
            "determinism": self.determinism,
            "implementation_hash": self.implementation_hash,
            "verifier_hash": self.verifier_hash,
            "resource_limits": self.resource_limits,
        }

    def record(self) -> dict[str, Any]:
        body = self.body()
        return body | {"executor_contract_hash": content_hash(body), "available": True}


def installed_contracts() -> tuple[ExecutorContract, ...]:
    return (
        ExecutorContract(
            executor_id="tabular-statistical/1",
            primary_candidate_kinds=frozenset({"linear_association", "group_difference"}),
            companion_candidate_kinds=frozenset(),
            supported_input_artifacts=frozenset({"table"}),
            required_plan_fields=("selected_dataset", "candidates", "thresholds"),
            supported_protocols=("orbita-research-plan/0.1",),
            output_schema="orbita-case-run/tabular-1",
            verification_mode="baseline-heldout-cross-seed-falsification",
            network_policy="disabled",
            determinism="seeded",
            implementation_hash=_source_hash("orbita_mvp/service.py", "orbita_mvp/table_domain.py"),
            verifier_hash=_source_hash("orbita_discovery/falsifiers.py", "orbita_discovery/judges.py"),
            resource_limits={"max_candidates": 200, "external_network": False},
        ),
        ExecutorContract(
            executor_id="prospective-blind-calibration/1",
            primary_candidate_kinds=frozenset({"prospective_blind_calibration"}),
            companion_candidate_kinds=frozenset(
                {"safety_falsification", "epistemic_falsification", "protocol_compliance"}
            ),
            supported_input_artifacts=frozenset({"table"}),
            required_plan_fields=("selected_dataset", "candidates", "thresholds"),
            supported_protocols=("orbita-research-plan/0.1",),
            output_schema="orbita-blind-calibration/1",
            verification_mode="prediction-before-reveal",
            network_policy="disabled-during-freeze",
            determinism="deterministic-executor-or-external-frozen-submission",
            implementation_hash=_source_hash("orbita_agent/blind_calibration.py"),
            verifier_hash=_source_hash("orbita_agent/blind_calibration.py"),
            resource_limits={"max_rows": 10_000, "scoring_access_during_freeze": False},
        ),
        ExecutorContract(
            executor_id="structured-research-operator/1",
            primary_candidate_kinds=frozenset({"research_operator", "external_experiment"}),
            companion_candidate_kinds=frozenset(
                {"safety_falsification", "epistemic_falsification", "protocol_compliance"}
            ),
            supported_input_artifacts=frozenset({"table", "text"}),
            required_plan_fields=("selected_dataset", "candidates", "thresholds"),
            supported_protocols=("orbita-research-plan/0.1",),
            output_schema="orbita-external-experiment/1",
            verification_mode="separate-independent-verifier-required",
            network_policy="disabled",
            determinism="frozen-oci-contract",
            implementation_hash=_source_hash("orbita_agent/external_experiments.py"),
            verifier_hash=_source_hash("orbita_agent/external_experiments.py", "orbita/execution.py"),
            resource_limits={"operators_per_plan": 1, "inline_inputs_only": True, "external_network": False},
        ),
    )


class ExecutorRegistry:
    def __init__(self, contracts: tuple[ExecutorContract, ...] | None = None):
        self.contracts = contracts or installed_contracts()

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": "orbita-executor-registry/1",
            "contracts": [contract.record() for contract in self.contracts],
            "routing_policy": "exact-compatible-contract-only",
            "coercion_enabled": False,
            "unavailable_known_kinds": [
                "formal_theorem", "graph_candidate", "language_primitive", "execution_adapter"
            ],
        }

    def resolve(self, kinds: set[str]) -> ExecutorContract:
        if not kinds or any(not isinstance(kind, str) or not kind.strip() for kind in kinds):
            raise ExecutionCapabilityLimit("candidate kinds must be explicit nonblank strings", candidate_kinds=kinds)
        matches = [contract for contract in self.contracts if contract.accepts(kinds)]
        if len(matches) != 1:
            required = ",".join(sorted(kinds))
            detail = "no registered compatible executor" if not matches else "ambiguous executor contracts"
            raise ExecutionCapabilityLimit(
                f"{detail} for candidate kind set {required!r}; no fallback or semantic coercion is permitted",
                candidate_kinds=kinds,
                required_executor=required,
                available_executor=None,
            )
        return matches[0]

    def bind_plan(self, plan: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
        plan = json.loads(_stable(plan))
        candidates = plan.get("candidates") or []
        if not candidates:
            return plan
        kinds = {candidate.get("kind") for candidate in candidates}
        contract = self.resolve(kinds)
        self._validate_contract(plan, case, contract, kinds)
        selected = plan["selected_dataset"]
        source = self._selected_file(case, selected.get("file_id"))
        record = contract.record()
        binding = {
            "schema_version": BINDING_SCHEMA,
            "executor_id": contract.executor_id,
            "executor_contract_hash": record["executor_contract_hash"],
            "candidate_kinds": sorted(kinds),
            "input_artifact": {
                "file_id": source["id"],
                "sha256": source["sha256"],
                "artifact_kind": source["artifact_kind"],
            },
            "protocol": plan.get("schema_version"),
        }
        plan["execution_binding"] = binding | {"binding_hash": content_hash(binding)}
        return plan

    def validate_bound_plan(self, plan: dict[str, Any], case: dict[str, Any]) -> ExecutorContract:
        candidates = plan.get("candidates") or []
        kinds = {candidate.get("kind") for candidate in candidates}
        contract = self.resolve(kinds)
        binding = plan.get("execution_binding")
        if not isinstance(binding, dict):
            raise ExecutionCapabilityLimit(
                "approved plan has no frozen executor binding; resubmit and approve a newly compiled plan",
                candidate_kinds=kinds,
                required_executor=contract.executor_id,
                available_executor=contract.executor_id,
            )
        rebound = self.bind_plan({key: value for key, value in plan.items() if key != "execution_binding"}, case)
        if rebound.get("execution_binding") != binding:
            raise ExecutionCapabilityLimit(
                "frozen executor binding no longer matches the installed implementation; recompile and reapprove",
                candidate_kinds=kinds,
                required_executor=binding.get("executor_id"),
                available_executor=contract.executor_id,
            )
        return contract

    @staticmethod
    def _selected_file(case: dict[str, Any], file_id: Any) -> dict[str, Any]:
        for item in case.get("files", []):
            if item.get("id") == file_id:
                return item
        raise ValueError("selected_dataset.file_id does not belong to this case")

    def _validate_contract(
        self, plan: dict[str, Any], case: dict[str, Any], contract: ExecutorContract, kinds: set[str]
    ) -> None:
        missing = [field for field in contract.required_plan_fields if field not in plan]
        if missing:
            raise ExecutionCapabilityLimit(
                "executor-required plan fields are missing: " + ", ".join(missing),
                candidate_kinds=kinds,
                required_executor=contract.executor_id,
                available_executor=contract.executor_id,
            )
        if plan.get("schema_version") not in contract.supported_protocols:
            raise ExecutionCapabilityLimit(
                f"executor does not support plan protocol {plan.get('schema_version')!r}",
                candidate_kinds=kinds,
                required_executor=contract.executor_id,
                available_executor=contract.executor_id,
            )
        source = self._selected_file(case, plan["selected_dataset"].get("file_id"))
        if source.get("artifact_kind") not in contract.supported_input_artifacts:
            raise ExecutionCapabilityLimit(
                f"executor does not accept input artifact kind {source.get('artifact_kind')!r}",
                candidate_kinds=kinds,
                required_executor=contract.executor_id,
                available_executor=contract.executor_id,
            )
        if contract.executor_id == "tabular-statistical/1":
            columns = {
                item.get("name") for item in source.get("profile", {}).get("column_profiles", [])
                if isinstance(item, dict)
            }
            for candidate in plan["candidates"]:
                required_fields = (
                    ("predictor", "outcome")
                    if candidate.get("kind") == "linear_association"
                    else ("group", "outcome")
                )
                missing_candidate = [
                    field for field in required_fields
                    if not isinstance(candidate.get(field), str) or not candidate[field].strip()
                ]
                if missing_candidate:
                    raise ExecutionCapabilityLimit(
                        f"{candidate.get('kind')} candidate is missing executor fields: "
                        + ", ".join(missing_candidate),
                        candidate_kinds=kinds,
                        required_executor=contract.executor_id,
                        available_executor=None,
                    )
                unknown_columns = sorted({candidate[field] for field in required_fields} - columns)
                if unknown_columns:
                    raise ExecutionCapabilityLimit(
                        "statistical candidate cites columns absent from the frozen input: "
                        + ", ".join(unknown_columns),
                        candidate_kinds=kinds,
                        required_executor=contract.executor_id,
                        available_executor=None,
                    )
        if contract.executor_id == "structured-research-operator/1":
            primary = [item for item in plan["candidates"] if item.get("kind") in contract.primary_candidate_kinds]
            if len(primary) != 1:
                raise ExecutionCapabilityLimit(
                    "structured research execution requires exactly one primary research operator per plan",
                    candidate_kinds=kinds,
                    required_executor=contract.executor_id,
                    available_executor=contract.executor_id,
                )
            execution = primary[0].get("execution_contract")
            required = {
                "scientific_question", "claim_scope", "execution_spec", "verdict_schema",
                "independent_verifier", "falsification_coverage", "anti_rescue_rules",
            }
            missing_execution = sorted(required - set(execution or {})) if isinstance(execution, dict) else sorted(required)
            if missing_execution:
                raise ExecutionCapabilityLimit(
                    "research_operator has no executable realization; execution_contract is missing: "
                    + ", ".join(missing_execution),
                    candidate_kinds=kinds,
                    required_executor=contract.executor_id,
                    available_executor=None,
                )
            if execution.get("network_policy", "disabled") != "disabled":
                raise ExecutionCapabilityLimit(
                    "research_operator execution_contract must freeze network_policy=disabled",
                    candidate_kinds=kinds,
                    required_executor=contract.executor_id,
                    available_executor=None,
                )
            self._validate_research_execution(execution, kinds, contract.executor_id)

    @staticmethod
    def _validate_research_execution(execution: dict[str, Any], kinds: set[str], executor_id: str) -> None:
        def limit(message: str) -> None:
            raise ExecutionCapabilityLimit(
                message,
                candidate_kinds=kinds,
                required_executor=executor_id,
                available_executor=None,
            )

        question = execution.get("scientific_question")
        if not isinstance(question, str) or len(question.strip()) < 12:
            limit("research_operator scientific_question is not executable and reviewable")
        scope = execution.get("claim_scope")
        if not isinstance(scope, dict) or not scope.get("domain") or not scope.get("quantifiers"):
            limit("research_operator claim_scope requires domain and quantifiers")
        verifier = execution.get("independent_verifier")
        if not isinstance(verifier, dict) or verifier.get("required") is not True:
            limit("research_operator requires an independent verifier contract")
        coverage = execution.get("falsification_coverage")
        if not isinstance(coverage, dict) or "known_uncovered_regions" not in coverage:
            limit("research_operator falsification_coverage must declare known_uncovered_regions")
        rules = execution.get("anti_rescue_rules")
        if not isinstance(rules, list) or not rules or any(not isinstance(item, str) or not item.strip() for item in rules):
            limit("research_operator requires nonblank anti_rescue_rules")
        verdict = execution.get("verdict_schema")
        if not isinstance(verdict, dict) or not verdict:
            limit("research_operator verdict_schema must be a nonempty object")
        created_by = execution.get("created_by")
        if created_by is not None and (not isinstance(created_by, str) or not created_by.strip() or len(created_by) > 160):
            limit("research_operator created_by must be a nonblank string of at most 160 characters")
        raw_spec = execution.get("execution_spec")
        if not isinstance(raw_spec, dict):
            limit("research_operator execution_spec must be an object")
        for group in ("code_files", "input_files"):
            files = raw_spec.get(group, [])
            if not isinstance(files, list):
                limit(f"research_operator execution_spec.{group} must be an array")
            if any(not isinstance(item, dict) or item.get("text") is None or item.get("source") is not None for item in files):
                limit(f"research_operator execution_spec.{group} accepts inline text only")
        if raw_spec.get("required_claims") or raw_spec.get("claim_tests"):
            limit("research_operator scientific verification must remain separate from deterministic execution")
        try:
            spec = ContainerExecutionSpec.from_dict(raw_spec)
        except (TypeError, ValueError) as exc:
            limit(f"research_operator execution_spec is invalid: {exc}")
        if not spec.outputs:
            limit("research_operator execution_spec must declare at least one output")


DEFAULT_EXECUTOR_REGISTRY = ExecutorRegistry()
