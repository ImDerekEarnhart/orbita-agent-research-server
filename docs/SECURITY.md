# Security and threat model

## Deployment modes

Local mode is a single-user process bound to localhost or launched over stdio by an MCP host. It does not require a
network credential when `ORBITA_AGENT_API_TOKEN` is unset.

Remote mode requires `ORBITA_AGENT_REQUIRE_AUTH=1` and a random `ORBITA_AGENT_API_TOKEN` containing at least 32
characters. `/mcp` then requires that exact bearer token using constant-time comparison. Railway terminates public
TLS; the application binds to Railway's assigned port. `/health` is deliberately public and returns only product
readiness metadata.

## Enforced controls

- Agent uploads accept UTF-8 content, not caller-selected local paths.
- Filenames are reduced to safe basenames and restricted to a text/table allowlist.
- Inline payloads default to 8 MB maximum.
- Plans are immutable versions with SHA-256 hashes.
- Approval requires the current plan hash, reviewer identity, and an exact confirmation phrase.
- Running never auto-approves.
- Research writes are confined to the configured agent home.
- The curated knowledge database is opened read-only.
- Graph inputs have vertex and edge caps plus bounded search time/state limits.
- Lean output filenames are sanitized and written only under the export directory.
- Agent tools do not expose the broader runtime's shell, git, desktop, email, browser, or arbitrary-action providers.
- Remote startup fails closed when authentication is required but its secret is absent.
- The container writes persistent state under `/data` and drops application execution to a dedicated user.

## Known boundaries

- Tool annotations are hints to clients, not an authorization system.
- One static bearer token identifies the deployment, not an individual user; v0.1.1 is not multi-tenant.
- Anyone holding the token can call write tools, although plan approval still requires the exact frozen hash and phrase.
- A malicious local user with write access to the state directory can tamper with local files.
- The product does not provide tenant separation.
- Large but allowed tables can still consume substantial CPU during discovery.
- PDF, DOCX, Excel, and ZIP uploads remain available through the older REST/browser route; they are not accepted inline through MCP.
- Lean source generation validates the witness in Python, but independent formal assurance requires installing Lean and running `lake build`.

## Reporting vulnerabilities

Before broader public distribution, add a private vulnerability-reporting address and a supported-version policy.
