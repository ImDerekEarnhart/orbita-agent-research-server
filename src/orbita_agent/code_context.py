from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]+")
_GENERIC = {
    "a",
    "an",
    "and",
    "bug",
    "code",
    "file",
    "fix",
    "for",
    "in",
    "is",
    "it",
    "of",
    "or",
    "the",
    "to",
}
MAX_FILES = 256
MAX_TOTAL_CHARACTERS = 1_000_000


class CodeContextError(ValueError):
    """A code-context bundle cannot be selected safely."""


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(value.casefold())
        if token not in _GENERIC and len(token) > 1
    }


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compress_code_context(
    issue: str,
    files: list[dict[str, Any]],
    *,
    max_files: int = 6,
    max_characters: int = 80_000,
) -> dict[str, Any]:
    """Select issue-relevant files without reading the filesystem or calling a model."""

    if not isinstance(issue, str) or not issue.strip():
        raise CodeContextError("issue must be a nonblank string")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise CodeContextError(f"files must contain between 1 and {MAX_FILES} entries")
    if not isinstance(max_files, int) or not 1 <= max_files <= 32:
        raise CodeContextError("max_files must be an integer between 1 and 32")
    if not isinstance(max_characters, int) or not 1_000 <= max_characters <= 500_000:
        raise CodeContextError("max_characters must be between 1000 and 500000")

    normalized = []
    seen_paths = set()
    for item in files:
        if not isinstance(item, dict):
            raise CodeContextError("every file entry must be an object")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path.strip() or len(path) > 500:
            raise CodeContextError("every file path must be a nonblank bounded string")
        if path in seen_paths:
            raise CodeContextError(f"duplicate file path: {path}")
        if not isinstance(content, str):
            raise CodeContextError(f"file content must be a string: {path}")
        seen_paths.add(path)
        normalized.append({"path": path, "content": content})
    total_characters = sum(len(item["content"]) for item in normalized)
    if total_characters > MAX_TOTAL_CHARACTERS:
        raise CodeContextError("code-context bundle is too large")

    issue_tokens = _tokens(issue)
    scored = []
    for position, item in enumerate(normalized):
        path_tokens = _tokens(item["path"])
        content_tokens = _tokens(item["content"])
        overlap = issue_tokens.intersection(path_tokens | content_tokens)
        path_overlap = issue_tokens.intersection(path_tokens)
        score = (20 * len(path_overlap)) + (5 * len(overlap))
        if item["path"].casefold().startswith(("test", "tests/")):
            score += 2 * len(overlap)
        scored.append((score, position, item))
    ranked = sorted(scored, key=lambda entry: (-entry[0], entry[1]))

    selected_positions = set()
    selected_characters = 0
    for score, position, item in ranked:
        if len(selected_positions) >= max_files:
            break
        if score <= 0 and selected_positions:
            break
        item_characters = len(item["content"])
        if selected_positions and selected_characters + item_characters > max_characters:
            continue
        selected_positions.add(position)
        selected_characters += item_characters
    if not selected_positions:
        selected_positions.add(0)

    retained = [
        item for position, item in enumerate(normalized) if position in selected_positions
    ]
    dropped = [
        item for position, item in enumerate(normalized) if position not in selected_positions
    ]
    source_hash = hashlib.sha256(
        _stable_json({"issue": issue, "files": normalized}).encode("utf-8")
    ).hexdigest()
    compact_hash = hashlib.sha256(
        _stable_json({"issue": issue, "files": retained}).encode("utf-8")
    ).hexdigest()
    retained_characters = sum(len(item["content"]) for item in retained)
    return {
        "schema_version": "orbita-code-context/1.0",
        "issue": issue,
        "files": retained,
        "receipt": {
            "strategy": "issue-symbol-lexical-selection/1.0",
            "source_hash": source_hash,
            "compact_hash": compact_hash,
            "original_files": len(normalized),
            "retained_files": len(retained),
            "dropped_files": len(dropped),
            "retained_paths": [item["path"] for item in retained],
            "dropped_paths": [item["path"] for item in dropped],
            "original_characters": total_characters,
            "retained_characters": retained_characters,
            "character_reduction": (
                1.0 - (retained_characters / total_characters)
                if total_characters
                else 0.0
            ),
            "model_calls": 0,
            "network_calls": 0,
        },
    }

