from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

TENANTS_DIRNAME = "tenants"


def tenant_slug(tenant: str) -> str:
    """Return a filesystem-safe directory name for one tenant.

    The trailing digest is what actually guarantees separation: two tenant names
    that reduce to the same readable stem still get different directories. The
    stem exists only so an operator can recognize a directory by eye.
    """
    value = (tenant or "").strip()
    if not value:
        raise ValueError("tenant must not be empty")
    stem = re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-")[:48]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{digest}" if stem else digest


@dataclass(frozen=True)
class AgentConfig:
    """Filesystem and resource limits for the local agent gateway."""

    home: Path
    tenant: str | None = None
    knowledge_db: Path | None = None
    max_inline_bytes: int = 8_000_000
    max_graph_vertices: int = 128
    max_graph_edges: int = 1_024

    @property
    def db_path(self) -> Path:
        return self.home / "orbita_agent.db"

    @property
    def improvement_db(self) -> Path:
        return self.home / "orbita_improvements.db"

    @property
    def external_experiment_db(self) -> Path:
        return self.home / "orbita_external_experiments.db"

    @property
    def blind_prediction_db(self) -> Path:
        return self.home / "orbita_blind_predictions.db"

    @property
    def blind_scoring_db(self) -> Path:
        return self.home / "orbita_blind_scoring.db"

    @property
    def epistemic_db(self) -> Path:
        return self.home / "orbita_epistemic.db"

    @property
    def execution_workspace(self) -> Path:
        return self.home / "external_executions"

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

    @property
    def memory_db(self) -> Path:
        return self.home / "orbita_memory.db"

    @property
    def inbox(self) -> Path:
        return self.home / "inbox"

    @property
    def exports(self) -> Path:
        return self.home / "exports"

    def ensure(self) -> AgentConfig:
        for path in (self.home, self.workspace, self.inbox, self.exports):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def for_tenant(self, tenant: str) -> AgentConfig:
        """Confine every piece of caller state to one tenant's own directory.

        Isolation is by construction rather than by filtering: the tenant gets its
        own sqlite database and workspace, so another tenant's case id is not merely
        excluded from results, it is absent from the database being queried. A
        forgotten predicate therefore cannot leak a case.

        The curated knowledge store is deliberately left shared — it is read-only
        reference data shipped with the server, not anything a caller wrote.
        """
        return replace(self, home=self.home / TENANTS_DIRNAME / tenant_slug(tenant), tenant=tenant)

    @classmethod
    def from_env(cls) -> AgentConfig:
        default_home = Path.home() / ".orbita-agent"
        home = Path(os.getenv("ORBITA_AGENT_HOME", str(default_home))).expanduser()
        knowledge = os.getenv("ORBITA_AGENT_KNOWLEDGE_DB")
        return cls(
            home=home,
            knowledge_db=Path(knowledge).expanduser() if knowledge else None,
            max_inline_bytes=int(os.getenv("ORBITA_AGENT_MAX_INLINE_BYTES", "8000000")),
            max_graph_vertices=int(os.getenv("ORBITA_AGENT_MAX_GRAPH_VERTICES", "128")),
            max_graph_edges=int(os.getenv("ORBITA_AGENT_MAX_GRAPH_EDGES", "1024")),
        )
