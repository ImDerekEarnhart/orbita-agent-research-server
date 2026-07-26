# Orbita Capability Benchmark v1

This benchmark maps where Orbita helps, ties, or hurts rather than reporting
only one aggregate score.

The new semantic stress partition crosses six domains with six prompt profiles:

- explicit evidence with high noise;
- multiple explicit evidence records with high noise;
- clean short context;
- paraphrased evidence linked through an alias record;
- diffuse evidence requiring several records;
- adversarial keyword distractors.

Each profile is evaluated with full-context GPT and independently sampled GPT
after Orbita's deterministic evidence compression. Results are reported by
domain and prompt profile, including evidence recall, accuracy, task score,
input tokens, total tokens, and latency.

The release report also incorporates the existing structured-adjudication,
coverage-routing, semantic-compression, and executable-coding pilots.

