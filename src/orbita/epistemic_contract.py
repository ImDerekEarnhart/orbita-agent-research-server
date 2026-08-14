from __future__ import annotations

from enum import StrEnum
from typing import Any


class EvidenceStatus(StrEnum):
    """What the available evidence warrants, independent of claim workflow state."""

    FORMALLY_PROVED = "FORMALLY_PROVED"
    EXHAUSTIVELY_VERIFIED_FINITE_DOMAIN = "EXHAUSTIVELY_VERIFIED_FINITE_DOMAIN"
    BOUNDED_VERIFIED = "BOUNDED_VERIFIED"
    EMPIRICAL_SURVIVOR = "EMPIRICAL_SURVIVOR"
    PROVISIONAL = "PROVISIONAL"
    NON_DETECTION = "NON_DETECTION"
    REFUTED = "REFUTED"
    UNRESOLVED = "UNRESOLVED"
    LANGUAGE_LIMIT = "LANGUAGE_LIMIT"
    RECORDED_BOUNDARY = "RECORDED_BOUNDARY"
    CANDIDATE_FOR_EXPERT_REVIEW = "CANDIDATE_FOR_EXPERT_REVIEW"


_UNIVERSAL_QUANTIFIERS = {"all", "universal", "for_all"}
_FINITE_QUANTIFIERS = {"observed_rows", "sample", "bounded_all", "finite_all"}


def normalize_claim_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(scope or {})
    domain = raw.get("domain") or raw.get("kind") or "unspecified"
    quantifier = str(raw.get("quantifier") or "unspecified").lower()
    boundary = raw.get("boundary")
    if boundary is None:
        boundary = {}
    if not isinstance(boundary, dict):
        raise ValueError("claim scope boundary must be an object")
    assumptions = raw.get("assumptions") or []
    if not isinstance(assumptions, list):
        raise ValueError("claim scope assumptions must be a list")
    structured = quantifier != "unspecified" and domain != "unspecified"
    return {
        **raw,
        "domain": domain,
        "quantifier": quantifier,
        "boundary": boundary,
        "assumptions": assumptions,
        "computational_model": raw.get("computational_model") or "unspecified",
        "representation_language": raw.get("representation_language") or "unspecified",
        "structured": structured,
    }


def normalize_falsification_coverage(coverage: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(coverage or {})
    list_fields = (
        "tested_domains",
        "tested_sizes",
        "seeds",
        "perturbations",
        "controls",
        "baselines",
        "known_failures",
        "known_uncovered_regions",
    )
    normalized = dict(raw)
    for field in list_fields:
        value = raw.get(field) or []
        if not isinstance(value, list):
            raise ValueError(f"falsification coverage {field} must be a list")
        normalized[field] = value
    normalized["exhaustive"] = bool(raw.get("exhaustive", False))
    normalized["formal_proof_receipt"] = raw.get("formal_proof_receipt")
    normalized["execution_receipt"] = raw.get("execution_receipt")
    return normalized


def discovery_evidence_status(final_status: str | None) -> EvidenceStatus:
    """Translate an operational discovery result without upgrading finite evidence."""

    return {
        "supported": EvidenceStatus.EMPIRICAL_SURVIVOR,
        "challenged": EvidenceStatus.PROVISIONAL,
        "provisional": EvidenceStatus.PROVISIONAL,
        "refuted": EvidenceStatus.REFUTED,
        "unknown": EvidenceStatus.UNRESOLVED,
        "unscorable": EvidenceStatus.RECORDED_BOUNDARY,
    }.get(str(final_status or "").lower(), EvidenceStatus.UNRESOLVED)


def guard_scope_escalation(
    evidence_scope: dict[str, Any] | None,
    proposed_claim_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fail closed when evidence is narrower than the proposed claim.

    This intentionally uses explicit scope declarations instead of guessing what a
    sentence means. Legacy or incomplete declarations can remain provisional, but
    cannot authorize an escalation.
    """

    evidence = normalize_claim_scope(evidence_scope)
    claim = normalize_claim_scope(proposed_claim_scope)
    reasons: list[str] = []
    if not evidence["structured"] or not claim["structured"]:
        reasons.append("scope is legacy or incomplete")
    if evidence["domain"] != claim["domain"]:
        reasons.append("claim domain differs from the tested evidence domain")
    if claim["quantifier"] in _UNIVERSAL_QUANTIFIERS and evidence["quantifier"] not in _UNIVERSAL_QUANTIFIERS:
        reasons.append("sample or bounded evidence cannot support a universal claim")
    if claim["quantifier"] in _FINITE_QUANTIFIERS and evidence["quantifier"] not in _FINITE_QUANTIFIERS | _UNIVERSAL_QUANTIFIERS:
        reasons.append("the evidence does not declare finite coverage")
    evidence_boundary = evidence["boundary"]
    claim_boundary = claim["boundary"]
    for key, claim_value in claim_boundary.items():
        if key not in evidence_boundary:
            reasons.append(f"evidence does not cover claim boundary field {key}")
            continue
        evidence_value = evidence_boundary[key]
        if isinstance(claim_value, (int, float)) and isinstance(evidence_value, (int, float)):
            if evidence_value < claim_value:
                reasons.append(f"evidence boundary {key} is narrower than the claim")
        elif claim_value != evidence_value:
            reasons.append(f"evidence boundary {key} differs from the claim")
    missing_assumptions = [item for item in evidence["assumptions"] if item not in claim["assumptions"]]
    if missing_assumptions:
        reasons.append("the proposed claim omits assumptions used by the evidence")
    return {
        "allowed": not reasons,
        "decision": "ALLOW" if not reasons else "REJECT_SCOPE_ESCALATION",
        "reasons": reasons,
        "evidence_scope": evidence,
        "proposed_claim_scope": claim,
    }


def warranted_status(
    requested: EvidenceStatus | str,
    *,
    claim_scope: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
) -> EvidenceStatus:
    """Apply proof/coverage prerequisites to a requested evidence status."""

    status = EvidenceStatus(requested)
    normalized_scope = normalize_claim_scope(claim_scope)
    normalized_coverage = normalize_falsification_coverage(coverage)
    if status is EvidenceStatus.FORMALLY_PROVED and not normalized_coverage["formal_proof_receipt"]:
        raise ValueError("FORMALLY_PROVED requires a formal proof receipt")
    if status is EvidenceStatus.EXHAUSTIVELY_VERIFIED_FINITE_DOMAIN:
        if not normalized_coverage["exhaustive"]:
            raise ValueError("finite-domain exhaustive verification requires exhaustive coverage")
        if normalized_scope["quantifier"] not in {"finite_all", "bounded_all"}:
            raise ValueError("exhaustive finite verification requires a finite scope")
    if status is EvidenceStatus.BOUNDED_VERIFIED and not normalized_scope["boundary"]:
        raise ValueError("BOUNDED_VERIFIED requires an explicit boundary")
    return status
