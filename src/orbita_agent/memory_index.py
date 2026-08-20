"""Searchable memory over ingested archives, where every hit carries its source.

This is deliberately the least clever component in the system. It does not summarize,
infer, rank by relevance to a goal, or decide what a message meant. It finds messages
containing terms and hands back enough identifiers to point at exactly where each one
came from: the conversation, the node inside that conversation, the timestamp, and the
file and case it was ingested from.

That restraint is the product. A memory tool that paraphrases your history is a tool
you cannot check. Every result here can be traced to a specific node in a specific
conversation in a specific upload, and the caller is told the timestamp so they can see
when a thing was said rather than assuming it is still true.

No epistemic status is attached to anything. A message being present means it was
written, not that it was correct, and nothing in this module implies otherwise.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

# Rows carried from the parsed export into the index. Each is either an identifier
# needed to cite the message or a field a caller may reasonably filter on.
INDEXED_COLUMNS = (
    "conversation_id",
    "conversation_title",
    "node_id",
    "role",
    "created_at",
    "create_time",
    "content_type",
    "text",
)


def _match_query(query: str) -> str:
    """Turn free text into an FTS5 MATCH expression that cannot be a syntax error.

    Callers type questions, not query syntax. Quoting each term means a stray asterisk
    or parenthesis produces no results rather than a database error, and nobody can
    inject FTS operators through the search box.
    """
    terms = [term for term in re.findall(r"[\w']+", query or "") if term]
    if not terms:
        raise ValueError("search query must contain at least one word")
    return " ".join(f'"{term}"' for term in terms)


class MemoryIndex:
    """One tenant's searchable archive memory."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.database_path, timeout=15, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._initialize()

    def _initialize(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                source_name TEXT,
                conversation_id TEXT,
                conversation_title TEXT,
                node_id TEXT,
                role TEXT,
                created_at TEXT,
                create_time REAL,
                content_type TEXT,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS memory_messages_case_idx ON memory_messages(case_id);
            CREATE INDEX IF NOT EXISTS memory_messages_file_idx ON memory_messages(file_id);
            CREATE INDEX IF NOT EXISTS memory_messages_conv_idx ON memory_messages(conversation_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                text, content='memory_messages', content_rowid='message_id'
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- writing --------------------------------------------------------------

    def index_frame(
        self, *, case_id: str, file_id: str, source_name: str, frame: pd.DataFrame
    ) -> dict[str, Any]:
        """Index one parsed export. Re-indexing the same file replaces its rows."""
        if frame.empty:
            return {"indexed": 0, "file_id": file_id}

        missing = [column for column in INDEXED_COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f"parsed export is missing expected columns: {', '.join(missing)}")

        self.delete_file(file_id)

        rows = []
        for record in frame.to_dict("records"):
            text = str(record.get("text") or "").strip()
            if not text:
                continue
            rows.append(
                (
                    case_id,
                    file_id,
                    source_name,
                    record.get("conversation_id"),
                    record.get("conversation_title"),
                    record.get("node_id"),
                    record.get("role"),
                    record.get("created_at"),
                    record.get("create_time"),
                    record.get("content_type"),
                    text,
                )
            )

        with self.conn:
            cursor = self.conn.executemany(
                """
                INSERT INTO memory_messages(
                    case_id, file_id, source_name, conversation_id, conversation_title,
                    node_id, role, created_at, create_time, content_type, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            del cursor
            # Rebuild rather than trigger-sync: the external-content table and the index
            # cannot disagree if the index is derived from the table in one step.
            self.conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")

        return {"indexed": len(rows), "file_id": file_id, "case_id": case_id}

    # -- deletion, which G22 requires be demonstrable ---------------------------

    def delete_file(self, file_id: str) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM memory_messages WHERE file_id = ?", (file_id,)
            )
            self.conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
        return cursor.rowcount

    def delete_case(self, case_id: str) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM memory_messages WHERE case_id = ?", (case_id,)
            )
            self.conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
        return cursor.rowcount

    def delete_everything(self) -> int:
        with self.conn:
            cursor = self.conn.execute("DELETE FROM memory_messages")
            self.conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
        return cursor.rowcount

    # -- reading --------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        case_id: str | None = None,
        role: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        where = ["memory_fts MATCH ?"]
        params: list[Any] = [_match_query(query)]
        if case_id:
            where.append("m.case_id = ?")
            params.append(case_id)
        if role:
            where.append("m.role = ?")
            params.append(role)
        if conversation_id:
            where.append("m.conversation_id = ?")
            params.append(conversation_id)
        params.append(limit)

        rows = self.conn.execute(
            f"""
            SELECT m.*, snippet(memory_fts, 0, '[', ']', ' … ', 24) AS snippet,
                   bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memory_messages m ON m.message_id = memory_fts.rowid
            WHERE {' AND '.join(where)}
            ORDER BY rank
            LIMIT ?
            """,
            params,
        ).fetchall()

        return {
            "query": query,
            "hit_count": len(rows),
            "hits": [self._hit(row) for row in rows],
            "boundary": (
                "These are messages that contain your search terms. Presence means the "
                "message was written, not that it was true then or is true now. Check the "
                "timestamp on each hit before relying on it."
            ),
        }

    @staticmethod
    def _hit(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "snippet": row["snippet"],
            "text": row["text"],
            "role": row["role"],
            "created_at": row["created_at"],
            # Everything needed to find this exact message again in the original export.
            "receipt": {
                "conversation_id": row["conversation_id"],
                "conversation_title": row["conversation_title"],
                "node_id": row["node_id"],
                "case_id": row["case_id"],
                "file_id": row["file_id"],
                "source_name": row["source_name"],
            },
        }

    def conversation(self, conversation_id: str, *, limit: int = 200) -> dict[str, Any]:
        """Return one conversation in order, for reading a hit in context."""
        limit = max(1, min(int(limit), 1_000))
        rows = self.conn.execute(
            """
            SELECT * FROM memory_messages
            WHERE conversation_id = ?
            ORDER BY create_time IS NULL, create_time, message_id
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
        return {
            "conversation_id": conversation_id,
            "title": rows[0]["conversation_title"] if rows else None,
            "message_count": len(rows),
            "messages": [
                {
                    "role": row["role"],
                    "created_at": row["created_at"],
                    "node_id": row["node_id"],
                    "text": row["text"],
                }
                for row in rows
            ],
        }

    def stats(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS messages,
                   COUNT(DISTINCT conversation_id) AS conversations,
                   COUNT(DISTINCT file_id) AS files,
                   MIN(created_at) AS earliest,
                   MAX(created_at) AS latest
            FROM memory_messages
            """
        ).fetchone()
        roles = self.conn.execute(
            "SELECT role, COUNT(*) AS n FROM memory_messages GROUP BY role ORDER BY n DESC"
        ).fetchall()
        return {
            "messages": row["messages"],
            "conversations": row["conversations"],
            "files": row["files"],
            "earliest": row["earliest"],
            "latest": row["latest"],
            "roles": {r["role"]: r["n"] for r in roles},
        }


def chat_export_members(record: dict[str, Any]) -> Iterable[tuple[str, Path]]:
    """Yield (label, normalized-csv path) for every chat export in one ingested file.

    An export usually arrives inside a zip, so the export is a member of an archive
    record rather than the record itself. Both shapes are handled.
    """
    if record.get("artifact_kind") == "chat_export" and record.get("extracted_path"):
        yield record.get("original_name") or "chat export", Path(record["extracted_path"])
        return

    profile = record.get("profile") or {}
    for member in profile.get("members", []) or []:
        if member.get("artifact_kind") == "chat_export" and member.get("extracted_path"):
            yield member.get("name") or "chat export", Path(member["extracted_path"])
