"""Build a content-addressed manifest for the Orbita capability benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PRIMARY_ARTIFACTS = (
    "CAPABILITY_REPORT.md",
    "capability_report.json",
    "summary.json",
    "report.json",
    "public_tasks.json",
    "compact_tasks.json",
    "compression_receipts.json",
    "gpt-full.response.json",
    "gpt-full.receipts.json",
    "gpt-compact.response.json",
    "gpt-compact.receipts.json",
    "private_suite.json",
)

SUPPORTING_SUMMARIES = (
    "benchmarks/hidden_adjudication_v1/runs/holdout-2026-07-26-02/summary.json",
    "benchmarks/hidden_adjudication_v1/runs/mixed-router-2026-07-26/summary.json",
    "benchmarks/hidden_adjudication_v1/runs/semantic-compression-2026-07-26/summary.json",
    "benchmarks/hidden_adjudication_v1/runs/coding-context-2026-07-26/summary.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(path: Path, repo_root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(repo_root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    run_dir = args.run_dir.resolve()
    paths = [run_dir / name for name in PRIMARY_ARTIFACTS]
    paths.extend(repo_root / name for name in SUPPORTING_SUMMARIES)

    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing release artifacts: {missing}")

    manifest = {
        "schema_version": "orbita-capability-release-manifest/1.0",
        "benchmark": "orbita-capability-v1",
        "run": run_dir.name,
        "artifacts": [_entry(path, repo_root) for path in paths],
    }
    destination = run_dir / "release_manifest.json"
    destination.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
