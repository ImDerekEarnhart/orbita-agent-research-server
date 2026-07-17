#!/usr/bin/env python3
"""Build the curated, read-only research database shipped with the agent server."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE documents (
  id INTEGER PRIMARY KEY,
  doc_id TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source_bundle TEXT NOT NULL,
  source_path TEXT NOT NULL,
  sha256 TEXT NOT NULL
);
CREATE VIRTUAL TABLE documents_fts USING fts5(
  doc_id UNINDEXED,
  title,
  content,
  source_bundle UNINDEXED,
  source_path UNINDEXED,
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE claim_cards (
  claim_id TEXT PRIMARY KEY,
  claim_type TEXT,
  statement TEXT NOT NULL,
  status TEXT NOT NULL,
  dependencies_json TEXT NOT NULL,
  extra_json TEXT NOT NULL
);
CREATE INDEX idx_claim_cards_status ON claim_cards(status);
CREATE TABLE eg_runs (
  run_id INTEGER PRIMARY KEY,
  relative_path TEXT UNIQUE,
  schema_family TEXT,
  records INTEGER,
  survivors INTEGER,
  counterexamples INTEGER,
  inconclusive INTEGER,
  near_misses INTEGER,
  min_n INTEGER,
  max_n INTEGER,
  status_counts_json TEXT,
  source_counts_json TEXT,
  first_cycle_counts_json TEXT,
  first_trial INTEGER,
  last_trial INTEGER,
  interrupted_or_partial INTEGER,
  notes TEXT
);
CREATE TABLE eg_highlights (
  highlight_id INTEGER PRIMARY KEY,
  run_id INTEGER,
  line_number INTEGER,
  graph_fingerprint TEXT,
  highlight_type TEXT,
  status TEXT,
  source TEXT,
  n INTEGER,
  m INTEGER,
  min_degree INTEGER,
  cycle_rank INTEGER,
  density REAL,
  first_power_cycle INTEGER,
  trial INTEGER,
  seed INTEGER,
  cycle_json TEXT,
  edges_json TEXT,
  certificate_json TEXT
);
CREATE INDEX idx_eg_highlights_type_n ON eg_highlights(highlight_type, n DESC);
CREATE INDEX idx_eg_highlights_fingerprint ON eg_highlights(graph_fingerprint);
CREATE TABLE eg_claims (
  claim_id TEXT PRIMARY KEY,
  statement TEXT,
  epistemic_status TEXT,
  basis TEXT,
  caveat TEXT
);
"""


def _title(content: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
    return match.group(1).strip() if match else fallback.replace("_", " ")


def _iter_docs(vault: Path, eg_root: Path, lemma_root: Path, lean_root: Path) -> Iterable[tuple[str, Path, Path]]:
    vault_files = [
        vault / "START_HERE.md",
        vault / "README.md",
        vault / "ORBITA_HANDOFF.md",
        vault / "inventory" / "CLAIM_STATUS_MATRIX.md",
        vault / "validation" / "BUILD_AND_TEST_REPORT.md",
        *sorted((vault / "research").rglob("*.md")),
    ]
    eg_files = [
        eg_root / "00_START_HERE" / "START_HERE.md",
        *sorted((eg_root / "01_problem_and_method").glob("*.md")),
        eg_root / "03_results" / "RESULTS_SYNTHESIS.md",
        eg_root / "09_conversation_handoff" / "CONVERSATION_HANDOFF.md",
    ]
    groups = [
        ("math_vault", vault, vault_files),
        ("eg_research_db", eg_root, eg_files),
        ("lemma_miner", lemma_root, [lemma_root / "README.md"]),
        ("lean_checker", lean_root, [lean_root / "README.md"]),
    ]
    seen: set[Path] = set()
    for bundle, root, paths in groups:
        for path in paths:
            path = path.resolve()
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            yield bundle, root.resolve(), path


def build(args: argparse.Namespace) -> dict[str, object]:
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    conn = sqlite3.connect(output)
    conn.executescript(SCHEMA)

    doc_count = 0
    for bundle, root, path in _iter_docs(args.vault, args.eg_root, args.lemma_root, args.lean_root):
        content = path.read_text(encoding="utf-8-sig")
        source_path = path.relative_to(root).as_posix()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        doc_id = f"{bundle}:{digest[:16]}"
        title = _title(content, path.stem)
        conn.execute(
            "INSERT INTO documents(doc_id,title,content,source_bundle,source_path,sha256) VALUES (?,?,?,?,?,?)",
            (doc_id, title, content, bundle, source_path, digest),
        )
        conn.execute(
            "INSERT INTO documents_fts(doc_id,title,content,source_bundle,source_path) VALUES (?,?,?,?,?)",
            (doc_id, title, content, bundle, source_path),
        )
        doc_count += 1

    cards_path = args.vault / "research" / "08_orbita_integration" / "ORBITA_CLAIM_CARDS.jsonl"
    card_count = 0
    for line in cards_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        known = {"id", "type", "statement", "status", "dependencies"}
        extra = {key: value for key, value in item.items() if key not in known}
        conn.execute(
            "INSERT INTO claim_cards VALUES (?,?,?,?,?,?)",
            (
                item["id"],
                item.get("type"),
                item["statement"],
                item["status"],
                json.dumps(item.get("dependencies", []), sort_keys=True),
                json.dumps(extra, sort_keys=True),
            ),
        )
        card_count += 1

    source_db = args.eg_root / "07_database" / "erdos_gyarfas_research.sqlite"
    source = sqlite3.connect(f"file:{source_db.resolve()}?mode=ro", uri=True)
    for table, destination in (("runs", "eg_runs"), ("graph_highlights", "eg_highlights"), ("claims", "eg_claims")):
        rows = source.execute(f"SELECT * FROM {table}").fetchall()
        placeholders = ",".join("?" for _ in source.execute(f"PRAGMA table_info({table})").fetchall())
        conn.executemany(f"INSERT INTO {destination} VALUES ({placeholders})", rows)
    source.close()

    metadata = {
        "schema_version": "1",
        "product_version": "0.3.0",
        "curation_rule": "Curated documents and structured receipts only; raw conversation transcripts excluded.",
        "source_archives": "math_vault_v0.1.0; eg_research_db_2026-06-23; lemma_miner_v1; lean_checker",
    }
    conn.executemany("INSERT INTO metadata(key,value) VALUES (?,?)", sorted(metadata.items()))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.execute("VACUUM")
    counts = {
        "documents": doc_count,
        "claim_cards": card_count,
        "eg_runs": conn.execute("SELECT COUNT(*) FROM eg_runs").fetchone()[0],
        "eg_highlights": conn.execute("SELECT COUNT(*) FROM eg_highlights").fetchone()[0],
        "eg_claims": conn.execute("SELECT COUNT(*) FROM eg_claims").fetchone()[0],
    }
    conn.close()
    counts["sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    counts["bytes"] = output.stat().st_size
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--eg-root", type=Path, required=True)
    parser.add_argument("--lemma-root", type=Path, required=True)
    parser.add_argument("--lean-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
