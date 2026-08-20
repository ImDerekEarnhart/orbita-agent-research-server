# Orbita local-adjudicator re-benchmark

Date: 2026-07-26

## Conditions

- **Control:** the previously saved `gpt-5.6-sol` direct responses. No model was called again.
- **Orbita:** fresh outputs from the deterministic local adjudicator over the identical public tasks.
- **Adjudication cost:** 0 input tokens, 0 output tokens, 0 model calls, and 0 network calls.

## Results

| Measure | Saved GPT-5.6 direct | Orbita local adjudicator |
|---|---:|---:|
| Tasks | 10 | 10 |
| Exact target-state accuracy | 12/13 (92.3%) | 13/13 (100%) |
| Overall score | 0.9915 | 1.0000 |
| Mean task score | 0.9667 | 1.0000 |
| Model tokens in this rerun | 0 | 0 |

Orbita correctly marked the post-recall drug-safety claim as `challenged`. The saved GPT-5.6 response called it
`retracted`, producing the control's only incorrect target state.

The paired mean task-score difference was `+0.0333` for Orbita, with a bootstrap 95% interval of `[0.0, 0.1]`.

## Plain-English verdict

Orbita won this zero-token rerun by one exact judgment. The result demonstrates that the deterministic adjudication
path works end to end and fixes the specific state-transition error made by the saved model response.

This is still a small, public development suite, and the confidence interval includes a tie. It is engineering
validation, not yet strong evidence of a general performance advantage. A release-grade claim needs a larger hidden
suite and fresh blinded model runs.

