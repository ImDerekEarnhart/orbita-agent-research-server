from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEGACY_BINDING_SOURCE = "legacy-single-principal"
ENV_BINDING_SOURCE = "env-seed"
OPERATOR_BINDING_SOURCE = "operator"


class TenantResolutionError(RuntimeError):
    """Raised when an authenticated identity has no Discovery Genome tenant."""


def _now() -> int:
    return int(time.time())


def _normalize_subject(subject: str) -> str:
    value = (subject or "").strip()
    if not value:
        raise ValueError("subject must not be empty")
    return value


def _normalize_username(username: str) -> str:
    value = (username or "").strip()
    if not value:
        raise ValueError("genome username must not be empty")
    return value


@dataclass(frozen=True)
class TenantBinding:
    subject: str
    genome_username: str
    bound_at: int
    bound_by: str
    note: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "genome_username": self.genome_username,
            "bound_at": self.bound_at,
            "bound_by": self.bound_by,
            "note": self.note,
        }


@dataclass(frozen=True)
class LegacySinglePrincipal:
    """The pre-multi-tenant configuration: one allowed GitHub user, one Genome username.

    This exists so the current single-owner deployment keeps working with no variable
    changes. It disengages the moment a second GitHub user is allowed, at which point
    explicit bindings become mandatory.
    """

    github_login: str
    genome_username: str

    @classmethod
    def from_env(cls) -> LegacySinglePrincipal | None:
        username = os.getenv("ORBITA_DISCOVERY_GENOME_USERNAME", "").strip()
        if not username:
            return None
        logins = [
            value.strip()
            for value in os.getenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "").split(",")
            if value.strip()
        ]
        distinct = {login.casefold() for login in logins}
        if len(distinct) != 1:
            return None
        return cls(github_login=logins[0], genome_username=username)


