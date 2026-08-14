from __future__ import annotations

import re
import shutil
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd

from orbita import guard_scope_escalation
from orbita_mvp import ResearchMVP
from orbita_mvp.execution_dispatch import DEFAULT_EXECUTOR_REGISTRY

from . import __version__
from .archive_policy import ArchivePolicy
from .blind_calibration import PREDICTION_KIND, BlindCalibrationService
from .candidate_execution import CandidateExecutionLedger
from .candidate_execution import content_hash as candidate_result_hash
from .config import AgentConfig
from .external_experiments import ExternalExperimentService
from .general_problem_loop import GeneralProblemLoopService
from .graph_adapter import analyze_graph, export_lean_certificate
from .improvement import PROMOTION_PHRASE, ROLLBACK_PHRASE, ImprovementLab
from .improvement_registry import ImprovementRegistry
from .knowledge import KnowledgeStore
from .memory_index import MemoryIndex, chat_export_members
from .object_store import build_object_store, object_key
from .reversals import find_candidate_reversals

APPROVAL_PHRASE = "I reviewed this exact frozen plan"
DELETION_PHRASE = "I permanently delete this case and its files"
INLINE_SUFFIXES = {".csv", ".tsv", ".json", ".jsonl", ".txt", ".md", ".py", ".r", ".tex", ".ipynb"}


def _safe_filename(filename: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).name).strip("._")
    if not name:
        raise ValueError("filename must contain at least one safe character")
    if len(name) > 120:
        stem, suffix = Path(name).stem[:100], Path(name).suffix[:16]
        name = stem + suffix
    if Path(name).suffix.lower() not in INLINE_SUFFIXES:
        allowed = ", ".join(sorted(INLINE_SUFFIXES))
        raise ValueError(f"Inline agent upload type is not allowed. Use one of: {allowed}")
    return name


