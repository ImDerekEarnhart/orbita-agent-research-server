"""Single-use upload tickets for files too large to pass through an MCP tool call.

Inline uploads travel inside the MCP protocol and are capped at 8 MB of text. A real
archive is two to three orders of magnitude larger, so the bytes have to go over plain
HTTP instead. That raises the question the rest of this module answers: how does an
unauthenticated HTTP route know which tenant's workspace it is writing into?

The answer is a capability. The authenticated MCP call does all the deciding — it
resolves the caller to a tenant, checks that the case is theirs, and mints a ticket
bound to exactly one tenant, one case, one filename, and one size ceiling. The upload
route then needs no session of its own: presenting the ticket *is* the authorization,
and it works exactly once.

Only the sha256 of each ticket is stored. Reading this database gives an attacker
nothing usable, the same reason a password table stores hashes.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Formats the ingestor can actually parse, plus the archives this exists to carry.
UPLOAD_SUFFIXES = {
    ".csv", ".tsv", ".json", ".jsonl", ".txt", ".md", ".py", ".r", ".tex",
    ".sql", ".yaml", ".yml", ".toml", ".ipynb",
    ".xlsx", ".xlsm", ".xls", ".parquet", ".pdf", ".docx", ".zip",
}

DEFAULT_TTL_SECONDS = 1800
DEFAULT_MAX_UPLOAD_BYTES = 500_000_000

# Refuse to accept an upload that would leave the volume with less headroom than this.
# The volume also holds every tenant's research; filling it to write one archive would
# take down the service for everyone.
DISK_HEADROOM_BYTES = 500_000_000


class UploadError(RuntimeError):
    pass


def _hash_ticket(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def safe_upload_filename(filename: str) -> str:
    name = Path(filename).name
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._-").strip("._")
    if not cleaned:
        raise UploadError("filename must contain at least one safe character")
    if len(cleaned) > 120:
        stem, suffix = Path(cleaned).stem[:100], Path(cleaned).suffix[:16]
        cleaned = stem + suffix
    suffix = Path(cleaned).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        allowed = ", ".join(sorted(UPLOAD_SUFFIXES))
        raise UploadError(f"upload type {suffix or '(none)'} is not accepted. Use one of: {allowed}")
    return cleaned


@dataclass(frozen=True)
class UploadTicket:
    ticket_hash: str
    tenant: str
    case_id: str
    filename: str
    max_bytes: int
    expires_at: int

    def public(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "filename": self.filename,
            "max_bytes": self.max_bytes,
            "expires_at": self.expires_at,
            "expires_in_seconds": max(0, self.expires_at - int(time.time())),
        }


class UploadTicketStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS upload_tickets (
                    ticket_hash TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    max_bytes INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS upload_tickets_expiry_idx
                    ON upload_tickets(expires_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def mint(
        self,
        *,
        tenant: str,
        case_id: str,
        filename: str,
        declared_bytes: int,
        max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        volume_path: Path | None = None,
    ) -> tuple[str, UploadTicket]:
        safe_name = safe_upload_filename(filename)
        if declared_bytes <= 0:
            raise UploadError("declared size must be a positive number of bytes")
        if declared_bytes > max_bytes:
            raise UploadError(f"declared size {declared_bytes} exceeds the {max_bytes} byte limit")

        if volume_path is not None:
            free = shutil.disk_usage(volume_path).free
            if declared_bytes + DISK_HEADROOM_BYTES > free:
                raise UploadError(
                    f"refusing this upload: {declared_bytes} bytes would leave the volume under "
                    f"{DISK_HEADROOM_BYTES} bytes of headroom ({free} free). This volume holds "
                    "every tenant's research."
                )

        ticket = secrets.token_urlsafe(32)
        now = int(time.time())
        record = UploadTicket(
            ticket_hash=_hash_ticket(ticket),
            tenant=tenant,
            case_id=case_id,
            filename=safe_name,
            max_bytes=min(declared_bytes * 2, max_bytes),
            expires_at=now + ttl_seconds,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO upload_tickets(
                    ticket_hash, tenant, case_id, filename, max_bytes, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.ticket_hash,
                    tenant,
                    case_id,
                    safe_name,
                    record.max_bytes,
                    now,
                    record.expires_at,
                ),
            )
        return ticket, record

    def claim(self, ticket: str) -> UploadTicket:
        """Atomically consume a ticket, or refuse.

        The UPDATE carries the unconsumed and unexpired conditions, so two requests
        racing on the same ticket cannot both win.
        """
        ticket_hash = _hash_ticket(ticket)
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE upload_tickets SET consumed_at = ?
                WHERE ticket_hash = ? AND consumed_at IS NULL AND expires_at > ?
                """,
                (now, ticket_hash, now),
            )
            if cursor.rowcount != 1:
                raise UploadError("this upload ticket is unknown, already used, or expired")
            row = connection.execute(
                "SELECT * FROM upload_tickets WHERE ticket_hash = ?", (ticket_hash,)
            ).fetchone()
        return UploadTicket(
            ticket_hash=row["ticket_hash"],
            tenant=row["tenant"],
            case_id=row["case_id"],
            filename=row["filename"],
            max_bytes=row["max_bytes"],
            expires_at=row["expires_at"],
        )

    def purge_expired(self, older_than_seconds: int = 86_400) -> int:
        cutoff = int(time.time()) - older_than_seconds
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM upload_tickets WHERE expires_at < ?", (cutoff,)
            )
            return cursor.rowcount


def max_upload_bytes() -> int:
    raw = os.getenv("ORBITA_AGENT_MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("ORBITA_AGENT_MAX_UPLOAD_BYTES must be an integer") from exc
    if value < 1:
        raise RuntimeError("ORBITA_AGENT_MAX_UPLOAD_BYTES must be positive")
    return value
