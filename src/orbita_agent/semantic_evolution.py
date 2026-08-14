"""Deterministic, governed primitives for finite semantic self-extension.

This module does not execute proposed code or change Orbita's active runtime.
It creates canonical, hash-bound artifacts that a separately governed executor
can evaluate and, after exact authorization, materialize as a new immutable
language snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any

TRANSITION_PHRASE = "I authorize this exact semantic transition"
SNAPSHOT_SCHEMA = "orbita-language-snapshot/1"
AUDIT_SCHEMA = "orbita-representation-audit/1"
CERTIFICATE_SCHEMA = "orbita-language-limit-certificate/1"
REPAIR_SCHEMA = "orbita-language-repair-candidate/1"
TRANSITION_SCHEMA = "orbita-language-transition-receipt/1"
COMPONENT_GRAPH_SCHEMA = "orbita-capability-component-graph/1"
TEMPORAL_AUDIT_SCHEMA = "orbita-temporal-unaskability-audit/1"
PROOF_PATHS = frozenset({"finite_enumeration", "structural_induction", "smt_model_check", "theorem_verifier"})


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic artifacts must contain finite JSON values") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _text(name: str, value: Any, *, maximum: int = 4_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{name} must be a nonblank string of at most {maximum} characters")
    return value.strip()


def _strings(name: str, value: Any, *, maximum: int = 256) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a list of at most {maximum} strings")
    result: list[str] = []
    for item in value:
        item = _text(f"{name} entry", item, maximum=500)
        if item not in result:
            result.append(item)
    return result


def _hash(name: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise ValueError(f"{name} must be a 64-character SHA-256 hex digest")
    return value.lower()


def _primitive(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("each primitive must be an object")
    allowed = {"name", "kind", "inputs", "output", "semantics", "dependencies"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unknown primitive fields: " + ", ".join(unknown))
    semantics = value.get("semantics")
    if not isinstance(semantics, dict) or not semantics:
        raise ValueError("primitive semantics must be a nonempty declarative object")
    _stable(semantics)
    return {
        "name": _text("primitive.name", value.get("name"), maximum=160),
        "kind": _text("primitive.kind", value.get("kind"), maximum=80),
        "inputs": _strings("primitive.inputs", value.get("inputs")),
        "output": _text("primitive.output", value.get("output"), maximum=160),
        "semantics": semantics,
        "dependencies": _strings("primitive.dependencies", value.get("dependencies")),
    }


def build_language_snapshot(spec: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize one executable-language boundary without running its semantics."""
    if not isinstance(spec, dict):
        raise ValueError("language specification must be an object")
    allowed = {
        "name", "version", "parent_snapshot_hash", "primitives", "observables", "refusal_conditions",
        "unknown_conditions", "read_permissions", "write_permissions", "grounding_rules", "invariants",
    }
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise ValueError("unknown language snapshot fields: " + ", ".join(unknown))
    primitives = [_primitive(item) for item in spec.get("primitives", [])]
    names = [item["name"] for item in primitives]
    if len(names) != len(set(names)):
        raise ValueError("primitive names must be unique")
    parent = spec.get("parent_snapshot_hash")
    if parent is not None:
        parent = _hash("parent_snapshot_hash", parent)
    body = {
        "schema": SNAPSHOT_SCHEMA,
        "name": _text("name", spec.get("name"), maximum=160),
        "version": _text("version", spec.get("version"), maximum=80),
        "parent_snapshot_hash": parent,
        "primitives": sorted(primitives, key=lambda item: item["name"]),
        "observables": sorted(_strings("observables", spec.get("observables"))),
        "refusal_conditions": _strings("refusal_conditions", spec.get("refusal_conditions")),
        "unknown_conditions": _strings("unknown_conditions", spec.get("unknown_conditions")),
        "read_permissions": sorted(_strings("read_permissions", spec.get("read_permissions"))),
        "write_permissions": sorted(_strings("write_permissions", spec.get("write_permissions"))),
        "grounding_rules": _strings("grounding_rules", spec.get("grounding_rules")),
        "invariants": _strings("invariants", spec.get("invariants")),
    }
    return body | {"snapshot_hash": content_hash(body), "active": False}


