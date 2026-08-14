# Deterministic External Experiment Route: Phase 0 Design

Status: first tenant-scoped Agent/MCP vertical slice implemented locally; no deployment performed.

## Why this route exists

An Orbita engine limitation must not become a scientific verdict. If the table runner cannot execute a candidate such as a universal mathematical bound, it should preserve that candidate's semantics and route an exact experiment to the existing deterministic execution runtime. It must not silently convert the question into a correlation test.

## Existing substrate

`orbita.execution` already provides most of the deterministic operator needed by this route:

- exact manifest hashing;
- declared code and input artifacts;
- separate writable output directory;
- network disabled;
- read-only root filesystem;
- dropped capabilities and no-new-privileges;
- non-root execution;
- bounded resources and timeout handling;
- output hashing, replay receipts, and ledger evidence.

## Proposed gateway contract

```text
approved Orbita plan and plan hash
-> external experiment specification and hash
-> declared runner/image hash
-> declared input hashes
-> independent verifier specification
-> execution receipt and output hashes
-> independent verifier receipt
-> separate integrity, reproducibility, and epistemic fields
```

Required request fields:

- `case_id`
- `approved_plan_id`
- `expected_plan_hash`
- `scientific_question`
- `claim_scope`
- `runner_manifest`
- `runner_hash`
- `input_artifacts[]` with hashes
- `verdict_schema`
- `independent_verifier`
- `falsification_coverage`
- `anti_rescue_rules`

Required response fields:

- `experiment_id`
- `experiment_hash`
- `execution_status`
- `failure_classification`
- `integrity_status`
- `bitwise_reproducibility_status`
- `scientific_reproducibility_status`
- `epistemic_status`
- `execution_receipt_hash`
- `verification_receipt_hash`
- `output_artifact_hashes`
- `untested_regions`

## Fail-closed rules

1. Refuse an unapproved or hash-mismatched plan.
2. Refuse network access in v1.
3. Refuse an undeclared output or mutable runner identity.
4. Report missing engine support as `ENGINE_CAPABILITY_LIMIT`, never `REFUTED`.
5. Treat a valid deterministic receipt as integrity/replay evidence only.
6. Never promote scientific status solely because execution succeeded.
7. Preserve a failed experiment and require a new lineage identity for any repair.

## Implemented vertical slice

The Agent gateway now freezes, submits, approves, runs, reads, lists, and independently verifies external experiments.
It uses the existing `ContainerExecutionRuntime`, keeps the active research policy unchanged, and accepts inline text
artifacts only. Exact experiment and execution-manifest hashes are required at approval and execution. Successful
integrity verification does not change scientific status. An unavailable OCI engine is recorded as
`EXECUTION_LIMIT`, not `REFUTED`.

Exact technical reproductions and first-class coverage bugs are also implemented. A reproduction is prepared from the
original staged artifacts, receives a new manifest and human approval, and compares output hashes. A validated missed
counterexample preserves the original record, creates a hash-bound replacement protocol version, and marks affected
results for reevaluation.

Replacement-protocol reevaluation is now executable. The corrected coverage, replacement code/data, exact affected
results, reevaluation hash, and staged execution manifest are frozen before another human approval. Completion requires
one receipt-bound resolution for every affected result; partial resolution is rejected.

Coverage bugs can also freeze bindings to historical Guided claim IDs. The bridge appends the corresponding epistemic
downgrade and queues dependent claims for re-examination without editing prior evidence. Because external experiment
and Guided memory records live in separate tenant-scoped databases, the projection has an explicit idempotent retry
operation keyed by the coverage-bug ID and exact replacement protocol hash.

## Next implementation slice

Project the same epistemic contract into remaining non-Guided claim writers and add a dedicated browser workflow for
coverage-bug history and re-examination work.
