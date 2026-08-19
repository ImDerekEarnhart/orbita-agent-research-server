"""Governed case-bound execution of the OrbitaLanguageLimit Lean kernel."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from orbita import ActorRole, EvidenceKind, Stance
from orbita.epistemic_contract import EvidenceStatus
from orbita_mvp import ResearchMVP

from .config import AgentConfig
from .semantic_evolution import (
    LEAN_KERNEL_MANIFEST_SHA256,
    LEAN_KERNEL_VERSION,
    audit_representation,
    build_language_snapshot,
    content_hash,
    evaluate_missing_primitive,
    freeze_language_limit_certificate,
    propose_missing_primitive,
    render_frozen_language_limit_lean_source,
)

ARTIFACT_SCHEMA = "orbita-language-limit-artifact/1"
RECEIPT_SCHEMA = "orbita-language-limit-lean-verification-receipt/1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS language_limit_artifacts (
    certificate_hash TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES research_cases(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    snapshot_json TEXT NOT NULL,
    audit_json TEXT NOT NULL,
    certificate_json TEXT NOT NULL,
    source_cases_hash TEXT NOT NULL,
    provenance_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    lean_source_json TEXT,
    receipt_json TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_language_limit_case ON language_limit_artifacts(case_id, created_at);
CREATE TABLE IF NOT EXISTS language_limit_verification_attempts (
    attempt_id TEXT PRIMARY KEY,
    certificate_hash TEXT NOT NULL REFERENCES language_limit_artifacts(certificate_hash),
    status TEXT NOT NULL,
    lean_source_json TEXT,
    receipt_json TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_language_limit_attempts
    ON language_limit_verification_attempts(certificate_hash, attempted_at);
CREATE TABLE IF NOT EXISTS language_refinement_experiments (
    proposal_hash TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES research_cases(id),
    snapshot_json TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    status TEXT NOT NULL,
    evaluation_json TEXT,
    created_at TEXT NOT NULL,
    evaluated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_language_refinement_case ON language_refinement_experiments(case_id, created_at);
"""


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_file_set_hash(case: dict[str, Any]) -> str:
    return content_hash(
        [
            {"id": item["id"], "sha256": item["sha256"], "size_bytes": item["size_bytes"]}
            for item in sorted(case.get("files", []), key=lambda value: value["id"])
        ]
    )


