# Prospective blind calibration

Orbita's blind-calibration route is a domain-neutral governed evaluation protocol. It separates prediction from gold
reveal so a model cannot inspect answers, rewrite its predictions, or change the scoring contract after seeing results.

## Required plan contract

An approved plan must contain exactly one candidate with kind `prospective_blind_calibration`. The candidate declares:

- `blind_event_id_field`: unique row identifier; defaults to `blind_event_id`;
- `visible_fields`: fields the predictor may inspect;
- `scoring_key_fields`: gold, resolution, and unresolved-holdout fields forbidden from the blind input;
- `allowed_hypotheses`: frozen structured prediction vocabulary;
- `allowed_epistemic_labels`: frozen primary-label vocabulary;
- `allowed_evidence_classes`: vocabulary for requested follow-up evidence;
- `forbidden_outputs`: domain-policy exclusions, including UAP-specific exclusions when applicable;
- `expected_row_count`: optional exact row-count commitment;
- `prediction_provider.kind`: `external_submission` or `deterministic_rules`; and
- `scoring_schema`: gold ID, primary-label, and optional acceptable-hypotheses fields.

The UAP policy may forbid `NHI`, `EXOTIC`, `ANOMALOUS_PHYSICS`, and `REPRODUCIBLE_RESIDUAL`. These are plan-level
constraints rather than permanent cross-domain restrictions.

## State sequence

```text
approved plan
  -> frozen blind protocol
  -> sanitized batch access
  -> immutable prediction freeze
  -> separately sealed scoring key
  -> exact-hash reveal approval
  -> immutable score receipt
```

Orbita rejects mixed blind/statistical plans, duplicate or missing event IDs, absent row materialization, scoring fields
inside the blind artifact, vocabulary violations, forbidden outputs, incomplete predictions, early key sealing, early
scoring, stale hashes, and repeat mutation of any frozen artifact.

## Provider boundary

Orbita governs predictions; it does not pretend to semantically generate them without a predictor. An LLM can retrieve
the sanitized batch through MCP, reason over visible fields, and submit structured predictions. A model-independent
rules engine or human review process can submit through the same schema. The receipt records the declared provider,
model/ruleset identity, and any response identifier supplied by the caller.

## Scoring

After reveal approval, Orbita reports primary-label accuracy, mean Brier error using the frozen confidence, optional
hypothesis hit rate, and row-level comparisons. These are calibration measurements inside the declared benchmark—not
proof of causality, novelty, or the truth of any explanation.
