from __future__ import annotations

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

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
        "orbita_improvement_status",
        "orbita_suggest_improvement",
        "orbita_evaluate_improvement",
        "orbita_promote_improvement",
        "orbita_rollback_improvement",
    }
    assert expected <= tools.keys()
    assert tools["orbita_capabilities"].annotations.readOnlyHint is True
    assert tools["orbita_approve_plan"].annotations.destructiveHint is True
    assert tools["orbita_run_discovery"].annotations.destructiveHint is True
    assert tools["orbita_promote_improvement"].annotations.destructiveHint is True
    assert tools["orbita_rollback_improvement"].annotations.destructiveHint is True
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
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "bearer")
    monkeypatch.delenv("ORBITA_AGENT_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Bearer mode is enabled"):
        build_mcp_server(gateway=gateway)


def test_remote_auth_and_health_route_are_configured(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "bearer")
    monkeypatch.setenv("ORBITA_AGENT_API_TOKEN", "b" * 48)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "orbita.example.test")
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    assert mcp.settings.auth is not None
    assert str(mcp.settings.auth.resource_server_url) == "https://orbita.example.test/mcp"
    assert mcp._token_verifier is not None
    assert any(route.path == "/health" for route in mcp._custom_starlette_routes)


def test_github_oauth_mode_is_configured(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "oauth-github")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "DerekEarnhart")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "orbita.example.test")
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    assert mcp.settings.auth is not None
    assert mcp.settings.auth.client_registration_options.enabled is True
    assert mcp.settings.auth.revocation_options.enabled is True
    assert mcp._auth_server_provider is not None
    assert str(mcp.settings.auth.resource_server_url) == "https://orbita.example.test/mcp"
    assert any(route.path == "/oauth/github/callback" for route in mcp._custom_starlette_routes)


def test_github_oauth_discovery_registration_and_challenge(gateway, monkeypatch):
    monkeypatch.setenv("ORBITA_AGENT_REQUIRE_AUTH", "1")
    monkeypatch.setenv("ORBITA_AGENT_AUTH_MODE", "oauth-github")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("ORBITA_OAUTH_GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setenv("ORBITA_OAUTH_ALLOWED_GITHUB_USERS", "DerekEarnhart")
    monkeypatch.setenv("ORBITA_AGENT_PUBLIC_URL", "https://orbita.example.test")
    mcp, _ = build_mcp_server(gateway=gateway, host="0.0.0.0", port=8000)
    with TestClient(mcp.streamable_http_app()) as client:
        authorization_metadata = client.get("/.well-known/oauth-authorization-server")
        assert authorization_metadata.status_code == 200
        assert authorization_metadata.json()["authorization_endpoint"] == "https://orbita.example.test/authorize"
        assert authorization_metadata.json()["registration_endpoint"] == "https://orbita.example.test/register"
        assert authorization_metadata.json()["code_challenge_methods_supported"] == ["S256"]

        resource_metadata = client.get("/.well-known/oauth-protected-resource/mcp")
        assert resource_metadata.status_code == 200
        assert resource_metadata.json()["resource"] == "https://orbita.example.test/mcp"
        assert resource_metadata.json()["scopes_supported"] == ["orbita:use"]

        registration = client.post(
            "/register",
            json={
                "redirect_uris": ["https://chatgpt.example.test/oauth/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "orbita:use",
                "client_name": "ChatGPT",
            },
        )
        assert registration.status_code == 201
        assert registration.json()["client_id"]
        assert registration.json().get("client_secret") is None

        oauth_provider = mcp._auth_server_provider

        async def allowed_github_user(_code):
            return {"login": "DerekEarnhart", "id": 1234}

        monkeypatch.setattr(oauth_provider, "_fetch_github_user", allowed_github_user)
        code_verifier = "test-verifier-" * 5
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        authorization = client.get(
            "/authorize",
            params={
                "client_id": registration.json()["client_id"],
                "redirect_uri": "https://chatgpt.example.test/oauth/callback",
                "response_type": "code",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": "chatgpt-state",
                "scope": "orbita:use",
                "resource": "https://orbita.example.test/mcp",
            },
            follow_redirects=False,
        )
        assert authorization.status_code == 302
        github_state = parse_qs(urlparse(authorization.headers["location"]).query)["state"][0]
        callback = client.get(
            "/oauth/github/callback",
            params={"code": "github-code", "state": github_state},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        callback_query = parse_qs(urlparse(callback.headers["location"]).query)
        assert callback_query["state"] == ["chatgpt-state"]
        token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": callback_query["code"][0],
                "redirect_uri": "https://chatgpt.example.test/oauth/callback",
                "client_id": registration.json()["client_id"],
                "code_verifier": code_verifier,
                "resource": "https://orbita.example.test/mcp",
            },
        )
        assert token.status_code == 200
        assert token.json()["token_type"] == "Bearer"
        assert token.json()["scope"] == "orbita:use"
        assert token.json()["refresh_token"]

        challenge = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert challenge.status_code == 401
        assert "oauth-protected-resource/mcp" in challenge.headers["www-authenticate"]
