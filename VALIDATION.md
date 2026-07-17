# Validation

Validation date: 2026-07-16.

## Baseline archive verification

- ZIP CRC checks: 7/7 passed.
- Orbita Research MVP: 6 tests passed.
- Discovery Kit smoke program: passed.
- Lemma Miner: 4 tests passed.
- Math research vault: 3 tests passed.
- Epistemic Runtime: 115 passed, 1 skipped, 1 environment-dependent failure because Graphviz `dot` was absent. The failing test expected SVG/HTML graph rendering; JSON and DOT graph data remained available.
- Shipped checksum manifests: passed for the MVP, runtime, math vault, and Erdős–Gyárfás research database.

## Product validation targets

The v0.3.0 server tests cover:

- inline upload type/path/size guards;
- deterministic case profiling;
- frozen-plan creation;
- wrong-hash and wrong-confirmation rejection;
- approved end-to-end discovery;
- report and belief-claim creation;
- cross-case plan rejection;
- curated full-text search and structured claim cards;
- preserved fast-run totals and 268 unique exact labeled near misses;
- the deterministic no-C4/no-C8 graph's C16 path;
- Lean witness source export without `sorry`;
- MCP tool registration, JSON schemas, descriptions, and read/destructive annotations.
- fail-closed remote-auth configuration;
- constant-time bearer-token acceptance/rejection;
- authenticated health-route and resource-server configuration.
- OAuth authorization-server and protected-resource discovery metadata;
- dynamic public-client registration;
- authorization code + PKCE exchange through the SDK HTTP routes;
- GitHub username allowlisting and denial redirects that preserve client state;
- requested-resource binding and scoped access tokens;
- single-use authorization codes, refresh rotation, and grant revocation;
- token-hash persistence without storing Orbita-issued bearer credentials.
- creation and persistence of the factory research policy;
- rejection of non-allowlisted policy fields;
- deterministic active-versus-proposed replay on exact completed-case receipts;
- replay comparison metrics, invariant checks, explicit criteria, and evaluation hashes;
- candidate-hash, evaluation-hash, reviewer, and phrase checks before promotion;
- binding newly compiled plans to the promoted policy hash and thresholds;
- wrong-hash rollback rejection and restoration of the prior policy;
- safe behavior when the history suggester has no completed benchmark runs;
- MCP schemas and annotations for all eight improvement tools.

The local protocol smoke test returned OAuth discovery documents, dynamically registered a public client, completed
authorization code + PKCE, issued an access/refresh pair, and returned `401` plus protected-resource metadata from an
unauthenticated `/mcp` request. The earlier live v0.1.1 Railway smoke test initialized, listed all 24 tools, and called
`orbita_capabilities` with its configured static bearer token; live v0.3.0 verification follows deployment.

The v0.3.0 integration suite passed 17 tests. Changed-file Ruff checks and Python byte compilation passed. The wheel
and source distribution built successfully, the wheel installed into a clean target, and `orbita-agent doctor`
created and read the versioned policy registry. A real installed-wheel HTTP process returned `200` health metadata
with version `0.3.0`; the MCP registry contained 32 tools.

The dependency vulnerability service did not return within the validation window, so no fresh vulnerability-database
claim is made for this build. Dependency versions remain pinned in `requirements.lock`.

The exact current test/build results are recorded in `BUILD_REPORT.md`.
