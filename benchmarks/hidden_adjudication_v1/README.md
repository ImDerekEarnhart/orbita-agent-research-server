# Orbita Hidden Adjudication Benchmark v1

This benchmark generates a private, seeded holdout for Orbita's deterministic
epistemic adjudicator.

The default configuration contains 140 tasks and 160 target-state decisions:
20 tasks in each of the seven evaluation categories. Identifiers, ordering,
receipt details, proof labels, time scopes, and distractors vary by seed.

Generated runs live under `runs/` and are intentionally ignored by Git. Each
run contains:

- `public_tasks.json`: the exact gold-free bundle presented to the system;
- `private_suite.json`: the seed and gold labels used only by the scorer;
- `orbita-local-adjudicator.response.json`: the system output;
- `report.json`: scored metrics;
- `verification.json`: integrity checks for the suite, run, and report.

Run locally:

```powershell
.venv\Scripts\python.exe benchmarks\hidden_adjudication_v1\run_hidden_local.py `
  --out benchmarks\hidden_adjudication_v1\runs\<run-name>
```

Omit `--seed` for a newly generated private seed. Supplying a seed is useful
only for reproducing an existing private run.

The public bundle omits both the seed and the private suite hash because either
could leak information about procedurally generated gold labels.
