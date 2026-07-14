from __future__ import annotations

import json
import re
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any


def bundled_knowledge_path() -> Path:
    candidate = resources.files("orbita_agent.resources").joinpath("knowledge.sqlite")
    return Path(str(candidate))


class KnowledgeStore:
    """Read-only, curated research memory shipped with the server."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else bundled_knowledge_path()
        if not self.path.exists():
            raise FileNotFoundError(f"Knowledge database not found: {self.path}")
        self.conn = sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _match_query(query: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9_\-]+", query)
        if not tokens:
            raise ValueError("Search query must contain a word or number")
        return " OR ".join(f'"{token}"' for token in tokens[:12])

    def status(self) -> dict[str, Any]:
        metadata = {
            row["key"]: row["value"]
            for row in self.conn.execute("SELECT key, value FROM metadata ORDER BY key")
        }
        metadata.update(
            {
                "documents": self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "claim_cards": self.conn.execute("SELECT COUNT(*) FROM claim_cards").fetchone()[0],
                "eg_runs": self.conn.execute("SELECT COUNT(*) FROM eg_runs").fetchone()[0],
                "eg_highlights": self.conn.execute("SELECT COUNT(*) FROM eg_highlights").fetchone()[0],
            }
        )
        return metadata

    def search(self, query: str, *, limit: int = 5, source_bundle: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 20))
        match = self._match_query(query)
        where = "documents_fts MATCH ?"
        params: list[Any] = [match]
        if source_bundle:
            where += " AND source_bundle = ?"
            params.append(source_bundle)
        params.append(limit)
        rows = self.conn.execute(
            f"""SELECT doc_id, title, source_bundle, source_path,
                       snippet(documents_fts, 2, '[', ']', ' … ', 32) AS snippet,
                       bm25(documents_fts) AS rank
                FROM documents_fts
                WHERE {where}
                ORDER BY rank
                LIMIT ?""",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def claim_cards(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        if status:
            rows = self.conn.execute(
                "SELECT * FROM claim_cards WHERE status = ? ORDER BY claim_id LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM claim_cards ORDER BY claim_id LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["dependencies"] = json.loads(item.pop("dependencies_json") or "[]")
            item["extra"] = json.loads(item.pop("extra_json") or "{}")
            result.append(item)
        return result

    def eg_summary(self) -> dict[str, Any]:
        fast = self.conn.execute(
            """SELECT COUNT(*) AS runs, SUM(records) AS records, SUM(survivors) AS survivors,
                      SUM(counterexamples) AS counterexamples, SUM(inconclusive) AS inconclusive,
                      SUM(near_misses) AS near_misses, MIN(min_n) AS min_n, MAX(max_n) AS max_n
               FROM eg_runs WHERE relative_path LIKE '%/fast_runs/%'"""
        ).fetchone()
        unique = self.conn.execute(
            """SELECT COUNT(DISTINCT graph_fingerprint)
               FROM eg_highlights h JOIN eg_runs r ON r.run_id = h.run_id
               WHERE r.relative_path LIKE '%/fast_runs/%' AND h.highlight_type = 'near_miss_no_C4'"""
        ).fetchone()[0]
        claims = [dict(row) for row in self.conn.execute("SELECT * FROM eg_claims ORDER BY claim_id")]
        return {
            "fast_run_totals": dict(fast),
            "unique_exact_labeled_fast_near_misses": unique,
            "claims": claims,
            "boundary": (
                "Finite searches and exact certificates only. Repeated deterministic stages occur across the two "
                "fast runs; no universal proof or disproof is claimed."
            ),
        }

    def find_eg_near_misses(
        self,
        *,
        limit: int = 10,
        min_n: int = 0,
        include_certificate: bool = False,
    ) -> list[dict[str, Any]]:
        limit_cap = 3 if include_certificate else 50
        limit = max(1, min(int(limit), limit_cap))
        columns = (
            "h.graph_fingerprint, h.source, h.n, h.m, h.min_degree, h.cycle_rank, h.density, "
            "h.first_power_cycle, h.trial, h.seed, r.relative_path"
        )
        if include_certificate:
            columns += ", h.cycle_json, h.edges_json, h.certificate_json"
        rows = self.conn.execute(
            f"""SELECT {columns}
                FROM eg_highlights h JOIN eg_runs r ON r.run_id = h.run_id
                WHERE r.relative_path LIKE '%/fast_runs/%'
                  AND h.highlight_type = 'near_miss_no_C4' AND h.n >= ?
                GROUP BY h.graph_fingerprint
                ORDER BY h.n DESC, h.graph_fingerprint
                LIMIT ?""",
            (int(min_n), limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("cycle_json", "edges_json", "certificate_json"):
                if key in item and item[key]:
                    item[key.removesuffix("_json")] = json.loads(item.pop(key))
            result.append(item)
        return result
