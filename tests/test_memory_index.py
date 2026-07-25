"""Search over an archive is only worth anything if every hit can be traced back."""

from __future__ import annotations

import json
import zipfile

import pytest

from orbita_agent.memory_index import MemoryIndex
from orbita_mvp.chatgpt_export import messages_to_frame, parse_conversations


def _node(node_id, parent, children, role=None, text=None, create_time=None):
    node = {"id": node_id, "parent": parent, "children": list(children)}
    node["message"] = (
        {
            "author": {"role": role},
            "create_time": create_time,
            "content": {"content_type": "text", "parts": [text]},
        }
        if role
        else None
    )
    return node


def _export():
    """Two conversations, months apart, holding a reversal of position."""
    march = {
        "conversation_id": "conv_march",
        "title": "Substrate choice",
        "current_node": "m2",
        "mapping": {
            "root": _node("root", None, ["m1"]),
            "m1": _node("m1", "root", ["m2"], "user", "I think we should use Postgres for the ledger.", 1_700_000_000.0),
            "m2": _node("m2", "m1", [], "assistant", "Postgres gives you transactional guarantees.", 1_700_000_060.0),
        },
    }
    june = {
        "conversation_id": "conv_june",
        "title": "Substrate revisited",
        "current_node": "j2",
        "mapping": {
            "root2": _node("root2", None, ["j1"]),
            "j1": _node("j1", "root2", ["j2"], "user", "Postgres was the wrong call for the ledger, sqlite is enough.", 1_710_000_000.0),
            "j2": _node("j2", "j1", [], "assistant", "A single-writer ledger does not need Postgres.", 1_710_000_060.0),
        },
    }
    return [march, june]


@pytest.fixture
def index(tmp_path):
    instance = MemoryIndex(tmp_path / "memory.db")
    messages, _ = parse_conversations(_export())
    instance.index_frame(
        case_id="case_1",
        file_id="file_1",
        source_name="conversations.json",
        frame=messages_to_frame(messages),
    )
    try:
        yield instance
    finally:
        instance.close()


def test_every_hit_carries_a_usable_receipt(index):
    result = index.search("Postgres ledger")

    assert result["hit_count"] > 0
    receipt = result["hits"][0]["receipt"]
    assert receipt["conversation_id"] in {"conv_march", "conv_june"}
    assert receipt["node_id"]
    assert receipt["case_id"] == "case_1"
    assert receipt["source_name"] == "conversations.json"
    assert result["hits"][0]["created_at"]


def test_results_never_assert_truth(index):
    result = index.search("Postgres")
    assert "was written" in result["boundary"]
    # No hit carries a status, score, verdict, or confidence of any kind.
    for hit in result["hits"]:
        assert set(hit) == {"snippet", "text", "role", "created_at", "receipt"}


def test_search_can_be_filtered_by_role_and_conversation(index):
    assert all(h["role"] == "user" for h in index.search("Postgres", role="user")["hits"])
    scoped = index.search("Postgres", conversation_id="conv_june")["hits"]
    assert scoped and all(h["receipt"]["conversation_id"] == "conv_june" for h in scoped)


def test_a_reversal_across_time_is_findable_by_date(index):
    """The raw material for contradiction detection: same topic, two dates, opposite stance."""
    hits = index.search("Postgres ledger", role="user")["hits"]
    dates = sorted(hit["created_at"] for hit in hits)

    assert len(dates) == 2
    assert dates[0] < dates[1]


def test_conversation_returns_messages_in_order(index):
    conversation = index.conversation("conv_march")

    assert conversation["title"] == "Substrate choice"
    assert [m["role"] for m in conversation["messages"]] == ["user", "assistant"]


def test_stats_describe_the_archive(index):
    stats = index.stats()

    assert stats["messages"] == 4
    assert stats["conversations"] == 2
    assert stats["files"] == 1
    assert stats["roles"] == {"user": 2, "assistant": 2}
    assert stats["earliest"] < stats["latest"]


@pytest.mark.parametrize("query", ["*", "((", 'AND OR NOT', "\"", "a AND b"])
def test_query_syntax_cannot_reach_the_engine(index, query):
    """Callers type questions, not FTS expressions; operators must not be injectable."""
    try:
        result = index.search(query)
    except ValueError:
        return  # queries with no words at all are refused, which is also fine
    assert isinstance(result["hit_count"], int)


def test_an_empty_query_is_refused(index):
    with pytest.raises(ValueError, match="at least one word"):
        index.search("   ")


def test_reindexing_the_same_file_replaces_rather_than_duplicates(index):
    messages, _ = parse_conversations(_export())
    index.index_frame(
        case_id="case_1",
        file_id="file_1",
        source_name="conversations.json",
        frame=messages_to_frame(messages),
    )
    assert index.stats()["messages"] == 4


# -- deletion, which G22 requires be demonstrable ------------------------------


def test_deleting_a_case_removes_it_from_search(index):
    assert index.search("Postgres")["hit_count"] > 0

    deleted = index.delete_case("case_1")

    assert deleted == 4
    assert index.search("Postgres")["hit_count"] == 0
    assert index.stats()["messages"] == 0


def test_deleting_everything_leaves_nothing_searchable(index):
    index.delete_everything()
    assert index.search("Postgres")["hit_count"] == 0


def test_deleting_one_file_leaves_the_other(tmp_path):
    instance = MemoryIndex(tmp_path / "memory.db")
    messages, _ = parse_conversations(_export())
    frame = messages_to_frame(messages)
    instance.index_frame(case_id="c1", file_id="f1", source_name="a.json", frame=frame)
    instance.index_frame(case_id="c2", file_id="f2", source_name="b.json", frame=frame)
    try:
        instance.delete_file("f1")
        remaining = {h["receipt"]["file_id"] for h in instance.search("Postgres")["hits"]}
        assert remaining == {"f2"}
    finally:
        instance.close()


# -- end to end through the gateway --------------------------------------------


def test_uploading_an_export_makes_it_searchable(gateway, tmp_path):
    archive = tmp_path / "chatgpt-export.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("conversations.json", json.dumps(_export()))

    case = gateway.create_case(name="My history", goal="")
    record = gateway.ingest_upload(case_id=case["id"], path=archive)

    assert record["artifact_kind"] == "archive"
    assert record["memory"]["messages_indexed"] == 4

    found = gateway.search_memory("sqlite ledger")
    assert found["hit_count"] > 0
    assert found["hits"][0]["receipt"]["conversation_id"] == "conv_june"

    assert gateway.memory_status()["conversations"] == 2

    forgotten = gateway.forget_memory(case_id=case["id"])
    assert forgotten["messages_deleted"] == 4
    assert gateway.search_memory("sqlite ledger")["hit_count"] == 0


def test_one_tenants_memory_is_invisible_to_another(tmp_path):
    from orbita_agent import AgentConfig, AgentGateway

    base = AgentConfig(home=tmp_path / "home")
    alice = AgentGateway(base.for_tenant("alice"))
    bob = AgentGateway(base.for_tenant("bob"))
    try:
        messages, _ = parse_conversations(_export())
        alice.memory.index_frame(
            case_id="c", file_id="f", source_name="a.json", frame=messages_to_frame(messages)
        )
        assert alice.search_memory("Postgres")["hit_count"] > 0
        assert bob.search_memory("Postgres")["hit_count"] == 0
    finally:
        alice.close()
        bob.close()


def test_forget_requires_a_scope(gateway):
    with pytest.raises(ValueError, match="case_id"):
        gateway.forget_memory()
