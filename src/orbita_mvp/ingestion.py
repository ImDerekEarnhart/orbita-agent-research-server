from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import warnings
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from .chatgpt_export import looks_like_chatgpt_export, messages_to_frame, parse_conversations


class IngestionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "column_profiles": [],
        "duplicates": int(df.duplicated().sum()) if len(df) else 0,
    }
    for raw_name in df.columns:
        name = str(raw_name)
        s = df[raw_name]
        missing = int(s.isna().sum())
        unique = int(s.nunique(dropna=True))
        role = "measurement"
        kind = "text"
        stats: dict[str, Any] = {}
        numeric = pd.to_numeric(s, errors="coerce")
        numeric_fraction = float(numeric.notna().mean()) if len(s) else 0.0
        if numeric_fraction >= 0.9 and unique > 0:
            kind = "numeric"
            vals = numeric.dropna().astype(float)
            if len(vals):
                stats = {
                    "min": float(vals.min()),
                    "max": float(vals.max()),
                    "mean": float(vals.mean()),
                    "median": float(vals.median()),
                    "std": float(vals.std(ddof=1)) if len(vals) > 1 else 0.0,
                }
        else:
            normalized_hint = name.lower().replace(" ", "_")
            parsed_dt = None
            if any(token in normalized_hint for token in ("date", "time", "timestamp")):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    parsed_dt = pd.to_datetime(s, errors="coerce", utc=True)
            if parsed_dt is not None and len(s) and float(parsed_dt.notna().mean()) >= 0.9:
                kind = "datetime"
                role = "time"
            elif unique <= max(20, int(len(s) * 0.1)):
                kind = "categorical"
                role = "group_or_category"
                counts = s.astype(str).value_counts(dropna=False).head(12)
                stats = {"top_values": {str(k): int(v) for k, v in counts.items()}}
        normalized = name.lower().replace(" ", "_")
        if unique == len(s) and len(s) > 3:
            if any(token in normalized for token in ("id", "uuid", "subject", "patient", "sample")):
                role = "identifier"
        if any(token in normalized for token in ("date", "time", "timestamp")):
            role = "time"
        if any(token in normalized for token in ("label", "class", "group", "condition", "diagnosis", "treatment")):
            role = "group_or_category"
        profile["column_profiles"].append(
            {
                "name": name,
                "kind": kind,
                "inferred_role": role,
                "missing": missing,
                "missing_fraction": float(missing / len(s)) if len(s) else 0.0,
                "unique": unique,
                "numeric_fraction": numeric_fraction,
                "stats": stats,
            }
        )
    return profile


