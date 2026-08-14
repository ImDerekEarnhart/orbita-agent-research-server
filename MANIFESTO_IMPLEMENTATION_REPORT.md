# Orbita Manifesto Implementation Report

Date: 2026-08-13
Pass: Phase 0 / first P0 vertical slice
Deployment: unified staging release candidate
Active policy change: none

This report covers the first-pass definition of done from `ORBITA_PRODUCT_SELF_IMPROVEMENT_MANIFESTO.md`. It does not claim that the full multi-phase manifesto is complete.

| Requirement | Files changed | Tests | Status | Notes / residual limitation |
|---|---|---|---|---|
| Architecture audit | `docs/SELF_IMPROVEMENT_ARCHITECTURE_AUDIT.md` | Baseline inspection | IMPLEMENTED | Maps policy improvement, claims, falsification, reports, deterministic execution, and gaps. |
| General `ImprovementCandidate` schema | `src/orbita_agent/improvement_registry.py` | `test_candidate_hash_is_semantic_and_changes_with_artifact` | IMPLEMENTED | Supports policy, code, adapter, operator, language, verifier, retrieval, UI, performance, and safety candidate kinds. All remain inactive. |
| `ImprovementEvaluation` and frozen plan | same | `test_freeze_and_evaluation_are_exact_hash_bound_and_immutable` | IMPLEMENTED | One immutable plan and one immutable result per candidate in v1. A revised attempt requires a new candidate lineage node. |
| Explicit state transitions | same | registry tests | IMPLEMENTED | Append-only events cover draft, frozen evaluation, and survived/refuted/inconclusive evaluation. |
| Immutable hashes | same | hash and mutation tests | IMPLEMENTED | Canonical JSON SHA-256; SQLite triggers reject update/delete for candidate, plan, evaluation, and event rows. |
| Persistence/migration | same; existing improvement SQLite database | registry and gateway tests | IMPLEMENTED | Additive `CREATE TABLE IF NOT EXISTS`; no legacy row or active policy is rewritten. |
| Gateway and MCP create/read surface | `gateway.py`, `mcp_server.py` | MCP surface and registry tests | IMPLEMENTED | Adds status, register, list, get, freeze, and record tools. |
| Illegal self-promotion blocked | same | `test_general_candidate_cannot_self_promote_or_change_active_policy` | IMPLEMENTED | Generalized registry has no activation tool and explicitly raises if called internally. |
| Illegal evaluation mutation blocked | registry schema | immutability test | IMPLEMENTED | Database-level trigger plus one-result constraint. |
| Active policy remains unchanged | gateway test | self-promotion test and legacy policy suite | IMPLEMENTED | Legacy bounded policy lane remains the only activation-capable lane. No production/local active policy database was mutated by this pass. |
| Inverted-leaderboard regression | `tests/test_inverted_leaderboard_regression.py` | `test_many_empirical_survivals_cannot_outvote_one_valid_counterexample` | IMPLEMENTED | 13,000 declared survivals cannot outvote one valid small-graph counterexample; the missed `n < 4` region stays visible in the fixture. |
| Exact technical reproduction | external-experiment service and MCP/gateway | `test_exact_reproduction_compares_outputs_without_claiming_scientific_replication` | IMPLEMENTED | Reproduction has a new manifest and approval; output hashes are compared. Bitwise agreement explicitly does not establish scientific reproduction. |
| First-class coverage bugs | external-experiment service and MCP/gateway | `test_coverage_bug_preserves_original_and_versions_replacement_protocol` | IMPLEMENTED | Validated receipt-backed counterexamples preserve the predecessor, change its evidence status, create a hash-bound replacement protocol, and require reevaluation. Generalization to older discovery routes remains future work. |
| Executable replacement protocol | external-experiment service and MCP/gateway | `test_replacement_protocol_executes_and_resolves_every_affected_result` | IMPLEMENTED | Affected results are frozen before exact-hash approval; partial resolution is rejected; each final disposition references the replacement execution receipt. |
| `SEARCH_FAILURE` versus `LANGUAGE_LIMIT` | registry validation | `test_language_limit_fails_closed_without_formal_certificate` | PARTIAL | The registry rejects unsupported `LANGUAGE_LIMIT` candidates and recognizes four proof paths. Full grammar enumeration/checker backends remain future work. |
| Failure/limit classification | registry and external-experiment service | language-limit and unavailable-executor tests | PARTIAL | General candidate classes are persisted; the external route distinguishes pending, executed, timeout, execution failure, and execution limit without falsifying the hypothesis. Automatic classification is not yet connected to every older route. |
| Deterministic external experiment | `src/orbita_agent/external_experiments.py`, gateway/MCP, config, docs | `tests/test_external_experiments.py` | IMPLEMENTED | Exact plan/scope/coverage/spec/verifier freeze, inline-only staging, dual-hash human approval, network denial, deterministic receipts, independent verification, reproduction comparison, and coverage reevaluation are implemented locally. |
| Legacy policy migration plan | audit | legacy improvement suite | IMPLEMENTED | Plan is read-only adaptation; existing hashes and activation flow remain authoritative. |
| Evidence taxonomy and claim scope | `src/orbita/epistemic_contract.py`, Guided memory/service/reporting, gateway/MCP | `tests/test_epistemic_contract.py`, Guided service tests | IMPLEMENTED | Central evidence taxonomy, normalized claim scope, append-only epistemic events, and fail-closed scope escalation now protect legacy Guided discovery. Existing claim workflow statuses remain compatible but are no longer treated as evidence labels. |
| Falsification coverage and coverage-bug objects | external experiment service plus Guided epistemic contracts and re-examination bridge | external experiment and epistemic-contract tests | IMPLEMENTED | Every new Guided finding records normalized coverage and untested regions. Validated external bugs freeze affected claim bindings, append `REFUTED`/`PROVISIONAL` epistemic events, update claim workflow status, and queue the full dependency blast radius. Projection is idempotently retryable. |
| Data partition contamination ledger | manifesto audit | Existing scout/holdout tests | PARTIAL | Existing scout/confirmation separation remains; universal access-ledger enforcement is not yet implemented. |
| Representation engine and formal certificates | manifesto audit | language-limit fail-closed test | PARTIAL | Certificate admission is fail-closed; representation depth, transformation hunter, grammar proofs, SMT, and ORB-1 adapters remain future phases. |
| Interpretation/selectivity/novelty competition | manifesto audit | none in this slice | PARTIAL | No automatic interpretation promotion was added. `NONE_OF_THE_ABOVE`, E13/E14 selectivity, and novelty review require later work. |
| Guided dashboard | none | none | PARTIAL | MCP/core surface exists locally; Guided UI wiring is not part of this core first pass. |

