# Orbita Self-Improvement Architecture Audit

Date: 2026-08-13
Scope: Phase 0 audit before the generalized improvement-registry implementation

## Current architecture

| Concern | Current implementation | Finding |
|---|---|---|
| Agent/API facade | `orbita_agent.gateway.AgentGateway` and `orbita_agent.mcp_server` | MCP tools are routed through a narrow tenant-scoped gateway. |
| Bounded self-improvement | `orbita_agent.improvement.ImprovementLab` | Mature policy-only lane with allowlisted fields, deterministic historical replay, exact candidate/evaluation hashes, human confirmation, and rollback. |
| Discovery candidates | `orbita_discovery.core`, domain adapters, falsifiers, and judges | Candidate results use operational states such as supported, provisional, challenged, refuted, and unscorable. These describe a finite run, not theorem status. |
| Claim/evidence memory | `orbita.ledger`, `orbita.graph`, `orbita_mvp.storage`, and gateway claim methods | Claims, evidence, contradictions, and supersession are preserved. Claim history is not silently erased with case data. |
| Plans and approvals | `orbita_mvp` storage/service plus gateway approval methods | Plans are frozen and SHA-256 bound before exact human approval. |
| Deterministic execution | `orbita.execution` | A manifest-bound, network-disabled, non-root container runtime already exists in the broader core. It is not yet exposed as a generic Guided/MCP experiment route. |
| Reporting | `orbita_mvp.reporting` | Reports already warn that finite survival is not proof, causality, novelty, or external replication, and separate unscorable engine limits from refutations. |
| Representation/DSL | language and operator modules exist in the broader core | There is not yet one formal `LanguageSpec`/certificate API capable of proving a declared grammar-wide language limit. |
| DerekX boundary | deterministic receipts and evidence provenance exist in the broader runtime | Integrity/replay evidence is available, but the Agent gateway does not yet expose a named DerekX external-experiment contract. |

## Existing status and promotion behavior

The current product has two different state systems that must not be conflated:

1. Discovery findings use finite-run operational labels. A survivor is explicitly not presented as a universal proof.
2. Policy improvement candidates move from proposed to evaluated/blocked and may become promoted only through exact candidate hash, exact evaluation hash, reviewer identity, and exact confirmation phrase.

The current policy lane is deliberately restricted to numeric, allowlisted research-policy fields. Arbitrary code, commands, deployments, and self-promotion are rejected. That lane remains the only activation-capable improvement mechanism in this pass.

## Current falsification and holdout behavior

- Table discovery separates scout and confirmation partitions.
- Baseline, held-out, and cross-seed falsifiers are used.
- The report preserves killed and unscorable candidates.
- The current policy-replay evaluator measures stability on completed cases.
- A general `D_search` / `D_selection` / `D_confirmation` / `D_prospective` access ledger is not yet implemented across every research route.
- Coverage is present in route-specific artifacts but is not yet normalized into one universal `FalsificationCoverage` object.

## Current provenance and ledger behavior

- Plans, candidates, evaluations, manifests, and execution receipts use stable content hashing in their respective subsystems.
- The claim ledger preserves contradictions and supersession.
- Historical policy rows are versioned rather than overwritten.
- The new generalized registry should remain append-only and share the existing tenant-scoped improvement database without reusing the legacy policy tables.

## Current overclaiming risks

1. Operational discovery statuses historically were not a full evidence taxonomy. Guided discoveries now record a separate append-only evidence status; older stored claims without that contract remain visibly legacy/unstructured.
2. A central fail-closed claim-scope escalation guard now exists in core and MCP. Remaining non-Guided claim writers must opt into the same contract before this risk is fully eliminated across the entire codebase.
3. Search exhaustion and a formally certified language limit are not yet modeled by one shared public API.
4. The deterministic container runtime is not yet joined to the Agent gateway as a generic external scientific experiment route.
5. Interpretation competition, selectivity, novelty, and correctness remain separate architectural goals rather than one enforced promotion pipeline.

## Compatible migration plan

### This pass

- Add a separate append-only generalized improvement registry in the existing tenant-scoped improvement database.
- Add typed candidate kinds and limitation classes.
- Freeze exact evaluation plans and record immutable evaluations using expected hashes.
- Expose create/read/freeze/record operations through `AgentGateway` and MCP.
- Provide no generalized promotion or deployment operation.
- Keep the active bounded policy and every legacy API unchanged.

### Next passes

1. Add a read-only adapter that represents legacy policy proposals in the generalized registry without rewriting their rows or hashes.
2. Extend the implemented deterministic external-experiment route with executable replacement-protocol reevaluation.
3. Continue the implemented Guided migration by propagating validated external coverage bugs into historical claims and adding data-partition access enforcement.
4. Add finite-language specifications and accepted proof-certificate backends before exposing `LANGUAGE_LIMIT`.
5. Add interpretation/selectivity competition and a dashboard.
6. Add an external, exact-hash human approval adapter for candidate kinds that have a safe activation mechanism. Code candidates must remain non-self-deploying.

## Baseline verification

Before behavior changes:

- `pytest -q tests/test_improvement.py tests/test_unscorable_candidates.py`: 12 passed.
- `ruff check src tests`: passed.
- A full-suite attempt exceeded the initial 120-second command window; it produced no failure result and is rerun with a larger window after implementation.
