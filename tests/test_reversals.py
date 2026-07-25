"""Reversal detection must surface candidates and refuse to adjudicate them.

The failure this guards against is the one already on record in this repository:
run_c6b010982fd04cf0 emitted six refutations from a scorer that could not evaluate its
candidates. Deciding that two sentences conflict requires reading them, and nothing here
reads anything. So the contract under test is mostly about what the module declines to
claim.
"""

from __future__ import annotations

import pytest

from orbita_agent.memory_index import MemoryIndex
from orbita_agent.reversals import (
    find_candidate_reversals,
    reversal_markers,
    salient_terms,
)
from orbita_mvp.chatgpt_export import messages_to_frame, parse_conversations

DAY = 86_400.0
MARCH = 1_700_000_000.0


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


def _conversation(cid, title, turns):
    """turns: list of (node_id, role, text, create_time)."""
    mapping = {"root": _node(f"root_{cid}", None, [turns[0][0]])}
    mapping["root"]["id"] = f"root_{cid}"
    parent = "root"
    ids = ["root"]
    for index, (node_id, role, text, when) in enumerate(turns):
        children = [turns[index + 1][0]] if index + 1 < len(turns) else []
        mapping[node_id] = _node(node_id, parent, children, role, text, when)
        parent = node_id
        ids.append(node_id)
    mapping["root"]["children"] = [turns[0][0]]
    return {
        "conversation_id": cid,
        "title": title,
        "current_node": turns[-1][0],
        "mapping": mapping,
    }


@pytest.fixture
def index(tmp_path):
    instance = MemoryIndex(tmp_path / "memory.db")
    export = [
        _conversation(
            "conv_march",
            "Ledger substrate",
            [("m1", "user", "We should use Postgres for the discovery ledger.", MARCH)],
        ),
        _conversation(
            "conv_june",
            "Ledger revisited",
            [
                (
                    "j1",
                    "user",
                    "Postgres for the discovery ledger was the wrong call, sqlite is enough.",
                    MARCH + 90 * DAY,
                )
            ],
        ),
        _conversation(
            "conv_unrelated",
            "Lunch",
            [("u1", "user", "I had a sandwich for lunch today.", MARCH + 5 * DAY)],
        ),
    ]
    messages, _ = parse_conversations(export)
    instance.index_frame(
        case_id="case_1", file_id="file_1", source_name="conversations.json",
        frame=messages_to_frame(messages),
    )
    try:
        yield instance
    finally:
        instance.close()


# -- what it finds -----------------------------------------------------------------


def test_a_marked_change_of_position_is_surfaced(index):
    result = find_candidate_reversals(index)

    assert result["candidate_count"] >= 1
    top = result["candidates"][0]
    assert "Postgres" in top["earlier"]["text"]
    assert "wrong call" in top["later"]["text"]
    assert "ledger" in top["shared_terms"]
    assert "explicit self-correction" in top["markers"]


def test_both_sides_carry_a_receipt_and_a_date(index):
    top = find_candidate_reversals(index)["candidates"][0]

    for side in ("earlier", "later"):
        receipt = top[side]["receipt"]
        assert receipt["conversation_id"]
        assert receipt["node_id"]
        assert receipt["case_id"] == "case_1"
        assert top[side]["created_at"]

    assert top["earlier"]["created_at"] < top["later"]["created_at"]
    assert top["days_apart"] == pytest.approx(90.0, abs=0.1)


def test_unrelated_messages_are_not_paired(index):
    result = find_candidate_reversals(index)
    for candidate in result["candidates"]:
        assert "sandwich" not in candidate["earlier"]["text"]
        assert "sandwich" not in candidate["later"]["text"]


# -- what it refuses to claim -------------------------------------------------------


def test_nothing_is_called_a_contradiction(index):
    result = find_candidate_reversals(index)

    assert all(c["status"] == "candidate_reversal" for c in result["candidates"])
    serialized = str(result).lower()
    assert "refuted" not in serialized
    assert "falsified" not in serialized


def test_every_candidate_says_it_is_not_adjudicated(index):
    for candidate in find_candidate_reversals(index)["candidates"]:
        assert "Not adjudicated" in candidate["adjudication"]
        assert "Only you can decide" in candidate["adjudication"]


def test_the_boundary_states_the_method_and_its_limits(index):
    result = find_candidate_reversals(index)

    assert "candidates, not contradictions" in result["boundary"]
    assert "did not read either statement" in result["boundary"]
    assert "Word overlap is not topic identity" in result["boundary"]
    assert "a change of position is not an error" in result["boundary"]
    assert "belief graph" in result["boundary"]
    assert "content words" in result["method"]


def test_no_score_confidence_or_verdict_is_attached(index):
    for candidate in find_candidate_reversals(index)["candidates"]:
        assert set(candidate) == {
            "status",
            "shared_terms",
            "markers",
            "overlap",
            "days_apart",
            "earlier",
            "later",
            "adjudication",
        }
        assert "confidence" not in candidate
        assert "verdict" not in candidate


# -- ordering and thresholds --------------------------------------------------------


def test_messages_without_a_timestamp_are_excluded(tmp_path):
    """A pair whose ordering cannot be established is not evidence of anything."""
    import pandas as pd

    instance = MemoryIndex(tmp_path / "memory.db")
    frame = pd.DataFrame(
        [
            {
                "conversation_id": "c", "conversation_title": "t", "node_id": "n1",
                "role": "user", "created_at": None, "create_time": None,
                "content_type": "text", "text": "Postgres for the ledger.",
            },
            {
                "conversation_id": "c", "conversation_title": "t", "node_id": "n2",
                "role": "user", "created_at": None, "create_time": None,
                "content_type": "text", "text": "Postgres for the ledger was wrong.",
            },
        ]
    )
    try:
        instance.index_frame(case_id="c", file_id="f", source_name="s", frame=frame)
        assert find_candidate_reversals(instance)["candidate_count"] == 0
    finally:
        instance.close()


def test_the_later_message_is_always_the_one_with_the_marker(index):
    for candidate in find_candidate_reversals(index)["candidates"]:
        assert reversal_markers(candidate["later"]["text"])


def test_min_days_apart_excludes_same_session_edits(index):
    assert find_candidate_reversals(index, min_days_apart=365)["candidate_count"] == 0


def test_a_case_filter_scopes_the_search(index):
    assert find_candidate_reversals(index, case_id="case_other")["candidate_count"] == 0


# -- the primitives ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I was wrong about that", "explicit self-correction"),
        ("changed my mind on it", "explicit self-correction"),
        ("turns out it does not scale", "revised by evidence"),
        ("in hindsight we over-built", "retrospective regret"),
        ("use sqlite instead of Postgres", "replacement marker"),
    ],
)
def test_reversal_markers_are_detected(text, expected):
    assert expected in reversal_markers(text)


def test_ordinary_statements_carry_no_marker():
    assert reversal_markers("We use Postgres for the ledger.") == []


def test_stopwords_do_not_count_as_shared_subject_matter():
    terms = salient_terms("I think that we should really use the thing for it")
    assert terms <= {"use"}
