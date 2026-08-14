from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import AgentConfig

_SUPPORTED_CONDITIONS = frozenset({"B2", "B3"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_VOLUME_ENV_NAMES = (
    "RAILWAY_VOLUME_ID",
    "RAILWAY_VOLUME_NAME",
    "RAILWAY_VOLUME_MOUNT_PATH",
)
_GUIDED_ENV_NAMES = (
    "ORBITA_DISCOVERY_GENOME_URL",
    "ORBITA_DISCOVERY_GENOME_SERVICE_TOKEN",
    "ORBITA_DISCOVERY_GENOME_USERNAME",
)


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(parent))
        return True
    except ValueError:
        return False


def _directory_state(path: Path) -> dict[str, Any]:
    resolved = _resolved(path)
    if not resolved.exists():
        return {"exists": False, "entry_count": 0, "entry_names_sha256": _canonical_hash({"names": []})}
    if not resolved.is_dir():
        return {"exists": True, "entry_count": -1, "entry_names_sha256": None}
    names = sorted(item.name for item in resolved.iterdir())
    return {
        "exists": True,
        "entry_count": len(names),
        # Preserve evidence that the observed directory listing has not changed
        # without publishing filenames from the host.
        "entry_names_sha256": _canonical_hash({"names": names}),
    }


def _required_identifier(name: str, violations: list[str]) -> str:
    value = os.getenv(name, "").strip()
    if not _IDENTIFIER.fullmatch(value):
        violations.append(f"{name} must be a non-secret benchmark identifier")
    return value


@dataclass(frozen=True)
class BenchmarkIsolationProbe:
    """Pre-gateway evidence for a fail-closed SHARB B2/B3 benchmark boot.

    This object is captured before AgentGateway creates any databases. It never
    reads a packet and never records an authentication or service credential.
    """

    condition: str
    replicate_id: str
    packet_id: str | None
    boot_nonce_sha256: str
    expected_commit: str
    observed_commit: str
    home: Path
    cache_root: Path
    home_before_gateway: dict[str, Any]
    cache_before_gateway: dict[str, Any]
    volume_environment_present: list[str]
    volume_mount_path: str | None
    captured_at_utc: str

    @classmethod
    def capture(cls, config: AgentConfig) -> BenchmarkIsolationProbe | None:
        raw_condition = os.getenv("ORBITA_BENCHMARK_CONDITION", "").strip().upper()
        if not raw_condition:
            return None

        violations: list[str] = []
        if raw_condition not in _SUPPORTED_CONDITIONS:
            violations.append("ORBITA_BENCHMARK_CONDITION must be B2 or B3")

        replicate_id = _required_identifier("ORBITA_BENCHMARK_REPLICATE_ID", violations)
        packet_id = None
        if raw_condition == "B3":
            packet_id = _required_identifier("ORBITA_BENCHMARK_PACKET_ID", violations)

        nonce = os.getenv("ORBITA_BENCHMARK_BOOT_NONCE", "")
        if len(nonce) < 32:
            violations.append("ORBITA_BENCHMARK_BOOT_NONCE must contain at least 32 characters")

        expected_commit = os.getenv("ORBITA_BENCHMARK_EXPECTED_COMMIT", "").strip().lower()
        observed_commit = os.getenv("GIT_COMMIT_SHA", "").strip().lower()
        if not _COMMIT.fullmatch(expected_commit):
            violations.append("ORBITA_BENCHMARK_EXPECTED_COMMIT must be a full Git commit SHA")
        if not _COMMIT.fullmatch(observed_commit):
            violations.append("GIT_COMMIT_SHA must be a full Git commit SHA")
        if expected_commit != observed_commit:
            violations.append("the observed Git commit does not match the frozen expected commit")

        configured_home = os.getenv("ORBITA_AGENT_HOME", "").strip()
        home = _resolved(config.home)
        if not configured_home or home != _resolved(Path(configured_home)):
            violations.append("ORBITA_AGENT_HOME must explicitly match the gateway home")

        cache_value = os.getenv("ORBITA_BENCHMARK_CACHE_ROOT", "").strip()
        cache_root = _resolved(Path(cache_value)) if cache_value else home / ".benchmark-cache-missing"
        if not cache_value:
            violations.append("ORBITA_BENCHMARK_CACHE_ROOT is required")

        auth_mode = os.getenv("ORBITA_AGENT_AUTH_MODE", "").strip().lower()
        require_auth = os.getenv("ORBITA_AGENT_REQUIRE_AUTH", "").strip().lower()
        if auth_mode != "bearer" or require_auth not in {"1", "true", "yes", "on"}:
            violations.append("benchmark services require authenticated bearer mode")
        if len(os.getenv("ORBITA_AGENT_API_TOKEN", "")) < 32:
            violations.append("benchmark bearer credential is missing or too short")

        guided_present = [name for name in _GUIDED_ENV_NAMES if os.getenv(name, "").strip()]
        if guided_present:
            violations.append("the external Guided/Genome bridge must be disabled for both B2 and B3")

        volume_present = [name for name in _VOLUME_ENV_NAMES if os.getenv(name, "").strip()]
        volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip() or None
        if raw_condition == "B3":
            if volume_present:
                violations.append("B3 must not have a Railway volume attached")
            if _is_within(home, Path("/data")) or _is_within(cache_root, Path("/data")):
                violations.append("B3 home and cache must not be under /data")
        elif raw_condition == "B2":
            if not volume_mount:
                violations.append("B2 requires a dedicated Railway volume mount")
            elif not _is_within(home, Path(volume_mount)) or not _is_within(cache_root, Path(volume_mount)):
                violations.append("B2 home and cache must be inside its dedicated volume")

        home_state = _directory_state(home)
        cache_state = _directory_state(cache_root)
        if home_state["entry_count"] != 0:
            violations.append("benchmark home was not empty before gateway initialization")
        if cache_state["entry_count"] != 0:
            violations.append("benchmark cache was not empty before gateway initialization")

        if violations:
            raise RuntimeError("benchmark isolation refused startup: " + "; ".join(violations))

        return cls(
            condition=raw_condition,
            replicate_id=replicate_id,
            packet_id=packet_id,
            boot_nonce_sha256=hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
            expected_commit=expected_commit,
            observed_commit=observed_commit,
            home=home,
            cache_root=cache_root,
            home_before_gateway=home_state,
            cache_before_gateway=cache_state,
            volume_environment_present=volume_present,
            volume_mount_path=volume_mount,
            captured_at_utc=datetime.now(UTC).isoformat(),
        )

    def finalize(self, gateway: Any, *, authentication: str, guided_bridge_disabled: bool) -> dict[str, Any]:
        violations: list[str] = []
        if authentication != "bearer":
            violations.append("runtime authentication did not remain in bearer mode")
        if not guided_bridge_disabled:
            violations.append("the external Guided/Genome bridge became available")
        if gateway.list_cases():
            violations.append("the gateway contained cases immediately after initialization")
        if violations:
            raise RuntimeError("benchmark isolation refused startup: " + "; ".join(violations))

        receipt: dict[str, Any] = {
            "schema": "orbita.sharb-isolation-attestation.v1",
            "status": "VERIFIED_EMPTY_BOOT",
            "condition": self.condition,
            "replicate_id": self.replicate_id,
            "packet_id": self.packet_id,
            "captured_at_utc": self.captured_at_utc,
            "boot_nonce_sha256": self.boot_nonce_sha256,
            "expected_commit": self.expected_commit,
            "observed_commit": self.observed_commit,
            "orbita_version": __version__,
            "authentication": authentication,
            "guided_bridge_disabled": guided_bridge_disabled,
            "home": str(self.home),
            "cache_root": str(self.cache_root),
            "home_before_gateway": self.home_before_gateway,
            "cache_before_gateway": self.cache_before_gateway,
            "volume_environment_present": self.volume_environment_present,
            "volume_mount_path": self.volume_mount_path,
            "gateway_case_count_after_initialization": 0,
            "packet_content_accessed_by_attestor": False,
            "secrets_in_attestation": False,
        }
        return receipt | {"attestation_sha256": _canonical_hash(receipt)}
