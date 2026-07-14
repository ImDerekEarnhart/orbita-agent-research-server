# Build and engineering report

Build date: 2026-07-13  
Product: Orbita Agent Research Server v0.1.1

## Outcome

The seven supplied archives were treated as an engineering parts bin. The strongest compatible research components were consolidated behind a new MCP-native agent boundary, while duplicate packages, stale build products, raw conversation material, and broad execution capabilities were excluded from the agent surface.

The delivered product supports a complete local workflow:

```text
AI/person → profile supplied data → freeze plan → review exact hash
          → run baseline/held-out/cross-seed attacks
          → persist survivors and refutations → inspect belief history/report
```

It also provides curated research-memory search, preserved finite graph receipts, bounded graph analysis, and complete Lean project export for concrete witnesses.

## Source audit

| Check | Result |
| --- | ---: |
| Supplied archives | 7 |
| Extracted files inspected | 457 |
| ZIP unsafe paths detected | 0 |
| ZIP CRC tests | 7/7 passed |
| Supplied checksum verifiers | Passed |
| Curated knowledge documents | 45 |
| Structured claim cards | 14 |
| Preserved EG runs | 8 |
| Preserved EG graph highlights | 981 |

## Baseline code verification

| Component | Result |
| --- | --- |
| Orbita Research MVP | 6 passed |
| Discovery Kit | Smoke program passed |
| Lemma Miner | 4 passed |
| Math research vault | 3 passed |
| Epistemic Runtime | 115 passed, 1 skipped, 1 Graphviz-environment failure |

The runtime failure was limited to a test expecting Graphviz `dot` to render SVG/HTML. Graphviz was not installed in the build environment. It did not indicate a failed claim, ledger, discovery, or memory test.

## Product verification

| Check | Result |
| --- | --- |
| Product integration suite | 10 passed |
| Authored-code Ruff checks | Passed |
| Python byte compilation | Passed |
| Wheel build | Passed |
| Source distribution build | Passed |
| Clean-target wheel install | Passed |
| Installed `doctor` | Passed |
| Installed end-to-end discovery demo | Completed; 3 candidates, 3 survivors |
| Authenticated MCP Streamable HTTP initialize/list/call | Passed; 24 tools listed |
| Missing/invalid remote bearer token | Startup refusal / HTTP 401 passed |
| Railway health endpoint | HTTP 200 with non-sensitive readiness metadata |
| Installed-wheel graph regression | C16 witness found |
| Installed-wheel Lean project export | Complete checker project written |
| Locked dependency vulnerability audit | No known vulnerabilities found |

## Security decisions

- Stable MCP Python SDK v1 is pinned below v2 because v2 was pre-release on the build date.
- The agent cannot supply arbitrary local filesystem paths for uploads or exports.
- Plan execution cannot auto-approve.
- Approval is bound to the current immutable plan hash and an explicit confirmation phrase.
- The knowledge database is read-only.
- The graph route is bounded by vertices, edges, time, and DFS states.
- Local HTTP binds to localhost by default. Remote mode fails closed without a 32+ character bearer secret.
- Railway deployment runs behind HTTPS, writes to a mounted volume, and exposes only a non-sensitive public health route.
- Shell, desktop, browser, email, git, and arbitrary runtime actions are not registered as MCP tools.

## Honest unverified boundaries

- Lean and Graphviz were unavailable in the build environment. Lean source generation and project completeness were tested, but `lake build` was not run here.
- The authenticated HTTP path was tested locally. The Railway container was not deployed from this build environment,
  which has no connected Railway session or container engine.
- Static bearer authentication is single-operator protection, not OAuth, user-level authorization, or tenant separation.
- The current automatic discovery compiler is tabular and supports linear association and group-difference candidate families.
- No claim of scientific novelty is made by this software build.
