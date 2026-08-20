"""Public, non-secret provenance for the running service build."""
from __future__ import annotations

import os
import re
from typing import Any

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _clean(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def public_build_provenance() -> dict[str, Any]:
    """Return deployment identity without exposing credentials or mutable secrets."""
    commit = (
        _clean("RAILWAY_GIT_COMMIT_SHA")
        or _clean("GIT_COMMIT_SHA")
        or _clean("SOURCE_COMMIT_SHA")
    )
    commit = commit.casefold() if commit else None
    return {
        "schema": "hodgeform-build-provenance/1",
        "commit_sha": commit if commit and _FULL_SHA.fullmatch(commit) else None,
        "branch": _clean("RAILWAY_GIT_BRANCH") or _clean("GIT_BRANCH"),
        "deployment_id": _clean("RAILWAY_DEPLOYMENT_ID"),
        "service_id": _clean("RAILWAY_SERVICE_ID"),
        "environment_id": _clean("RAILWAY_ENVIRONMENT_ID"),
        "source": "runtime_environment",
        "secrets_included": False,
    }