def _verified_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    expected = snapshot.get("snapshot_hash")
    body = {key: value for key, value in snapshot.items() if key not in {"snapshot_hash", "active"}}
    if body.get("schema") != SNAPSHOT_SCHEMA or expected != content_hash(body):
        raise ValueError("snapshot hash does not match its canonical contents")
    return snapshot


def map_equivalence(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Partition finite worlds by the exact language_view visible to the language."""
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a nonempty finite list")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case must be an object")
        world_id = _text("world_id", case.get("world_id"), maximum=200)
        view = case.get("language_view")
        if not isinstance(view, dict):
            raise ValueError("each case requires an explicit language_view object")
        _stable(view)
        groups[content_hash(view)].append(
            {"world_id": world_id, "language_view": view, "outcome": case.get("outcome"), "nuisance_class": case.get("nuisance_class")}
        )
    return [
        {"equivalence_hash": key, "language_view": rows[0]["language_view"], "members": rows}
        for key, rows in sorted(groups.items())
    ]


def audit_representation(snapshot: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Find exact collisions and candidate nuisance overseparation in a finite world set."""
    snapshot = _verified_snapshot(snapshot)
    groups = map_equivalence(cases)
    collisions = []
    for group in groups:
        outcomes = {_stable(member["outcome"]) for member in group["members"]}
        if len(outcomes) > 1:
            collisions.append(
                {
                    "equivalence_hash": group["equivalence_hash"],
                    "world_ids": [member["world_id"] for member in group["members"]],
                    "outcomes": [member["outcome"] for member in group["members"]],
                }
            )
    nuisance_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in groups:
        for member in group["members"]:
            if member["nuisance_class"] is not None:
                nuisance_groups[_stable(member["nuisance_class"])].append(member)
    overseparations = []
    for nuisance_key, members in sorted(nuisance_groups.items()):
        view_hashes = {content_hash(member["language_view"]) for member in members}
        outcomes = {_stable(member["outcome"]) for member in members}
        if len(view_hashes) > 1 and len(outcomes) == 1:
            overseparations.append(
                {
                    "nuisance_class": json.loads(nuisance_key),
                    "world_ids": [member["world_id"] for member in members],
                    "distinct_view_count": len(view_hashes),
                }
            )
    body = {
        "schema": AUDIT_SCHEMA,
        "snapshot_hash": snapshot["snapshot_hash"],
        "case_count": len(cases),
        "equivalence_class_count": len(groups),
        "collisions": collisions,
        "overseparations": overseparations,
        "verdict": "LANGUAGE_LIMIT_WITNESS" if collisions else "NO_FINITE_COLLISION_FOUND",
        "scope": "finite_cases_only",
    }
    return body | {"audit_hash": content_hash(body)}


def build_language_limit_certificate(
    snapshot: dict[str, Any],
    audit: dict[str, Any],
    *,
    proof_path: str,
    proof_artifact_hash: str,
    checker_receipt_hash: str,
) -> dict[str, Any]:
    snapshot = _verified_snapshot(snapshot)
    if proof_path not in PROOF_PATHS:
        raise ValueError("proof_path is not accepted")
    if not isinstance(audit, dict) or audit.get("schema") != AUDIT_SCHEMA:
        raise ValueError("audit must be a representation audit")
    audit_body = {key: value for key, value in audit.items() if key != "audit_hash"}
    if audit.get("audit_hash") != content_hash(audit_body) or audit.get("snapshot_hash") != snapshot["snapshot_hash"]:
        raise ValueError("audit hash or snapshot binding is invalid")
    if audit.get("verdict") != "LANGUAGE_LIMIT_WITNESS" or not audit.get("collisions"):
        raise ValueError("a LANGUAGE_LIMIT certificate requires at least one exact collision witness")
    body = {
        "schema": CERTIFICATE_SCHEMA,
        "proof_path": proof_path,
        "grammar_hash": snapshot["snapshot_hash"],
        "audit_hash": audit["audit_hash"],
        "proof_artifact_hash": _hash("proof_artifact_hash", proof_artifact_hash),
        "checker_receipt_hash": _hash("checker_receipt_hash", checker_receipt_hash),
        "witnesses": audit["collisions"],
        "scope": audit["scope"],
    }
    return body | {"certificate_hash": content_hash(body)}


def build_repair_candidate(
    snapshot: dict[str, Any],
    certificate: dict[str, Any],
    *,
    primitive: dict[str, Any],
    predicted_resolved_collisions: list[str],
    predicted_unchanged_cases: list[str],
    predicted_new_failures: list[str],
    minimality_claim: str,
) -> dict[str, Any]:
    snapshot = _verified_snapshot(snapshot)
    if not isinstance(certificate, dict) or certificate.get("schema") != CERTIFICATE_SCHEMA:
        raise ValueError("certificate must be a language-limit certificate")
    cert_body = {key: value for key, value in certificate.items() if key != "certificate_hash"}
    if certificate.get("certificate_hash") != content_hash(cert_body):
        raise ValueError("certificate hash does not match its contents")
    if certificate.get("grammar_hash") != snapshot["snapshot_hash"]:
        raise ValueError("certificate is not bound to this parent language")
    normalized = _primitive(primitive)
    if normalized["name"] in {item["name"] for item in snapshot["primitives"]}:
        raise ValueError("repair primitive already exists in the parent language")
    body = {
        "schema": REPAIR_SCHEMA,
        "parent_snapshot_hash": snapshot["snapshot_hash"],
        "certificate_hash": certificate["certificate_hash"],
        "primitive": normalized,
        "predicted_resolved_collisions": _strings("predicted_resolved_collisions", predicted_resolved_collisions),
        "predicted_unchanged_cases": _strings("predicted_unchanged_cases", predicted_unchanged_cases),
        "predicted_new_failures": _strings("predicted_new_failures", predicted_new_failures),
        "minimality_claim": _text("minimality_claim", minimality_claim),
    }
    if not body["predicted_resolved_collisions"] or not body["predicted_unchanged_cases"]:
        raise ValueError("repair candidate requires prospective recovery and unchanged-case predictions")
    return body | {"candidate_hash": content_hash(body), "activation_enabled": False}


def materialize_authorized_transition(
    snapshot: dict[str, Any],
    candidate: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    expected_candidate_hash: str,
    expected_evaluation_hash: str,
    authorized_by: str,
    confirmation: str,
    new_version: str,
) -> dict[str, Any]:
    """Create L(t+1) as an inert snapshot; never patch or activate the running service."""
    snapshot = _verified_snapshot(snapshot)
    if not isinstance(candidate, dict) or candidate.get("schema") != REPAIR_SCHEMA:
        raise ValueError("candidate must be a language repair candidate")
    candidate_body = {key: value for key, value in candidate.items() if key not in {"candidate_hash", "activation_enabled"}}
    actual_candidate_hash = content_hash(candidate_body)
    if candidate.get("candidate_hash") != actual_candidate_hash:
        raise ValueError("candidate hash does not match its contents")
    if _hash("expected_candidate_hash", expected_candidate_hash) != actual_candidate_hash:
        raise ValueError("candidate hash mismatch")
    if candidate.get("parent_snapshot_hash") != snapshot["snapshot_hash"]:
        raise ValueError("candidate is not bound to this parent language")
    if not isinstance(evaluation, dict) or evaluation.get("verdict") != "survived":
        raise PermissionError("only a prospectively survived candidate can receive a transition receipt")
    evaluation_hash = evaluation.get("evaluation_hash")
    evaluation_body = {key: value for key, value in evaluation.items() if key != "evaluation_hash"}
    if evaluation_hash != content_hash(evaluation_body):
        raise ValueError("evaluation hash does not match its contents")
    if _hash("evaluation_hash", evaluation_hash) != _hash("expected_evaluation_hash", expected_evaluation_hash):
        raise ValueError("evaluation hash mismatch")
    if evaluation.get("candidate_hash") != actual_candidate_hash:
        raise ValueError("evaluation is not bound to the repair candidate")
    if confirmation != TRANSITION_PHRASE:
        raise PermissionError(f"confirmation must be exactly: {TRANSITION_PHRASE}")
    next_snapshot = build_language_snapshot(
        {
            "name": snapshot["name"],
            "version": _text("new_version", new_version, maximum=80),
            "parent_snapshot_hash": snapshot["snapshot_hash"],
            "primitives": [*snapshot["primitives"], candidate["primitive"]],
            "observables": [*snapshot["observables"], candidate["primitive"]["output"]],
            "refusal_conditions": snapshot["refusal_conditions"],
            "unknown_conditions": snapshot["unknown_conditions"],
            "read_permissions": snapshot["read_permissions"],
            "write_permissions": snapshot["write_permissions"],
            "grounding_rules": snapshot["grounding_rules"],
            "invariants": snapshot["invariants"],
        }
    )
    receipt_body = {
        "schema": TRANSITION_SCHEMA,
        "parent_snapshot_hash": snapshot["snapshot_hash"],
        "candidate_hash": actual_candidate_hash,
        "evaluation_hash": evaluation_hash,
        "new_snapshot_hash": next_snapshot["snapshot_hash"],
        "authorized_by": _text("authorized_by", authorized_by, maximum=160),
        "confirmation": confirmation,
        "runtime_activation": "disabled",
    }
    return {
        "new_snapshot": next_snapshot,
        "transition_receipt": receipt_body | {"transition_hash": content_hash(receipt_body)},
        "production_runtime_changed": False,
    }


def build_capability_component_graph(components: list[dict[str, Any]]) -> dict[str, Any]:
    """Compile archive ideas into typed interfaces and exact complementary edges."""
    if not isinstance(components, list) or not components:
        raise ValueError("components must be a nonempty list")
    normalized = []
    for item in components:
        if not isinstance(item, dict):
            raise ValueError("each component must be an object")
        component = {
            "id": _text("component.id", item.get("id"), maximum=160),
            "type": _text("component.type", item.get("type"), maximum=160),
            "inputs": sorted(_strings("component.inputs", item.get("inputs"))),
            "outputs": sorted(_strings("component.outputs", item.get("outputs"))),
            "capabilities": sorted(_strings("component.capabilities", item.get("capabilities"))),
            "needs": sorted(_strings("component.needs", item.get("needs"))),
            "failure_modes": _strings("component.failure_modes", item.get("failure_modes")),
            "assumptions": _strings("component.assumptions", item.get("assumptions")),
            "falsifiers": _strings("component.falsifiers", item.get("falsifiers")),
        }
        normalized.append(component)
    ids = [item["id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("component ids must be unique")
    normalized.sort(key=lambda item: item["id"])
    edges = []
    for source in normalized:
        for target in normalized:
            if source["id"] == target["id"]:
                continue
            interfaces = sorted(set(source["outputs"]) & set(target["inputs"]))
            if interfaces:
                edges.append({"source": source["id"], "target": target["id"], "kind": "dataflow", "matches": interfaces})
            resolutions = sorted(set(source["capabilities"]) & set(target["needs"]))
            if resolutions:
                edges.append(
                    {"source": source["id"], "target": target["id"], "kind": "limitation_resolution", "matches": resolutions}
                )
    body = {"schema": COMPONENT_GRAPH_SCHEMA, "components": normalized, "edges": edges}
    return body | {"graph_hash": content_hash(body), "claim_scope": "interface_matches_only"}


def _finite_number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _temporal_value(values: list[float], candidate: dict[str, Any]) -> float:
    operator = candidate["operator"]
    params = candidate["parameters"]
    if operator == "current_value":
        return values[-1]
    if operator == "lag":
        lag = int(params.get("lag", 1))
        if lag < 1 or lag >= len(values):
            raise ValueError("lag must be between 1 and history length - 1")
        return values[-1 - lag]
    if operator == "window_mean":
        window = int(params.get("window", len(values)))
        if window < 1 or window > len(values):
            raise ValueError("window must be between 1 and history length")
        return sum(values[-window:]) / window
    if operator == "ewma":
        alpha = _finite_number("ewma alpha", params.get("alpha", 0.2))
        if not 0 < alpha <= 1:
            raise ValueError("ewma alpha must be in (0, 1]")
        state = values[0]
        for value in values[1:]:
            state = alpha * value + (1 - alpha) * state
        return state
    if operator == "linear_recurrence":
        decay = _finite_number("linear recurrence decay", params.get("decay", 0.8))
        gain = _finite_number("linear recurrence gain", params.get("gain", 1.0))
        state = _finite_number("linear recurrence initial", params.get("initial", 0.0))
        for value in values:
            state = decay * state + gain * value
        return state
    if operator == "threshold_crossings":
        threshold = _finite_number("crossing threshold", params.get("threshold", 0.0))
        return float(
            sum(
                (left < threshold <= right) or (right < threshold <= left)
                for left, right in zip(values, values[1:], strict=False)
            )
        )
    if operator == "hysteresis":
        low = _finite_number("hysteresis low", params.get("low", -0.25))
        high = _finite_number("hysteresis high", params.get("high", 0.25))
        if not low < high:
            raise ValueError("hysteresis low must be less than high")
        state = int(params.get("initial", 0))
        if state not in {-1, 0, 1}:
            raise ValueError("hysteresis initial must be -1, 0, or 1")
        for value in values:
            if value <= low:
                state = -1
            elif value >= high:
                state = 1
        return float(state)
    if operator == "state_inertia":
        alpha = _finite_number("state-inertia alpha", params.get("alpha", 0.1))
        eta = _finite_number("state-inertia eta", params.get("eta", 0.2))
        state = _finite_number("state-inertia initial", params.get("initial", 0.0))
        for value in values:
            state = state + alpha * state * (1 - state * state) + eta * value
            if not math.isfinite(state) or abs(state) > 1_000_000:
                raise ValueError("state-inertia candidate became numerically unstable")
        return state
    raise ValueError("unsupported temporal operator")


def audit_temporal_unaskability(
    histories: list[dict[str, Any]], candidates: list[dict[str, Any]], *, tolerance: float = 1e-9
) -> dict[str, Any]:
    """Test which allowlisted temporal operators break exact present-only collisions.

    This is a finite diagnostic, not model fitting. Candidate parameters are fixed
    in the request, and no winner is admitted or activated by this function.
    """
    tolerance = _finite_number("tolerance", tolerance)
    if tolerance < 0:
        raise ValueError("tolerance must be nonnegative")
    if not isinstance(histories, list) or len(histories) < 2:
        raise ValueError("histories must contain at least two worlds")
    worlds = []
    for item in histories:
        if not isinstance(item, dict):
            raise ValueError("each history must be an object")
        values_raw = item.get("values")
        if not isinstance(values_raw, list) or len(values_raw) < 2:
            raise ValueError("each history requires at least two values")
        worlds.append(
            {
                "world_id": _text("world_id", item.get("world_id"), maximum=200),
                "values": [_finite_number("history value", value) for value in values_raw],
                "outcome": item.get("outcome"),
            }
        )
    baseline_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for world in worlds:
        baseline_groups[_stable(world["values"][-1])].append(world)
    collision_pairs = []
    for rows in baseline_groups.values():
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                if _stable(left["outcome"]) != _stable(right["outcome"]):
                    collision_pairs.append((left, right))
    if not collision_pairs:
        raise ValueError("no exact present-only collision with differing outcomes was supplied")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a nonempty list")
    results = []
    allowed = {
        "current_value", "lag", "window_mean", "ewma", "linear_recurrence", "threshold_crossings", "hysteresis",
        "state_inertia",
    }
    for raw in candidates:
        if not isinstance(raw, dict):
            raise ValueError("each temporal candidate must be an object")
        operator = _text("candidate.operator", raw.get("operator"), maximum=80)
        if operator not in allowed:
            raise ValueError("unsupported temporal operator")
        parameters = raw.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("candidate parameters must be an object")
        normalized = {"name": _text("candidate.name", raw.get("name"), maximum=160), "operator": operator, "parameters": parameters}
        outputs = {world["world_id"]: _temporal_value(world["values"], normalized) for world in worlds}
        resolved = []
        unresolved = []
        for left, right in collision_pairs:
            pair = [left["world_id"], right["world_id"]]
            if abs(outputs[left["world_id"]] - outputs[right["world_id"]]) > tolerance:
                resolved.append(pair)
            else:
                unresolved.append(pair)
        results.append(
            {
                "name": normalized["name"],
                "operator": operator,
                "parameters": parameters,
                "outputs": outputs,
                "resolved_collision_count": len(resolved),
                "unresolved_collision_count": len(unresolved),
                "resolved_pairs": resolved,
                "unresolved_pairs": unresolved,
                "candidate_hash": content_hash(normalized),
            }
        )
    results.sort(key=lambda item: (-item["resolved_collision_count"], item["name"]))
    body = {
        "schema": TEMPORAL_AUDIT_SCHEMA,
        "world_count": len(worlds),
        "present_only_collision_count": len(collision_pairs),
        "candidate_results": results,
        "scope": "finite_fixed_parameter_diagnostic",
        "admission_decision": "none",
    }
    return body | {"audit_hash": content_hash(body)}
