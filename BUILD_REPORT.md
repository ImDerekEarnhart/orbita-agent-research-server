# Build and engineering report

Build date: 2026-07-16  
Product: Orbita Agent Research Server v0.3.0

## Outcome

Orbita is now a 32-tool governed research server with bounded self-improvement. It learns from completed cases by
proposing declarative policy changes, deterministically replays them against frozen benchmark receipts, and stores
versioned comparison evidence. It cannot edit code, execute commands, deploy, or activate a proposal by itself.
Promotion and rollback are exact-hash human approval actions.

```text
completed cases → conservative proposal → active/candidate replay → eligible or blocked
                                                        ↓
                              exact human approval → active policy → frozen plan receipt
                                                        ↓
                                         exact rollback → prior active policy
```

## Preserved source audit

| Check | Result |
| --- | ---: |
| Supplied archives | 7 |
| Extracted files inspected | 457 |
| ZIP unsafe paths detected | 0 |
| ZIP CRC tests | 7/7 passed |
| Curated knowledge documents | 45 |
| Structured claim cards | 14 |
| Preserved EG runs | 8 |
| Preserved EG graph highlights | 981 |

## v0.3.0 verification

| Check | Result |
| --- | --- |
| Product integration suite | 17 passed |
| Changed-file Ruff checks | Passed |
| Python byte compilation | Passed |
| Wheel build | Passed |
| Source distribution build | Passed |
| Clean-target v0.3.0 wheel install | Passed |
| Installed `doctor` | Passed; vault and active policy readable |
| Installed-wheel real-process HTTP smoke | `/health` HTTP 200 with version 0.3.0 |
| Tool surface | 32 typed MCP tools |
| Complete policy lifecycle | Propose, replay, reject wrong hash, promote, bind plan, rollback |
| Forbidden policy capability | Rejected before persistence |

The preserved OAuth tests still use the MCP Python SDK's actual discovery, registration, authorization, token, and
resource routes. Only the outbound GitHub profile lookup is replaced with a deterministic local test response.

## Security decisions

- GitHub performs interactive identity verification; Orbita does not store account passwords.
- Sign-in is restricted to case-insensitive usernames in `ORBITA_OAUTH_ALLOWED_GITHUB_USERS`.
- Dynamic client registration and PKCE are enabled for ChatGPT compatibility.
- OAuth redirect URIs require HTTPS, except local loopback clients.
- Authorization transactions and codes are high entropy, single-use, and short-lived.
- Access tokens default to one hour; refresh tokens default to 30 days and rotate on use.
- Orbita-issued transaction, code, access, and refresh tokens are persisted only as SHA-256 hashes.
- GitHub access tokens are used only to fetch the signed-in profile and are discarded.
- OAuth tokens are bound to the exact public `/mcp` resource and require `orbita:use`.
- Remote OAuth startup fails closed when the GitHub client credentials or username allowlist are absent.
- Static bearer auth is retained only as an explicit rollback/non-interactive mode.
- Plan execution still cannot auto-approve; approval remains bound to the current immutable plan hash and exact phrase.
- Policy changes are restricted to candidate generation, split, judge, and falsifier numbers with explicit ranges.
- Replay binds case, prior run, plan hash, and dataset hash; evaluation results receive their own SHA-256 receipt.
- Passing criteria produce `eligible_for_review`, not automatic promotion or a claim of scientific superiority.
- Promotion requires the exact candidate and latest evaluation hashes, reviewer identity, and confirmation phrase.
- Rollback requires the exact active-policy hash, reviewer identity, and a separate confirmation phrase.
- At most 25 completed cases are replayed per evaluation; candidate and cross-seed budgets are bounded.
- Railway runs behind HTTPS, as a non-root user, with research and OAuth state on the mounted `/data` volume.

## Deployment status and honest boundaries

- v0.3.0 is locally built and verified but has not yet been pushed to or deployed from the transferred private GitHub
  repository because that repository is not visible to the connected GitHub app.
- Creating the GitHub OAuth App and entering its client secret in Railway are manual account-owner steps. Secrets must
  not be pasted into chat.
- The outbound GitHub exchange requires Railway network access to `github.com` and `api.github.com`; this is expected
  on Railway but can only be proven after deployment.
- The service is single-workspace, not multi-tenant. Multiple allowed users share the same research state.
- SQLite-backed OAuth/research state requires one active Railway replica.
- The improvement evaluator has no external ground truth. Its metrics detect configured stability regressions and
  invariant failures; representative benchmarks and human scientific judgment remain required.
- A fresh dependency vulnerability query did not complete during the validation window; no new audit claim is made.
- Lean and Graphviz were unavailable in this build environment. Lean source generation and project completeness were
  previously tested, but `lake build` was not run here.
- No claim of scientific novelty is made by this software build.
