# Changelog

## 0.7.0 - 2026-08-14

- Added a tenant-scoped, append-only General Problem Loop for arbitrary bounded objectives.
- Enforced the `GOAL -> REPRESENT -> PLAN -> ACT -> OBSERVE -> FALSIFY -> DIAGNOSE -> REPAIR/LEARN -> RETRY ->
  COMMIT/REFUSE` lifecycle with deterministic branching and frozen retry budgets.
- Added artifact schemas, exact previous-event hashes, hash-chain verification, language-limit certificate requirements,
  action-receipt requirements, and fail-closed self-activation rejection.
- Exposed safe create/read/advance/verify operations through MCP, the Guided internal API, and Guided hybrid chat.


## 0.6.0 - 2026-08-14

- Added canonical, inert Language Snapshots covering primitives, observables, epistemic boundaries, permissions, and
  invariants.
- Added finite representation collision and nuisance-overseparation audits plus machine-checkable language-limit
  certificates.
- Added hash-bound repair candidates and exact-authorization transition receipts that cannot activate the runtime.
- Added a fixed-parameter Temporal Unaskability audit comparing ordinary memory baselines, hysteresis, and State-Inertia.
- Added typed capability-component graphs for conservative archive synthesis.
- Added seven adversarial semantic-evolution tests and exposed the new operations through read-only MCP tools.

## Unreleased

## 0.5.0 — 2026-08-13

- Added provider-neutral `prospective_blind_calibration` as a separate governed execution route.
- Added sanitized row batches, strict output vocabularies, forbidden-output validation, and one prediction per event.
- Added immutable SHA-256 prediction freezes before any scoring-key intake is possible.
- Added a separate tenant-scoped sealed-key store, exact-hash reveal approval, and immutable score receipts.
- Added primary-label accuracy, confidence calibration error, and optional hypothesis-hit scoring.
- Exposed the complete workflow through both MCP tools and Guided HTTP routes.
- Added UAP-shaped leakage, holdout, row-materialization, vocabulary, and ten-row acceptance tests.

## 0.4.0 — 2026-08-13

- Unified MCP and Guided workflows over one tenant-scoped core.
- Added generalized inactive improvement candidates and deterministic external experiments.
- Added explicit evidence status, claim scope, falsification coverage, coverage repair, and re-examination propagation.

- Replaced the single-principal Discovery Genome collapse with per-request tenant resolution from the authenticated
  OAuth subject. `ORBITA_DISCOVERY_GENOME_USERNAME` is no longer a deployment-wide tenant.
- Added a persistent tenant registry (`orbita_tenants.db`) mapping authenticated subject to Guided UI username, with
  an append-only binding audit trail.
- Resolution fails closed: an unbound or unauthenticated identity is refused and never served a default tenant.
- Added `orbita-agent tenants` (`bind`, `unbind`, `list`, `identities`, `events`). Binding is operator-only and is
  deliberately not exposed as an MCP tool, so no caller can grant itself a tenant.
- Refused, by default, to bind two subjects to one tenant or to silently rebind a subject.
- Recorded the GitHub login for each allowlisted identity at sign-in so operators can bind by login instead of by
  numeric subject.
- Added `orbita_genome_whoami`, and made `orbita_genome_status` report tenancy state instead of failing when the
  caller has no tenant.
- Replaced the "exactly one allowed GitHub user" startup interlock: multiple users are now permitted once every
  identity is bound, and refused while no bindings exist.
- Preserved existing single-owner deployments: one allowed GitHub user plus `ORBITA_DISCOVERY_GENOME_USERNAME`
  auto-binds on first sign-in, and that fallback disengages when a second user is allowed.

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
