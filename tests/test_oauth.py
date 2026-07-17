from __future__ import annotations

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlparse

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request

from orbita_agent.oauth import ORBITA_SCOPE, GitHubOAuthProvider


def _provider(tmp_path):
    return GitHubOAuthProvider(
        database_path=tmp_path / "oauth.db",
        public_url="https://orbita.example.test",
        github_client_id="github-client-id",
        github_client_secret="github-client-secret",
        allowed_github_users=["DerekEarnhart"],
        access_token_ttl=600,
        refresh_token_ttl=3600,
    )


def _client():
    return OAuthClientInformationFull(
        client_id="chatgpt-client",
        client_secret="chatgpt-secret",
        redirect_uris=["https://chatgpt.example.test/oauth/callback"],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=ORBITA_SCOPE,
    )


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _callback_request(*, code: str, state: str) -> Request:
    query = urlencode({"code": code, "state": state}).encode()
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/oauth/github/callback",
            "raw_path": b"/oauth/github/callback",
            "query_string": query,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("orbita.example.test", 443),
        }
    )


def test_oauth_authorization_code_refresh_and_revoke(tmp_path, monkeypatch):
    async def scenario():
        provider = _provider(tmp_path)
        client = _client()
        await provider.register_client(client)
        assert (await provider.get_client("chatgpt-client")).client_name == client.client_name

        verifier = "v" * 64
        github_url = await provider.authorize(
            client,
            AuthorizationParams(
                state="chatgpt-state",
                scopes=[ORBITA_SCOPE],
                code_challenge=_challenge(verifier),
                redirect_uri="https://chatgpt.example.test/oauth/callback",
                redirect_uri_provided_explicitly=True,
                resource="https://orbita.example.test/mcp",
            ),
        )
        github_state = parse_qs(urlparse(github_url).query)["state"][0]

        async def allowed_user(_code):
            return {"login": "DerekEarnhart", "id": 1234}

        monkeypatch.setattr(provider, "_fetch_github_user", allowed_user)
        callback = await provider.github_callback(_callback_request(code="github-code", state=github_state))
        callback_query = parse_qs(urlparse(callback.headers["location"]).query)
        assert callback_query["state"] == ["chatgpt-state"]
        raw_code = callback_query["code"][0]

        authorization_code = await provider.load_authorization_code(client, raw_code)
        assert authorization_code is not None
        assert authorization_code.resource == "https://orbita.example.test/mcp"
        tokens = await provider.exchange_authorization_code(client, authorization_code)
        assert await provider.load_authorization_code(client, raw_code) is None

        access = await provider.load_access_token(tokens.access_token)
        assert access is not None
        assert access.subject == "github:1234"
        assert access.resource == "https://orbita.example.test/mcp"
        assert access.scopes == [ORBITA_SCOPE]

        refresh = await provider.load_refresh_token(client, tokens.refresh_token)
        assert refresh is not None
        rotated = await provider.exchange_refresh_token(client, refresh, [ORBITA_SCOPE])
        assert await provider.load_access_token(tokens.access_token) is None
        assert await provider.load_refresh_token(client, tokens.refresh_token) is None
        rotated_access = await provider.load_access_token(rotated.access_token)
        assert rotated_access is not None

        await provider.revoke_token(rotated_access)
        assert await provider.load_access_token(rotated.access_token) is None
        assert await provider.load_refresh_token(client, rotated.refresh_token) is None

    asyncio.run(scenario())


def test_oauth_rejects_unlisted_github_user(tmp_path, monkeypatch):
    async def scenario():
        provider = _provider(tmp_path)
        client = _client()
        await provider.register_client(client)
        github_url = await provider.authorize(
            client,
            AuthorizationParams(
                state="original-state",
                scopes=[ORBITA_SCOPE],
                code_challenge="challenge",
                redirect_uri="https://chatgpt.example.test/oauth/callback",
                redirect_uri_provided_explicitly=True,
                resource=None,
            ),
        )
        github_state = parse_qs(urlparse(github_url).query)["state"][0]

        async def other_user(_code):
            return {"login": "not-allowed", "id": 9876}

        monkeypatch.setattr(provider, "_fetch_github_user", other_user)
        callback = await provider.github_callback(_callback_request(code="github-code", state=github_state))
        callback_query = parse_qs(urlparse(callback.headers["location"]).query)
        assert callback_query["error"] == ["access_denied"]
        assert callback_query["state"] == ["original-state"]

    asyncio.run(scenario())
