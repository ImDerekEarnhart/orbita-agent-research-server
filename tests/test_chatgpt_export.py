"""The export is a branching tree, and reading it as a flat list invents a transcript."""

from __future__ import annotations

import json
import zipfile

import pytest

from orbita_mvp.chatgpt_export import (
    looks_like_chatgpt_export,
    messages_to_frame,
    parse_conversations,
    parse_export_file,
)
from orbita_mvp.ingestion import ArtifactIngestor


def _node(node_id, parent, children, role=None, text=None, create_time=None):
    node = {"id": node_id, "parent": parent, "children": list(children)}
    if role is not None:
        node["message"] = {
            "id": f"msg_{node_id}",
            "author": {"role": role, "name": None, "metadata": {}},
            "create_time": create_time,
            "content": {"content_type": "text", "parts": [text]},
            "status": "finished_successfully",
        }
    else:
        node["message"] = None
    return node


def _conversation_with_a_regeneration():
    """root -> q1 -> (a1_abandoned | a1_kept); current_node points at the kept answer."""
    mapping = {
        "root": _node("root", None, ["q1"]),
        "q1": _node("q1", "root", ["a1_abandoned", "a1_kept"], "user", "What is a symmetry?", 1000.0),
        "a1_abandoned": _node("a1_abandoned", "q1", [], "assistant", "A DISCARDED DRAFT", 1001.0),
        "a1_kept": _node("a1_kept", "q1", ["q2"], "assistant", "An invariance under a transform.", 1002.0),
        "q2": _node("q2", "a1_kept", [], "user", "Give an example.", 1003.0),
    }
    return {
        "title": "Symmetry chat",
        "conversation_id": "conv_1",
        "current_node": "q2",
        "mapping": mapping,
    }


def test_only_the_displayed_branch_becomes_a_transcript():
    messages, summary = parse_conversations([_conversation_with_a_regeneration()])

    texts = [message.text for message in messages]
    assert texts == [
        "What is a symmetry?",
        "An invariance under a transform.",
        "Give an example.",
    ]
    # The abandoned regeneration is counted, never presented as something that was said.
    assert "A DISCARDED DRAFT" not in texts
    assert summary.branch_nodes == 1
    assert summary.messages == 3
    assert summary.conversations == 1


def test_messages_carry_what_is_needed_to_cite_them():
    messages, _ = parse_conversations([_conversation_with_a_regeneration()])
    first = messages[0]

    assert first.conversation_id == "conv_1"
    assert first.conversation_title == "Symmetry chat"
    assert first.node_id == "q1"
    assert first.role == "user"
    assert first.depth == 1
    assert first.created_at == "1970-01-01T00:16:40+00:00"
    assert first.is_conversational is True


def test_a_missing_current_node_falls_back_to_the_deepest_branch():
    conversation = _conversation_with_a_regeneration()
    del conversation["current_node"]

    messages, summary = parse_conversations([conversation])

    assert summary.conversations_without_current_node == 1
    assert [message.text for message in messages][-1] == "Give an example."


def test_a_dangling_current_node_does_not_lose_the_conversation():
    conversation = _conversation_with_a_regeneration()
    conversation["current_node"] = "node_that_does_not_exist"

    messages, summary = parse_conversations([conversation])

    assert summary.conversations_without_current_node == 1
    assert messages


def test_a_cycle_cannot_hang_the_walk():
    mapping = {
        "a": _node("a", "b", ["b"], "user", "one", 1.0),
        "b": _node("b", "a", ["a"], "assistant", "two", 2.0),
    }
    messages, _ = parse_conversations(
        [{"conversation_id": "c", "current_node": "b", "mapping": mapping}]
    )
    assert len(messages) == 2


def test_empty_and_hidden_nodes_are_counted_not_emitted():
    mapping = {
        "root": _node("root", None, ["sys"]),
        "sys": _node("sys", "root", ["q"], "system", "", 1.0),
        "q": _node("q", "sys", [], "user", "hello", 2.0),
    }
    messages, summary = parse_conversations(
        [{"conversation_id": "c", "current_node": "q", "mapping": mapping}]
    )

    assert [message.text for message in messages] == ["hello"]
    assert summary.empty_nodes == 1


