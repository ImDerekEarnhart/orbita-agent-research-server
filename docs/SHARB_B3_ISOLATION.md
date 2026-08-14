# SHARB B3 per-packet isolation

This benchmark profile proves a fresh Orbita boot for every B3 packet without
opening the packet during setup. It preserves the frozen 5/3/3 comparison:

- B1: five model-only replicates.
- B2: three model-plus-Orbita replicates. Each replicate starts in a fresh,
  volume-backed namespace and may retain state only within that replicate.
- B3: three model-plus-Orbita replicates. Every packet gets a newly deployed
  container with no attached volume and empty home and cache directories.

B2 and B3 must use the same Git commit, image, model settings, runner prompt,
MCP tool catalog, tool budgets, and packet bytes. The external Guided/Genome
bridge is disabled in both profiles. The only intended difference is whether
Orbita state persists across packet boundaries.

## What the server proves

When `ORBITA_BENCHMARK_CONDITION` is `B2` or `B3`, Orbita refuses startup unless
all required isolation conditions hold. The check runs before `AgentGateway`
creates a database and verifies:

- explicit replicate and, for B3, packet identifiers;
- a new high-entropy boot nonce;
- exact expected and observed Git commits;
- bearer authentication without recording the credential;
- disabled external Guided/Genome bridge;
- empty Orbita home and cache namespaces;
- a volume-backed namespace for B2 or no Railway volume for B3; and
- zero research cases immediately after gateway initialization.

The service exposes the signed-by-hash administrative evidence at
`/benchmark-isolation`. It is deliberately not an MCP tool, so it does not
alter or disclose the experimental condition through the model-visible tool
catalog.

## Per-packet B3 procedure

The administrator runs `tools/sharb_isolation.py prepare-b3` with opaque labels
only. The command has no packet-path input and cannot read packet content. It:

1. rotates the boot nonce and writes only non-secret benchmark variables;
2. forces a fresh Railway deployment;
3. verifies the new deployment ID and the absence of a volume;
4. verifies the server's empty-boot attestation and SHA-256;
5. authenticates to `/mcp`, hashes the complete tool catalog; and
6. writes a chained local receipt marked `READY_FOR_ONE_PACKET`.

Only after the receipt succeeds may the independent evaluator open that one
packet in a fresh Responses API conversation. The next packet requires another
successful `prepare-b3` call with a new deployment and nonce. A failed or
restarted container invalidates that packet attempt and requires a new receipt.

No benchmark packet, answer key, scoring file, API credential, or private
chain-of-thought belongs in an isolation receipt.