class TenantRegistry:
    """Maps an authenticated MCP subject to exactly one Discovery Genome tenant.

    The registry is the authorization gate. The GitHub allowlist is only the admission
    gate: being allowed to sign in never implies access to a tenant. Resolution fails
    closed — an unbound subject is refused, never silently served a default tenant.
    """

    def __init__(
        self,
        database_path: Path,
        *,
        legacy: LegacySinglePrincipal | None = None,
        allow_shared_tenants: bool = False,
    ) -> None:
        self.database_path = database_path
        self.legacy = legacy
        self.allow_shared_tenants = allow_shared_tenants
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS genome_tenant_bindings (
                    subject TEXT PRIMARY KEY,
                    genome_username TEXT NOT NULL,
                    bound_at INTEGER NOT NULL,
                    bound_by TEXT NOT NULL,
                    note TEXT
                );
                CREATE TABLE IF NOT EXISTS observed_identities (
                    subject TEXT PRIMARY KEY,
                    login TEXT NOT NULL,
                    first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenant_binding_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    genome_username TEXT,
                    actor TEXT NOT NULL,
                    note TEXT
                );
                CREATE INDEX IF NOT EXISTS tenant_binding_username_idx
                    ON genome_tenant_bindings(genome_username);
                """
            )

    def _record_event(
        self,
        connection: sqlite3.Connection,
        *,
        action: str,
        subject: str,
        genome_username: str | None,
        actor: str,
        note: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO tenant_binding_events(
                occurred_at, action, subject, genome_username, actor, note
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_now(), action, subject, genome_username, actor, note),
        )

    # -- identity observation -------------------------------------------------

    def record_identity(self, subject: str, login: str) -> None:
        """Remember which GitHub login a subject belongs to, for operator binding.

        Only identities that already passed the sign-in allowlist should reach this,
        so the table cannot be filled by unauthenticated callers.
        """
        subject = _normalize_subject(subject)
        login = (login or "").strip()
        if not login:
            return
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO observed_identities(subject, login, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(subject) DO UPDATE SET login = excluded.login, last_seen_at = excluded.last_seen_at
                """,
                (subject, login, now, now),
            )

    def list_identities(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM observed_identities ORDER BY last_seen_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def subject_login(self, subject: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT login FROM observed_identities WHERE subject = ?", (subject,)
            ).fetchone()
        return row["login"] if row else None

    # -- bindings -------------------------------------------------------------

    def bind(
        self,
        subject: str,
        genome_username: str,
        *,
        actor: str = OPERATOR_BINDING_SOURCE,
        note: str | None = None,
        allow_shared: bool = False,
        overwrite: bool = False,
    ) -> TenantBinding:
        subject = _normalize_subject(subject)
        genome_username = _normalize_username(genome_username)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM genome_tenant_bindings WHERE subject = ?", (subject,)
            ).fetchone()
            if existing is not None and not overwrite:
                if existing["genome_username"] == genome_username:
                    return TenantBinding(
                        subject=existing["subject"],
                        genome_username=existing["genome_username"],
                        bound_at=existing["bound_at"],
                        bound_by=existing["bound_by"],
                        note=existing["note"],
                    )
                raise TenantResolutionError(
                    "subject is already bound to a different Discovery Genome tenant; "
                    "unbind it explicitly before rebinding"
                )
            if not (allow_shared or self.allow_shared_tenants):
                conflict = connection.execute(
                    """
                    SELECT subject FROM genome_tenant_bindings
                    WHERE genome_username = ? AND subject <> ?
                    """,
                    (genome_username, subject),
                ).fetchone()
                if conflict is not None:
                    raise TenantResolutionError(
                        "another subject is already bound to this Discovery Genome tenant; "
                        "pass allow_shared to bind collaborators deliberately"
                    )
            now = _now()
            connection.execute(
                """
                INSERT INTO genome_tenant_bindings(subject, genome_username, bound_at, bound_by, note)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(subject) DO UPDATE SET
                    genome_username = excluded.genome_username,
                    bound_at = excluded.bound_at,
                    bound_by = excluded.bound_by,
                    note = excluded.note
                """,
                (subject, genome_username, now, actor, note),
            )
            self._record_event(
                connection,
                action="bind",
                subject=subject,
                genome_username=genome_username,
                actor=actor,
                note=note,
            )
        return TenantBinding(
            subject=subject,
            genome_username=genome_username,
            bound_at=now,
            bound_by=actor,
            note=note,
        )

    def unbind(self, subject: str, *, actor: str = OPERATOR_BINDING_SOURCE) -> bool:
        subject = _normalize_subject(subject)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT genome_username FROM genome_tenant_bindings WHERE subject = ?", (subject,)
            ).fetchone()
            if row is None:
                return False
            connection.execute("DELETE FROM genome_tenant_bindings WHERE subject = ?", (subject,))
            self._record_event(
                connection,
                action="unbind",
                subject=subject,
                genome_username=row["genome_username"],
                actor=actor,
            )
        return True

    def list_bindings(self) -> list[TenantBinding]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM genome_tenant_bindings ORDER BY bound_at DESC"
            ).fetchall()
        return [
            TenantBinding(
                subject=row["subject"],
                genome_username=row["genome_username"],
                bound_at=row["bound_at"],
                bound_by=row["bound_by"],
                note=row["note"],
            )
            for row in rows
        ]

    def list_events(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5_000))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tenant_binding_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def _lookup(self, subject: str) -> TenantBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM genome_tenant_bindings WHERE subject = ?", (subject,)
            ).fetchone()
        if row is None:
            return None
        return TenantBinding(
            subject=row["subject"],
            genome_username=row["genome_username"],
            bound_at=row["bound_at"],
            bound_by=row["bound_by"],
            note=row["note"],
        )

    # -- seeding --------------------------------------------------------------

    def seed_from_env(self) -> list[TenantBinding]:
        """Apply ORBITA_GENOME_TENANT_BINDINGS idempotently.

        The value is a JSON object mapping subject to Genome username, for example
        {"github:1234": "alice", "github:5678": "bob"}. Operator bindings already in
        the registry are never overwritten by the environment.
        """
        raw = os.getenv("ORBITA_GENOME_TENANT_BINDINGS", "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ORBITA_GENOME_TENANT_BINDINGS must be a JSON object") from exc
        if not isinstance(parsed, dict) or not parsed:
            raise RuntimeError("ORBITA_GENOME_TENANT_BINDINGS must be a non-empty JSON object")

        applied: list[TenantBinding] = []
        for subject, username in parsed.items():
            if not isinstance(subject, str) or not isinstance(username, str):
                raise RuntimeError("ORBITA_GENOME_TENANT_BINDINGS keys and values must be strings")
            existing = self._lookup(subject.strip())
            if existing is not None and existing.bound_by == OPERATOR_BINDING_SOURCE:
                continue
            applied.append(
                self.bind(
                    subject,
                    username,
                    actor=ENV_BINDING_SOURCE,
                    note="seeded from ORBITA_GENOME_TENANT_BINDINGS",
                    overwrite=True,
                    allow_shared=True,
                )
            )
        return applied

    # -- resolution -----------------------------------------------------------

    def resolve(self, subject: str | None) -> str:
        """Return the Genome username for an authenticated subject, or refuse.

        Never returns a default tenant. An unbound subject is an authorization
        failure, not a reason to fall back.
        """
        if not subject or not subject.strip():
            raise TenantResolutionError(
                "the Discovery Genome requires an authenticated identity; this request had none"
            )
        subject = subject.strip()

        binding = self._lookup(subject)
        if binding is not None:
            return binding.genome_username

        legacy = self.legacy
        if legacy is not None:
            # Legacy mode allows exactly one GitHub login to sign in at all, so any
            # subject holding a valid token must be that user. The observed login is
            # checked when it is known, but its absence cannot block resolution: a
            # deployment upgraded while a refresh token is live never re-enters the
            # GitHub callback, so the identity may legitimately not have been observed.
            login = self.subject_login(subject)
            if login is None or login.casefold() == legacy.github_login.casefold():
                self.bind(
                    subject,
                    legacy.genome_username,
                    actor=LEGACY_BINDING_SOURCE,
                    note="auto-bound from the single-principal deployment configuration",
                    allow_shared=True,
                )
                return legacy.genome_username

        raise TenantResolutionError(
            "no Discovery Genome tenant is bound to this identity; "
            "an operator must bind it before Genome tools can be used"
        )

    def describe(self) -> dict[str, Any]:
        return {
            "binding_count": len(self.list_bindings()),
            "legacy_single_principal": self.legacy is not None,
            "shared_tenants_allowed": self.allow_shared_tenants,
            "tenant_selected_by": "authenticated subject binding",
        }


def build_registry(
    home: Path,
    *,
    allow_shared_tenants: bool | None = None,
) -> TenantRegistry:
    if allow_shared_tenants is None:
        allow_shared_tenants = os.getenv(
            "ORBITA_GENOME_ALLOW_SHARED_TENANTS", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
    registry = TenantRegistry(
        home / "orbita_tenants.db",
        legacy=LegacySinglePrincipal.from_env(),
        allow_shared_tenants=allow_shared_tenants,
    )
    registry.seed_from_env()
    return registry
