from __future__ import annotations

import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from orbita_mvp import ResearchMVP

from . import __version__
from .config import AgentConfig
from .graph_adapter import analyze_graph, export_lean_certificate
from .improvement import PROMOTION_PHRASE, ROLLBACK_PHRASE, ImprovementLab
from .knowledge import KnowledgeStore

APPROVAL_PHRASE = "I reviewed this exact frozen plan"
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
        self.improvements.close()
        self.knowledge.close()
        self.service.close()

    def __enter__(self) -> AgentGateway:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def capabilities(self) -> dict[str, Any]:
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
            ],
            "approval_phrase": APPROVAL_PHRASE,
            "self_improvement": {
                "mode": "bounded_policy_improvement",
                "promotion_phrase": PROMOTION_PHRASE,
                "rollback_phrase": ROLLBACK_PHRASE,
                "active_policy": self.improvements.active_policy(),
            },
            "limits": {
                "max_inline_bytes": self.config.max_inline_bytes,
                "max_graph_vertices": self.config.max_graph_vertices,
                "max_graph_edges": self.config.max_graph_edges,
            },
            "knowledge": self.knowledge.status(),
            "boundaries": [
                "Surviving a configured gauntlet is not universal proof, causality, or novelty.",
                "Plan approval is a distinct hash-bound action.",
                "Inline agent uploads are text-only; browser/REST intake supports richer files.",
                "Lean export checks a concrete finite witness only.",
                "Self-improvement changes allowlisted research-policy values only and never promotes itself.",
            ],
        }

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
        }

    def ingest_upload(self, *, case_id: str, path: Path) -> dict[str, Any]:
        """Ingest an already-staged file, bypassing the inline text-only size ceiling.

        The caller is responsible for having staged the bytes safely; this only owns
        resolving the case and handing the file to the ingestor.
        """
        with self._lock:
            self.service.store.get_case(case_id)
            record = self.service.add_file(case_id, path)
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

    def submit_plan(self, case_id: str, *, plan: dict[str, Any], compiler: str = "external-ai") -> dict[str, Any]:
        if not compiler.strip() or len(compiler) > 120:
            raise ValueError("compiler must be a short nonblank identifier")
        with self._lock:
            return self.service.submit_external_plan(case_id, plan, compiler=compiler)

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
            return self.service.approve_plan(plan_id, reviewer=reviewer)

    def run_discovery(self, case_id: str, *, plan_id: str) -> dict[str, Any]:
        with self._lock:
            plan = self.service.store.get_plan(plan_id)
            if plan["case_id"] != case_id:
                raise ValueError("The plan does not belong to this case")
            if plan["status"] != "approved":
                raise ValueError("The exact plan must be approved before execution")
            run = self.service.run_case(case_id, plan_id=plan_id, auto_approve=False)
            return self._run_view(run, include_findings=True)

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
