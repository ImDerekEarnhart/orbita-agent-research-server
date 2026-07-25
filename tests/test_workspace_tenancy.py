"""The research workspace must be isolated per caller, not merely filtered per caller.

These tests are the gate on opening ORBITA_OAUTH_ALLOWED_GITHUB_USERS to anyone but
the operator. Until they pass, a second sign-in is a second reader of the first
user's research. They deliberately test the strongest property available: a case id
belonging to another tenant is absent from the database being queried, so forgetting
a predicate somewhere cannot turn into a disclosure.
"""

from __future__ import annotations

import json

import pytest

from orbita_agent import AgentConfig, AgentGateway
from orbita_agent.config import tenant_slug
from orbita_agent.mcp_server import build_mcp_server
from orbita_agent.tenancy import TenantResolutionError

DEREK = "github:263305214"
STRANGER = "github:999999999"


class _FakeToken:
    """Stands in for the access token the auth middleware puts in request context."""

    def __init__(self, subject: str) -> None:
        self.subject = subject


def _oauth_env(monkeypatch, allowed_users: str) -> None:
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "oauth-github")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", allowed_users)
    monkeypatch.setenv("ORBITA_AGENT_PUBLIC_URL", "https://orbita.example.test")
    monkeypatch.delenv("ORBITA_DISCOVERY_GENOME_URL", raising=False)
    monkeypatch.delenv("ORBITA_DISCOVERY_GENOME_USERNAME", raising=False)


def _tool(mcp, name: str):
    tool = mcp._tool_manager._tools[name]
    return tool.fn


@pytest.fixture
def two_tenants(gateway, monkeypatch):
    """A server where two distinct GitHub identities are each bound to their own tenant."""
    _oauth_env(monkeypatch, "DerekEarnhart,Stranger")
    monkeypatch.setenv(
        "ORBITA_GENOME_TENANT_BINDINGS",
        json.dumps({DEREK: "dkscr711", STRANGER: "stranger-tenant"}),
    )
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)

    def act_as(subject: str | None):
        monkeypatch.setattr(
            "orbita_agent.mcp_server.get_access_token",
            lambda: _FakeToken(subject) if subject else None,
        )

    return mcp, act_as


def test_two_tenants_never_share_a_database_or_workspace(tmp_path):
    base = AgentConfig(home=tmp_path / "home")
    alice = base.for_tenant("alice")
    bob = base.for_tenant("bob")

    assert alice.db_path != bob.db_path
    assert alice.workspace != bob.workspace
    assert base.home not in (alice.home, bob.home)
    # Neither tenant's state may sit inside the other's directory tree.
    assert alice.home not in bob.home.parents
    assert bob.home not in alice.home.parents


def test_tenant_directories_stay_distinct_when_names_collide(tmp_path):
    """Two names that reduce to the same readable stem must not share a directory."""
    base = AgentConfig(home=tmp_path / "home")
    assert tenant_slug("Ada Lovelace") != tenant_slug("ada-lovelace")
    assert base.for_tenant("Ada Lovelace").home != base.for_tenant("ada-lovelace").home


@pytest.mark.parametrize("hostile", ["../escape", "..", "a/../../b", "C:\\Windows", "./."])
def test_a_hostile_tenant_name_cannot_escape_the_tenants_directory(tmp_path, hostile):
    base = AgentConfig(home=tmp_path / "home")
    resolved = base.for_tenant(hostile).home.resolve()
    assert (tmp_path / "home" / "tenants").resolve() in resolved.parents


def test_a_case_id_from_another_tenant_is_simply_absent(two_tenants):
    mcp, act_as = two_tenants

    act_as(DEREK)
    case = _tool(mcp, "orbita_create_case")(name="Golden Gate", goal="")
    case_id = case["id"]
    assert _tool(mcp, "orbita_case_context")(case_id)["case"]["id"] == case_id

    # The stranger holds a valid token and the exact case id. That must not be enough.
    act_as(STRANGER)
    with pytest.raises(KeyError):
        _tool(mcp, "orbita_case_context")(case_id)


