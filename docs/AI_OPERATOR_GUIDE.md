# AI operator guide

Orbita is a research compiler and evidence governor. It is not permission for an agent to overstate a result.

## Operating contract

An AI may:

- inspect deterministic file profiles and existing belief history;
- propose bounded candidates using fields that actually exist;
- compile or submit an explicit plan;
- ask the human to review an immutable plan and hash;
- run an already approved plan;
- summarize survivors, refutations, limitations, and next tests;
- search curated historical research and finite receipts;
- export a concrete graph witness for independent Lean checking.
- propose or replay an allowlisted research-policy improvement;
- ask a human to approve one exact candidate/evaluation pair or an exact rollback.

An AI must not:

- invent fields, units, identifiers, or missing-value meanings;
- use confirmation results to rewrite the already-frozen candidate list;
- self-approve unless the user explicitly delegates that exact approval action;
- describe association as causation;
- treat a score as a p-value or truth probability;
- turn a finite survivor into a universal theorem;
- erase weakened claims or inconvenient counterexamples;
- bind the unauthenticated HTTP server to a public interface.
- claim a replay-eligible policy is objectively better without external evaluation;
- promote or roll back a policy without explicit authorization for that exact action;
- use self-improvement to edit code, execute commands, deploy, or add capabilities.

## Improvement operating contract

Read `orbita_improvement_status` first. Use completed cases that are representative of the intended workload. Review
the proposal patch, acceptance criteria, both metric sets, errors, invariants, and benchmark receipts. Treat
`eligible_for_review` as permission to consider activation, never as automatic approval. Show the exact hashes and
material metric changes to the user before invoking promotion. After activation, compile new plans normally; each
plan freezes the active policy receipt. Use rollback if subsequent evidence shows a regression.

## General improvement candidates

Use `orbita_governed_improvement_status` for the inactive generalized registry. Register a candidate only when its
observed limitation, base artifact, proposed artifact, risks, and expected benefit are concrete. Freeze the benchmark,
controls, metrics, gates, and anti-rescue rules before recording results. There is deliberately no generalized
promotion tool. Do not describe a registered or surviving code candidate as installed, active, or deployed.

## External experiment operating contract

Use the external-experiment route when preserving the scientific question requires an executable or verifier that the
built-in domain runner does not provide. Freeze only against an already approved Orbita plan. Show the human the exact
experiment hash and staged manifest hash before approval. Never approve on the user's behalf.

Treat `integrity_status=VERIFIED` as evidence that the exact frozen execution and receipt are intact. It is not evidence
that the hypothesis is true. Before scientific interpretation, attach the declared independent-verifier receipt. A
supporting verifier result yields at most `EMPIRICAL_SURVIVOR` in v1; untested regions and bounded scope remain visible.
An unavailable container engine is `EXECUTION_LIMIT`, not scientific refutation.

A reproduction is another separately approved execution, not a free rerun. Matching output hashes establish bitwise
technical reproducibility. Do not rewrite that result as independent scientific reproduction.

Record a coverage bug only when the missed counterexample is validated and carries an exact validation receipt hash
and validator identity. Use `refutes_claim` only when that counterexample actually invalidates the frozen claim scope;
otherwise use `challenges_coverage`. The replacement protocol is a new immutable version and does not repair or erase
the predecessor.

When executing the replacement protocol, resolution targets must exactly equal the coverage bug's affected-results
list. Do not add or remove targets after seeing the result. A coverage bug becomes `RESOLVED` only after the replacement
execution succeeds and every target has one disposition tied to that exact execution receipt. Resolution never restores
the old claim; a new supported claim requires a new lineage object and its own evidence.

## Result language

Prefer:

- “supported in this held-out confirmation split”;
- “survived the configured baseline, held-out, and cross-seed checks”;
- “refuted by the named falsifier”;
- “bounded search was inconclusive after reaching its state/time limit”;
- “the generated Lean source checks one concrete finite certificate.”
- “Orbita rendered a hash-bound finite-collision source artifact; it remains unproved until an independent Lean checker issues a matching receipt.”

Avoid:

- “proved true” for empirical claims;
- “AI discovered a law” without novelty review and replication;
- “no counterexample exists” when only a finite range was searched;
- “the score is 90% confidence” unless a calibrated confidence procedure actually produced that value.

## Compact prompt

```text
Operate Orbita as a cautious research compiler. Inspect case context first. Use only observed fields. Freeze candidates before confirmation scoring. Ask for explicit review before calling the hash-bound approval tool. After running, report survivors and refutations with their exact checks and scope. Preserve contradictions and superseded claims. Never describe a finite-data survivor as causality, novelty, or universal proof without independent warrant.
```

Before restating a claim more broadly, call `orbita_guard_claim_scope` with the evidence scope and proposed claim
scope. A rejected scope escalation is a policy boundary, not a wording suggestion. An execution hash shows what ran;
only an admitted formal-proof receipt can warrant `FORMALLY_PROVED`.

## Language-limit verification workflow

Use `orbita_discover_and_freeze_language_limit` with an existing case, a frozen L0 specification, and finite worlds.
Supply each world's `language_view` and numeric `outcome`, but do not supply a witness. Orbita discovers an exact
same-representation/different-target pair, creates the originating claim, and freezes the full JSON envelope. Then call
`orbita_lean_verify_language_limit` with its certificate hash. The checker regenerates Lean deterministically from the
stored JSON and refuses any changed certificate, changed case-file provenance, kernel mismatch, or unavailable checker.
Retrieve the receipt with `orbita_get_language_limit_verification`. `LEAN_VERIFIED_FINITE` means only the exact frozen
finite worlds were verified; it is never a universal theorem.

For the repair experiment, call `orbita_propose_language_refinement` with L0, O, and discovery worlds containing raw
`state` fields. Do not name the missing primitive. Orbita freezes its selected field projection and prediction. Test it
once on prospectively supplied worlds with `orbita_test_frozen_language_refinement`; a survivor requires the exact
finite inequality `Delta_L1(O) < Delta_L0(O)` and still carries no universal scope.

When a validated external coverage bug affects existing Guided claims, pass their exact IDs as `affected_claim_ids`.
Orbita will challenge or reject those claims according to `claim_effect` and queue dependent claims for review. If the
initial projection is interrupted, call `orbita_propagate_external_coverage_bug_to_claims` with the exact replacement
protocol hash; the operation is idempotent.

## Prospective blind calibration

Use `prospective_blind_calibration` only with a sanitized input artifact that contains no declared scoring-key or
unresolved-holdout fields. Freeze a nonempty scoring schema, output vocabularies, forbidden outputs, and one declared
prediction-provider kind in the plan. After plan approval, `orbita_run_discovery` prepares the blind protocol instead
of invoking the association engine. Retrieve visible rows with `orbita_get_blind_prediction_batch`, then submit one
schema-valid prediction per event through `orbita_freeze_blind_predictions`.

Do not request or supply gold data in the prediction conversation. Only after the prediction freeze exists may an
independent key custodian call `orbita_seal_blind_scoring_key`. Reveal and scoring require a separate exact-hash
approval through `orbita_approve_blind_reveal`. Never treat calibration accuracy as scientific proof or as evidence
for an explanation outside the frozen vocabulary.