class AgentGateway:
    """Narrow, agent-safe facade over Orbita's governed research core."""

    def __init__(self, config: AgentConfig | None = None):
        self.config = (config or AgentConfig.from_env()).ensure()
        self.service = ResearchMVP(self.config.db_path, self.config.workspace)
        self.knowledge = KnowledgeStore(self.config.knowledge_db)
        self.improvements = ImprovementLab(self.config.improvement_db, self.service)
        self.improvement_registry = ImprovementRegistry(self.config.improvement_db)
        self.external_experiments = ExternalExperimentService(
            self.config.external_experiment_db,
            self.config.epistemic_db,
            self.config.execution_workspace,
            self.service,
        )
        self.blind_calibration = BlindCalibrationService(
            self.config.blind_prediction_db,
            self.config.blind_scoring_db,
            self.service,
        )
        self.general_problem_loops = GeneralProblemLoopService(self.config.general_problem_loop_db)
        self.candidate_executions = CandidateExecutionLedger(self.config.candidate_execution_db)
        self.memory = MemoryIndex(self.config.memory_db)
        self.objects = build_object_store(self.config.home / "objects")
        self._lock = threading.RLock()
        self._children: list[AgentGateway] = []

    def for_tenant(self, tenant: str) -> AgentGateway:
        """Open a gateway confined to one tenant, closed when this one closes."""
        child = AgentGateway(self.config.for_tenant(tenant))
        with self._lock:
            self._children.append(child)
        return child

    def close(self) -> None:
        with self._lock:
            children, self._children = self._children, []
        for child in children:
            child.close()
        self.memory.close()
        self.candidate_executions.close()
        self.general_problem_loops.close()
        self.blind_calibration.close()
        self.external_experiments.close()
        self.improvement_registry.close()
        self.improvements.close()
        self.knowledge.close()
        self.service.close()

    def __enter__(self) -> AgentGateway:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def capabilities(self) -> dict[str, Any]:
        try:
            active_policy = self.improvements.active_policy()
        except Exception as exc:  # capability discovery must remain available during partial outages
            active_policy = {"status": "unavailable", "error_type": type(exc).__name__}
        try:
            knowledge = self.knowledge.status()
        except Exception as exc:  # report a bounded diagnostic instead of breaking tool discovery
            knowledge = {"status": "unavailable", "error_type": type(exc).__name__}
        return {
            "product": "Orbita Agent Research Server",
            "version": __version__,
            "core": {
                "epistemic_runtime": "1.5-derived",
                "discovery_engine": "0.2-compatible",
                "research_mvp": "0.1-derived",
            },
            "research_routes": [
                "tabular intake and deterministic profiling",
                "frozen scout/confirmation plans",
                "held-out, baseline, and cross-seed falsification",
                "persistent claims, evidence, contradictions, and supersession",
                "curated research-memory search",
                "bounded graph analysis and Lean finite-certificate export",
                "frozen deterministic external experiments with independent verification",
                "prospective blind calibration with prediction-before-reveal scoring",
                "hash-bound language snapshots, finite representation audits, and inert semantic transitions",
                "typed capability-component graphs for archive synthesis",
                "append-only General Problem Loops with hash-chained evidence and bounded retries",
                "compile-time candidate-to-executor binding with append-only dispatch receipts",
            ],
            "approval_phrase": APPROVAL_PHRASE,
            "deletion_phrase": DELETION_PHRASE,
            "self_improvement": {
                "mode": "bounded_policy_improvement",
                "promotion_phrase": PROMOTION_PHRASE,
                "rollback_phrase": ROLLBACK_PHRASE,
                "active_policy": active_policy,
                "general_candidate_registry": self.improvement_registry.status(),
            },
            "limits": {
                "max_inline_bytes": self.config.max_inline_bytes,
                "max_graph_vertices": self.config.max_graph_vertices,
                "max_graph_edges": self.config.max_graph_edges,
            },
            "archive_processing": self.service.ingestor.describe(),
            "external_experiments": self.external_experiments.status(),
            "blind_calibration": self.blind_calibration.status(),
            "general_problem_loop": self.general_problem_loops.status(),
            "candidate_execution": {
                "registry": DEFAULT_EXECUTOR_REGISTRY.status(),
                "ledger": self.candidate_executions.status(),
            },
            "knowledge": knowledge,
            "boundaries": [
                "Surviving a configured gauntlet is not universal proof, causality, or novelty.",
                "Plan approval is a distinct hash-bound action.",
                "Inline agent uploads are text-only; browser/REST intake supports richer files.",
                "Lean export checks a concrete finite witness only.",
                "Self-improvement changes allowlisted research-policy values only and never promotes itself.",
                "General improvement candidates can be frozen and evaluated, but cannot activate themselves.",
                "Language transitions create inert snapshots and receipts; they never patch or activate this runtime.",
                "The General Problem Loop records executor receipts but cannot autonomously execute external actions.",
                "Candidate kinds are never coerced into a different executor; missing grounding fails before approval.",
                "Deterministic execution integrity is reported separately from scientific validity.",
            ],
        }

    def create_general_problem_loop(
        self,
        *,
        goal: str,
        success_criteria: list[str],
        allowed_capabilities: list[str],
        max_cycles: int = 3,
        created_by: str = "user",
    ) -> dict[str, Any]:
        with self._lock:
            return self.general_problem_loops.create(
                goal=goal,
                success_criteria=success_criteria,
                allowed_capabilities=allowed_capabilities,
                max_cycles=max_cycles,
                created_by=created_by,
            )

    def general_problem_loop_status(self) -> dict[str, Any]:
        with self._lock:
            return self.general_problem_loops.status()

    def list_general_problem_loops(self, *, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            return self.general_problem_loops.list(limit=limit)

    def get_general_problem_loop(self, loop_id: str) -> dict[str, Any]:
        with self._lock:
            return self.general_problem_loops.get(loop_id)

    def advance_general_problem_loop(
        self,
        loop_id: str,
        *,
        expected_state: str,
        expected_previous_event_hash: str,
        artifact: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.general_problem_loops.submit(
                loop_id,
                expected_state=expected_state,
                expected_previous_event_hash=expected_previous_event_hash,
                artifact=artifact,
                actor=actor,
            )

    def verify_general_problem_loop(self, loop_id: str) -> dict[str, Any]:
        with self._lock:
            return self.general_problem_loops.verify_chain(loop_id)

    @staticmethod
    def _case_view(case: dict[str, Any]) -> dict[str, Any]:
        return {
            key: case.get(key)
            for key in ("id", "name", "goal", "mode", "domain_hint", "status", "created_at", "updated_at")
        } | {
            "file_count": len(case.get("files", [])),
            "plan_count": len(case.get("plans", [])),
            "run_count": len(case.get("runs", [])),
        }

    def list_cases(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._case_view(case) for case in self.service.store.list_cases()]

    def create_case(self, *, name: str, goal: str = "", domain_hint: str | None = None) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("Case name cannot be blank")
        if len(name) > 200 or len(goal) > 4_000:
            raise ValueError("Case name or goal exceeds the local agent limit")
        with self._lock:
            return self._case_view(self.service.create_case(name=name, goal=goal, domain_hint=domain_hint))

    def add_inline_file(self, *, case_id: str, filename: str, content: str) -> dict[str, Any]:
        safe_name = _safe_filename(filename)
        payload = content.encode("utf-8")
        if len(payload) > self.config.max_inline_bytes:
            raise ValueError(f"Inline payload exceeds {self.config.max_inline_bytes} bytes")
        # Resolve the case before staging anything. Without this the insert fails on a
        # foreign key deep in storage, which both writes a temporary file for a case
        # that cannot accept it and reports a raw sqlite error instead of "unknown case".
        with self._lock:
            self.service.store.get_case(case_id)
        staging = self.config.inbox / f"upload_{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        path = staging / safe_name
        try:
            path.write_bytes(payload)
            with self._lock:
                record = self.service.add_file(case_id, path)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        memory = self._index_chat_exports(case_id, record)
        return {
            key: record.get(key)
            for key in (
                "id",
                "case_id",
                "original_name",
                "media_type",
                "size_bytes",
                "sha256",
                "parse_status",
                "artifact_kind",
                "profile",
                "error",
            )
        } | ({"memory": memory} if memory else {})

    def _index_chat_exports(self, case_id: str, record: dict[str, Any]) -> dict[str, Any] | None:
        """Index any chat export in a freshly ingested file so it becomes searchable.

        Indexing failures never fail the upload: the file and its profile are already
        durable, and a searchable copy is a convenience layered on top of them. The
        error is reported so it is visible rather than silently swallowed.
        """
        exports = list(chat_export_members(record))
        if exports:
            # Checked at indexing too, not only at upload. The inline path can carry a
            # small export, and a policy enforced at one entrance is not enforced.
            ArchivePolicy.from_env().ensure(self.config.tenant)

        indexed: list[dict[str, Any]] = []
        for label, extracted in exports:
            try:
                frame = pd.read_csv(extracted)
                indexed.append(
                    self.memory.index_frame(
                        case_id=case_id,
                        file_id=f"{record['id']}::{label}",
                        source_name=label,
                        frame=frame,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - reported, never fatal to the upload
                indexed.append({"source_name": label, "error": f"{type(exc).__name__}: {exc}"})
        if not indexed:
            return None
        return {
            "sources": indexed,
            "messages_indexed": sum(entry.get("indexed", 0) for entry in indexed),
        }

    def search_memory(
        self,
        query: str,
        *,
        limit: int = 20,
        case_id: str | None = None,
        role: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        return self.memory.search(
            query, limit=limit, case_id=case_id, role=role, conversation_id=conversation_id
        )

    def memory_conversation(self, conversation_id: str, *, limit: int = 200) -> dict[str, Any]:
        return self.memory.conversation(conversation_id, limit=limit)

    def memory_status(self) -> dict[str, Any]:
        return self.memory.stats()

    def find_reversals(
        self,
        *,
        case_id: str | None = None,
        role: str = "user",
        limit: int = 20,
        min_days_apart: float = 1.0,
    ) -> dict[str, Any]:
        return find_candidate_reversals(
            self.memory,
            case_id=case_id,
            role=role,
            limit=limit,
            min_days_apart=min_days_apart,
        )

    def forget_memory(self, *, case_id: str | None = None, everything: bool = False) -> dict[str, Any]:
        if everything:
            return {"scope": "everything", "messages_deleted": self.memory.delete_everything()}
        if not case_id:
            raise ValueError("pass a case_id, or everything=True to clear the whole index")
        return {"scope": case_id, "messages_deleted": self.memory.delete_case(case_id)}

    def receive_upload(
        self,
        *,
        case_id: str,
        filename: str,
        chunks: Iterator[bytes],
        max_bytes: int,
    ) -> dict[str, Any]:
        """Stream an upload into object storage, then ingest it from there.

        The durable copy goes to object storage first, so the archive survives even if
        parsing fails and can be re-parsed later without asking the user to upload a
        gigabyte again. The volume only ever sees a working copy, which is removed once
        the parsed artifacts exist.

        This is what keeps a fixed-size volume from being the limit on how many people
        can use the service.
        """
        with self._lock:
            self.service.store.get_case(case_id)

        file_id = f"upl_{uuid.uuid4().hex}"
        key = object_key(self.config.tenant, case_id, file_id, filename)
        stored = self.objects.put_stream(key, chunks, max_bytes=max_bytes)
        if stored.size_bytes == 0:
            self.objects.delete(key)
            raise ValueError("upload was empty")

        staging = self.config.inbox / f"upload_{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=True)
        working = staging / Path(key).name
        try:
            with self.objects.open(key) as source, working.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            record = self.ingest_upload(case_id=case_id, path=working, object_key=key)
        except BaseException:
            # A failed ingest must not leave an orphan in the store that nothing
            # references and nobody is accounting for.
            self.objects.delete(key)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        self._prune_volume_original(record)
        return record | {"object": stored.public()}

    def _prune_volume_original(self, record: dict[str, Any]) -> None:
        """Drop the volume's copy of the raw upload once a durable copy exists elsewhere.

        The extracted and normalized artifacts stay, because those are what the case and
        the search index read. The original is reachable from object storage, so keeping
        a second copy on a fixed-size disk buys nothing and is the thing that fills it.
        """
        if not record.get("object_key"):
            return
        stored_path = record.get("stored_path")
        if not stored_path:
            return
        candidate = Path(stored_path).resolve()
        workspace = self.config.workspace.resolve()
        if workspace in candidate.parents and candidate.is_file():
            candidate.unlink(missing_ok=True)
            record["volume_original_pruned"] = True

    def ingest_upload(
        self, *, case_id: str, path: Path, object_key: str | None = None
    ) -> dict[str, Any]:
        """Ingest an already-staged file, bypassing the inline text-only size ceiling.

        The caller is responsible for having staged the bytes safely; this only owns
        resolving the case and handing the file to the ingestor.
        """
        with self._lock:
            self.service.store.get_case(case_id)
            record = self.service.add_file(case_id, path)
        memory = self._index_chat_exports(case_id, record)
        return {
            key: record.get(key)
            for key in (
                "id",
                "case_id",
                "original_name",
                "media_type",
                "size_bytes",
                "sha256",
                "parse_status",
                "artifact_kind",
                "profile",
                "error",
                "stored_path",
            )
        } | ({"memory": memory} if memory else {}) | (
            {"object_key": object_key} if object_key else {}
        )

    def delete_case(self, case_id: str, *, confirmation: str) -> dict[str, Any]:
        """Permanently remove a case, its uploaded bytes, and its indexed memory.

        This exists because "I deleted it" has to mean the bytes are gone. Clearing a
        search index while the archive still sits on the volume would be a false
        statement to someone who uploaded their whole chat history.

        What it does not do is truncate the claim ledger. Claims are hash-chained
        evidence records that other cases can reference, and quietly deleting them
        under a data-erasure request would corrupt exactly the provenance this system
        exists to keep. Any linked claims are reported and left in place; retracting a
        finding is a separate deliberate act through supersession.
        """
        if confirmation != DELETION_PHRASE:
            raise ValueError(f'deletion requires the exact phrase: "{DELETION_PHRASE}"')

        with self._lock:
            case = self.service.store.get_case(case_id)

            files = [
                {
                    "id": item["id"],
                    "original_name": item["original_name"],
                    "size_bytes": item.get("size_bytes"),
                    "sha256": item.get("sha256"),
                }
                for item in case.get("files", [])
            ]
            linked_claims = [
                row["claim_id"]
                for row in self.service.store.ledger.db.conn.execute(
                    "SELECT claim_id FROM case_claims WHERE case_id = ?", (case_id,)
                ).fetchall()
            ]

            memory_deleted = self.memory.delete_case(case_id)

            connection = self.service.store.ledger.db.conn
            with connection:
                # Child rows first: the schema declares real foreign keys.
                for table in (
                    "case_reports",
                    "case_claims",
                    "case_runs",
                    "analysis_plans",
                    "case_files",
                ):
                    connection.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,))
                connection.execute("DELETE FROM research_cases WHERE id = ?", (case_id,))

            # Only then the bytes, and only inside our own workspace. A stored path is
            # data, and data is never a licence to remove an arbitrary directory.
            workspace_root = self.config.workspace.resolve()
            case_directory = (workspace_root / "cases" / case_id).resolve()
            removed_bytes = 0
            removed_files = 0
            if workspace_root in case_directory.parents and case_directory.is_dir():
                for path in case_directory.rglob("*"):
                    if path.is_file():
                        removed_bytes += path.stat().st_size
                        removed_files += 1
                shutil.rmtree(case_directory, ignore_errors=False)

            # And the durable copies in object storage, which are the point of it.
            objects_removed = self.objects.delete_prefix(
                f"tenants/{self.config.tenant or 'operator'}/cases/{case_id}"
            )

            still_present = case_directory.exists()
            remaining_rows = connection.execute(
                "SELECT COUNT(*) FROM research_cases WHERE id = ?", (case_id,)
            ).fetchone()[0]

        if still_present or remaining_rows:
            raise RuntimeError(
                f"deletion did not complete: directory_present={still_present}, "
                f"rows_remaining={remaining_rows}"
            )

        return {
            "deleted": True,
            "case_id": case_id,
            "case_name": case.get("name"),
            "files_removed": removed_files,
            "bytes_removed": removed_bytes,
            "file_manifest": files,
            "memory_messages_removed": memory_deleted,
            "objects_removed": objects_removed,
            "directory_removed": str(case_directory),
            "verified_absent": not still_present and remaining_rows == 0,
            "claims_left_in_ledger": linked_claims,
            "boundary": (
                "The uploaded files, their extracted copies, the case record, and the "
                "searchable memory are gone from this tenant. "
                + (
                    f"{len(linked_claims)} claim(s) derived from this case remain in the "
                    "hash-chained ledger and were not deleted; retract a finding through "
                    "supersession rather than by erasing its record."
                    if linked_claims
                    else "No claims were derived from this case, so nothing remains in the ledger."
                )
            ),
        }

    def case_context(self, case_id: str) -> dict[str, Any]:
        with self._lock:
            case = self.service.store.get_case(case_id)
        return {
            "case": self._case_view(case),
            "files": [
                {
                    "id": item["id"],
                    "name": item["original_name"],
                    "artifact_kind": item["artifact_kind"],
                    "parse_status": item["parse_status"],
                    "sha256": item["sha256"],
                    "profile": item["profile"],
                }
                for item in case.get("files", [])
            ],
            "plans": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "version",
                        "status",
                        "plan_hash",
                        "compiler",
                        "created_at",
                        "approved_at",
                        "approved_by",
                    )
                }
                for item in case.get("plans", [])
            ],
            "runs": [self._run_view(item, include_findings=False) for item in case.get("runs", [])],
            "agent_contract": (
                "Use only profiled fields. Treat relations as non-causal unless the design warrants causality. "
                "Freeze candidates before confirmation data is scored."
            ),
        }

    def compile_plan(self, case_id: str, *, max_candidates: int | None = None) -> dict[str, Any]:
        active = self.improvements.active_policy()
        policy = dict(active["policy"])
        override = max_candidates is not None
        if override:
            if not 1 <= int(max_candidates) <= 200:
                raise ValueError("max_candidates must be between 1 and 200")
            policy["max_candidates"] = int(max_candidates)
        receipt = {
            "policy_id": active["id"],
            "policy_version": active["version"],
            "policy_hash": active["policy_hash"],
            "max_candidates_overridden": override,
        }
        with self._lock:
            return self.service.compile_case(
                case_id,
                max_candidates=policy["max_candidates"],
                policy=policy,
                policy_receipt=receipt,
            )

    def improvement_status(self) -> dict[str, Any]:
        with self._lock:
            return self.improvements.status()

    def guard_claim_scope(
        self,
        *,
        evidence_scope: dict[str, Any],
        proposed_claim_scope: dict[str, Any],
    ) -> dict[str, Any]:
        """Reject claims whose scope is broader than their declared evidence."""

        return guard_scope_escalation(evidence_scope, proposed_claim_scope)

    def improvement_history(self, *, limit: int = 25) -> dict[str, Any]:
        with self._lock:
            return self.improvements.history(limit=limit)

    def get_improvement(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            return self.improvements.get_candidate(candidate_id)

    def suggest_improvement(self) -> dict[str, Any]:
        with self._lock:
            return self.improvements.suggest()

    def propose_improvement(
        self,
        *,
        name: str,
        rationale: str,
        patch: dict[str, Any],
        acceptance_criteria: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self.improvements.propose(
                name=name,
                rationale=rationale,
                patch=patch,
                acceptance_criteria=acceptance_criteria,
            )

    def evaluate_improvement(self, candidate_id: str, *, case_ids: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            return self.improvements.evaluate(candidate_id, case_ids=case_ids)

    def promote_improvement(
        self,
        candidate_id: str,
        *,
        expected_candidate_hash: str,
        expected_evaluation_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.improvements.promote(
                candidate_id,
                expected_candidate_hash=expected_candidate_hash,
                expected_evaluation_hash=expected_evaluation_hash,
                reviewer=reviewer,
                confirmation=confirmation,
            )

    def rollback_improvement(
        self,
        target_policy_id: str,
        *,
        expected_active_policy_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.improvements.rollback(
                target_policy_id,
                expected_active_policy_hash=expected_active_policy_hash,
                reviewer=reviewer,
                confirmation=confirmation,
            )

    def governed_improvement_status(self) -> dict[str, Any]:
        with self._lock:
            return self.improvement_registry.status()

    def register_improvement_candidate(
        self,
        *,
        candidate_kind: str,
        limitation_kind: str,
        base_artifact: dict[str, Any],
        candidate_artifact: dict[str, Any],
        problem_statement: str,
        rationale: str,
        expected_benefit: str,
        observed_failure_ids: list[str] | None = None,
        known_risks: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        parent_candidate_id: str | None = None,
        created_by: str = "agent-proposal",
    ) -> dict[str, Any]:
        """Register an inactive candidate without changing production behavior."""
        with self._lock:
            return self.improvement_registry.create_candidate(
                candidate_kind=candidate_kind,
                limitation_kind=limitation_kind,
                base_artifact=base_artifact,
                candidate_artifact=candidate_artifact,
                problem_statement=problem_statement,
                rationale=rationale,
                expected_benefit=expected_benefit,
                observed_failure_ids=observed_failure_ids,
                known_risks=known_risks,
                evidence=evidence,
                parent_candidate_id=parent_candidate_id,
                created_by=created_by,
            )

    def list_governed_improvements(self, *, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            return self.improvement_registry.list_candidates(limit=limit)

    def get_governed_improvement(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            return self.improvement_registry.get_candidate(candidate_id)

    def freeze_improvement_evaluation(
        self,
        candidate_id: str,
        *,
        evaluation_plan: dict[str, Any],
        frozen_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.improvement_registry.freeze_evaluation(
                candidate_id,
                evaluation_plan=evaluation_plan,
                frozen_by=frozen_by,
            )

    def record_governed_improvement_evaluation(
        self,
        candidate_id: str,
        *,
        expected_candidate_hash: str,
        expected_plan_hash: str,
        result: dict[str, Any],
        verdict: str,
        evaluated_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.improvement_registry.record_evaluation(
                candidate_id,
                expected_candidate_hash=expected_candidate_hash,
                expected_plan_hash=expected_plan_hash,
                result=result,
                verdict=verdict,
                evaluated_by=evaluated_by,
            )

    def external_experiment_status(self) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.status()

    def freeze_external_experiment(
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
        with self._lock:
            return self.external_experiments.freeze(
                case_id=case_id,
                plan_id=plan_id,
                expected_plan_hash=expected_plan_hash,
                scientific_question=scientific_question,
                claim_scope=claim_scope,
                execution_spec=execution_spec,
                verdict_schema=verdict_schema,
                independent_verifier=independent_verifier,
                falsification_coverage=falsification_coverage,
                anti_rescue_rules=anti_rescue_rules,
                created_by=created_by,
            )

    def submit_external_experiment(
        self, experiment_id: str, *, expected_experiment_hash: str, submitted_by: str
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.submit(
                experiment_id,
                expected_experiment_hash=expected_experiment_hash,
                submitted_by=submitted_by,
            )

    def approve_external_experiment(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_manifest_hash: str,
        reviewer: str,
        rationale: str,
        confirmation: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.approve(
                experiment_id,
                expected_experiment_hash=expected_experiment_hash,
                expected_manifest_hash=expected_manifest_hash,
                reviewer=reviewer,
                rationale=rationale,
                confirmation=confirmation,
            )

    def run_external_experiment(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_manifest_hash: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.execute(
                experiment_id,
                expected_experiment_hash=expected_experiment_hash,
                expected_manifest_hash=expected_manifest_hash,
            )

    def record_external_verification(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_execution_receipt_hash: str,
        verifier_receipt: dict[str, Any],
        conclusion: str,
        verified_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.record_verification(
                experiment_id,
                expected_experiment_hash=expected_experiment_hash,
                expected_execution_receipt_hash=expected_execution_receipt_hash,
                verifier_receipt=verifier_receipt,
                conclusion=conclusion,
                verified_by=verified_by,
            )

    def prepare_external_reproduction(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_execution_receipt_hash: str,
        submitted_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.prepare_reproduction(
                experiment_id,
                expected_experiment_hash=expected_experiment_hash,
                expected_execution_receipt_hash=expected_execution_receipt_hash,
                submitted_by=submitted_by,
            )

    def approve_external_reproduction(
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
        with self._lock:
            return self.external_experiments.approve_reproduction(
                experiment_id,
                expected_experiment_hash=expected_experiment_hash,
                expected_original_receipt_hash=expected_original_receipt_hash,
                expected_reproduction_manifest_hash=expected_reproduction_manifest_hash,
                reviewer=reviewer,
                rationale=rationale,
                confirmation=confirmation,
            )

    def run_external_reproduction(
        self,
        experiment_id: str,
        *,
        expected_experiment_hash: str,
        expected_original_receipt_hash: str,
        expected_reproduction_manifest_hash: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.execute_reproduction(
                experiment_id,
                expected_experiment_hash=expected_experiment_hash,
                expected_original_receipt_hash=expected_original_receipt_hash,
                expected_reproduction_manifest_hash=expected_reproduction_manifest_hash,
            )

    def record_external_coverage_bug(
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
        with self._lock:
            return self.external_experiments.record_coverage_bug(
                experiment_id,
                expected_experiment_hash=expected_experiment_hash,
                claim_effect=claim_effect,
                missed_counterexample=missed_counterexample,
                reason=reason,
                fix=fix,
                old_results_impacted=old_results_impacted,
                replacement_coverage=replacement_coverage,
                recorded_by=recorded_by,
                affected_claim_ids=affected_claim_ids,
            )

    def propagate_external_coverage_bug_to_claims(
        self,
        coverage_bug_id: str,
        *,
        expected_replacement_protocol_hash: str,
    ) -> dict[str, Any]:
        """Retry or audit the idempotent external-to-Guided claim bridge."""

        with self._lock:
            return self.external_experiments.propagate_coverage_bug_to_claims(
                coverage_bug_id,
                expected_replacement_protocol_hash=expected_replacement_protocol_hash,
            )

    def prepare_coverage_reevaluation(
        self,
        coverage_bug_id: str,
        *,
        expected_replacement_protocol_hash: str,
        execution_spec: dict[str, Any],
        resolution_targets: list[str],
        submitted_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.prepare_coverage_reevaluation(
                coverage_bug_id,
                expected_replacement_protocol_hash=expected_replacement_protocol_hash,
                execution_spec=execution_spec,
                resolution_targets=resolution_targets,
                submitted_by=submitted_by,
            )

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
        with self._lock:
            return self.external_experiments.approve_coverage_reevaluation(
                coverage_bug_id,
                expected_replacement_protocol_hash=expected_replacement_protocol_hash,
                expected_reevaluation_hash=expected_reevaluation_hash,
                expected_execution_manifest_hash=expected_execution_manifest_hash,
                reviewer=reviewer,
                rationale=rationale,
                confirmation=confirmation,
            )

    def run_coverage_reevaluation(
        self,
        coverage_bug_id: str,
        *,
        expected_replacement_protocol_hash: str,
        expected_reevaluation_hash: str,
        expected_execution_manifest_hash: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.execute_coverage_reevaluation(
                coverage_bug_id,
                expected_replacement_protocol_hash=expected_replacement_protocol_hash,
                expected_reevaluation_hash=expected_reevaluation_hash,
                expected_execution_manifest_hash=expected_execution_manifest_hash,
            )

    def record_coverage_resolutions(
        self,
        coverage_bug_id: str,
        *,
        expected_reevaluation_hash: str,
        expected_execution_receipt_hash: str,
        resolutions: list[dict[str, Any]],
        recorded_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.record_coverage_resolutions(
                coverage_bug_id,
                expected_reevaluation_hash=expected_reevaluation_hash,
                expected_execution_receipt_hash=expected_execution_receipt_hash,
                resolutions=resolutions,
                recorded_by=recorded_by,
            )

    def get_coverage_bug(self, coverage_bug_id: str) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.get_coverage_bug(coverage_bug_id)

    def get_external_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            return self.external_experiments.get(experiment_id)

    def list_external_experiments(self, *, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            return self.external_experiments.list(limit=limit)

    def submit_plan(self, case_id: str, *, plan: dict[str, Any], compiler: str = "external-ai") -> dict[str, Any]:
        if not compiler.strip() or len(compiler) > 120:
            raise ValueError("compiler must be a short nonblank identifier")
        with self._lock:
            return self.service.submit_external_plan(case_id, plan, compiler=compiler)

    def executor_registry_status(self) -> dict[str, Any]:
        return DEFAULT_EXECUTOR_REGISTRY.status() | {"ledger": self.candidate_executions.status()}

    def list_candidate_execution_receipts(self, *, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            return self.candidate_executions.list(limit=limit)

    def get_candidate_execution_receipt(self, receipt_id: str) -> dict[str, Any]:
        with self._lock:
            return self.candidate_executions.get(receipt_id)

    def verify_candidate_execution_receipt(self, receipt_id: str) -> dict[str, Any]:
        with self._lock:
            return self.candidate_executions.verify(receipt_id)

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            return self.service.store.get_plan(plan_id)

    def approve_plan(
        self,
        plan_id: str,
        *,
        expected_plan_hash: str,
        reviewer: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != APPROVAL_PHRASE:
            raise ValueError(f"confirmation must exactly equal: {APPROVAL_PHRASE}")
        if not reviewer.strip() or len(reviewer) > 160:
            raise ValueError("reviewer must identify the approving person or authorized principal")
        with self._lock:
            plan = self.service.store.get_plan(plan_id)
            if plan["plan_hash"] != expected_plan_hash:
                raise ValueError("Plan hash mismatch; fetch and review the current immutable plan again")
            if plan["status"] == "approved":
                return plan
            case = self.service.store.get_case(plan["case_id"])
            DEFAULT_EXECUTOR_REGISTRY.validate_bound_plan(plan["plan"], case)
            return self.service.approve_plan(plan_id, reviewer=reviewer)

    def run_discovery(self, case_id: str, *, plan_id: str) -> dict[str, Any]:
        with self._lock:
            plan = self.service.store.get_plan(plan_id)
            if plan["case_id"] != case_id:
                raise ValueError("The plan does not belong to this case")
            if plan["status"] != "approved":
                raise ValueError("The exact plan must be approved before execution")
            plan_body = plan["plan"]
            case = self.service.store.get_case(case_id)
            contract = DEFAULT_EXECUTOR_REGISTRY.validate_bound_plan(plan_body, case)
            binding = plan_body["execution_binding"]
            try:
                if contract.executor_id == "tabular-statistical/1":
                    run = self.service.run_case(case_id, plan_id=plan_id, auto_approve=False)
                    result = self._run_view(run, include_findings=True)
                    return self._record_candidate_execution(plan, binding, "completed", result)
                if contract.executor_id == "prospective-blind-calibration/1":
                    candidates = plan_body.get("candidates", [])
                    blind_policy = plan_body.get("blind_calibration", {})
                    provider = (
                        blind_policy.get("prediction_provider") if isinstance(blind_policy, dict) else None
                    ) or plan_body.get("prediction_provider")
                    for candidate in candidates:
                        if candidate.get("kind") == PREDICTION_KIND and candidate.get("prediction_provider"):
                            provider = candidate["prediction_provider"]
                            break
                    if isinstance(provider, dict) and provider.get("kind") == "external_submission":
                        result = self.blind_calibration.prepare_from_approved_plan(case_id, plan_id)
                    else:
                        run = self.blind_calibration.execute_from_approved_plan(case_id, plan_id)
                        result = self._run_view(run, include_findings=False)
                    return self._record_candidate_execution(plan, binding, "prepared", result)
                if contract.executor_id == "structured-research-operator/1":
                    candidate = next(
                        item for item in plan_body["candidates"]
                        if item.get("kind") in {"research_operator", "external_experiment"}
                    )
                    execution = candidate["execution_contract"]
                    experiment = self.external_experiments.freeze(
                        case_id=case_id,
                        plan_id=plan_id,
                        expected_plan_hash=plan["plan_hash"],
                        scientific_question=execution["scientific_question"],
                        claim_scope=execution["claim_scope"],
                        execution_spec=execution["execution_spec"],
                        verdict_schema=execution["verdict_schema"],
                        independent_verifier=execution["independent_verifier"],
                        falsification_coverage=execution["falsification_coverage"],
                        anti_rescue_rules=execution["anti_rescue_rules"],
                        created_by=str(execution.get("created_by") or "unified-candidate-dispatch"),
                    )
                    result = {
                        "status": "frozen_external_experiment",
                        "candidate_id": candidate["id"],
                        "experiment": experiment,
                        "next_required_action": "submit the frozen experiment, then separately approve its exact execution manifest",
                        "scientific_claim_committed": False,
                    }
                    return self._record_candidate_execution(plan, binding, "prepared", result)
                raise RuntimeError(f"registered executor {contract.executor_id!r} has no dispatcher implementation")
            except Exception as exc:
                self.candidate_executions.record(
                    case_id=case_id,
                    plan_id=plan_id,
                    plan_hash=plan["plan_hash"],
                    binding=binding,
                    outcome="failed",
                    result_reference={"error_type": type(exc).__name__, "error": str(exc)},
                )
                raise

    def _record_candidate_execution(
        self,
        plan: dict[str, Any],
        binding: dict[str, Any],
        outcome: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        reference = {
            "payload_hash": candidate_result_hash(result),
            "result_id": result.get("id") or result.get("protocol_id") or result.get("experiment", {}).get("id"),
            "status": result.get("status"),
        }
        receipt = self.candidate_executions.record(
            case_id=plan["case_id"],
            plan_id=plan["id"],
            plan_hash=plan["plan_hash"],
            binding=binding,
            outcome=outcome,
            result_reference=reference,
        )
        return result | {"execution_dispatch": receipt}

    def blind_calibration_status(self) -> dict[str, Any]:
        with self._lock:
            return self.blind_calibration.status()

    def get_blind_calibration(self, protocol_id: str) -> dict[str, Any]:
        with self._lock:
            return self.blind_calibration.get(protocol_id)

    def get_blind_prediction_batch(self, protocol_id: str) -> dict[str, Any]:
        with self._lock:
            return self.blind_calibration.prediction_batch(protocol_id)

    def freeze_blind_predictions(
        self,
        protocol_id: str,
        *,
        expected_protocol_hash: str,
        predictions: list[dict[str, Any]],
        provider: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            return self.blind_calibration.freeze_predictions(
                protocol_id,
                expected_protocol_hash=expected_protocol_hash,
                predictions=predictions,
                provider=provider,
            )

    def seal_blind_scoring_key(
        self,
        protocol_id: str,
        *,
        expected_protocol_hash: str,
        expected_prediction_freeze_hash: str,
        filename: str,
        content: str,
        sealed_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.blind_calibration.seal_scoring_key(
                protocol_id,
                expected_protocol_hash=expected_protocol_hash,
                expected_prediction_freeze_hash=expected_prediction_freeze_hash,
                filename=filename,
                content=content,
                sealed_by=sealed_by,
            )

    def approve_blind_reveal(
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
        with self._lock:
            return self.blind_calibration.approve_reveal(
                protocol_id,
                expected_protocol_hash=expected_protocol_hash,
                expected_prediction_freeze_hash=expected_prediction_freeze_hash,
                expected_scoring_key_hash=expected_scoring_key_hash,
                reviewer=reviewer,
                rationale=rationale,
                confirmation=confirmation,
            )

    def score_blind_calibration(
        self,
        protocol_id: str,
        *,
        expected_prediction_freeze_hash: str,
        expected_scoring_key_hash: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self.blind_calibration.score(
                protocol_id,
                expected_prediction_freeze_hash=expected_prediction_freeze_hash,
                expected_scoring_key_hash=expected_scoring_key_hash,
            )

    @staticmethod
    def _run_view(run: dict[str, Any], *, include_findings: bool, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        result = run.get("result", {})
        findings = result.get("findings", [])
        view = {
            key: run.get(key)
            for key in ("id", "case_id", "plan_id", "status", "started_at", "completed_at")
        }
        view["summary"] = {
            key: result.get(key)
            for key in (
                "engine",
                "domain",
                "candidate_count",
                "survivor_count",
                "survivor_ids",
                "graph_snapshot_id",
                "belief_import",
                "reports",
                "error_type",
                "error",
                "protocol_id",
                "protocol_hash",
                "plan_hash",
                "constraint_count",
                "prediction_count",
                "freeze_status",
                "prediction_freeze_hash",
                "freeze_receipt",
                "scoring_accessed",
                "scored",
            )
            if key in result
        }
        if include_findings:
            view["findings_page"] = {
                "offset": offset,
                "limit": limit,
                "total": len(findings),
                "items": findings[offset : offset + limit],
            }
        return view

    def get_run(self, run_id: str, *, findings_offset: int = 0, findings_limit: int = 50) -> dict[str, Any]:
        offset = max(0, int(findings_offset))
        limit = max(1, min(int(findings_limit), 100))
        with self._lock:
            run = self.service.store.get_run(run_id)
        return self._run_view(run, include_findings=True, offset=offset, limit=limit)

    def case_claims(self, case_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self.service.store.get_case(case_id)
            return self.service.store.case_claims(case_id)

    def claim_history(self, claim_id: str) -> dict[str, Any]:
        with self._lock:
            return self.service.claim_history(claim_id)

    def claim_impact(self, claim_id: str) -> dict[str, Any]:
        with self._lock:
            return self.service.memory.impact_view(claim_id)

    def add_contradiction(self, claim_a: str, claim_b: str, *, rationale: str) -> dict[str, Any]:
        if len(rationale.strip()) < 8:
            raise ValueError("A specific contradiction rationale is required")
        with self._lock:
            return self.service.add_contradiction(claim_a, claim_b, rationale=rationale)

    def supersede_claim(self, claim_id: str, *, new_statement: str, rationale: str) -> dict[str, Any]:
        if len(new_statement.strip()) < 4 or len(rationale.strip()) < 8:
            raise ValueError("A substantive replacement statement and rationale are required")
        with self._lock:
            return self.service.supersede_claim(claim_id, new_statement=new_statement, rationale=rationale)

    def reexamination_queue(self) -> list[dict[str, Any]]:
        with self._lock:
            return self.service.reexamination_queue()

    def report(self, case_id: str, *, format: str = "markdown") -> dict[str, Any]:
        if format not in {"markdown", "json", "html"}:
            raise ValueError("format must be markdown, json, or html")
        with self._lock:
            case = self.service.store.get_case(case_id)
        for run in case.get("runs", []):
            artifact = run.get("result", {}).get("reports", {}).get(format)
            if artifact:
                path = Path(artifact["path"])
                if path.exists():
                    content = path.read_text(encoding="utf-8")
                    return {
                        "case_id": case_id,
                        "run_id": run["id"],
                        "format": format,
                        "sha256": artifact["sha256"],
                        "content": content,
                    }
        raise KeyError(f"No {format} report exists for case {case_id}")

    def search_knowledge(self, query: str, *, limit: int = 5, source_bundle: str | None = None) -> list[dict[str, Any]]:
        return self.knowledge.search(query, limit=limit, source_bundle=source_bundle)

    def knowledge_claims(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.knowledge.claim_cards(status=status, limit=limit)

    def eg_summary(self) -> dict[str, Any]:
        return self.knowledge.eg_summary()

    def eg_near_misses(
        self,
        *,
        limit: int = 10,
        min_n: int = 0,
        include_certificate: bool = False,
    ) -> list[dict[str, Any]]:
        return self.knowledge.find_eg_near_misses(limit=limit, min_n=min_n, include_certificate=include_certificate)

    def analyze_graph(
        self,
        *,
        n: int,
        edges: list[list[int]],
        timeout_seconds: float = 2.0,
        max_states: int = 500_000,
    ) -> dict[str, Any]:
        return analyze_graph(
            n,
            edges,
            max_vertices=self.config.max_graph_vertices,
            max_edges=self.config.max_graph_edges,
            timeout_seconds=timeout_seconds,
            max_states=max_states,
        )

    def export_lean_witness(
        self,
        *,
        n: int,
        edges: list[list[int]],
        cycle: list[int],
        project_name: str = "lean_certificate",
    ) -> dict[str, Any]:
        return export_lean_certificate(
            self.config.exports,
            n=n,
            edges=edges,
            cycle=cycle,
            project_name=project_name,
        )
