# Source provenance and selection

Build date: 2026-07-13.

## Selected sources

### `orbita_research_mvp_v0.1.0`

Used as the integration baseline because it already combines the v1.5 epistemic runtime and v0.2 discovery engine with research intake, frozen plans, memory, reports, and API tests. Its bundled discovery source is functionally identical to the standalone Discovery Kit source; duplicate build and wheel trees were excluded.

### `orbita_epistemic_runtime_v1.5`

Used through the MVP's integrated source. The MVP's `db.py` intentionally differs by enabling SQLite cross-thread access, WAL, busy timeout, and a 30-second connection timeout for the local FastAPI service. The broader desktop, coding, shell, scheduling, email, and browser capabilities remain in the source package for compatibility but are not registered as agent tools.

### `orbita_discovery_kit_v0.2.0`

Used through the exact source integrated into the MVP. The standalone archive's build tree, wheel, `__pycache__`, and generated package metadata were excluded.

### `orbita_math_discovery_research_vault_v0.1.0`

Curated research Markdown and `ORBITA_CLAIM_CARDS.jsonl` were indexed. Raw conversations, recovered attachments, and recovered code blocks were deliberately excluded from the agent-facing database.

### `derek_erdos_gyarfas_discovery_research_db_2026-06-23`

The `runs`, `graph_highlights`, and `claims` tables were copied into the curated read-only database. Raw JSONL files and duplicate snapshots were not bundled. Queries preserve warnings that the two fast runs repeat deterministic stages and that labeled hashes are not graph-isomorphism classes.

### `eg_lemma_miner_v1` and `erdos_gyarfas_lean_certificate_checker`

The lemma miner's tested graph functions were adapted behind strict limits. The Lean checker project is included under `lean/`; the agent exporter writes only concrete witness modules compatible with that checker.

## Integrity evidence

- Every supplied ZIP passed CRC testing.
- Supplied checksum verifiers passed for the MVP, runtime, math vault, and Erdős–Gyárfás database.
- The curated database is reproducible with `tools/build_knowledge.py` when the original archive roots are available.

## License note

The integrated Orbita MVP/runtime/discovery sources carried MIT licenses. The graph/Lean archives were supplied by the same named author but did not each include an explicit standalone license file. Confirm intended public redistribution terms before publishing this combined product outside the owner's private use.
