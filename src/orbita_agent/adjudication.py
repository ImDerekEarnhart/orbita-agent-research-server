from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

SCHEMA_VERSION = "orbita-epistemic-adjudication/1.0"
MAX_CONTEXT_ITEMS = 256
MAX_SEQUENCE_EVENTS = 256
MAX_TARGETS_PER_KIND = 128
MAX_TEXT_CHARACTERS = 100_000

_TOKEN = re.compile(r"[a-z0-9]+")
_OPAQUE_TOKEN = re.compile(r"[0-9a-f]{12,64}")
_WEAK_SOURCE_KINDS = {
    "blog",
    "model",
    "model_proposal",
    "proposal",
    "unverified_web",
}
_SUPPORT_OUTCOMES = {"support", "supported", "confirm", "confirmed", "pass", "passed"}
_REFUTE_OUTCOMES = {"refute", "refuted", "reject", "rejected", "fail", "failed"}
_REVOKE_EVENTS = {"revoke", "revoked", "withdraw", "withdrawn"}
_REFUTE_EVENTS = {"contradict", "contradicted", "refute", "refuted"}
_GENERIC_TOKENS = {
    "a",
    "an",
    "and",
    "c",
    "claim",
    "conclusion",
    "e",
    "evidence",
    "h",
    "hypothesis",
    "id",
    "p",
    "proof",
    "the",
}


class AdjudicationError(ValueError):
    """The public task cannot be adjudicated safely or deterministically."""


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _tokens(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, str):
        value = _stable_json(value)
    return {token for token in _TOKEN.findall(value.casefold()) if token not in _GENERIC_TOKENS}


def _semantic_tokens(value: Any) -> set[str]:
    return {token for token in _tokens(value) if not _OPAQUE_TOKEN.fullmatch(token)}


def _item_tokens(item: dict[str, Any]) -> set[str]:
    values = [
        item.get("id"),
        item.get("text"),
        item.get("hypothesis"),
        item.get("claim"),
        item.get("subject"),
        item.get("time_scope"),
    ]
    return set().union(*(_tokens(value) for value in values))


def _nonblank_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdjudicationError(f"{field} must be a nonblank string")
    if len(value) > 240:
        raise AdjudicationError(f"{field} is too long")
    return value.strip()


def _target_ids(task: dict[str, Any], kind: str) -> list[str]:
    targets = task.get("targets")
    if not isinstance(targets, dict):
        raise AdjudicationError("task.targets must be an object")
    values = targets.get(kind, [])
    if not isinstance(values, list):
        raise AdjudicationError(f"task.targets.{kind} must be an array")
    if len(values) > MAX_TARGETS_PER_KIND:
        raise AdjudicationError(f"task.targets.{kind} exceeds {MAX_TARGETS_PER_KIND} entries")
    result = [_nonblank_id(value, f"task.targets.{kind}") for value in values]
    if len(result) != len(set(result)):
        raise AdjudicationError(f"task.targets.{kind} contains duplicate IDs")
    return result


def _object_list(task: dict[str, Any], field: str, maximum: int) -> list[dict[str, Any]]:
    raw = task.get(field, [])
    if not isinstance(raw, list):
        raise AdjudicationError(f"task.{field} must be an array")
    if len(raw) > maximum:
        raise AdjudicationError(f"task.{field} exceeds {maximum} entries")
    if not all(isinstance(item, dict) for item in raw):
        raise AdjudicationError(f"every task.{field} entry must be an object")
    return [dict(item) for item in raw]


