# Changelog

## 0.3.0 — 2026-07-16

- Added a persistent, versioned registry for the active research policy and every improvement proposal.
- Added history-derived conservative policy suggestions without autonomous activation.
- Added deterministic active-versus-proposed replay over completed case, plan, and dataset-hash receipts.
- Added explicit acceptance criteria, invariant checks, comparison metrics, and hash-bound evaluation receipts.
- Added exact candidate/evaluation hash approval before policy promotion.
- Added hash-bound rollback to a previously active policy.
- Restricted improvements to allowlisted numeric research settings; code, shell, filesystem, deployment, and new
  capability fields are rejected.
- Bound every newly compiled plan to the active policy ID, version, and hash.
- Expanded the MCP surface from 24 to 32 tools and added an improvement-status resource.

## 0.2.0 — 2026-07-14

- Added MCP OAuth 2.1 authorization code flow with PKCE and dynamic client registration.
- Added GitHub-backed operator identity with a case-insensitive username allowlist.
- Added RFC 8414 authorization-server metadata and RFC 9728 protected-resource metadata.
- Added short-lived scoped access tokens, rotating refresh tokens, and RFC 7009 revocation.
- Stored Orbita-issued transaction, code, access, and refresh tokens only as SHA-256 hashes.
- Added fail-closed Railway OAuth configuration and a complete ChatGPT developer-mode connection guide.
- Preserved static bearer authentication as an explicit rollback/non-interactive mode.

## 0.1.1 — 2026-07-13

- Added production bearer-token verification for remote MCP requests.
- Added a public health route that does not disclose research state.
- Added a non-root Docker image and Railway config-as-code.
- Added environment-aware host and port handling for Railway.
- Added remote deployment and Codex connection documentation.

## 0.1.0 — 2026-07-13

- Added a production-oriented MCP v1 tool boundary over the proven Research MVP.
- Added hash-bound plan approval and disabled agent auto-approval.
- Added inline text/table intake with path, extension, and byte limits.
- Added compact paginated result views for AI clients.
- Added persistent belief-history, contradiction, supersession, and impact tools.
- Added a curated read-only research database built from the math vault and Erdős–Gyárfás receipts.
- Added bounded graph analysis and concrete Lean witness export.
- Added Windows and cross-platform setup, doctor, demo, security documentation, and integration tests.