## Exact new MCP capabilities

- `orbita_governed_improvement_status`
- `orbita_guard_claim_scope`
- `orbita_register_improvement_candidate`
- `orbita_list_governed_improvements`
- `orbita_get_governed_improvement`
- `orbita_freeze_improvement_evaluation`
- `orbita_record_governed_improvement_evaluation`
- `orbita_external_experiment_status`
- `orbita_freeze_external_experiment`
- `orbita_submit_external_experiment`
- `orbita_approve_external_experiment`
- `orbita_run_external_experiment`
- `orbita_record_external_verification`
- `orbita_get_external_experiment`
- `orbita_list_external_experiments`
- `orbita_prepare_external_reproduction`
- `orbita_approve_external_reproduction`
- `orbita_run_external_reproduction`
- `orbita_record_external_coverage_bug`
- `orbita_propagate_external_coverage_bug_to_claims`
- `orbita_prepare_coverage_reevaluation`
- `orbita_approve_coverage_reevaluation`
- `orbita_run_coverage_reevaluation`
- `orbita_record_coverage_resolutions`
- `orbita_get_coverage_bug`

There is intentionally no generalized approve, promote, merge, deploy, or rollback tool.

## Verification

- Focused claim-contract/MCP/Guided suite: 55 passed, one third-party deprecation warning.
- Focused lint: passed.
- Full suite: 313 passed, with one unrelated third-party Starlette/httpx deprecation warning.
- Repository-wide lint: passed.

## Next exact slice

Add the same epistemic-contract projection to any remaining non-Guided claim writers and expose claim coverage-bug
history plus re-examination work as a dedicated browser dashboard workflow.