def test_system_and_tool_turns_are_kept_but_flagged_non_conversational():
    mapping = {
        "root": _node("root", None, ["t"]),
        "t": _node("t", "root", ["q"], "tool", "search results", 1.0),
        "q": _node("q", "t", [], "user", "thanks", 2.0),
    }
    messages, summary = parse_conversations(
        [{"conversation_id": "c", "current_node": "q", "mapping": mapping}]
    )

    by_role = {message.role: message for message in messages}
    assert by_role["tool"].is_conversational is False
    assert by_role["user"].is_conversational is True
    assert summary.roles == {"tool": 1, "user": 1}


def test_multimodal_parts_record_an_attachment_rather_than_inventing_text():
    mapping = {
        "root": _node("root", None, ["q"]),
        "q": {
            "id": "q",
            "parent": "root",
            "children": [],
            "message": {
                "author": {"role": "user"},
                "create_time": 1.0,
                "content": {
                    "content_type": "multimodal_text",
                    "parts": [{"asset_pointer": "file-service://abc"}, "what is this?"],
                },
            },
        },
    }
    messages, _ = parse_conversations(
        [{"conversation_id": "c", "current_node": "q", "mapping": mapping}]
    )

    assert messages[0].text == "[attachment file-service://abc]\nwhat is this?"
    assert messages[0].content_type == "multimodal_text"


def test_the_frame_has_one_row_per_message_with_counts():
    messages, _ = parse_conversations([_conversation_with_a_regeneration()])
    frame = messages_to_frame(messages)

    assert len(frame) == 3
    assert list(frame["role"]) == ["user", "assistant", "user"]
    assert frame["char_count"].iloc[0] == len("What is a symmetry?")
    assert frame["word_count"].iloc[0] == 4


def test_an_empty_export_produces_an_empty_frame_not_a_crash():
    messages, summary = parse_conversations([])
    frame = messages_to_frame(messages)

    assert summary.conversations == 0
    assert frame.empty
    assert "text" in frame.columns


def test_a_non_list_payload_is_refused():
    with pytest.raises(ValueError, match="list of conversations"):
        parse_conversations({"mapping": {}})


@pytest.mark.parametrize(
    "payload,expected",
    [
        ([{"mapping": {}}], True),
        ([{"id": "a", "text": "hello"}], False),
        ([], False),
        ({"mapping": {}}, False),
        ("not json", False),
    ],
)
def test_export_detection_is_by_shape(payload, expected):
    assert looks_like_chatgpt_export(payload) is expected


def test_parse_export_file_round_trips(tmp_path):
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps([_conversation_with_a_regeneration()]), encoding="utf-8")

    frame, summary = parse_export_file(path)

    assert len(frame) == 3
    assert summary.earliest == "1970-01-01T00:16:40+00:00"
    assert summary.latest == "1970-01-01T00:16:43+00:00"


def test_an_export_inside_a_zip_is_recognised_end_to_end(tmp_path):
    """The real shape: a zip containing conversations.json plus the other export files."""
    archive = tmp_path / "chatgpt-export.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("conversations.json", json.dumps([_conversation_with_a_regeneration()]))
        handle.writestr("message_feedback.json", json.dumps([]))
        handle.writestr("chat.html", "<html></html>")

    result = ArtifactIngestor().ingest(archive, tmp_path / "out")

    members = {member["name"].replace("\\", "/"): member for member in result["profile"]["members"]}
    export = members["conversations.json"]
    assert export["artifact_kind"] == "chat_export"
    assert export["profile"]["rows"] == 3
    assert export["profile"]["chat_export"]["branch_nodes"] == 1
    assert export["profile"]["chat_export"]["conversations"] == 1


def test_a_renamed_export_is_still_recognised(tmp_path):
    path = tmp_path / "my-history.json"
    path.write_text(json.dumps([_conversation_with_a_regeneration()]), encoding="utf-8")

    result = ArtifactIngestor().ingest(path, tmp_path / "out")

    assert result["artifact_kind"] == "chat_export"
    assert result["profile"]["rows"] == 3


def test_an_unrelated_json_list_is_not_treated_as_an_export(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"a": 1, "b": 2}, {"a": 3, "b": 4}]), encoding="utf-8")

    result = ArtifactIngestor().ingest(path, tmp_path / "out")

    assert result["artifact_kind"] == "table"
    assert "chat_export" not in result["profile"]
