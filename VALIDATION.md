# Validation

Validation date: 2026-07-13.

## Baseline archive verification

- ZIP CRC checks: 7/7 passed.
- Orbita Research MVP: 6 tests passed.
- Discovery Kit smoke program: passed.
- Lemma Miner: 4 tests passed.
- Math research vault: 3 tests passed.
- Epistemic Runtime: 115 passed, 1 skipped, 1 environment-dependent failure because Graphviz `dot` was absent. The failing test expected SVG/HTML graph rendering; JSON and DOT graph data remained available.
- Shipped checksum manifests: passed for the MVP, runtime, math vault, and Erdős–Gyárfás research database.

## Product validation targets

The v0.1.1 server tests cover:

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

The live HTTP smoke test returned `200` from `/health`, `401` from an unauthenticated `/mcp` initialization, and
successfully initialized, listed all 24 tools, and called `orbita_capabilities` with the configured bearer token.

The exact current test/build results are recorded in `BUILD_REPORT.md`.
