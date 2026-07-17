from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    """Filesystem and resource limits for the local agent gateway."""

    home: Path
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
    def workspace(self) -> Path:
        return self.home / "workspace"

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
