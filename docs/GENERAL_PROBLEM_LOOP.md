# Orbita General Problem Loop

The General Problem Loop turns an arbitrary objective into a governed, inspectable process. A frontier model can propose
representations, plans, diagnoses, and repairs, but Orbita owns the state machine and the evidence ledger.

```
GOAL -> REPRESENT -> PLAN -> ACT -> OBSERVE -> FALSIFY
                                             | survived
                                             v
                                      COMMIT/REFUSE -> COMPLETED
                                             ^
                                             |
                         DIAGNOSE -> REPAIR/LEARN -> RETRY
```

Every event is immutable and contains its artifact hash plus the previous event hash. Callers must submit the exact current
state and previous hash, preventing stale models or concurrent sessions from skipping or overwriting work.

## Authority boundaries

- The LLM proposes one current-stage artifact.
- Orbita validates the artifact and deterministically chooses the next state.
- `ACT` records receipts from separately governed executors; it does not execute arbitrary tools.
- A completed action requires at least one SHA-256 receipt.
- Observations may cite only hashes already introduced by prior action receipts.
- A `LANGUAGE_LIMIT` diagnosis requires a Language-Limit Certificate hash.
- Repairs require prospective predictions and risks and always remain inactive.
- Retry count is frozen when the loop is created.
- A commit requires exact evidence hashes. Refusal remains a valid terminal outcome.
- No loop stage can request runtime activation.

## What this demonstrates

It demonstrates durable, falsification-governed orchestration around a cognitive model. It does not demonstrate AGI, ASI,
autonomous tool use, or autonomous self-modification. Those require blind cross-domain benchmarks and independently
verified transfer, not merely the existence of the loop.
