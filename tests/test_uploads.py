"""The upload route has no session of its own, so the ticket has to carry everything."""

from __future__ import annotations

import json
import time
import zipfile

import pytest
from starlette.testclient import TestClient

from orbita_agent.mcp_server import build_mcp_server
from orbita_agent.uploads import (
    DEFAULT_MAX_UPLOAD_BYTES,
    UploadError,
    UploadTicketStore,
    safe_upload_filename,
)

DEREK = "github:263305214"
STRANGER = "github:999999999"


class _FakeToken:
    def __init__(self, subject: str) -> None:
        self.subject = subject


@pytest.fixture
def store(tmp_path) -> UploadTicketStore:
    return UploadTicketStore(tmp_path / "uploads.db")


# -- the ticket itself -------------------------------------------------------------


def test_a_ticket_works_exactly_once(store):
    ticket, _ = store.mint(tenant="alice", case_id="case_1", filename="a.csv", declared_bytes=100)

    claimed = store.claim(ticket)
    assert claimed.case_id == "case_1"

    with pytest.raises(UploadError, match="already used"):
        store.claim(ticket)


def test_an_expired_ticket_is_refused(store):
    ticket, _ = store.mint(
        tenant="alice", case_id="case_1", filename="a.csv", declared_bytes=100, ttl_seconds=-1
    )
    with pytest.raises(UploadError):
        store.claim(ticket)


def test_an_unknown_ticket_is_refused(store):
    with pytest.raises(UploadError):
        store.claim("not-a-real-ticket")


def test_only_the_hash_is_stored(store, tmp_path):
    import sqlite3

    ticket, record = store.mint(
        tenant="alice", case_id="case_1", filename="a.csv", declared_bytes=100
    )

    # Scan the database and its write-ahead log: reading either must not yield
    # anything a caller could present as a ticket.
    for path in tmp_path.glob("uploads.db*"):
        assert ticket.encode() not in path.read_bytes(), f"raw ticket found in {path.name}"

    connection = sqlite3.connect(tmp_path / "uploads.db")
    try:
        stored = connection.execute("SELECT ticket_hash FROM upload_tickets").fetchall()
    finally:
        connection.close()
    assert stored == [(record.ticket_hash,)]
    assert record.ticket_hash != ticket


def test_the_ticket_is_bound_to_one_case_and_filename(store):
    _, record = store.mint(
        tenant="alice", case_id="case_1", filename="../../escape.csv", declared_bytes=100
    )
    assert record.case_id == "case_1"
    assert record.filename == "escape.csv"


@pytest.mark.parametrize("name", ["payload.exe", "script.sh", "noextension", "a.dll"])
def test_unaccepted_upload_types_are_refused(name):
    with pytest.raises(UploadError, match="not accepted"):
        safe_upload_filename(name)


@pytest.mark.parametrize("name", ["export.zip", "rows.csv", "notes.md", "book.pdf", "data.parquet"])
def test_accepted_upload_types(name):
    assert safe_upload_filename(name) == name


def test_a_declared_size_over_the_ceiling_is_refused(store):
    with pytest.raises(UploadError, match="exceeds"):
        store.mint(
            tenant="alice",
            case_id="case_1",
            filename="big.zip",
            declared_bytes=DEFAULT_MAX_UPLOAD_BYTES + 1,
        )


def test_an_upload_that_would_fill_the_volume_is_refused(store, tmp_path, monkeypatch):
    """Pinned to a fake volume, so the result does not depend on the test machine."""
    import collections

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "orbita_agent.uploads.shutil.disk_usage",
        lambda path: usage(total=4_600_000_000, used=4_500_000_000, free=100_000_000),
    )
    with pytest.raises(UploadError, match="headroom"):
        store.mint(
            tenant="alice",
            case_id="case_1",
            filename="big.zip",
            declared_bytes=50_000_000,
            volume_path=tmp_path,
        )


def test_an_upload_that_fits_with_headroom_is_allowed(store, tmp_path, monkeypatch):
    import collections

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "orbita_agent.uploads.shutil.disk_usage",
        lambda path: usage(total=4_600_000_000, used=100_000_000, free=4_500_000_000),
    )
    _, record = store.mint(
        tenant="alice",
        case_id="case_1",
        filename="big.zip",
        declared_bytes=50_000_000,
        volume_path=tmp_path,
    )
    assert record.case_id == "case_1"


def test_expired_tickets_can_be_purged(store):
    store.mint(
        tenant="alice", case_id="c", filename="a.csv", declared_bytes=10, ttl_seconds=-100_000
    )
    assert store.purge_expired(older_than_seconds=0) == 1


