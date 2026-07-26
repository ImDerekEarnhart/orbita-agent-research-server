# Orbita Capability Map v1

This benchmark maps Orbita's demonstrated behavior beyond token compression.
It tests:

- governed state adjudication;
- contradiction recovery;
- proof-dependency invalidation and alternate-proof preservation;
- execution-receipt verification;
- replication and declared independence;
- temporal-scope separation;
- abstention on insufficient evidence;
- coverage-aware routing;
- exact determinism;
- context-order and irrelevant-distractor robustness;
- counterfactual sensitivity;
- audit-trace and hash integrity;
- invalid-input rejection;
- latency scaling;
- code-context selection across favorable and unfavorable prompt profiles.

The local benchmark makes no model or network calls. Prior GPT comparisons are
incorporated as separately labelled supporting evidence and are not counted as
new local trials.

Run:

```powershell
.venv\Scripts\python.exe -m benchmarks.orbita_capability_map_v1.run_capability_map `
  --out benchmarks\orbita_capability_map_v1\runs\capability-map-2026-07-26
```

The run directory contains the private task bundle, raw measurements,
machine-readable scorecard, human-readable capability map, and a SHA-256
manifest.