def test_listing_cases_never_reveals_another_tenants_research(two_tenants):
    mcp, act_as = two_tenants

    act_as(DEREK)
    _tool(mcp, "orbita_create_case")(name="Operation Golden Gate", goal="")

    act_as(STRANGER)
    assert _tool(mcp, "orbita_list_cases")() == []

    _tool(mcp, "orbita_create_case")(name="Stranger's own case", goal="")
    stranger_names = {case["name"] for case in _tool(mcp, "orbita_list_cases")()}
    assert stranger_names == {"Stranger's own case"}

    act_as(DEREK)
    derek_names = {case["name"] for case in _tool(mcp, "orbita_list_cases")()}
    assert derek_names == {"Operation Golden Gate"}


def test_another_tenants_case_cannot_be_written_to(two_tenants):
    mcp, act_as = two_tenants

    act_as(DEREK)
    case_id = _tool(mcp, "orbita_create_case")(name="Private", goal="")["id"]

    act_as(STRANGER)
    with pytest.raises(KeyError):
        _tool(mcp, "orbita_add_inline_file")(
            case_id=case_id, filename="inject.csv", content="a,b\n1,2\n"
        )
    with pytest.raises(KeyError):
        _tool(mcp, "orbita_compile_plan")(case_id)

    # And the owner's case is untouched by the attempt.
    act_as(DEREK)
    assert _tool(mcp, "orbita_case_context")(case_id)["case"]["file_count"] == 0


def test_operator_evidence_cannot_confirm_another_tenants_case(two_tenants):
    """The existence check before attaching Genome evidence is itself a disclosure path."""
    mcp, act_as = two_tenants

    act_as(DEREK)
    case_id = _tool(mcp, "orbita_create_case")(name="Private", goal="")["id"]

    act_as(STRANGER)
    with pytest.raises(Exception) as excinfo:
        _tool(mcp, "orbita_genome_add_evidence")(
            operator_id="op_1",
            case_id=case_id,
            domain="anything",
            outcome="supported",
            independence_level="same_case",
        )
    # The failure must not be a Genome-side error, which would imply the case resolved.
    assert isinstance(excinfo.value, KeyError)


def test_an_unbound_subject_is_refused_rather_than_served(gateway, monkeypatch):
    _oauth_env(monkeypatch, "DerekEarnhart,Stranger")
    monkeypatch.setenv("ORBITA_GENOME_TENANT_BINDINGS", json.dumps({DEREK: "dkscr711"}))
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)

    monkeypatch.setattr(
        "orbita_agent.mcp_server.get_access_token", lambda: _FakeToken("github:000000")
    )
    with pytest.raises(TenantResolutionError):
        _tool(mcp, "orbita_list_cases")()


def test_an_unauthenticated_request_is_refused(gateway, monkeypatch):
    _oauth_env(monkeypatch, "DerekEarnhart")
    monkeypatch.setenv("ORBITA_GENOME_TENANT_BINDINGS", json.dumps({DEREK: "dkscr711"}))
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)

    monkeypatch.setattr("orbita_agent.mcp_server.get_access_token", lambda: None)
    with pytest.raises(TenantResolutionError):
        _tool(mcp, "orbita_list_cases")()


def test_single_operator_deployments_keep_the_shared_workspace(gateway, monkeypatch):
    """Bearer and unauthenticated modes have one operator and must not change behaviour."""
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "bearer")
    monkeypatch.setenv("ORBITA_AGENT_API_TOKEN", "b" * 48)
    monkeypatch.delenv("ORBITA_DISCOVERY_GENOME_URL", raising=False)
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)

    case_id = _tool(mcp, "orbita_create_case")(name="Operator case", goal="")["id"]
    assert _tool(mcp, "orbita_case_context")(case_id)["case"]["id"] == case_id
    assert [case["name"] for case in _tool(mcp, "orbita_list_cases")()] == ["Operator case"]
    # It lands in the base workspace, exactly as before this change.
    assert gateway.config.db_path.exists()


def test_closing_the_base_gateway_closes_every_tenant_gateway(tmp_path):
    base = AgentGateway(AgentConfig(home=tmp_path / "home"))
    child = base.for_tenant("alice")
    child.create_case(name="Alice case", goal="")
    base.close()

    with pytest.raises(Exception):
        child.list_cases()
