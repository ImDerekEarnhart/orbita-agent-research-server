"""Deterministic reader for a ChatGPT `conversations.json` export.

The export is not a list of messages. Each conversation is a `mapping` of node id to
node, forming a tree: every regeneration, edit, or branch adds a sibling rather than
replacing anything. Reading the mapping values in dict order therefore yields a
transcript that was never actually shown to anyone — abandoned drafts interleaved with
the surviving conversation.

This module instead walks parents from `current_node` back to the root, which is exactly
the single path the interface displays. Nodes off that path are counted and reported as
`branch_nodes` so nothing is silently dropped, but they are not presented as things the
user said.

No model is involved and no interpretation is applied. Every record carries the ids and
timestamps needed to point back at its exact source in the export.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd

# Roles whose text is the conversation itself. Tool and system turns are preserved in
# the record set but flagged, because treating a system preamble as something the user
# wrote is the kind of attribution error the whole point of this is to avoid.
CONVERSATIONAL_ROLES = {"user", "assistant"}


@dataclass(frozen=True)
class Message:
    conversation_id: str
    conversation_title: str
    node_id: str
    parent_id: str | None
    role: str
    author_name: str | None
    create_time: float | None
    text: str
    content_type: str
    depth: int
    is_conversational: bool

    @property
    def created_at(self) -> str | None:
        if self.create_time is None:
            return None
        try:
            return datetime.fromtimestamp(self.create_time, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None


@dataclass
class ExportSummary:
    conversations: int = 0
    messages: int = 0
    branch_nodes: int = 0
    empty_nodes: int = 0
    conversations_without_current_node: int = 0
    roles: dict[str, int] = field(default_factory=dict)
    earliest: str | None = None
    latest: str | None = None


def looks_like_chatgpt_export(payload: Any) -> bool:
    """Recognise the export without relying on the filename."""
    if not isinstance(payload, list) or not payload:
        return False
    head = payload[0]
    return isinstance(head, dict) and isinstance(head.get("mapping"), dict)


def _extract_text(content: Any) -> tuple[str, str]:
    """Return (text, content_type) for one message's content block."""
    if not isinstance(content, dict):
        return "", "unknown"
    content_type = str(content.get("content_type") or "unknown")

    parts = content.get("parts")
    if isinstance(parts, list):
        chunks: list[str] = []
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                # Multimodal parts carry an asset pointer rather than text. Record that
                # something was attached instead of inventing a description of it.
                pointer = part.get("asset_pointer") or part.get("image_url")
                if pointer:
                    chunks.append(f"[attachment {pointer}]")
                elif isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        return "\n".join(chunk for chunk in chunks if chunk).strip(), content_type

    for key in ("text", "result"):
        value = content.get(key)
        if isinstance(value, str):
            return value.strip(), content_type

    return "", content_type


def _walk_current_path(mapping: dict[str, Any], current_node: str | None) -> list[str]:
    """Return root-to-leaf node ids along the displayed branch."""
    if not current_node or current_node not in mapping:
        return []
    path: list[str] = []
    seen: set[str] = set()
    node_id: str | None = current_node
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        path.append(node_id)
        parent = mapping[node_id].get("parent")
        node_id = parent if isinstance(parent, str) else None
    path.reverse()
    return path


def _fallback_path(mapping: dict[str, Any]) -> list[str]:
    """Pick the deepest branch when `current_node` is missing or dangling.

    Some exports omit `current_node`. Rather than dropping the conversation, take the
    longest root-to-leaf chain, which is the closest available stand-in for the branch
    that was actually developed.
    """
    leaves = [
        node_id
        for node_id, node in mapping.items()
        if isinstance(node, dict) and not node.get("children")
    ]
    best: list[str] = []
    for leaf in leaves:
        candidate = _walk_current_path(mapping, leaf)
        if len(candidate) > len(best):
            best = candidate
    return best


def parse_conversations(payload: Any) -> tuple[list[Message], ExportSummary]:
    """Parse a decoded `conversations.json` into ordered messages plus a summary."""
    if not isinstance(payload, list):
        raise ValueError("a ChatGPT export must decode to a list of conversations")

    messages: list[Message] = []
    summary = ExportSummary()

    for index, conversation in enumerate(payload):
        if not isinstance(conversation, dict):
            continue
        mapping = conversation.get("mapping")
        if not isinstance(mapping, dict):
            continue

        summary.conversations += 1
        conversation_id = str(
            conversation.get("conversation_id") or conversation.get("id") or f"conversation_{index}"
        )
        title = str(conversation.get("title") or "Untitled conversation")

        current_node = conversation.get("current_node")
        path = _walk_current_path(mapping, current_node if isinstance(current_node, str) else None)
        if not path:
            summary.conversations_without_current_node += 1
            path = _fallback_path(mapping)

        on_path = set(path)
        summary.branch_nodes += sum(
            1
            for node_id, node in mapping.items()
            if node_id not in on_path and isinstance(node, dict) and node.get("message")
        )

        for depth, node_id in enumerate(path):
            node = mapping.get(node_id)
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue

            author = message.get("author") if isinstance(message.get("author"), dict) else {}
            role = str(author.get("role") or "unknown")
            text, content_type = _extract_text(message.get("content"))

            if not text:
                summary.empty_nodes += 1
                continue

            create_time = message.get("create_time")
            if not isinstance(create_time, (int, float)):
                create_time = None

            parent = node.get("parent")
            record = Message(
                conversation_id=conversation_id,
                conversation_title=title,
                node_id=str(node_id),
                parent_id=parent if isinstance(parent, str) else None,
                role=role,
                author_name=author.get("name") if isinstance(author.get("name"), str) else None,
                create_time=float(create_time) if create_time is not None else None,
                text=text,
                content_type=content_type,
                depth=depth,
                is_conversational=role in CONVERSATIONAL_ROLES,
            )
            messages.append(record)
            summary.messages += 1
            summary.roles[role] = summary.roles.get(role, 0) + 1

    stamps = sorted(m.created_at for m in messages if m.created_at)
    if stamps:
        summary.earliest, summary.latest = stamps[0], stamps[-1]

    return messages, summary


def messages_to_frame(messages: Iterable[Message]) -> pd.DataFrame:
    """Return one row per message, with every field needed to cite it."""
    rows = [asdict(message) | {"created_at": message.created_at} for message in messages]
    frame = pd.DataFrame(
        rows,
        columns=[
            "conversation_id",
            "conversation_title",
            "node_id",
            "parent_id",
            "role",
            "author_name",
            "create_time",
            "created_at",
            "content_type",
            "depth",
            "is_conversational",
            "text",
        ],
    )
    if not frame.empty:
        frame.insert(len(frame.columns), "char_count", frame["text"].str.len())
        frame.insert(len(frame.columns), "word_count", frame["text"].str.split().str.len())
    return frame


def parse_export_file(path) -> tuple[pd.DataFrame, ExportSummary]:
    """Read a `conversations.json` file from disk into a frame plus its summary."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    messages, summary = parse_conversations(payload)
    return messages_to_frame(messages), summary
