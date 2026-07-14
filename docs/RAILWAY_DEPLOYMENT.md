# Railway deployment

This release is prepared for a dedicated Railway service. The public MCP route is `/mcp`; the readiness route is
`/health`. `/mcp` refuses unauthenticated requests whenever `ORBITA_AGENT_REQUIRE_AUTH=1`.

## Create the service

1. Put this directory in a private GitHub repository.
2. In Railway, create a **New Project**, choose **Deploy from GitHub repo**, and select that repository.
3. In the service **Variables** tab, add:

   ```text
   ORBITA_AGENT_REQUIRE_AUTH=1
   ORBITA_AGENT_API_TOKEN=<a random secret containing at least 32 characters>
   ORBITA_AGENT_HOME=/data
   ```

   Generate a strong token locally with:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Store the token in a password manager and seal the Railway variable after the first successful connection.

4. Add a Railway Volume mounted at `/data`. This preserves cases, runs, reports, and the epistemic database across
   redeployments. Use one replica while SQLite-backed state is enabled.
5. Under **Settings → Networking**, generate a Railway domain. Railway supplies HTTPS automatically.
6. Confirm `https://YOUR-DOMAIN/health` returns `status: ok` and `authentication: bearer`.

Do not put the bearer token in Git, screenshots, issue reports, or a browser URL.

## Verify the MCP endpoint

Set the token in your shell, then initialize with an MCP client. A request without the header should return `401`:

```powershell
$headers = @{ Authorization = "Bearer $env:ORBITA_AGENT_API_TOKEN" }
Invoke-WebRequest -Method Post -Uri "https://YOUR-DOMAIN/mcp" -Headers $headers -ContentType "application/json" -Body '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'
```

## Connect Codex or the ChatGPT desktop app

Codex configuration:

```toml
[mcp_servers.orbita]
url = "https://YOUR-DOMAIN/mcp"
bearer_token_env_var = "ORBITA_AGENT_API_TOKEN"
default_tools_approval_mode = "writes"
tool_timeout_sec = 120
```

Set `ORBITA_AGENT_API_TOKEN` on the computer running Codex, restart the client, and use `/mcp` to confirm the
server is connected. In the ChatGPT desktop app, use **Settings → MCP servers → Add server → Streamable HTTP**.

ChatGPT web uses MCP tools through installed plugins rather than the local Codex configuration file. A personal
Orbita plugin can point at the same remote endpoint after deployment.

## Operational boundaries

- Keep one Railway replica because the durable store is SQLite plus a mounted volume.
- `/health` intentionally reveals no case data and requires no token.
- Rotate the bearer token if it is exposed. Existing clients must then receive the new value.
- The server can write research cases and approve/run plans. Keep client-side approval prompts enabled for write tools.
- Add automated volume backups before relying on the service for irreplaceable research.
