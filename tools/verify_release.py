#!/usr/bin/env python3
"""Verify the release manifest produced for the source bundle."""

from __future__ import annotations

import hashlib
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "MANIFEST.sha256"
    if not manifest.exists():
        raise SystemExit("MANIFEST.sha256 is missing")
    checked = 0
    errors: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split("  ", 1)
        path = root / relative
        if not path.is_file():
            errors.append(f"missing: {relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"hash mismatch: {relative}")
        checked += 1
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"OK: {checked} files verified")


if __name__ == "__main__":
    main()
