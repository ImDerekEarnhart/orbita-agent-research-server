# GPT-5.6 alone vs GPT-5.6 with the live Orbita MCP

Date: 2026-07-26

## Conditions

- **Control:** `gpt-5.6-sol` received the public benchmark task and response schema with no tools.
- **Orbita MCP context:** the same model and tasks, with the production Orbita remote MCP attached. The model was
  required to call the read-only `orbita_case_context` tool for the benchmark case before each judgment.

This is a real remote-MCP comparison, but it is not the full Orbita runtime. The current MCP does not expose a
general epistemic-task adjudication operation, so this condition measures the value of live structured case context.

## Verified results

| Measure | GPT-5.6 alone | GPT-5.6 + Orbita MCP |
|---|---:|---:|
| Tasks | 10 | 10 |
| Exact target-state accuracy | 12/13 (92.3%) | 12/13 (92.3%) |
| Overall score | 0.9915 | 0.9915 |
| Mean task score | 0.9667 | 0.9667 |
| MCP calls | 0 | 10 |
| MCP call errors | 0 | 0 |
| Cumulative task latency | 32.09 s | 58.51 s |
| Input tokens | 5,018 | 26,348 |
| Output tokens | 1,750 | 2,574 |

Paired mean task-score difference: `0.0`; bootstrap 95% CI `[0.0, 0.0]`.

Suite, both runs, report, and artifacts passed hash verification. The MCP receipts contain ten tool-list events and
ten successful `orbita_case_context` calls.

## Plain-English verdict

Attaching Orbita's current read-only case context did not make GPT-5.6 more accurate on this small, easy benchmark.
It made the run slower and increased context tokens. That is a useful negative result: tool access by itself is not
governance, and merely reading an Orbita case is not the same as executing Orbita's evidence and dependency rules.

The headline follow-up should expose a bounded full-runtime adjudication interface and test longer stateful problems
where evidence is added, revoked, contradicted, and propagated through alternate proofs over multiple steps.