class ArtifactIngestor:
    """Preserve uploads, extract supported content, and create deterministic profiles."""

    # Suffixes never opened as an archive member. Nested archives are the classic zip
    # bomb shape, so they are preserved and inventoried but never expanded.
    NESTED_ARCHIVE_SUFFIXES = {".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
    TEXT_SUFFIXES = {".txt", ".md", ".py", ".r", ".tex", ".sh", ".sql", ".yaml", ".yml", ".toml", ".names"}
    PARSEABLE_MEMBER_SUFFIXES = {
        ".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".parquet",
        ".json", ".jsonl", ".ipynb", ".pdf", ".docx",
    } | TEXT_SUFFIXES

    def __init__(
        self,
        max_unpacked_bytes: int = 250_000_000,
        max_members_parsed: int = 500,
        max_member_bytes: int = 20_000_000,
    ):
        self.max_unpacked_bytes = max_unpacked_bytes
        self.max_members_parsed = max_members_parsed
        self.max_member_bytes = max_member_bytes

    def describe(self) -> dict[str, Any]:
        return {
            "zip_strategy": "complete_inventory_bounded_selective_parse",
            "max_extracted_working_set_bytes": self.max_unpacked_bytes,
            "max_members_parsed": self.max_members_parsed,
            "max_member_bytes": self.max_member_bytes,
            "priority": "research text and scripts, then supported tables smallest-first",
            "large_members": "inventoried with size and compressed size; not parsed automatically",
            "semantic_boundary": "inventory and deterministic profiles are not whole-archive semantic understanding",
        }

    def ingest(self, source: str | Path, destination_dir: str | Path) -> dict[str, Any]:
        source = Path(source)
        destination_dir = Path(destination_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        safe_name = source.name.replace("/", "_").replace("\\", "_")
        stored = destination_dir / safe_name
        if source.resolve() != stored.resolve():
            shutil.copy2(source, stored)
        suffix = stored.suffix.lower()
        media_type = mimetypes.guess_type(stored.name)[0] or "application/octet-stream"
        base = {
            "original_name": source.name,
            "stored_path": str(stored.resolve()),
            "media_type": media_type,
            "size_bytes": stored.stat().st_size,
            "sha256": sha256_file(stored),
            "parse_status": "preserved",
            "artifact_kind": "unknown",
            "profile": {},
            "extracted_path": None,
            "error": None,
        }
        try:
            if suffix in {".csv", ".tsv"}:
                df = pd.read_csv(stored, sep="\t" if suffix == ".tsv" else ",")
                return self._table_result(base, df, destination_dir, stored.stem)
            if suffix in {".xlsx", ".xlsm", ".xls"}:
                sheets = pd.read_excel(stored, sheet_name=None)
                if not sheets:
                    raise IngestionError("Workbook has no readable sheets")
                selected_name, df = max(sheets.items(), key=lambda item: len(item[1]))
                result = self._table_result(base, df, destination_dir, stored.stem)
                result["profile"]["selected_sheet"] = str(selected_name)
                result["profile"]["sheet_names"] = [str(x) for x in sheets]
                return result
            if suffix == ".parquet":
                df = pd.read_parquet(stored)
                return self._table_result(base, df, destination_dir, stored.stem)
            if suffix == ".jsonl":
                rows = [json.loads(line) for line in stored.read_text(encoding="utf-8").splitlines() if line.strip()]
                if rows and all(isinstance(row, dict) for row in rows):
                    return self._table_result(base, pd.DataFrame(rows), destination_dir, stored.stem)
                return self._text_result(base, json.dumps(rows, indent=2), destination_dir, stored.stem)
            if suffix in {".json", ".ipynb"}:
                obj = json.loads(stored.read_text(encoding="utf-8"))
                # Recognised by shape rather than filename, so a renamed export still
                # parses and an unrelated conversations.json does not get misread.
                if looks_like_chatgpt_export(obj):
                    messages, summary = parse_conversations(obj)
                    result = self._table_result(
                        base, messages_to_frame(messages), destination_dir, stored.stem
                    )
                    result["artifact_kind"] = "chat_export"
                    result["profile"]["chat_export"] = asdict(summary)
                    return result
                if isinstance(obj, list) and obj and all(isinstance(row, dict) for row in obj):
                    return self._table_result(base, pd.DataFrame(obj), destination_dir, stored.stem)
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
                            result = self._table_result(base, pd.DataFrame(value), destination_dir, stored.stem)
                            result["profile"]["json_record_key"] = key
                            return result
                return self._text_result(base, json.dumps(obj, indent=2), destination_dir, stored.stem)
            if suffix in self.TEXT_SUFFIXES:
                return self._text_result(
                    base,
                    stored.read_text(encoding="utf-8", errors="replace"),
                    destination_dir,
                    stored.stem,
                )
            if suffix == ".pdf":
                from pypdf import PdfReader

                reader = PdfReader(str(stored))
                text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
                result = self._text_result(base, text, destination_dir, stored.stem)
                result["profile"]["pages"] = len(reader.pages)
                return result
            if suffix == ".docx":
                from docx import Document

                document = Document(str(stored))
                text = "\n".join(p.text for p in document.paragraphs)
                return self._text_result(base, text, destination_dir, stored.stem)
            if suffix == ".zip":
                return self._zip_result(base, stored, destination_dir)
        except Exception as exc:
            base["parse_status"] = "partially_parsed" if base["size_bytes"] else "failed"
            base["error"] = f"{type(exc).__name__}: {exc}"
            return base
        base["parse_status"] = "unsupported"
        base["error"] = "File preserved but no safe parser is registered for this format."
        return base

    def _table_result(self, base: dict[str, Any], df: pd.DataFrame, destination_dir: Path, stem: str) -> dict[str, Any]:
        normalized = destination_dir / f"{stem}.normalized.csv"
        df.to_csv(normalized, index=False)
        base.update(
            {
                "parse_status": "parsed",
                "artifact_kind": "table",
                "extracted_path": str(normalized.resolve()),
                "profile": profile_dataframe(df),
            }
        )
        return base

    def _text_result(self, base: dict[str, Any], text: str, destination_dir: Path, stem: str) -> dict[str, Any]:
        extracted = destination_dir / f"{stem}.extracted.txt"
        extracted.write_text(text, encoding="utf-8")
        words = text.split()
        base.update(
            {
                "parse_status": "parsed" if text.strip() else "partially_parsed",
                "artifact_kind": "text",
                "extracted_path": str(extracted.resolve()),
                "profile": {
                    "characters": len(text),
                    "words": len(words),
                    "lines": len(text.splitlines()),
                    "empty": not bool(text.strip()),
                },
            }
        )
        return base

    def _zip_result(self, base: dict[str, Any], stored: Path, destination_dir: Path) -> dict[str, Any]:
        extract_dir = destination_dir / f"{stored.stem}_unpacked"
        extract_dir.mkdir(parents=True, exist_ok=True)
        declared_total = 0
        extracted_total = 0
        members: list[dict[str, Any]] = []
        with zipfile.ZipFile(stored) as archive:
            infos = archive.infolist()
            seen_targets: set[Path] = set()
            def processing_priority(info: zipfile.ZipInfo) -> tuple[int, int, str]:
                suffix = Path(info.filename).suffix.lower()
                # Research declarations, scripts, manifests, and prose carry the
                # programme's logic and are cheap to inspect. Profile small tables next;
                # giant evidence matrices remain in the complete manifest for a later
                # targeted/sample pass instead of exhausting RAM during blind intake.
                kind = 0 if suffix in self.TEXT_SUFFIXES else 1 if suffix in self.PARSEABLE_MEMBER_SUFFIXES else 2
                return kind, int(info.file_size), info.filename.casefold()

            for info in sorted(infos, key=processing_priority):
                declared_total += info.file_size
                target = (extract_dir / info.filename).resolve()
                if extract_dir.resolve() not in target.parents and target != extract_dir.resolve():
                    raise IngestionError("ZIP contains an unsafe path")
                if target in seen_targets and not info.is_dir():
                    raise IngestionError("ZIP contains duplicate member paths")
                seen_targets.add(target)

            # Inventory every member, but extract only supported members that fit the
            # bounded working-set budget. This lets a multi-gigabyte archive be mapped
            # without inflating the whole thing onto a fixed-size service volume.
            parsed_count = 0
            skipped: list[dict[str, Any]] = []
            for member_index, info in enumerate(sorted(infos, key=processing_priority)):
                if info.is_dir():
                    continue
                original = Path(info.filename)
                suffix = original.suffix.lower()
                safe_stem = "".join(ch for ch in original.stem if ch.isalnum() or ch in "._-")[:60] or "member"
                name_hash = hashlib.sha256(info.filename.encode("utf-8")).hexdigest()[:12]
                # Flat, bounded working names avoid Windows path-length failures while
                # the complete original archive path remains in the manifest.
                path = (extract_dir / f"{member_index:04d}_{name_hash}_{safe_stem}{suffix[:16]}").resolve()
                size_bytes = int(info.file_size)
                entry: dict[str, Any] = {
                    "name": info.filename.replace("\\", "/"),
                    "size_bytes": size_bytes,
                    "compressed_bytes": int(info.compress_size),
                }
                unix_mode = (info.external_attr >> 16) & 0o170000
                if unix_mode == 0o120000:
                    entry["parse_status"] = "skipped"
                    entry["skip_reason"] = "symbolic links are inventoried but never extracted"
                elif info.flag_bits & 0x1:
                    entry["parse_status"] = "skipped"
                    entry["skip_reason"] = "encrypted members require a separately reviewed intake path"
                elif suffix in self.NESTED_ARCHIVE_SUFFIXES:
                    entry["parse_status"] = "skipped"
                    entry["skip_reason"] = "nested archives are preserved but never expanded"
                elif suffix not in self.PARSEABLE_MEMBER_SUFFIXES:
                    entry["parse_status"] = "skipped"
                    entry["skip_reason"] = "member type is inventoried but not parsed"
                elif size_bytes > self.max_member_bytes:
                    entry["parse_status"] = "skipped"
                    entry["skip_reason"] = f"member exceeds {self.max_member_bytes} bytes"
                elif parsed_count >= self.max_members_parsed:
                    entry["parse_status"] = "skipped"
                    entry["skip_reason"] = f"only the first {self.max_members_parsed} members are parsed"
                elif extracted_total + size_bytes > self.max_unpacked_bytes:
                    entry["parse_status"] = "skipped"
                    entry["skip_reason"] = (
                        f"parsing this member would exceed the {self.max_unpacked_bytes} byte working-set budget"
                    )
                else:
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, path.open("wb") as sink:
                            shutil.copyfileobj(source, sink, length=1024 * 1024)
                        extracted_total += size_bytes
                        member = self.ingest(path, path.parent)
                        parsed_count += 1
                        entry.update(
                            {
                                "parse_status": member["parse_status"],
                                "artifact_kind": member["artifact_kind"],
                                "sha256": member["sha256"],
                                "extracted_path": member["extracted_path"],
                                "profile": member["profile"],
                                "error": member["error"],
                            }
                        )
                    except Exception as exc:  # one inherited member must not erase the archive inventory
                        path.unlink(missing_ok=True)
                        entry["parse_status"] = "failed"
                        entry["error"] = f"{type(exc).__name__} while extracting or parsing this member"
                if entry["parse_status"] == "skipped":
                    skipped.append(entry)
                members.append(entry)

        manifest_path = destination_dir / f"{stored.stem}.archive-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "archive": stored.name,
                    "member_count": len(members),
                    "declared_unpacked_bytes": declared_total,
                    "extracted_working_set_bytes": extracted_total,
                    "members": members,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        tables = [m for m in members if m.get("artifact_kind") == "table"]
        base.update(
            {
                "parse_status": "parsed" if parsed_count else "partially_parsed",
                "artifact_kind": "archive",
                "extracted_path": str(extract_dir.resolve()),
                "profile": {
                    "members": members[:200],
                    "member_count": len(members),
                    "parsed_member_count": parsed_count,
                    "skipped_member_count": len(skipped),
                    "table_member_count": len(tables),
                    "declared_unpacked_bytes": declared_total,
                    "extracted_working_set_bytes": extracted_total,
                    "working_set_limit_bytes": self.max_unpacked_bytes,
                    "manifest_path": str(manifest_path.resolve()),
                    "members_truncated_in_profile": len(members) > 200,
                },
            }
        )
        return base
