# Unified Candidate Execution Layer

Orbita binds candidate semantics to an exact executor before a plan can receive human approval.

```text
Candidate plan
  -> ExecutorRegistry.resolve(candidate kinds)
  -> validate input, protocol, and required execution fields
  -> freeze ExecutorContract hash and input hash in the plan
  -> human reviews and approves the complete plan hash
  -> dispatch only to the bound executor
  -> append immutable CandidateExecutionReceipt
```

## Installed contracts

- `tabular-statistical/1` executes `linear_association` and `group_difference` through the statistical table engine.
- `prospective-blind-calibration/1` prepares or executes prediction-before-reveal calibration without scoring access.
- `structured-research-operator/1` converts one executable `research_operator` or `external_experiment` candidate into
  a frozen external experiment. It does not submit, approve, run, verify, or scientifically promote the experiment.

The registry reports formal theorem, graph, language-primitive, and execution-adapter candidate kinds as unavailable
until dedicated plan adapters exist. They are not routed to a "close enough" executor.

## Research operator contract

A research operator is executable only when its candidate freezes:

- scientific question;
- claim scope and quantifiers;
- inline-only, network-disabled OCI execution specification;
- verdict schema;
- required independent verifier;
- falsification coverage with known uncovered regions;
- anti-rescue rules.

If any field is missing or invalid, plan submission fails with `ENGINE_CAPABILITY_LIMIT`. No proposed or approved plan is
created, and the table executor is never called.

## Integrity boundaries

- The contract contains implementation and verifier hashes.
- The binding contains the selected input artifact ID, SHA-256, kind, candidate kinds, and protocol.
- A runtime implementation change invalidates old bindings; the plan must be recompiled and reapproved.
- Dispatch receipts are append-only and bind the plan hash, contract hash, binding hash, outcome, and result hash.
- Receipt verification recomputes both the result-reference hash and the outer receipt hash.
- Execution integrity is not scientific validity.
- Research-operator preparation preserves the existing separate external submission, human execution approval,
  independent verification, and claim-admission boundaries.
