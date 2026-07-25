"""Refuse to store anyone else's personal archive until it can be protected properly.

Per-tenant encryption is specified but not built. Until it is, accepting someone's chat
export would mean holding an unusually complete record of their private life with only
infrastructure-level disk encryption — which defends against a stolen drive and almost
nothing else.

The decision to wait is easy to make and easy to forget. A note in a document does not
survive a tired week before a deadline when someone asks for access and it would take
one moment to say yes. So the decision lives here, as a refusal the server performs,
and opening the door requires deliberately setting a variable whose name says what it
is asserting.

This is not a security boundary against an attacker — an operator can always change
their own configuration. It is a boundary against their own future haste.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENCRYPTION_READY_VAR = "ORBITA_ARCHIVE_ENCRYPTION_REVIEWED"
OPERATOR_TENANT_VAR = "ORBITA_OPERATOR_TENANT"
TRUTHY = {"1", "true", "yes", "on"}


class ArchiveIngestionRefused(RuntimeError):
    """Raised when a tenant may not store a personal archive yet."""


@dataclass(frozen=True)
class ArchivePolicy:
    encryption_reviewed: bool
    operator_tenant: str | None

    @classmethod
    def from_env(cls) -> ArchivePolicy:
        operator = os.getenv(OPERATOR_TENANT_VAR, "").strip()
        if not operator:
            # The single-principal deployment already names its operator; reuse it rather
            # than requiring a second variable that could drift out of agreement with it.
            operator = os.getenv("ORBITA_DISCOVERY_GENOME_USERNAME", "").strip()
        return cls(
            encryption_reviewed=os.getenv(ENCRYPTION_READY_VAR, "").strip().lower() in TRUTHY,
            operator_tenant=operator or None,
        )

    def allows(self, tenant: str | None) -> bool:
        if self.encryption_reviewed:
            return True
        if tenant is None:
            # No tenant means a single-operator deployment: the caller is the operator by
            # construction, because nobody else can reach it.
            return True
        if not self.operator_tenant:
            # Multi-tenant, no operator named, encryption unreviewed. Fail closed: an
            # unconfigured deployment must not be the one that accepts a stranger's
            # medical history.
            return False
        return tenant.casefold() == self.operator_tenant.casefold()

    def refusal(self, tenant: str | None) -> str:
        return (
            "Orbita is not accepting personal archives from anyone but its operator yet. "
            "Per-tenant encryption is specified but not implemented and reviewed, and a "
            "chat export is too complete a record of someone's private life to store "
            "with only infrastructure-level disk encryption. See docs/DATA_STATEMENT.md. "
            f"This opens when {ENCRYPTION_READY_VAR} is set, which should happen only "
            "after the encryption work is actually reviewed."
        )

    def ensure(self, tenant: str | None) -> None:
        if not self.allows(tenant):
            raise ArchiveIngestionRefused(self.refusal(tenant))

    def describe(self) -> dict[str, object]:
        return {
            "accepting_archives_from": (
                "any bound tenant" if self.encryption_reviewed else "the operator only"
            ),
            "encryption_reviewed": self.encryption_reviewed,
            "operator_tenant_configured": bool(self.operator_tenant),
            "reason": None if self.encryption_reviewed else self.refusal(None),
        }
