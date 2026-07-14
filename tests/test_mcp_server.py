from __future__ import annotations

import asyncio

import pytest

from orbita_agent.mcp_server import StaticBearerTokenVerifier, build_mcp_server


def test_mcp_surface_has_governed_tool_annotations(gateway):
    mcp, same_gateway = build_mcp_server(gateway=gateway)
    assert same_gateway is gateway
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    expected = {
        "orbita_capabilities",
        "orbita_create_case",
        "orbita_add_inline_file",
        "orbita_compile_plan",
        "orbita_approve_plan",
        "orbita_run_discovery",
        "orbita_claim_history",
        "orbita_search_knowledge",
        "orbita_analyze_graph",
        "orbita_export_lean_witness",
    }
    assert expected <= tools.keys()
    assert tools["orbita_capabilities"].annotations.readOnlyHint is True
    assert tools["orbita_approve_plan"].annotations.destructiveHint is True
    assert tools["orbita_run_discovery"].annotations.destructiveHint is True
    assert tools["orbita_approve_plan"].description


def test_mcp_schemas_are_machine_usable(gateway):
    mcp, _ = build_mcp_server(gateway=gateway)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    approval = tools["orbita_approve_plan"].parameters
    assert set(approval["required"]) == {"plan_id", "expected_plan_hash", "reviewer", "confirmation"}
    graph = tools["orbita_analyze_graph"].parameters
    assert graph["properties"]["edges"]["type"] == "array"


def test_static_bearer_token_verifier():
    token = "a" * 48
    verifier = StaticBearerTokenVerifier(token)
    assert asyncio.run(verifier.verify_token("not-the-token")) is None
    access = asyncio.run(verifier.verify_token(token))
    assert access is not None
    assert access.scopes == ["orbita:use"]


def test_remote_auth_is_required_when_configured(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.delenv("ORBITA_AGENT_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="API_TOKEN is missing"):
        build_mcp_server(gateway=gateway)


def test_remote_auth_and_health_route_are_configured(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_API_TOKEN", "b" * 48)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "orbita.example.test")
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    assert mcp.settings.auth is not None
    assert str(mcp.settings.auth.resource_server_url) == "https://orbita.example.test/mcp"
    assert mcp._token_verifier is not None
    assert any(route.path == "/health" for route in mcp._custom_starlette_routes)
