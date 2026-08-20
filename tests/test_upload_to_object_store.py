"""Uploads must end up in object storage, and must stop occupying the volume.

The point of the object store is that a fixed-size disk stops being the limit on how
many people can use the service. That only holds if the volume copy actually goes away,
so these tests check the disk rather than the return value.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from orbita_agent.gateway import DELETION_PHRASE


def _export():
    return [
        {
            "conversation_id": "conv_1",
            "title": "A conversation",
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
                        "content": {"content_type": "text", "parts": ["Distinctive phrase here."]},
                    },
                },
            },
        }
    ]


def _archive_bytes() -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("conversations.json", json.dumps(_export()))
        handle.writestr("data/rows.csv", "x,y\n1,2\n3,4\n5,6\n")
    return buffer.getvalue()


def _chunks(payload: bytes, size: int = 4096):
    for index in range(0, len(payload), size):
        yield payload[index : index + size]


def test_an_upload_lands_in_the_object_store(gateway):
    case_id = gateway.create_case(name="Archive", goal="")["id"]
    payload = _archive_bytes()

    record = gateway.receive_upload(
        case_id=case_id,
        filename="export.zip",
        chunks=_chunks(payload),
        max_bytes=10_000_000,
    )

    assert record["object"]["size_bytes"] == len(payload)
    assert record["object"]["backend"] == "local"
    assert gateway.objects.exists(record["object_key"]) is True
    assert record["artifact_kind"] == "archive"


def test_the_volume_copy_of_the_original_is_pruned(gateway):
    case_id = gateway.create_case(name="Archive", goal="")["id"]

    record = gateway.receive_upload(
        case_id=case_id,
        filename="export.zip",
        chunks=_chunks(_archive_bytes()),
        max_bytes=10_000_000,
    )

    assert record.get("volume_original_pruned") is True
    assert not Path(record["stored_path"]).exists()
    # The durable copy is still reachable.
    assert gateway.objects.exists(record["object_key"]) is True


def test_the_parsed_content_survives_the_pruning(gateway):
    """Pruning the original must not take the extracted artifacts with it."""
    case_id = gateway.create_case(name="Archive", goal="")["id"]

    gateway.receive_upload(
        case_id=case_id,
        filename="export.zip",
        chunks=_chunks(_archive_bytes()),
        max_bytes=10_000_000,
    )

    found = gateway.search_memory("distinctive phrase")
    assert found["hit_count"] == 1
    assert found["hits"][0]["receipt"]["conversation_id"] == "conv_1"
    assert gateway.case_context(case_id)["case"]["file_count"] == 1


def test_an_oversized_upload_is_refused_and_leaves_nothing(gateway):
    from orbita_agent.object_store import ObjectStoreError

    case_id = gateway.create_case(name="Too big", goal="")["id"]

    with pytest.raises(ObjectStoreError, match="exceeded"):
        gateway.receive_upload(
            case_id=case_id,
            filename="export.zip",
            chunks=_chunks(b"x" * 50_000),
            max_bytes=1_000,
        )

    assert gateway.objects.total_bytes("tenants") == 0
    assert gateway.case_context(case_id)["case"]["file_count"] == 0


def test_an_empty_upload_is_refused_and_leaves_nothing(gateway):
    case_id = gateway.create_case(name="Empty", goal="")["id"]

    with pytest.raises(ValueError, match="empty"):
        gateway.receive_upload(
            case_id=case_id, filename="export.zip", chunks=iter([]), max_bytes=1_000
        )

    assert gateway.objects.total_bytes("tenants") == 0


def test_an_unknown_case_is_refused_before_anything_is_stored(gateway):
    with pytest.raises(KeyError):
        gateway.receive_upload(
            case_id="case_nope",
            filename="export.zip",
            chunks=_chunks(_archive_bytes()),
            max_bytes=10_000_000,
        )

    assert gateway.objects.total_bytes("tenants") == 0


def test_a_failed_ingest_does_not_orphan_an_object(gateway, monkeypatch):
    """An object nothing references is an object nobody is accounting for."""
    case_id = gateway.create_case(name="Breaks", goal="")["id"]

    def explode(*args, **kwargs):
        raise RuntimeError("ingestion blew up")

    monkeypatch.setattr(gateway.service, "add_file", explode)

    with pytest.raises(RuntimeError, match="blew up"):
        gateway.receive_upload(
            case_id=case_id,
            filename="export.zip",
            chunks=_chunks(_archive_bytes()),
            max_bytes=10_000_000,
        )

    assert gateway.objects.total_bytes("tenants") == 0


def test_deleting_a_case_removes_the_stored_objects(gateway):
    case_id = gateway.create_case(name="Delete me", goal="")["id"]
    record = gateway.receive_upload(
        case_id=case_id,
        filename="export.zip",
        chunks=_chunks(_archive_bytes()),
        max_bytes=10_000_000,
    )
    assert gateway.objects.exists(record["object_key"]) is True

    result = gateway.delete_case(case_id, confirmation=DELETION_PHRASE)

    assert result["objects_removed"] >= 1
    assert gateway.objects.exists(record["object_key"]) is False
    assert gateway.objects.total_bytes("tenants") == 0


def test_usage_is_measurable_per_tenant(tmp_path, monkeypatch):
    from orbita_agent import AgentConfig, AgentGateway

    # Name alice as the operator, otherwise the archive policy fails closed and refuses
    # her upload — correct behaviour, but not what this test is about.
    monkeypatch.setenv("ORBITA_OPERATOR_TENANT", "alice")
    base = AgentConfig(home=tmp_path / "home")
    alice = AgentGateway(base.for_tenant("alice"))
    try:
        case_id = alice.create_case(name="Mine", goal="")["id"]
        alice.receive_upload(
            case_id=case_id,
            filename="export.zip",
            chunks=_chunks(_archive_bytes()),
            max_bytes=10_000_000,
        )
        assert alice.objects.total_bytes("tenants/alice") > 0
        assert alice.objects.total_bytes("tenants/bob") == 0
    finally:
        alice.close()