def _validate_task(task: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(task, dict):
        raise AdjudicationError("task must be an object")
    if "gold" in task:
        raise AdjudicationError("gold labels are forbidden; submit only the public task")
    _nonblank_id(task.get("id"), "task.id")
    _nonblank_id(task.get("category"), "task.category")
    prompt = task.get("prompt", "")
    if not isinstance(prompt, str):
        raise AdjudicationError("task.prompt must be a string")
    if len(prompt) > MAX_TEXT_CHARACTERS:
        raise AdjudicationError("task.prompt is too large")
    context = _object_list(task, "context", MAX_CONTEXT_ITEMS)
    sequence = _object_list(task, "sequence", MAX_SEQUENCE_EVENTS)
    serialized_size = len(_stable_json({"context": context, "sequence": sequence}))
    if serialized_size > MAX_TEXT_CHARACTERS:
        raise AdjudicationError("task context and sequence are too large")
    for kind in ("claims", "actions", "discoveries"):
        _target_ids(task, kind)
    return context, sequence


def assess_adjudication_coverage(task: dict[str, Any]) -> dict[str, Any]:
    """Report whether a task is inside the deterministic engine's declared schema."""

    task_id = task.get("id") if isinstance(task, dict) else None
    try:
        context, sequence = _validate_task(task)
    except AdjudicationError as exc:
        return {
            "task_id": task_id,
            "covered": False,
            "reason": f"invalid structured task: {exc}",
        }

    category = str(task["category"])
    kinds = {str(item.get("kind", "")).casefold() for item in context}
    if category == "unsupported_commitment":
        recognized = _WEAK_SOURCE_KINDS | {
            "analysis_receipt",
            "evidence",
            "regulator",
            "sensor",
            "study",
        }
        covered = bool(_target_ids(task, "claims")) and kinds.issubset(recognized)
        reason = "recognized evidence-source schema" if covered else "free-form or unknown evidence source"
    elif category == "contradiction_recovery":
        covered = bool(_target_ids(task, "claims")) and any(
            _event_name(event) in _REFUTE_EVENTS and event.get("evidence_id")
            for event in sequence
        )
        reason = "explicit refutation event" if covered else "no explicit structured refutation event"
    elif category in {"evidence_collapse", "evidence_preservation"}:
        covered = bool(_target_ids(task, "claims")) and bool(_proofs(context))
        reason = "structured proof graph" if covered else "no structured proof graph"
    elif category == "false_success":
        covered = bool(_target_ids(task, "actions")) and (
            not context or kinds.issubset({"execution_receipt"})
        )
        reason = "structured execution receipts" if covered else "free-form execution record"
    elif category == "replicated_discovery":
        covered = bool(_target_ids(task, "discoveries")) and (
            not context or kinds.issubset({"analysis_receipt"})
        )
        reason = "structured analysis receipts" if covered else "free-form discovery record"
    elif category == "temporal_scope":
        covered = (
            bool(_target_ids(task, "claims"))
            and bool(context)
            and kinds.issubset({"evidence", "sensor"})
        )
        reason = "structured time-scoped observations" if covered else "free-form temporal record"
    else:
        covered = False
        reason = f"unsupported category: {category}"
    return {"task_id": str(task["id"]), "covered": covered, "reason": reason}


def compress_epistemic_task(
    task: dict[str, Any],
    *,
    max_context_items: int = 8,
) -> dict[str, Any]:
    """Select target-relevant evidence without model, network, or gold access."""

    if not isinstance(max_context_items, int) or not 1 <= max_context_items <= 32:
        raise AdjudicationError("max_context_items must be an integer between 1 and 32")
    context, sequence = _validate_task(task)
    target_values = [
        target
        for kind in ("claims", "actions", "discoveries")
        for target in _target_ids(task, kind)
    ]
    target_tokens = set().union(*(_tokens(target) for target in target_values))
    prompt_tokens = _tokens(task.get("prompt", ""))
    referenced_ids = {
        str(value)
        for event in sequence
        for key, value in event.items()
        if key.endswith("_id") and isinstance(value, str) and value.strip()
    }
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for position, item in enumerate(context):
        item_tokens = _item_tokens(item)
        item_id = _context_id(item)
        target_overlap = len(target_tokens.intersection(item_tokens))
        prompt_overlap = len(prompt_tokens.intersection(item_tokens))
        score = (100 * target_overlap) + (2 * prompt_overlap)
        if item_id in referenced_ids:
            score += 10_000
        scored.append((score, position, item))

    relevant = [entry for entry in scored if entry[0] > 0]
    if not relevant and scored:
        relevant = sorted(scored, key=lambda entry: (-entry[0], entry[1]))[:1]
    selected_positions = {
        position
        for _, position, _ in sorted(
            relevant,
            key=lambda entry: (-entry[0], entry[1]),
        )[:max_context_items]
    }
    retained = [item for position, item in enumerate(context) if position in selected_positions]
    dropped = [item for position, item in enumerate(context) if position not in selected_positions]
    compact_task = {
        key: task.get(key)
        for key in ("id", "category", "prompt", "sequence", "targets", "metadata")
        if key in task
    }
    compact_task["context"] = retained
    original_chars = len(_stable_json({"context": context, "sequence": sequence}))
    compact_chars = len(_stable_json({"context": retained, "sequence": sequence}))
    original_hash = hashlib.sha256(_stable_json(task).encode("utf-8")).hexdigest()
    compact_hash = hashlib.sha256(_stable_json(compact_task).encode("utf-8")).hexdigest()
    return {
        "schema_version": "orbita-evidence-compression/1.0",
        "task": compact_task,
        "receipt": {
            "task_id": str(task["id"]),
            "strategy": "target-and-sequence-lexical-selection/1.0",
            "original_task_hash": original_hash,
            "compact_task_hash": compact_hash,
            "original_context_items": len(context),
            "retained_context_items": len(retained),
            "dropped_context_items": len(dropped),
            "retained_ids": [
                item_id for item in retained if (item_id := _context_id(item))
            ],
            "dropped_ids": [
                item_id for item in dropped if (item_id := _context_id(item))
            ],
            "original_context_characters": original_chars,
            "compact_context_characters": compact_chars,
            "character_reduction": (
                1.0 - (compact_chars / original_chars)
                if original_chars
                else 0.0
            ),
            "model_calls": 0,
            "network_calls": 0,
        },
    }


def _best_items(target_id: str, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = list(items)
    if not candidates:
        return []
    target_tokens = _tokens(target_id)
    scored = [
        (len(target_tokens.intersection(_item_tokens(item))), position, item)
        for position, item in enumerate(candidates)
    ]
    best = max(score for score, _, _ in scored)
    if best <= 0:
        return candidates
    return [item for score, _, item in scored if score == best]


def _context_id(item: dict[str, Any]) -> str | None:
    value = item.get("id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _outcome(item: dict[str, Any]) -> str:
    for field in ("outcome", "state", "status", "result"):
        value = item.get(field)
        if isinstance(value, str):
            return value.casefold().strip()
    return ""


def _is_weak_source(item: dict[str, Any]) -> bool:
    return str(item.get("kind", "")).casefold() in _WEAK_SOURCE_KINDS


def _is_support(item: dict[str, Any]) -> bool:
    outcome = _outcome(item)
    if outcome in _SUPPORT_OUTCOMES:
        return True
    if outcome in _REFUTE_OUTCOMES:
        return False
    if item.get("supported") is True or item.get("checks_passed") is True:
        return True
    kind = str(item.get("kind", "")).casefold()
    return bool(_context_id(item)) and not _is_weak_source(item) and kind in {
        "analysis_receipt",
        "evidence",
        "regulator",
        "sensor",
        "study",
    }


def _is_refute(item: dict[str, Any]) -> bool:
    outcome = _outcome(item)
    return outcome in _REFUTE_OUTCOMES or item.get("supported") is False


def _event_name(event: dict[str, Any]) -> str:
    value = event.get("event")
    return value.casefold().strip() if isinstance(value, str) else ""


def _revoked_evidence_ids(sequence: list[dict[str, Any]]) -> set[str]:
    return {
        str(event["evidence_id"])
        for event in sequence
        if _event_name(event) in _REVOKE_EVENTS | _REFUTE_EVENTS and event.get("evidence_id")
    }


def _proofs(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in context
        if str(item.get("kind", "")).casefold() in {"derivation", "proof"}
        and isinstance(item.get("premises"), list)
        and _context_id(item)
    ]


def _premise_is_revoked(
    premise: Any,
    revoked_ids: set[str],
    context_by_id: dict[str, dict[str, Any]],
) -> bool:
    premise_text = str(premise).casefold().strip()
    premise_tokens = _semantic_tokens(premise)
    for evidence_id in revoked_ids:
        evidence = context_by_id.get(evidence_id, {"id": evidence_id})
        references = {
            str(evidence.get(field, "")).casefold().strip()
            for field in ("id", "claim", "subject", "hypothesis")
            if evidence.get(field)
        }
        if premise_text in references or premise_tokens.intersection(
            _semantic_tokens({key: evidence.get(key) for key in ("id", "claim", "subject", "text")})
        ):
            return True
    return False


def _support_for_premise(
    premise: Any,
    context: list[dict[str, Any]],
    revoked_ids: set[str],
) -> list[str]:
    premise_text = str(premise).casefold().strip()
    premise_tokens = _semantic_tokens(premise)
    matches = []
    for item in context:
        item_id = _context_id(item)
        if not item_id or item_id in revoked_ids or not _is_support(item):
            continue
        references = {
            str(item.get(field, "")).casefold().strip()
            for field in ("id", "claim", "subject", "hypothesis")
            if item.get(field)
        }
        if premise_text in references or premise_tokens.intersection(
            _semantic_tokens({key: item.get(key) for key in ("id", "claim", "subject", "text")})
        ):
            matches.append(item_id)
    return matches


def _surviving_proofs(
    context: list[dict[str, Any]],
    sequence: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[str]]]:
    revoked_ids = _revoked_evidence_ids(sequence)
    context_by_id = {item_id: item for item in context if (item_id := _context_id(item))}
    survivors = []
    for proof in _proofs(context):
        premises = list(proof.get("premises", []))
        if any(_premise_is_revoked(premise, revoked_ids, context_by_id) for premise in premises):
            continue
        support_ids: list[str] = []
        complete = True
        for premise in premises:
            matches = _support_for_premise(premise, context, revoked_ids)
            if not matches:
                complete = False
                break
            support_ids.extend(matches)
        if complete:
            survivors.append((proof, list(dict.fromkeys(support_ids))))
    return survivors


def _trace(kind: str, identifiers: Iterable[str]) -> list[dict[str, str]]:
    return [{"kind": kind, "id": identifier} for identifier in dict.fromkeys(identifiers) if identifier]


def _claim_judgments(
    task: dict[str, Any],
    context: list[dict[str, Any]],
    sequence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    category = str(task["category"])
    results: list[dict[str, Any]] = []
    audit: list[dict[str, str]] = []
    sequence_refutes = [
        event for event in sequence if _event_name(event) in _REFUTE_EVENTS and event.get("evidence_id")
    ]
    sequence_revokes = [
        event for event in sequence if _event_name(event) in _REVOKE_EVENTS and event.get("evidence_id")
    ]
    survivors = _surviving_proofs(context, sequence)

    for target_id in _target_ids(task, "claims"):
        evidence_ids: list[str] = []
        derivation_ids: list[str] = []
        state = "unknown"
        rationale = "No independent structured support establishes this claim."

        if category == "contradiction_recovery" and sequence_refutes:
            state = "challenged"
            evidence_ids = [str(event["evidence_id"]) for event in sequence_refutes]
            rationale = "Later refuting evidence requires the previously supported claim to be challenged."
        elif category in {"evidence_collapse", "evidence_preservation"}:
            if survivors:
                proof, evidence_ids = survivors[0]
                state = "supported"
                derivation_ids = [_context_id(proof) or ""]
                rationale = "A complete proof with non-revoked supporting evidence remains."
            else:
                state = "unknown"
                rationale = "No complete proof remains after revoked evidence is propagated."
        elif category == "temporal_scope":
            matches = _best_items(target_id, context)
            supported = [item for item in matches if _is_support(item)]
            if supported:
                state = "supported"
                evidence_ids = [item_id for item in supported if (item_id := _context_id(item))]
                rationale = "The matching time-scoped observation supports this claim independently."
        else:
            matches = _best_items(target_id, context)
            refuters = [item for item in matches if _is_refute(item)]
            supporters = [item for item in matches if _is_support(item) and not _is_weak_source(item)]
            if refuters:
                state = "challenged"
                evidence_ids = [item_id for item in refuters if (item_id := _context_id(item))]
                rationale = "Structured refuting evidence challenges the claim."
            elif supporters:
                state = "supported"
                evidence_ids = [item_id for item in supporters if (item_id := _context_id(item))]
                rationale = "Structured non-weak evidence supports the claim."
            elif sequence_revokes:
                rationale = "The claim lacks a surviving support path after revocation."

        results.append(
            {
                "claim_id": target_id,
                "state": state,
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
                "derivation_ids": list(dict.fromkeys(identifier for identifier in derivation_ids if identifier)),
                "rationale": rationale,
            }
        )
        audit.extend(_trace("evidence", evidence_ids))
        audit.extend(_trace("proof", derivation_ids))
    return results, audit


def _action_judgments(
    task: dict[str, Any],
    context: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    receipts = [item for item in context if str(item.get("kind", "")).casefold() == "execution_receipt"]
    results = []
    audit: list[dict[str, str]] = []
    for target_id in _target_ids(task, "actions"):
        matches = _best_items(target_id, receipts)
        receipt_ids = [item_id for item in matches if (item_id := _context_id(item))]
        failed = any(
            (isinstance(item.get("exit_code"), (int, float)) and item["exit_code"] != 0)
            or item.get("checks_passed") is False
            or item.get("artifact_exists") is False
            for item in matches
        )
        succeeded = bool(matches) and all(
            (not isinstance(item.get("exit_code"), (int, float)) or item["exit_code"] == 0)
            and item.get("checks_passed") is not False
            and item.get("artifact_exists") is not False
            for item in matches
        )
        if failed:
            state = "failure"
            rationale = "The execution receipt records a nonzero exit, failed check, or missing required artifact."
        elif succeeded:
            state = "success"
            rationale = "The execution receipts contain no recorded failure condition."
        else:
            state = "unknown"
            rationale = "No sufficient execution receipt establishes success or failure."
        results.append(
            {
                "action_id": target_id,
                "state": state,
                "receipt_ids": receipt_ids,
                "rationale": rationale,
            }
        )
        audit.extend(_trace("execution_receipt", receipt_ids))
    return results, audit


def _discovery_judgments(
    task: dict[str, Any],
    context: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    receipts = [item for item in context if str(item.get("kind", "")).casefold() == "analysis_receipt"]
    results = []
    audit: list[dict[str, str]] = []
    for target_id in _target_ids(task, "discoveries"):
        matches = [item for item in receipts if str(item.get("hypothesis", "")) == target_id]
        evidence_ids = [item_id for item in matches if (item_id := _context_id(item))]
        refuters = [item for item in matches if _is_refute(item)]
        supporters = [item for item in matches if _is_support(item)]
        independence = {
            str(item["independence_key"])
            for item in supporters
            if isinstance(item.get("independence_key"), str) and item["independence_key"].strip()
        }
        if refuters:
            state = "rejected"
            rationale = "At least one supplied analysis receipt refutes this hypothesis."
        elif len(independence) >= 2:
            state = "committed"
            rationale = "Support survives across at least two declared independent evidence sources."
        elif supporters:
            state = "provisional"
            rationale = "The hypothesis has support but lacks independent replication."
        else:
            state = "unknown"
            rationale = "No supplied analysis receipt adjudicates this hypothesis."
        results.append(
            {
                "hypothesis_id": target_id,
                "state": state,
                "evidence_ids": evidence_ids,
                "rationale": rationale,
            }
        )
        audit.extend(_trace("analysis_receipt", evidence_ids))
    return results, audit


def adjudicate_epistemic_task(task: dict[str, Any]) -> dict[str, Any]:
    """Deterministically adjudicate one bounded public epistemic task.

    This function has no database, network, model, or filesystem access. It rejects
    gold labels and derives judgments only from the structured context, event
    sequence, and target identifiers supplied in the public task.
    """

    context, sequence = _validate_task(task)
    coverage = assess_adjudication_coverage(task)
    claim_judgments, claim_trace = _claim_judgments(task, context, sequence)
    action_judgments, action_trace = _action_judgments(task, context)
    discovery_judgments, discovery_trace = _discovery_judgments(task, context)
    audit_trace = []
    seen_trace: set[tuple[str, str]] = set()
    for item in claim_trace + action_trace + discovery_trace:
        key = (item["kind"], item["id"])
        if key not in seen_trace:
            seen_trace.add(key)
            audit_trace.append(item)
    states = [
        *(item["state"] for item in claim_judgments),
        *(item["state"] for item in action_judgments),
        *(item["state"] for item in discovery_judgments),
    ]
    final_answer = (
        f"Orbita deterministically adjudicated {len(states)} target(s): "
        + ", ".join(states)
        if states
        else "The task contains no adjudication targets."
    )
    public_payload = {
        key: task.get(key)
        for key in ("id", "category", "prompt", "context", "sequence", "targets", "metadata")
        if key in task
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": str(task["id"]),
        "task_hash": hashlib.sha256(_stable_json(public_payload).encode("utf-8")).hexdigest(),
        "final_answer": final_answer,
        "claim_judgments": claim_judgments,
        "action_judgments": action_judgments,
        "discovery_judgments": discovery_judgments,
        "audit_trace": audit_trace,
        "decision_basis": {
            "engine": "orbita-deterministic-adjudicator/1.0",
            "model_calls": 0,
            "network_calls": 0,
            "context_items": len(context),
            "sequence_events": len(sequence),
            "coverage": coverage,
        },
        "limitations": [
            "Only explicit structured evidence, receipts, proofs, events, targets, and time tokens are adjudicated.",
            (
                "Free-form scientific truth, causality, source authenticity, "
                "and semantic equivalence remain outside scope."
            ),
            "Unknown is returned when the structured record is insufficient.",
        ],
    }
