# Calibration verdict: do not publish this as a superiority leaderboard

Date: 2026-07-26

The first empirical calibration used `gpt-5.6-sol` in two conditions:

1. direct schema-constrained judgment; and
2. the same model with a frozen Orbita epistemic policy card.

Both runs were imported, scored, and hash-verified by Orbita. Both produced:

- overall score: `0.9914529914529915`;
- exact target-state accuracy: `12/13` (`92.3%`);
- mean task score: `0.9666666666666666`; and
- paired mean task-score difference: `0.0`, bootstrap 95% CI `[0.0, 0.0]`.

The only exact-state discrepancy was the regulator-recall task. The sealed label was `challenged`; the direct condition
returned `retracted` and the policy-card condition returned `refuted`. Both correctly performed contradiction
recovery, so presenting this as a meaningful model failure would overstate the rubric.

## What the calibration establishes

- The benchmark runner executes real provider calls without exposing gold labels.
- Public target identifiers are now exposed without leaking target states.
- Correct provisional and rejected discoveries contribute to target-state accuracy.
- Suite, run, report, and artifact hashes verify.
- A policy card alone did not improve this model on this small transparent suite.

## Release thesis

**Prompting is not governance.**

Publish v1 as an open harness and challenge specification, not as evidence that Orbita beats a frontier model. The
headline benchmark should use a larger private partition with stateful evidence graphs, delayed contradictions,
alternate proofs, tool-receipt failures, temporal scopes, and replicated-discovery decisions. Compare:

- direct frontier model;
- retrieval or final-answer verifier;
- frozen policy prompt; and
- the full Orbita runtime.

The partition, gold labels, and scoring code should be committed only after provider responses are frozen. At least
one independent operator should reproduce the run before any superiority claim.

## Pending

- Anthropic baseline: not scored because the configured account had insufficient API credit.
- Full Orbita runtime adapter: required for the headline comparison.
- Hard private partition: required before public performance claims.
