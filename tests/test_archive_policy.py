"""A decision to wait must be enforced, because a decision to wait is easy to forget.

Per-tenant encryption is specified but unbuilt. Until it is reviewed, only the operator
may store a personal archive. These tests exist so that policy survives a tired week
before a deadline rather than depending on anyone remembering it.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from orbita_agent import AgentConfig, AgentGateway
from orbita_agent.archive_policy import (
    ENCRYPTION_READY_VAR,
    OPERATOR_TENANT_VAR,
    ArchiveIngestionRefused,
    ArchivePolicy,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (ENCRYPTION_READY_VAR, OPERATOR_TENANT_VAR, "ORBITA_DISCOVERY_GENOME_USERNAME"):
        monkeypatch.delenv(name, raising=False)


def _export():
    return [
        {
            "conversation_id": "c1",
            "title": "Private",
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
                        "content": {"content_type": "text", "parts": ["Something personal."]},
                    },
                },
            },
        }
    ]


# -- the policy itself --------------------------------------------------------------


def test_the_operator_may_ingest(monkeypatch):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    assert ArchivePolicy.from_env().allows("dkscr711") is True


def test_anyone_else_may_not(monkeypatch):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    assert ArchivePolicy.from_env().allows("someone-else") is False


def test_an_unconfigured_multi_tenant_deployment_fails_closed():
    """No operator named, encryption unreviewed: refuse everyone rather than guess."""
    policy = ArchivePolicy.from_env()
    assert policy.operator_tenant is None
    assert policy.allows("anybody") is False


def test_a_single_operator_deployment_is_unaffected():
    """No tenant means nobody else can reach it, so the caller is the operator."""
    assert ArchivePolicy.from_env().allows(None) is True


def test_the_operator_variable_falls_back_to_the_deployment_username(monkeypatch):
    monkeypatch.setenv("ORBITA_DISCOVERY_GENOME_USERNAME", "dkscr711")
    assert ArchivePolicy.from_env().allows("dkscr711") is True


def test_the_operator_match_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "DKSCR711")
    assert ArchivePolicy.from_env().allows("dkscr711") is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_review_opens_the_door_for_everyone(monkeypatch, value):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    monkeypatch.setenv(ENCRYPTION_READY_VAR, value)
    assert ArchivePolicy.from_env().allows("anyone-at-all") is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "soon", "almost"])
def test_anything_short_of_an_explicit_yes_keeps_the_door_shut(monkeypatch, value):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    monkeypatch.setenv(ENCRYPTION_READY_VAR, value)
    assert ArchivePolicy.from_env().allows("someone-else") is False


def test_the_refusal_explains_itself_and_points_somewhere(monkeypatch):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    message = ArchivePolicy.from_env().refusal("someone-else")

    assert "not implemented and reviewed" in message
    assert "DATA_STATEMENT" in message
    assert ENCRYPTION_READY_VAR in message


def test_ensure_raises_for_a_refused_tenant(monkeypatch):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    with pytest.raises(ArchiveIngestionRefused):
        ArchivePolicy.from_env().ensure("someone-else")


def test_describe_states_the_current_stance(monkeypatch):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    described = ArchivePolicy.from_env().describe()

    assert described["accepting_archives_from"] == "the operator only"
    assert described["encryption_reviewed"] is False
    assert described["reason"]


# -- enforcement at the ingestion path ------------------------------------------------


def test_a_refused_tenant_cannot_index_a_chat_export(tmp_path, monkeypatch):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    base = AgentConfig(home=tmp_path / "home")
    stranger = AgentGateway(base.for_tenant("stranger"))
    try:
        archive = tmp_path / "export.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("conversations.json", json.dumps(_export()))

        case_id = stranger.create_case(name="Their history", goal="")["id"]
        with pytest.raises(ArchiveIngestionRefused):
            stranger.ingest_upload(case_id=case_id, path=archive)

        # And nothing of theirs became searchable.
        assert stranger.memory_status()["messages"] == 0
    finally:
        stranger.close()


def test_the_operator_can_still_ingest_their_own(tmp_path, monkeypatch):
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    base = AgentConfig(home=tmp_path / "home")
    operator = AgentGateway(base.for_tenant("dkscr711"))
    try:
        archive = tmp_path / "export.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("conversations.json", json.dumps(_export()))

        case_id = operator.create_case(name="My history", goal="")["id"]
        record = operator.ingest_upload(case_id=case_id, path=archive)

        assert record["memory"]["messages_indexed"] == 1
        assert operator.search_memory("personal")["hit_count"] == 1
    finally:
        operator.close()


def test_non_archive_uploads_are_unaffected(tmp_path, monkeypatch):
    """The policy is about personal archives, not about ordinary research data."""
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    base = AgentConfig(home=tmp_path / "home")
    stranger = AgentGateway(base.for_tenant("stranger"))
    try:
        case_id = stranger.create_case(name="Ordinary research", goal="")["id"]
        record = stranger.add_inline_file(
            case_id=case_id, filename="rows.csv", content="x,y\n1,2\n3,4\n5,6\n"
        )
        assert record["artifact_kind"] == "table"
    finally:
        stranger.close()


def test_a_small_export_through_the_inline_path_is_also_refused(tmp_path, monkeypatch):
    """A policy enforced at one entrance is not enforced."""
    monkeypatch.setenv(OPERATOR_TENANT_VAR, "dkscr711")
    base = AgentConfig(home=tmp_path / "home")
    stranger = AgentGateway(base.for_tenant("stranger"))
    try:
        case_id = stranger.create_case(name="Sneaky", goal="")["id"]
        with pytest.raises(ArchiveIngestionRefused):
            stranger.add_inline_file(
                case_id=case_id,
                filename="conversations.json",
                content=json.dumps(_export()),
            )
    finally:
        stranger.close()