# -- the route end to end ----------------------------------------------------------


@pytest.fixture
def server(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "oauth-github")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "DerekEarnhart,Stranger")
    monkeypatch.setenv("ORBITA_AGENT_PUBLIC_URL", "https://orbita.example.test")
    monkeypatch.setenv(
        "ORBITA_GENOME_TENANT_BINDINGS",
        json.dumps({DEREK: "dkscr711", STRANGER: "stranger-tenant"}),
    )
    monkeypatch.delenv("ORBITA_DISCOVERY_GENOME_URL", raising=False)
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)

    def act_as(subject: str):
        monkeypatch.setattr(
            "orbita_agent.mcp_server.get_access_token", lambda: _FakeToken(subject)
        )

    def tool(name: str):
        return mcp._tool_manager._tools[name].fn

    return mcp, act_as, tool


def _ticket_path(url: str) -> str:
    return "/uploads/" + url.rsplit("/", 1)[1]


def test_a_large_zip_uploads_and_is_parsed(server):
    mcp, act_as, tool = server
    act_as(DEREK)
    case_id = tool("orbita_create_case")(name="Archive case", goal="")["id"]

    payload = b""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data/rows.csv", "x,y\n1,2\n3,4\n5,6\n")
        archive.writestr("notes.md", "# hello\n")
    payload = buffer.getvalue()

    minted = tool("orbita_request_upload")(
        case_id=case_id, filename="export.zip", size_bytes=len(payload)
    )
    assert minted["upload_url"].startswith("https://orbita.example.test/uploads/")
    assert minted["single_use"] is True

    with TestClient(mcp.streamable_http_app()) as client:
        response = client.post(_ticket_path(minted["upload_url"]), content=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["bytes_received"] == len(payload)
    assert body["file"]["artifact_kind"] == "archive"
    assert body["file"]["profile"]["parsed_member_count"] == 2

    act_as(DEREK)
    assert tool("orbita_case_context")(case_id)["case"]["file_count"] == 1


def test_the_same_url_cannot_be_used_twice(server):
    mcp, act_as, tool = server
    act_as(DEREK)
    case_id = tool("orbita_create_case")(name="Once only", goal="")["id"]
    minted = tool("orbita_request_upload")(
        case_id=case_id, filename="rows.csv", size_bytes=20
    )

    with TestClient(mcp.streamable_http_app()) as client:
        first = client.post(_ticket_path(minted["upload_url"]), content=b"x,y\n1,2\n3,4\n5,6\n")
        second = client.post(_ticket_path(minted["upload_url"]), content=b"x,y\n1,2\n3,4\n5,6\n")

    assert first.status_code == 200
    assert second.status_code == 403


def test_an_upload_over_its_ceiling_is_cut_off(server):
    mcp, act_as, tool = server
    act_as(DEREK)
    case_id = tool("orbita_create_case")(name="Too big", goal="")["id"]
    minted = tool("orbita_request_upload")(
        case_id=case_id, filename="rows.csv", size_bytes=100
    )

    with TestClient(mcp.streamable_http_app()) as client:
        response = client.post(_ticket_path(minted["upload_url"]), content=b"a" * 100_000)

    assert response.status_code == 413
    act_as(DEREK)
    # The oversized body must not have landed in the case.
    assert tool("orbita_case_context")(case_id)["case"]["file_count"] == 0


def test_a_ticket_cannot_be_minted_for_another_tenants_case(server):
    mcp, act_as, tool = server
    act_as(DEREK)
    case_id = tool("orbita_create_case")(name="Derek private", goal="")["id"]

    act_as(STRANGER)
    with pytest.raises(KeyError):
        tool("orbita_request_upload")(case_id=case_id, filename="rows.csv", size_bytes=20)


def test_a_garbage_ticket_gets_the_same_answer_as_a_spent_one(server):
    mcp, act_as, tool = server
    with TestClient(mcp.streamable_http_app()) as client:
        response = client.post("/uploads/completely-made-up", content=b"x")
    assert response.status_code == 403


def test_an_empty_upload_is_refused(server):
    mcp, act_as, tool = server
    act_as(DEREK)
    case_id = tool("orbita_create_case")(name="Empty", goal="")["id"]
    minted = tool("orbita_request_upload")(case_id=case_id, filename="rows.csv", size_bytes=20)

    with TestClient(mcp.streamable_http_app()) as client:
        response = client.post(_ticket_path(minted["upload_url"]), content=b"")

    assert response.status_code == 413