class LanguageLimitVerificationService:
    """One lifecycle: discover, freeze, deterministically translate, verify, persist."""

    def __init__(self, config: AgentConfig, research_service: ResearchMVP):
        self.config = config
        self.research_service = research_service
        self.connection = research_service.store.ledger.db.conn
        self.connection.executescript(SCHEMA)
        self._backfill_canonical_attempts()
        self.connection.commit()

    def _backfill_canonical_attempts(self) -> None:
        """Preserve receipts created before append-only attempt history existed."""
        rows = self.connection.execute(
            """SELECT certificate_hash, status, lean_source_json, receipt_json, created_at, verified_at
               FROM language_limit_artifacts WHERE receipt_json IS NOT NULL"""
        ).fetchall()
        for row in rows:
            receipt = json.loads(row["receipt_json"])
            receipt_hash = receipt.get("receipt_hash") or content_hash(receipt)
            self.connection.execute(
                """INSERT OR IGNORE INTO language_limit_verification_attempts
                   (attempt_id, certificate_hash, status, lean_source_json, receipt_json, attempted_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    f"legacy_{receipt_hash}",
                    row["certificate_hash"],
                    row["status"],
                    row["lean_source_json"],
                    row["receipt_json"],
                    row["verified_at"] or row["created_at"],
                ),
            )

    def discover_and_freeze(
        self,
        *,
        case_id: str,
        snapshot_spec: dict[str, Any],
        cases: list[dict[str, Any]],
        provenance_hashes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Let Orbita discover the witness and freeze the resulting proof obligation."""
        case = self.research_service.store.get_case(case_id)
        snapshot = build_language_snapshot(snapshot_spec)
        audit = audit_representation(snapshot, cases)
        if audit["verdict"] != "LANGUAGE_LIMIT_WITNESS":
            raise ValueError("Orbita found no same-representation/different-target witness in the finite cases")
        source_cases_hash = content_hash(cases)
        provenance = {
            **dict(provenance_hashes or {}),
            "source_cases_sha256": source_cases_hash,
            "snapshot_sha256": snapshot["snapshot_hash"],
            "audit_sha256": audit["audit_hash"],
            "case_file_set_sha256": _case_file_set_hash(case),
        }
        claim_scope = {
            "domain": "frozen_finite_representation",
            "quantifier": "finite_all",
            "boundary": {"case_id": case_id, "world_count": len(cases), "source_cases_sha256": source_cases_hash},
            "assumptions": ["exact JSON equality defines representation fibers"],
            "computational_model": "Orbita finite equivalence-class audit plus Lean kernel checking",
            "representation_language": snapshot["name"],
        }
        claim_id, _ = self.research_service.memory.resolve_or_create_claim(
            f"Target O is not exactly representable by frozen language {snapshot['name']} on the {len(cases)} supplied worlds.",
            scope=claim_scope,
            claim_type="representational_hole",
            metadata={"case_id": case_id, "snapshot_hash": snapshot["snapshot_hash"]},
        )
        certificate = freeze_language_limit_certificate(
            snapshot,
            audit,
            cases,
            case_id=case_id,
            claim_id=claim_id,
            arithmetic_semantics={"distance": "absolute", "domain": "rational", "unit": "unitless"},
            provenance_hashes=provenance,
        )
        artifact_dir = self.research_service.store.case_dir(case_id) / "language_limit" / certificate["certificate_hash"]
        artifact_dir.mkdir(parents=True, exist_ok=False)
        certificate_path = artifact_dir / "certificate.json"
        certificate_path.write_text(json.dumps(certificate, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        self.connection.execute(
            """INSERT INTO language_limit_artifacts
               (certificate_hash, case_id, claim_id, snapshot_json, audit_json, certificate_json,
                source_cases_hash, provenance_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'FROZEN_PENDING_LEAN', ?)""",
            (
                certificate["certificate_hash"], case_id, claim_id, _stable(snapshot), _stable(audit),
                _stable(certificate), source_cases_hash, content_hash(certificate["provenance_hashes"]), _now(),
            ),
        )
        self.research_service.store.link_claim(
            case_id=case_id,
            run_id=None,
            claim_id=claim_id,
            finding_type="representational_hole",
            source_candidate_id=certificate["certificate_hash"],
        )
        self.research_service.memory.record_epistemic_contract(
            claim_id,
            evidence_status=EvidenceStatus.LANGUAGE_LIMIT,
            claim_scope=claim_scope,
            falsification_coverage={
                "tested_domains": ["frozen finite worlds"],
                "tested_sizes": [len(cases)],
                "known_uncovered_regions": ["all worlds outside the frozen finite set"],
                "exhaustive": True,
                "execution_receipt": certificate["certificate_hash"],
            },
            reason="Orbita discovered and froze an exact finite collision; Lean verification is pending.",
        )
        self.connection.commit()
        return self.get(certificate["certificate_hash"])

    def _row(self, certificate_hash: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM language_limit_artifacts WHERE certificate_hash = ?", (certificate_hash,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown language-limit certificate: {certificate_hash}")
        return dict(row)

    def get(self, certificate_hash: str) -> dict[str, Any]:
        row = self._row(certificate_hash)
        attempt_rows = self.connection.execute(
            """SELECT attempt_id, status, lean_source_json, receipt_json, attempted_at
               FROM language_limit_verification_attempts
               WHERE certificate_hash = ? ORDER BY attempted_at, attempt_id""",
            (certificate_hash,),
        ).fetchall()
        return {
            "schema": ARTIFACT_SCHEMA,
            "case_id": row["case_id"],
            "claim_id": row["claim_id"],
            "status": row["status"],
            "snapshot": json.loads(row["snapshot_json"]),
            "audit": json.loads(row["audit_json"]),
            "certificate": json.loads(row["certificate_json"]),
            "lean_source": json.loads(row["lean_source_json"]) if row["lean_source_json"] else None,
            "verification_receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
            "verification_attempts": [
                {
                    "attempt_id": attempt["attempt_id"],
                    "status": attempt["status"],
                    "lean_source": json.loads(attempt["lean_source_json"]) if attempt["lean_source_json"] else None,
                    "verification_receipt": json.loads(attempt["receipt_json"]),
                    "attempted_at": attempt["attempted_at"],
                }
                for attempt in attempt_rows
            ],
            "created_at": row["created_at"],
            "verified_at": row["verified_at"],
        }

    def list_for_case(self, case_id: str) -> list[dict[str, Any]]:
        self.research_service.store.get_case(case_id)
        rows = self.connection.execute(
            "SELECT certificate_hash FROM language_limit_artifacts WHERE case_id = ? ORDER BY created_at", (case_id,)
        ).fetchall()
        return [self.get(row["certificate_hash"]) for row in rows]

    def propose_refinement(
        self, *, case_id: str, snapshot_spec: dict[str, Any], discovery_cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.research_service.store.get_case(case_id)
        snapshot = build_language_snapshot(snapshot_spec)
        proposal = propose_missing_primitive(snapshot, discovery_cases)
        self.connection.execute(
            """INSERT INTO language_refinement_experiments
               (proposal_hash, case_id, snapshot_json, proposal_json, status, created_at)
               VALUES (?, ?, ?, ?, 'FROZEN_PENDING_PROSPECTIVE_TEST', ?)""",
            (proposal["proposal_hash"], case_id, _stable(snapshot), _stable(proposal), _now()),
        )
        artifact_dir = self.research_service.store.case_dir(case_id) / "language_refinement" / proposal["proposal_hash"]
        artifact_dir.mkdir(parents=True, exist_ok=False)
        (artifact_dir / "proposal.json").write_text(
            json.dumps(proposal, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        self.connection.commit()
        return self.get_refinement(proposal["proposal_hash"])

    def evaluate_refinement(self, *, proposal_hash: str, evaluation_cases: list[dict[str, Any]]) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM language_refinement_experiments WHERE proposal_hash = ?", (proposal_hash,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown language refinement proposal: {proposal_hash}")
        row = dict(row)
        if row["evaluation_json"] is not None:
            raise ValueError("this frozen refinement proposal has already been evaluated")
        evaluation = evaluate_missing_primitive(
            json.loads(row["snapshot_json"]), json.loads(row["proposal_json"]), evaluation_cases
        )
        status = "PROSPECTIVE_REFINEMENT_SURVIVED" if evaluation["strict_improvement"] else "PROSPECTIVE_REFINEMENT_FAILED"
        self.connection.execute(
            """UPDATE language_refinement_experiments
               SET status = ?, evaluation_json = ?, evaluated_at = ? WHERE proposal_hash = ?""",
            (status, _stable(evaluation), _now(), proposal_hash),
        )
        artifact_dir = self.research_service.store.case_dir(row["case_id"]) / "language_refinement" / proposal_hash
        (artifact_dir / "evaluation.json").write_text(
            json.dumps(evaluation, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        self.connection.commit()
        return self.get_refinement(proposal_hash)

    def get_refinement(self, proposal_hash: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM language_refinement_experiments WHERE proposal_hash = ?", (proposal_hash,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown language refinement proposal: {proposal_hash}")
        item = dict(row)
        return {
            "case_id": item["case_id"],
            "status": item["status"],
            "snapshot": json.loads(item["snapshot_json"]),
            "proposal": json.loads(item["proposal_json"]),
            "evaluation": json.loads(item["evaluation_json"]) if item["evaluation_json"] else None,
            "created_at": item["created_at"],
            "evaluated_at": item["evaluated_at"],
        }

    def verify(
        self,
        *,
        certificate_hash: str,
        certificate: dict[str, Any] | None = None,
        provenance_hashes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Verify only the exact stored envelope; any mutation rejects before Lean."""
        row = self._row(certificate_hash)
        stored = json.loads(row["certificate_json"])
        supplied = certificate if certificate is not None else stored
        rendered: dict[str, Any] | None = None
        try:
            if _stable(supplied) != _stable(stored):
                raise ValueError("supplied certificate differs from the frozen case artifact")
            if certificate_hash != stored["certificate_hash"]:
                raise ValueError("certificate hash does not match the stored artifact")
            current_case = self.research_service.store.get_case(row["case_id"])
            if stored["provenance_hashes"].get("case_file_set_sha256") != _case_file_set_hash(current_case):
                raise ValueError("originating case file provenance changed after certificate freeze")
            if provenance_hashes is not None and content_hash(provenance_hashes) != row["provenance_hash"]:
                raise ValueError("provenance differs from the frozen case artifact")
            rendered = render_frozen_language_limit_lean_source(stored)
            receipt = self._run_lean(rendered, stored)
            status = "LEAN_VERIFIED_FINITE" if receipt["build_result"] == "accepted" else "LEAN_REJECTED"
        except Exception as exc:
            receipt_body = {
                "schema": RECEIPT_SCHEMA,
                "source_json_hash": stored["certificate_hash"],
                "generated_lean_hash": rendered["generated_lean_hash"] if rendered else None,
                "theorem_kernel_version": LEAN_KERNEL_VERSION,
                "kernel_manifest_sha256": LEAN_KERNEL_MANIFEST_SHA256,
                "lean_version": None,
                "mathlib_version": "v4.32.1",
                "build_result": "rejected",
                "scope_verified": "none",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            receipt = receipt_body | {"receipt_hash": content_hash(receipt_body)}
            status = "LEAN_REJECTED"
        now = _now()
        attempt_uuid = uuid4().hex
        attempt_id = f"attempt_{attempt_uuid}"
        artifact_dir = self.research_service.store.case_dir(row["case_id"]) / "language_limit" / certificate_hash
        # Keep the physical path short enough for Windows verification workers;
        # SQLite retains the full attempt/certificate relationship.
        attempt_dir = self.research_service.store.case_dir(row["case_id"]) / "lla" / attempt_uuid
        attempt_dir.mkdir(parents=True, exist_ok=False)
        if rendered is not None:
            (attempt_dir / "GeneratedCertificate.lean").write_text(rendered["lean_source"], encoding="utf-8")
        (attempt_dir / "verification_receipt.json").write_text(
            json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        self.connection.execute(
            """INSERT INTO language_limit_verification_attempts
               (attempt_id, certificate_hash, status, lean_source_json, receipt_json, attempted_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (attempt_id, certificate_hash, status, _stable(rendered) if rendered else None, _stable(receipt), now),
        )
        was_verified = row["status"] == "LEAN_VERIFIED_FINITE"
        if not was_verified:
            if rendered is not None:
                (artifact_dir / "GeneratedCertificate.lean").write_text(rendered["lean_source"], encoding="utf-8")
            (artifact_dir / "verification_receipt.json").write_text(
                json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        self.connection.execute(
            """UPDATE language_limit_artifacts
               SET status = ?, lean_source_json = ?, receipt_json = ?, verified_at = ?
               WHERE certificate_hash = ? AND status <> 'LEAN_VERIFIED_FINITE'""",
            (status, _stable(rendered) if rendered else None, _stable(receipt), now, certificate_hash),
        )
        if status == "LEAN_VERIFIED_FINITE" and not was_verified:
            evidence_id = self.research_service.ledger.add_evidence(
                f"orbita://language-limit/{certificate_hash}/receipt",
                "Lean accepted the exact finite representational-hole certificate.",
                source_kind=EvidenceKind.FORMAL_PROOF,
                independence_key=f"lean:{receipt['receipt_hash']}",
                content=_stable(receipt),
                metadata={"case_id": row["case_id"], "certificate_hash": certificate_hash, "bounded_status": status},
                actor="orbita-language-limit-checker",
                actor_role=ActorRole.TOOL,
            )
            self.research_service.ledger.attest(
                row["claim_id"], evidence_id, Stance.SUPPORT,
                actor="orbita-language-limit-checker", actor_role=ActorRole.TOOL,
            )
            self.research_service.memory.record_epistemic_contract(
                row["claim_id"],
                evidence_status=EvidenceStatus.EXHAUSTIVELY_VERIFIED_FINITE_DOMAIN,
                claim_scope={
                    "domain": "frozen_finite_representation",
                    "quantifier": "finite_all",
                    "boundary": {"case_id": row["case_id"], "source_cases_sha256": row["source_cases_hash"]},
                    "assumptions": ["exact JSON equality defines representation fibers"],
                    "computational_model": "OrbitaLanguageLimit Lean kernel",
                    "representation_language": json.loads(row["snapshot_json"])["name"],
                },
                falsification_coverage={
                    "tested_domains": ["exact frozen finite world set"],
                    "tested_sizes": [len(stored["X"])],
                    "known_uncovered_regions": ["all worlds outside the frozen finite set"],
                    "exhaustive": True,
                    "formal_proof_receipt": receipt["receipt_hash"],
                },
                reason="Lean verified the concrete witness and gap; scope remains finite and is not universalized.",
            )
        self.connection.commit()
        return self.get(certificate_hash)

    def _run_lean(self, rendered: dict[str, Any], certificate: dict[str, Any]) -> dict[str, Any]:
        kernel_root = self.config.lean_kernel_root
        if kernel_root is None:
            raise RuntimeError("ORBITA_LANGUAGE_LIMIT_KERNEL_ROOT is not configured")
        kernel_root = kernel_root.resolve()
        manifest = kernel_root / "MANIFEST.json"
        if not manifest.is_file() or _sha256_file(manifest) != LEAN_KERNEL_MANIFEST_SHA256:
            raise RuntimeError("configured theorem kernel does not match the frozen 0.2.0-rc1 manifest")
        executable = shutil.which(self.config.lean_executable) or self.config.lean_executable
        with tempfile.TemporaryDirectory(prefix="orbita-lean-") as temporary:
            source_path = Path(temporary) / "GeneratedCertificate.lean"
            source_path.write_text(rendered["lean_source"], encoding="utf-8")
            completed = subprocess.run(
                [executable, "env", "lean", str(source_path)],
                cwd=kernel_root,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        version = subprocess.run(
            [executable, "env", "lean", "--version"], cwd=kernel_root, capture_output=True, text=True, timeout=30, check=False
        )
        stdout_hash = hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest()
        stderr_hash = hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest()
        body = {
            "schema": RECEIPT_SCHEMA,
            "source_json_hash": certificate["certificate_hash"],
            "generated_lean_hash": rendered["generated_lean_hash"],
            "theorem_kernel_version": LEAN_KERNEL_VERSION,
            "kernel_manifest_sha256": LEAN_KERNEL_MANIFEST_SHA256,
            "lean_version": version.stdout.strip(),
            "mathlib_version": "v4.32.1",
            "build_result": "accepted" if completed.returncode == 0 else "rejected",
            "exit_code": completed.returncode,
            "stdout_sha256": stdout_hash,
            "stderr_sha256": stderr_hash,
            "scope_verified": "exactly_the_frozen_finite_worlds" if completed.returncode == 0 else "none",
            "claim_status": "LEAN_VERIFIED_FINITE" if completed.returncode == 0 else "LEAN_REJECTED",
            "universal_theorem_claimed": False,
            "llm_prose_used_as_proof": False,
        }
        return body | {"receipt_hash": content_hash(body)}
