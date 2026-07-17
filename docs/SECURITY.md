# Security and threat model

## Deployment modes

Local mode is a single-user process bound to localhost or launched over stdio by an MCP host. It does not require a
network credential when `ORBITA_AGENT_API_TOKEN` is unset.

Remote production mode uses OAuth 2.1 authorization code + PKCE with dynamic client registration. GitHub performs
interactive identity verification; Orbita restricts sign-in to `ORBITA_OAUTH_ALLOWED_GITHUB_USERS`, then issues
short-lived opaque access tokens and rotating refresh tokens with the `orbita:use` scope. Railway terminates public
TLS; the application binds to Railway's assigned port. `/health` is deliberately public and returns only product
readiness metadata.

Legacy bearer mode remains available through `ORBITA_AGENT_AUTH_MODE=bearer`. It requires a random
`ORBITA_AGENT_API_TOKEN` containing at least 32 characters and compares it in constant time.

## Enforced controls

- Agent uploads accept UTF-8 content, not caller-selected local paths.
- Filenames are reduced to safe basenames and restricted to a text/table allowlist.
- Inline payloads default to 8 MB maximum.
- Plans are immutable versions with SHA-256 hashes.
- Approval requires the current plan hash, reviewer identity, and an exact confirmation phrase.
- Running never auto-approves.
- Improvement proposals accept only allowlisted, range-checked policy values.
- Policy promotion requires the exact candidate hash, latest eligible evaluation hash, reviewer, and confirmation.
- Policy rollback requires the exact active-policy hash, reviewer, and confirmation.
- The improvement lab has no code-editing, shell, deployment, or arbitrary capability-registration route.
- Research writes are confined to the configured agent home.
- The curated knowledge database is opened read-only.
- Graph inputs have vertex and edge caps plus bounded search time/state limits.
- Lean output filenames are sanitized and written only under the export directory.
- Agent tools do not expose the broader runtime's shell, git, desktop, email, browser, or arbitrary-action providers.
- Remote startup fails closed when authentication is required but its secret is absent.
- OAuth clients are restricted to HTTPS redirect URIs, except local loopback clients.
- OAuth authorization codes expire after five minutes and are single-use.
- Access tokens default to one hour; refresh tokens default to 30 days and rotate on use.
- OAuth transaction, authorization, access, and refresh tokens are stored only as SHA-256 hashes.
- OAuth grants can be revoked; revocation invalidates the paired access and refresh token.
- GitHub callback state is high entropy, single-use, and expires after ten minutes.
- The requested OAuth resource must identify this exact `/mcp` endpoint.
- GitHub access tokens are used transiently to read the profile and are never persisted.
- The container writes persistent state under `/data` and drops application execution to a dedicated user.

## Known boundaries

- Tool annotations are hints to clients, not an authorization system.
- OAuth authenticates an allowed GitHub identity but v0.3.0 still uses one shared Orbita research workspace; it is not
  tenant-isolated.
- Anyone with a valid Orbita access token can call write tools, although plan approval still requires the exact
  frozen hash and phrase.
- Dynamic client registration is intentionally public as required by MCP OAuth. It can create durable client metadata
  but cannot authorize access without an allowlisted GitHub sign-in.
- Registered confidential-client secrets are stored in the protected SQLite volume because the MCP SDK must validate
  them at the token endpoint. Orbita-issued end-user tokens are stored only as hashes.
- A malicious local user with write access to the state directory can tamper with local files.
- The product does not provide tenant separation.
- Large but allowed tables can still consume substantial CPU during discovery.
- Historical replay uses held-out data and invariants but has no external ground truth; eligibility is not proof that
  a proposed policy is scientifically better or unbiased.
- PDF, DOCX, Excel, and ZIP uploads remain available through the older REST/browser route; they are not accepted inline through MCP.
- Lean source generation validates the witness in Python, but independent formal assurance requires installing Lean and running `lake build`.

## Reporting vulnerabilities

Before broader public distribution, add a private vulnerability-reporting address and a supported-version policy.
