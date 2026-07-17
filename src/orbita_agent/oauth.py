from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlencode

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

ORBITA_SCOPE = "orbita:use"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(48)


def _scopes_json(scopes: Iterable[str]) -> str:
    return json.dumps(sorted(set(scopes)), separators=(",", ":"))


def _safe_error_page(title: str, message: str, status_code: int = 400) -> HTMLResponse:
    # These strings are controlled by Orbita. Do not insert upstream error text into this page.
    body = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="font-family:system-ui,sans-serif;max-width:42rem;margin:4rem auto;padding:0 1.25rem">
<h1>{title}</h1><p>{message}</p><p>You may close this window.</p>
</body></html>"""
    return HTMLResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})


class GitHubOAuthProvider:
    """OAuth 2.1 provider for MCP, with GitHub as the operator identity source.

    ChatGPT dynamically registers an OAuth client with Orbita and uses authorization-code
    flow with PKCE. Orbita delegates the interactive sign-in to GitHub, restricts access
    to an explicit username allowlist, and issues its own short-lived opaque tokens.
    Only token hashes are persisted.
    """

    def __init__(
        self,
        *,
        database_path: Path,
        public_url: str,
        github_client_id: str,
        github_client_secret: str,
        allowed_github_users: Iterable[str],
        access_token_ttl: int = 3600,
        refresh_token_ttl: int = 30 * 24 * 3600,
    ) -> None:
        self.database_path = database_path
        self.public_url = public_url.rstrip("/")
        self.resource_url = f"{self.public_url}/mcp"
        self.github_client_id = github_client_id
        self._github_client_secret = github_client_secret
        self.allowed_github_users = {user.strip().casefold() for user in allowed_github_users if user.strip()}
        self.access_token_ttl = access_token_ttl
        self.refresh_token_ttl = refresh_token_ttl
        if not self.github_client_id or not self._github_client_secret:
            raise ValueError("GitHub OAuth client ID and secret are required")
        if not self.allowed_github_users:
            raise ValueError("At least one allowed GitHub username is required")
        if not 300 <= self.access_token_ttl <= 24 * 3600:
            raise ValueError("OAuth access-token TTL must be between 300 and 86400 seconds")
        if not 3600 <= self.refresh_token_ttl <= 90 * 24 * 3600:
            raise ValueError("OAuth refresh-token TTL must be between 3600 and 7776000 seconds")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_pending (
                    state_hash TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    client_state TEXT,
                    scopes_json TEXT NOT NULL,
                    code_challenge TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    redirect_uri_provided INTEGER NOT NULL,
                    resource TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY(client_id) REFERENCES oauth_clients(client_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS oauth_codes (
                    code_hash TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    code_challenge TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    redirect_uri_provided INTEGER NOT NULL,
                    resource TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    FOREIGN KEY(client_id) REFERENCES oauth_clients(client_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS oauth_access_tokens (
                    token_hash TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    resource TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    grant_id TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(client_id) REFERENCES oauth_clients(client_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
                    token_hash TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    grant_id TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(client_id) REFERENCES oauth_clients(client_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS oauth_access_grant_idx ON oauth_access_tokens(grant_id);
                CREATE INDEX IF NOT EXISTS oauth_refresh_grant_idx ON oauth_refresh_tokens(grant_id);
                """
            )

    def _prune(self, connection: sqlite3.Connection, now: int) -> None:
        connection.execute("DELETE FROM oauth_pending WHERE expires_at < ?", (now,))
        connection.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,))
        connection.execute("DELETE FROM oauth_access_tokens WHERE expires_at < ? OR revoked = 1", (now,))
        connection.execute("DELETE FROM oauth_refresh_tokens WHERE expires_at < ? OR revoked = 1", (now,))

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM oauth_clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row["metadata_json"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise RegistrationError("invalid_client_metadata", "client_id is required")
        for redirect_uri in client_info.redirect_uris or []:
            scheme = redirect_uri.scheme.casefold()
            host = (redirect_uri.host or "").casefold()
            if redirect_uri.fragment:
                raise RegistrationError("invalid_redirect_uri", "redirect URIs must not include fragments")
            if scheme != "https" and not (scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}):
                raise RegistrationError(
                    "invalid_redirect_uri", "redirect URIs must use HTTPS, except for local loopback clients"
                )
        metadata = client_info.model_dump_json(exclude_none=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO oauth_clients(client_id, metadata_json, created_at) VALUES (?, ?, ?)",
                    (client_info.client_id, metadata, int(time.time())),
                )
        except sqlite3.IntegrityError as exc:
            raise RegistrationError("invalid_client_metadata", "client_id is already registered") from exc

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not client.client_id:
            raise AuthorizeError("invalid_request", "registered client has no client_id")
        resource = (params.resource or self.resource_url).rstrip("/")
        if resource != self.resource_url:
            raise AuthorizeError("invalid_request", "resource does not identify this Orbita MCP server")
        scopes = params.scopes or [ORBITA_SCOPE]
        if ORBITA_SCOPE not in scopes:
            raise AuthorizeError("invalid_scope", f"required scope is {ORBITA_SCOPE}")

        github_state = _new_token()
        now = int(time.time())
        with self._connect() as connection:
            self._prune(connection, now)
            connection.execute(
                """
                INSERT INTO oauth_pending(
                    state_hash, client_id, client_state, scopes_json, code_challenge,
                    redirect_uri, redirect_uri_provided, resource, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _token_hash(github_state),
                    client.client_id,
                    params.state,
                    _scopes_json(scopes),
                    params.code_challenge,
                    str(params.redirect_uri),
                    int(params.redirect_uri_provided_explicitly),
                    resource,
                    now + 600,
                ),
            )
        query = urlencode(
            {
                "client_id": self.github_client_id,
                "redirect_uri": f"{self.public_url}/oauth/github/callback",
                "scope": "read:user",
                "state": github_state,
                "allow_signup": "false",
            }
        )
        return f"{GITHUB_AUTHORIZE_URL}?{query}"

    async def github_callback(self, request: Request) -> Response:
        raw_state = request.query_params.get("state", "")
        if not raw_state:
            return _safe_error_page("Orbita sign-in failed", "The authorization state was missing.")

        now = int(time.time())
        with self._connect() as connection:
            self._prune(connection, now)
            pending = connection.execute(
                "SELECT * FROM oauth_pending WHERE state_hash = ?", (_token_hash(raw_state),)
            ).fetchone()
            if pending is not None:
                connection.execute("DELETE FROM oauth_pending WHERE state_hash = ?", (_token_hash(raw_state),))
        if pending is None:
            return _safe_error_page("Orbita sign-in expired", "Restart the connection from ChatGPT and try again.")

        if request.query_params.get("error"):
            return self._client_error_redirect(pending, "access_denied", "GitHub authorization was not completed")
        github_code = request.query_params.get("code", "")
        if not github_code:
            return self._client_error_redirect(pending, "access_denied", "GitHub did not return an authorization code")

        try:
            github_user = await self._fetch_github_user(github_code)
        except (httpx.HTTPError, ValueError):
            return self._client_error_redirect(
                pending, "temporarily_unavailable", "GitHub sign-in could not be verified"
            )

        login = str(github_user.get("login", ""))
        github_id = github_user.get("id")
        if not login or github_id is None or login.casefold() not in self.allowed_github_users:
            return self._client_error_redirect(
                pending, "access_denied", "This GitHub account is not allowed to use Orbita"
            )

        authorization_code = _new_token()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO oauth_codes(
                    code_hash, client_id, scopes_json, expires_at, code_challenge,
                    redirect_uri, redirect_uri_provided, resource, subject
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _token_hash(authorization_code),
                    pending["client_id"],
                    pending["scopes_json"],
                    now + 300,
                    pending["code_challenge"],
                    pending["redirect_uri"],
                    pending["redirect_uri_provided"],
                    pending["resource"],
                    f"github:{github_id}",
                ),
            )
        target = construct_redirect_uri(
            pending["redirect_uri"], code=authorization_code, state=pending["client_state"]
        )
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    def _client_error_redirect(
        self, pending: sqlite3.Row, error: str, description: str
    ) -> RedirectResponse:
        target = construct_redirect_uri(
            pending["redirect_uri"],
            error=error,
            error_description=description,
            state=pending["client_state"],
        )
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    async def _fetch_github_user(self, github_code: str) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "client_id": self.github_client_id,
                    "client_secret": self._github_client_secret,
                    "code": github_code,
                    "redirect_uri": f"{self.public_url}/oauth/github/callback",
                },
                headers={"Accept": "application/json", "User-Agent": "orbita-agent-research-server"},
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise ValueError("GitHub token response did not contain an access token")
            user_response = await client.get(
                GITHUB_USER_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": "orbita-agent-research-server",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            user_response.raise_for_status()
            data = user_response.json()
            if not isinstance(data, dict):
                raise ValueError("GitHub user response was invalid")
            return data

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        if not client.client_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM oauth_codes WHERE code_hash = ? AND client_id = ?",
                (_token_hash(authorization_code), client.client_id),
            ).fetchone()
        if row is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=bool(row["redirect_uri_provided"]),
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not client.client_id or authorization_code.client_id != client.client_id:
            raise TokenError("invalid_grant", "authorization code belongs to a different client")
        now = int(time.time())
        access_token = _new_token()
        refresh_token = _new_token()
        grant_id = secrets.token_hex(24)
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM oauth_codes WHERE code_hash = ? AND client_id = ?",
                (_token_hash(authorization_code.code), client.client_id),
            ).rowcount
            if deleted != 1:
                raise TokenError("invalid_grant", "authorization code was already used")
            self._store_token_pair(
                connection,
                access_token=access_token,
                refresh_token=refresh_token,
                client_id=client.client_id,
                scopes=authorization_code.scopes,
                resource=authorization_code.resource or self.resource_url,
                subject=authorization_code.subject or "github:unknown",
                grant_id=grant_id,
                now=now,
            )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self.access_token_ttl,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_token,
        )

    def _store_token_pair(
        self,
        connection: sqlite3.Connection,
        *,
        access_token: str,
        refresh_token: str,
        client_id: str,
        scopes: list[str],
        resource: str,
        subject: str,
        grant_id: str,
        now: int,
    ) -> None:
        scopes_json = _scopes_json(scopes)
        connection.execute(
            """
            INSERT INTO oauth_access_tokens(
                token_hash, client_id, scopes_json, expires_at, resource, subject, grant_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _token_hash(access_token),
                client_id,
                scopes_json,
                now + self.access_token_ttl,
                resource,
                subject,
                grant_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO oauth_refresh_tokens(
                token_hash, client_id, scopes_json, expires_at, subject, grant_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _token_hash(refresh_token),
                client_id,
                scopes_json,
                now + self.refresh_token_ttl,
                subject,
                grant_id,
            ),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        if not client.client_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM oauth_refresh_tokens
                WHERE token_hash = ? AND client_id = ? AND revoked = 0
                """,
                (_token_hash(refresh_token), client.client_id),
            ).fetchone()
        if row is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not client.client_id or refresh_token.client_id != client.client_id:
            raise TokenError("invalid_grant", "refresh token belongs to a different client")
        now = int(time.time())
        new_access = _new_token()
        new_refresh = _new_token()
        new_grant_id = secrets.token_hex(24)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT grant_id FROM oauth_refresh_tokens
                WHERE token_hash = ? AND client_id = ? AND revoked = 0
                """,
                (_token_hash(refresh_token.token), client.client_id),
            ).fetchone()
            if row is None:
                raise TokenError("invalid_grant", "refresh token was already used or revoked")
            connection.execute("UPDATE oauth_access_tokens SET revoked = 1 WHERE grant_id = ?", (row["grant_id"],))
            connection.execute("UPDATE oauth_refresh_tokens SET revoked = 1 WHERE grant_id = ?", (row["grant_id"],))
            self._store_token_pair(
                connection,
                access_token=new_access,
                refresh_token=new_refresh,
                client_id=client.client_id,
                scopes=scopes,
                resource=self.resource_url,
                subject=refresh_token.subject or "github:unknown",
                grant_id=new_grant_id,
                now=now,
            )
        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=self.access_token_ttl,
            scope=" ".join(scopes),
            refresh_token=new_refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM oauth_access_tokens WHERE token_hash = ? AND revoked = 0",
                (_token_hash(token),),
            ).fetchone()
        if row is None or row["expires_at"] < int(time.time()):
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
            claims={"iss": self.public_url},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        table = "oauth_access_tokens" if isinstance(token, AccessToken) else "oauth_refresh_tokens"
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT grant_id FROM {table} WHERE token_hash = ?",  # noqa: S608 - table is a fixed constant
                (_token_hash(token.token),),
            ).fetchone()
            if row is None:
                return
            connection.execute("UPDATE oauth_access_tokens SET revoked = 1 WHERE grant_id = ?", (row["grant_id"],))
            connection.execute("UPDATE oauth_refresh_tokens SET revoked = 1 WHERE grant_id = ?", (row["grant_id"],))
