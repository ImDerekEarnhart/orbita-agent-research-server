"""Adopt a pre-tenancy research workspace into one tenant's directory.

Before per-tenant scoping there was exactly one database and one workspace at the
agent home. Those files are still there and still correct; they are simply no longer
where a tenant-scoped gateway looks. This copies them into the tenant directory.

Three rules, because this runs against a mounted volume holding research that is not
reproducible:

  * It copies. The originals are never moved, renamed, or deleted, so the pre-migration
    state remains available as a rollback with no further action.
  * It verifies. Every copied file is compared by sha256, and the case count in the
    copied database is compared against the source, before the migration reports success.
  * It refuses rather than overwrites. A tenant directory that already holds a database
    is left untouched unless explicitly told otherwise.

Dry-run is the default everywhere, including the CLI.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AgentConfig


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_ids(database: Path) -> list[str]:
    """Read case ids straight from sqlite, without going through the gateway."""
    if not database.exists():
        return []
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT id FROM research_cases ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [row[0] for row in rows]


def _match_ownership(source: Path, target: Path) -> dict[str, Any]:
    """Give the migrated tree the same owner as the home it came from.

    Migration is normally run by an operator over a shell, which on a container is
    root, while the service itself runs as an unprivileged user. Files copied as root
    are readable but the service cannot create anything beside them, so the tenant
    loads and then fails on the first write with a bare permission error. Copying the
    source home's ownership keeps the migrated tree usable by whoever runs the server.

    Not applicable on Windows, and a no-op when the caller lacks the privilege to
    change ownership — in that case the caller already owns what it just created.
    """
    if not hasattr(os, "chown"):
        return {"applied": False, "reason": "not a POSIX platform"}
    try:
        stat = source.stat()
        for path in [target, *target.rglob("*")]:
            os.chown(path, stat.st_uid, stat.st_gid)
    except (PermissionError, OSError) as exc:
        return {"applied": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"applied": True, "uid": stat.st_uid, "gid": stat.st_gid}


@dataclass
class MigrationPlan:
    tenant: str
    source_home: Path
    target_home: Path
    files: list[tuple[Path, Path]] = field(default_factory=list)
    directories: list[tuple[Path, Path]] = field(default_factory=list)
    source_case_ids: list[str] = field(default_factory=list)
    blocked: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.files and not self.directories

    def describe(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
            "source_home": str(self.source_home),
            "target_home": str(self.target_home),
            "files": [str(src.name) for src, _ in self.files],
            "directories": [str(src.name) for src, _ in self.directories],
            "source_case_count": len(self.source_case_ids),
            "source_case_ids": self.source_case_ids,
            "blocked": self.blocked,
        }


# State that belongs to the caller and must follow them into their tenant directory.
# orbita_oauth.db and orbita_tenants.db are deliberately absent: those are deployment
# level, they stay at the base home, and copying them into a tenant would duplicate the
# identity records that decide who may sign in at all.
TENANT_FILES = ("orbita_agent.db", "orbita_improvements.db")
TENANT_DIRECTORIES = ("workspace", "exports")


def plan_migration(base: AgentConfig, tenant: str) -> MigrationPlan:
    target = base.for_tenant(tenant)
    plan = MigrationPlan(tenant=tenant, source_home=base.home, target_home=target.home)

    if target.db_path.exists():
        plan.blocked = (
            f"{target.db_path} already exists; this tenant has been migrated or has its "
            "own research already. Refusing to overwrite it."
        )
        return plan

    for name in TENANT_FILES:
        source = base.home / name
        if source.exists():
            plan.files.append((source, target.home / name))

    for name in TENANT_DIRECTORIES:
        source = base.home / name
        if source.is_dir() and any(source.iterdir()):
            plan.directories.append((source, target.home / name))

    plan.source_case_ids = _case_ids(base.db_path)
    return plan


def apply_migration(plan: MigrationPlan) -> dict[str, Any]:
    """Copy and verify. Raises before reporting success if anything fails to match."""
    if plan.blocked:
        raise RuntimeError(plan.blocked)
    if plan.is_empty:
        return {"migrated": False, "reason": "nothing to migrate", **plan.describe()}

    plan.target_home.mkdir(parents=True, exist_ok=True)

    copied_files: list[dict[str, str]] = []
    for source, destination in plan.files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        source_digest = _sha256(source)
        destination_digest = _sha256(destination)
        if source_digest != destination_digest:
            raise RuntimeError(
                f"copy of {source} did not verify: {source_digest} != {destination_digest}"
            )
        copied_files.append({"name": source.name, "sha256": source_digest})

    copied_directories: list[dict[str, Any]] = []
    for source, destination in plan.directories:
        shutil.copytree(source, destination, dirs_exist_ok=True)
        mismatches = []
        source_files = sorted(p for p in source.rglob("*") if p.is_file())
        for path in source_files:
            mirrored = destination / path.relative_to(source)
            if not mirrored.exists() or _sha256(path) != _sha256(mirrored):
                mismatches.append(str(path.relative_to(source)))
        if mismatches:
            raise RuntimeError(
                f"{len(mismatches)} file(s) under {source} did not verify after copy: "
                + ", ".join(mismatches[:5])
            )
        copied_directories.append({"name": source.name, "file_count": len(source_files)})

    ownership = _match_ownership(plan.source_home, plan.target_home)

    target_case_ids = _case_ids(plan.target_home / "orbita_agent.db")
    if target_case_ids != plan.source_case_ids:
        missing = set(plan.source_case_ids) - set(target_case_ids)
        raise RuntimeError(
            f"case ids did not match after migration; {len(missing)} missing: "
            + ", ".join(sorted(missing)[:5])
        )

    return {
        "migrated": True,
        "files": copied_files,
        "directories": copied_directories,
        "case_count": len(target_case_ids),
        "case_ids": target_case_ids,
        "source_preserved_at": str(plan.source_home),
        "ownership": ownership,
        **plan.describe(),
    }
