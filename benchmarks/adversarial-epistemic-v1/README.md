# Orbita Adversarial Epistemic Benchmark v1

This benchmark asks a practical question: when evidence changes or a tool fails, does an AI system update what it
claims—or merely produce a plausible final answer?

The ten-task launch suite measures:

- unsupported commitment;
- contradiction recovery;
- evidence-collapse propagation and alternate-proof preservation;
- false-success detection from execution receipts;
- independent-replication discipline; and
- temporal scoping.

The runner sends public tasks to real provider APIs without gold labels, imports schema-constrained responses into
Orbita, scores them, compiles paired bootstrap comparisons, and verifies suite, run, report, and artifact hashes.

## Honest interpretation boundary

The `direct` condition is a schema-constrained model baseline. The `orbita-policy` condition uses the same model plus
a frozen Orbita epistemic policy card. It is **not** the full Orbita runtime. The built-in synthetic fixtures are not
publication evidence and are excluded from this run.

The `orbita-mcp` condition attaches the live production Orbita remote MCP and requires a read-only
`orbita_case_context` call for each task. Its Responses API receipts preserve the MCP tool-list and tool-call trace.
This proves live MCP use, but still does not represent the full Orbita adjudication runtime.

This v1 suite is small and transparent after release. Treat it as a launch benchmark and harness demonstration, not
as a permanent contamination-resistant leaderboard. A larger private partition and independently operated runs are
required for durable external claims.

## Run

From the repository root:

```powershell
python benchmarks/adversarial-epistemic-v1/run_empirical.py
```

Required environment variables: `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`. The release artifacts contain provider
response IDs and token usage, never API keys.
