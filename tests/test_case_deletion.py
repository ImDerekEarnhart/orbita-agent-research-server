"""Deletion has to mean the bytes are gone, or the word should not be used.

Someone who uploads their entire chat history and asks for it to be deleted is owed an
actual erasure, not a cleared search index sitting on top of an intact archive. These
tests check the volume, not the API's reply.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from orbita_agent import AgentConfig, AgentGateway
from orbita_agent.gateway import DELETION_PHRASE

PHRASE = DELETION_PHRASE


def _export():
    return [
        {
            "conversation_id": "conv_1",
            "title": "Private conversation",
            "current_node": "m1",
            "mapping": {
                "root": {"id": "root", "parent": None, "children": ["m1"], "message": None},
                "m1": {
                    "id": "m1",
                    "parent": "root",
                    "children": [],
                    "message": {
                        "author": {"role": "user"},
                        "create_time": 1_700_000_000.0,
                        "content": {
                            "content_type": "text",
                            "parts": ["My private medical situation is complicated."],
                        },
                    },
                },
            },
        }
    ]


@pytest.fixture
def case_with_archive(gateway, tmp_path):
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("conversations.json", json.dumps(_export()))

    case = gateway.create_case(name="My chat history", goal="")
    gateway.ingest_upload(case_id=case["id"], path=archive)
    return case["id"]


def _case_dir(gateway, case_id):
    return gateway.config.workspace / "cases" / case_id


def test_the_bytes_are_actually_gone(gateway, case_with_archive):
    directory = _case_dir(gateway, case_with_archive)
    assert directory.is_dir()
    assert any(directory.rglob("*.json")) or any(directory.rglob("*.zip"))

    result = gateway.delete_case(case_with_archive, confirmation=PHRASE)

    assert result["deleted"] is True
    assert result["verified_absent"] is True
    assert result["bytes_removed"] > 0
    assert result["files_removed"] > 0
    assert not directory.exists()


def test_the_uploaded_content_is_not_recoverable_from_the_volume(gateway, case_with_archive):
    """The distinguishing string must not survive anywhere under the workspace."""
    gateway.delete_case(case_with_archive, confirmation=PHRASE)

    needle = b"My private medical situation"
    for path in gateway.config.workspace.rglob("*"):
        if path.is_file():
            assert needle not in path.read_bytes(), f"content survived in {path}"


def test_the_case_is_gone_from_the_database(gateway, case_with_archive):
    gateway.delete_case(case_with_archive, confirmation=PHRASE)

    assert case_with_archive not in [case["id"] for case in gateway.list_cases()]
    with pytest.raises(KeyError):
        gateway.case_context(case_with_archive)


def test_indexed_memory_goes_with_it(gateway, case_with_archive):
    assert gateway.search_memory("medical")["hit_count"] > 0

    result = gateway.delete_case(case_with_archive, confirmation=PHRASE)

    assert result["memory_messages_removed"] > 0
    assert gateway.search_memory("medical")["hit_count"] == 0
    assert gateway.memory_status()["messages"] == 0


def test_a_manifest_records_what_was_destroyed(gateway, case_with_archive):
    result = gateway.delete_case(case_with_archive, confirmation=PHRASE)

    assert result["case_name"] == "My chat history"
    assert result["file_manifest"]
    entry = result["file_manifest"][0]
    assert entry["original_name"] == "export.zip"
    assert entry["sha256"]


def test_deletion_requires_the_exact_phrase(gateway, case_with_archive):
    for wrong in ["", "delete it", PHRASE.upper(), PHRASE + " "]:
        with pytest.raises(ValueError, match="exact phrase"):
            gateway.delete_case(case_with_archive, confirmation=wrong)

    # Nothing was touched by any of those attempts.
    assert _case_dir(gateway, case_with_archive).is_dir()
    assert gateway.case_context(case_with_archive)["case"]["id"] == case_with_archive


def test_deleting_one_case_leaves_others_intact(gateway, case_with_archive):
    keeper = gateway.create_case(name="Keep me", goal="")
    gateway.add_inline_file(case_id=keeper["id"], filename="rows.csv", content="a,b\n1,2\n3,4\n")

    gateway.delete_case(case_with_archive, confirmation=PHRASE)

    assert gateway.case_context(keeper["id"])["case"]["file_count"] == 1
    assert _case_dir(gateway, keeper["id"]).is_dir()


def test_an_unknown_case_is_refused(gateway):
    with pytest.raises(KeyError):
        gateway.delete_case("case_does_not_exist", confirmation=PHRASE)


def test_another_tenants_case_cannot_be_deleted(tmp_path):
    base = AgentConfig(home=tmp_path / "home")
    alice = AgentGateway(base.for_tenant("alice"))
    bob = AgentGateway(base.for_tenant("bob"))
    try:
        case_id = alice.create_case(name="Alice private", goal="")["id"]

        with pytest.raises(KeyError):
            bob.delete_case(case_id, confirmation=PHRASE)

        assert alice.case_context(case_id)["case"]["id"] == case_id
    finally:
        alice.close()
        bob.close()


def test_the_ledger_is_not_silently_truncated(gateway, case_with_archive):
    """A chat archive produces no claims, and the reply must say so rather than imply erasure."""
    result = gateway.delete_case(case_with_archive, confirmation=PHRASE)

    assert result["claims_left_in_ledger"] == []
    assert "No claims were derived" in result["boundary"]


def test_deletion_cannot_escape_the_workspace(gateway, tmp_path, monkeypatch):
    """A stored path is data. Data is never a licence to remove an arbitrary directory."""
    outsider = tmp_path / "not-ours"
    outsider.mkdir()
    (outsider / "important.txt").write_text("must survive", encoding="utf-8")

    case_id = gateway.create_case(name="Traversal", goal="")["id"]
    # Even a case id shaped like a traversal resolves outside the workspace and is skipped.
    hostile = "../../../not-ours"
    with pytest.raises(KeyError):
        gateway.delete_case(hostile, confirmation=PHRASE)

    gateway.delete_case(case_id, confirmation=PHRASE)
    assert (outsider / "important.txt").exists()
