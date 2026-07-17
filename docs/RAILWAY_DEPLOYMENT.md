# Railway deployment with ChatGPT OAuth

Orbita v0.3.0 exposes Streamable HTTP MCP at `/mcp`, public readiness at `/health`, OAuth authorization-server
metadata, RFC 9728 protected-resource metadata, dynamic client registration, authorization-code + PKCE, rotating
refresh tokens, and revocation. GitHub supplies the interactive user identity; Orbita issues and validates the MCP
tokens.

## 1. Create the GitHub OAuth App

While signed into the GitHub account that should authorize Orbita, open
<https://github.com/settings/applications/new> and enter:

```text
Application name: Orbita Research MCP
Homepage URL: https://orbita-agent-research-server-production.up.railway.app
Authorization callback URL: https://orbita-agent-research-server-production.up.railway.app/oauth/github/callback
```

Create the app, copy its **Client ID**, and generate one **Client secret**. Never commit or paste the secret into
chat, an issue, or a browser URL.

## 2. Configure Railway

Keep the existing volume mounted at `/data` and use one replica while SQLite-backed state is enabled. In the
service **Variables** tab, set:

```text
ORBITA_AGENT_AUTH_MODE=oauth-github
ORBITA_AGENT_REQUIRE_AUTH=1
ORBITA_AGENT_HOME=/data
ORBITA_OAUTH_GITHUB_CLIENT_ID=<GitHub OAuth App client ID>
ORBITA_OAUTH_GITHUB_CLIENT_SECRET=<GitHub OAuth App client secret>
ORBITA_OAUTH_ALLOWED_GITHUB_USERS=DerekEarnhart
```

`ORBITA_OAUTH_ALLOWED_GITHUB_USERS` is a comma-separated, case-insensitive allowlist. Add another username only if
that person should be able to approve plans, run research, and modify the persistent Orbita state.

Optional token lifetime settings are:

```text
ORBITA_OAUTH_ACCESS_TOKEN_TTL=3600
ORBITA_OAUTH_REFRESH_TOKEN_TTL=2592000
```

The access-token range is 5 minutes to 24 hours. The refresh-token range is 1 hour to 90 days. Defaults are one
hour and 30 days.

The v0.3.0 Docker image defaults to `oauth-github` and fails closed if any required OAuth variable is missing. The
old `ORBITA_AGENT_API_TOKEN` may remain during the rollout but is ignored in OAuth mode; remove it after OAuth is
verified.

## 3. Verify the deployment

After Railway redeploys, these requests should succeed without credentials:

```text
GET https://orbita-agent-research-server-production.up.railway.app/health
GET https://orbita-agent-research-server-production.up.railway.app/.well-known/oauth-authorization-server
GET https://orbita-agent-research-server-production.up.railway.app/.well-known/oauth-protected-resource/mcp
```

Expected health metadata includes:

```json
{
  "status": "ok",
  "product": "orbita-agent-research-server",
  "version": "0.3.0",
  "authentication": "oauth-github"
}
```

An unauthenticated `POST /mcp` must return `401` and a `WWW-Authenticate` header pointing to the protected-resource
metadata route.

## 4. Add Orbita to ChatGPT web

1. In ChatGPT, open **Settings → Security and login** and enable **Developer mode**.
2. Open **Settings → Plugins**, or go to <https://chatgpt.com/plugins>.
3. Select the plus button and create a developer-mode app.
4. Use this MCP URL:

   ```text
   https://orbita-agent-research-server-production.up.railway.app/mcp
   ```

5. Choose OAuth if ChatGPT asks for the authentication type, create the app, and select **Connect**.
6. GitHub will show the Orbita OAuth App authorization page. Sign in with an allowed username and approve it.
7. Open a new chat, add Orbita from the tools/plugins menu, and ask it to call `orbita_capabilities`.

ChatGPT should discover and dynamically register its client. No Orbita API token is copied into ChatGPT.

## Rollback to static bearer mode

Set these variables and redeploy:

```text
ORBITA_AGENT_AUTH_MODE=bearer
ORBITA_AGENT_API_TOKEN=<a random secret containing at least 32 characters>
ORBITA_AGENT_REQUIRE_AUTH=1
```

Static bearer mode works with Codex and other clients that can read a token from a private environment variable,
but ChatGPT web OAuth linking requires `oauth-github` mode.

## Operational boundaries

- Keep one Railway replica because research and OAuth state use SQLite on the mounted volume.
- Back up `/data`; it contains cases, claims, policy/evaluation history, OAuth client registrations, and token hashes.
- `/health`, `/`, OAuth discovery, registration, authorization, token, revocation, and the GitHub callback are public
  protocol routes. `/mcp` requires the `orbita:use` scope.
- GitHub access tokens are used only to fetch the signed-in profile and are not persisted.
- Orbita persists hashes of its access, refresh, authorization, and transaction tokens; registered OAuth client
  metadata may include a generated client secret.
- Removing a username from the allowlist blocks new sign-ins. Revoke existing grants or remove `/data/orbita_oauth.db`
  during a maintenance window if immediate global logout is required.
- Keep client-side approval prompts enabled for write tools. OAuth authenticates the operator; it does not replace
  Orbita's exact-plan review and hash-bound approval rule.
- Keep client-side approval prompts enabled for policy promotion and rollback. OAuth identity does not imply approval
  of an evaluated change.
