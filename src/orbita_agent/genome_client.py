from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, replace
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

OPERATOR_FREEZE_PHRASE = "I reviewed this exact discovery operator"
TOURNAMENT_FREEZE_PHRASE = "I reviewed this exact blind tournament"
TOURNAMENT_REVEAL_PHRASE = "I reviewed this exact tournament reveal"
RESULT_RECORD_PHRASE = "I reviewed this exact tournament result"
SAFE_GENOME_ERROR_CODES = frozenset(
    {
        "invalid_prediction",
        "duplicate_operator_entry",
        "attachment_target_not_found",
        "attachment_conflict",
    }
)


class DiscoveryGenomeError(RuntimeError):
    """Safe, redacted error raised by the Discovery Genome service client."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def tournament_result_receipt(
    tournament_id: str,
    entry_id: str,
    verdict: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "orbita.discovery-tournament-result.v1",
        "tournament_id": tournament_id,
        "entry_id": entry_id,
        "verdict": verdict,
        "result": result,
    }


def tournament_reveal_receipt(
    tournament_id: str,
    manifest_hash: str,
    reveal: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "orbita.discovery-tournament-reveal.v1",
        "tournament_id": tournament_id,
        "manifest_hash": manifest_hash,
        "reveal": reveal,
    }


@dataclass(frozen=True)
class DiscoveryGenomeConfig:
    base_url: str
    service_token: str
    username: str = ""
    timeout_seconds: float = 20.0

    def with_username(self, username: str) -> DiscoveryGenomeConfig:
        return replace(self, username=username.strip())

    @classmethod
    def from_env(cls) -> DiscoveryGenomeConfig:
        raw_timeout = os.getenv("ORBITA_DISCOVERY_GENOME_TIMEOUT", "20").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise RuntimeError("ORBITA_DISCOVERY_GENOME_TIMEOUT must be numeric") from exc
        return cls(
            base_url=os.getenv("ORBITA_DISCOVERY_GENOME_URL", "").strip().rstrip("/"),
            service_token=os.getenv("ORBITA_DISCOVERY_GENOME_SERVICE_TOKEN", "").strip(),
            username=os.getenv("ORBITA_DISCOVERY_GENOME_USERNAME", "").strip(),
            timeout_seconds=max(1.0, min(timeout, 120.0)),
        )

    def missing(self) -> list[str]:
        """Deployment-level configuration that must be present for any tenant.

        The tenant username is deliberately absent: it is resolved per request from
        the authenticated subject, not from deployment configuration.
        """
        missing = []
        if not self.base_url:
            missing.append("ORBITA_DISCOVERY_GENOME_URL")
        if len(self.service_token) < 32:
            missing.append("ORBITA_DISCOVERY_GENOME_SERVICE_TOKEN")
        return missing


class DiscoveryGenomeClient:
    """Narrow server-to-server client; it never receives database credentials or tenant UUIDs."""

    def __init__(self, config: DiscoveryGenomeConfig | None = None):
        self.config = config or DiscoveryGenomeConfig.from_env()

    def for_username(self, username: str) -> DiscoveryGenomeClient:
        """Return a client bound to one resolved tenant, sharing this deployment config."""
        bound = username.strip()
        if not bound:
            raise DiscoveryGenomeError("a Discovery Genome tenant username is required")
        return type(self)(self.config.with_username(bound))

    def _require_configured(self) -> None:
        missing = self.config.missing()
        if missing:
            raise DiscoveryGenomeError(
                "Discovery Genome bridge is not configured; missing: " + ", ".join(missing)
            )
        if not self.config.username.strip():
            raise DiscoveryGenomeError(
                "this Discovery Genome client is not bound to a tenant; "
                "requests must be made through a client resolved from the authenticated identity"
            )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_configured()
        body = None if payload is None else canonical_json(payload).encode("utf-8")
        request = Request(
            self.config.base_url + "/api/internal/discovery-genome" + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.config.service_token}",
                "Content-Type": "application/json",
                "X-Orbita-Genome-User": self.config.username,
            },
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            safe_code = ""
            try:
                raw_error = exc.read(8_192).decode("utf-8")
                error_body = json.loads(raw_error) if raw_error else {}
                candidate = str(error_body.get("code") or "") if isinstance(error_body, dict) else ""
                if candidate in SAFE_GENOME_ERROR_CODES:
                    safe_code = candidate
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            finally:
                exc.close()
            suffix = f" ({safe_code})" if safe_code else ""
            raise DiscoveryGenomeError(
                f"Discovery Genome service request failed with HTTP {exc.code}{suffix}"
            ) from exc
        except OSError as exc:
            raise DiscoveryGenomeError("Discovery Genome service is unavailable") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DiscoveryGenomeError("Discovery Genome service returned an invalid response") from exc

    def status(self) -> dict[str, Any]:
        missing = self.config.missing()
        if missing:
            return {"configured": False, "missing": missing}
        return {"configured": True, **self._request("GET", "/status")}

    def core_tenant_id(self) -> str:
        """Resolve this Guided username to its opaque shared-core tenant boundary."""
        value = str(self._request("GET", "/status").get("core_tenant_id") or "")
        if len(value) != 34 or not value.startswith("g-"):
            raise DiscoveryGenomeError("Discovery Genome service did not return a core tenant identity")
        digest = value[2:]
        if any(character not in "0123456789abcdef" for character in digest):
            raise DiscoveryGenomeError("Discovery Genome service returned an invalid core tenant identity")
        return value

    def list_operators(self) -> dict[str, Any]:
        return self._request("GET", "/operators")

    def list_graphs(self) -> dict[str, Any]:
        return self._request("GET", "/graphs")

    def programme_state(self, graph_id: str) -> dict[str, Any]:
        return self._request("GET", f"/graphs/{quote(graph_id, safe='')}/programme-state")

    def compile_programme_state(self, graph_id: str) -> dict[str, Any]:
        return self._request("POST", f"/graphs/{quote(graph_id, safe='')}/programme-state/compile", {})

    def list_questions(self, graph_id: str) -> dict[str, Any]:
        return self._request("GET", f"/graphs/{quote(graph_id, safe='')}/questions")

    def generate_questions(self, graph_id: str) -> dict[str, Any]:
        return self._request("POST", f"/graphs/{quote(graph_id, safe='')}/questions/generate", {})

    def seed_operators(self) -> dict[str, Any]:
        return self._request("POST", "/operators/seed", {})

    def create_operator(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/operators", payload)

    def freeze_operator(
        self,
        operator_id: str,
        *,
        expected_review_hash: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != OPERATOR_FREEZE_PHRASE:
            raise DiscoveryGenomeError(f"confirmation must exactly equal: {OPERATOR_FREEZE_PHRASE}")
        operators = self.list_operators().get("operators", [])
        operator = next((item for item in operators if item.get("id") == operator_id), None)
        if not operator:
            raise DiscoveryGenomeError("Discovery operator not found")
        actual = str(operator.get("review_hash") or "")
        if not actual or not hmac.compare_digest(actual, expected_review_hash):
            raise DiscoveryGenomeError("Discovery operator review hash mismatch")
        result = self._request(
            "POST",
            f"/operators/{quote(operator_id, safe='')}/freeze",
            {"expected_review_hash": expected_review_hash},
        )
        frozen_hash = str(result.get("operator", {}).get("contract_hash") or "")
        if not hmac.compare_digest(frozen_hash, expected_review_hash):
            raise DiscoveryGenomeError("Frozen operator hash does not match the reviewed hash")
        return result

    def add_operator_evidence(self, operator_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/operators/{quote(operator_id, safe='')}/evidence", payload)

    def list_tournaments(self) -> dict[str, Any]:
        return self._request("GET", "/tournaments")

    def get_tournament(self, tournament_id: str) -> dict[str, Any]:
        return self._request("GET", f"/tournaments/{quote(tournament_id, safe='')}")

    def create_tournament(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/tournaments", payload)

    def add_tournament_entry(self, tournament_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/tournaments/{quote(tournament_id, safe='')}/entries",
            payload,
        )

    def freeze_tournament(
        self,
        tournament_id: str,
        *,
        expected_review_hash: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != TOURNAMENT_FREEZE_PHRASE:
            raise DiscoveryGenomeError(f"confirmation must exactly equal: {TOURNAMENT_FREEZE_PHRASE}")
        current = self.get_tournament(tournament_id).get("tournament", {})
        actual = str(current.get("review_hash") or "")
        if not actual or not hmac.compare_digest(actual, expected_review_hash):
            raise DiscoveryGenomeError("Discovery tournament review hash mismatch")
        result = self._request(
            "POST",
            f"/tournaments/{quote(tournament_id, safe='')}/freeze",
            {"expected_review_hash": expected_review_hash},
        )
        frozen_hash = str(result.get("tournament", {}).get("manifest_hash") or "")
        if not hmac.compare_digest(frozen_hash, expected_review_hash):
            raise DiscoveryGenomeError("Frozen tournament hash does not match the reviewed hash")
        return result

    def mark_tournament_revealed(
        self,
        tournament_id: str,
        *,
        expected_manifest_hash: str,
        reveal: dict[str, Any],
        confirmation: str,
        expected_reveal_hash: str | None = None,
    ) -> dict[str, Any]:
        if confirmation != TOURNAMENT_REVEAL_PHRASE:
            raise DiscoveryGenomeError(f"confirmation must exactly equal: {TOURNAMENT_REVEAL_PHRASE}")
        current = self.get_tournament(tournament_id).get("tournament", {})
        manifest_hash = str(current.get("manifest_hash") or "")
        if not manifest_hash or not hmac.compare_digest(manifest_hash, expected_manifest_hash):
            raise DiscoveryGenomeError("Discovery tournament manifest hash mismatch")
        reviewed_receipt = tournament_reveal_receipt(tournament_id, expected_manifest_hash, reveal)
        actual = hash_json(reviewed_receipt)
        if expected_reveal_hash and not hmac.compare_digest(actual, expected_reveal_hash):
            raise DiscoveryGenomeError("Tournament reveal hash mismatch")
        response = self._request(
            "POST",
            f"/tournaments/{quote(tournament_id, safe='')}/reveal",
            {
                "expected_manifest_hash": expected_manifest_hash,
                "reveal": reveal,
                "expected_reveal_hash": expected_reveal_hash or actual,
            },
        )
        tournament = response.get("tournament", {})
        if (
            str(tournament.get("id") or "") != tournament_id
            or not hmac.compare_digest(str(tournament.get("manifest_hash") or ""), expected_manifest_hash)
            or not hmac.compare_digest(str(tournament.get("reveal_hash") or ""), actual)
            or not tournament.get("revealed_at")
        ):
            raise DiscoveryGenomeError("Persisted tournament reveal does not match the reviewed operation")
        return {**response, "reveal_hash": actual}

    def record_tournament_result(
        self,
        tournament_id: str,
        entry_id: str,
        *,
        verdict: str,
        result: dict[str, Any],
        expected_result_hash: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != RESULT_RECORD_PHRASE:
            raise DiscoveryGenomeError(f"confirmation must exactly equal: {RESULT_RECORD_PHRASE}")
        reviewed_receipt = tournament_result_receipt(tournament_id, entry_id, verdict, result)
        actual = hash_json(reviewed_receipt)
        if not hmac.compare_digest(actual, expected_result_hash):
            raise DiscoveryGenomeError("Tournament result hash mismatch")
        response = self._request(
            "POST",
            (
                f"/tournaments/{quote(tournament_id, safe='')}/entries/"
                f"{quote(entry_id, safe='')}/result"
            ),
            {
                "verdict": verdict,
                "result": result,
                "expected_result_hash": expected_result_hash,
            },
        )
        entry = response.get("entry", {})
        persisted_receipt = tournament_result_receipt(
            str(entry.get("tournament_id") or ""),
            str(entry.get("id") or ""),
            str(entry.get("verdict") or ""),
            entry.get("result_json") if isinstance(entry.get("result_json"), dict) else {},
        )
        persisted_hash = str(entry.get("result_hash") or "")
        if (
            persisted_receipt != reviewed_receipt
            or not hmac.compare_digest(persisted_hash, actual)
            or not hmac.compare_digest(hash_json(persisted_receipt), actual)
        ):
            raise DiscoveryGenomeError("Persisted tournament result does not match the reviewed operation")
        return {**response, "result_hash": actual}
