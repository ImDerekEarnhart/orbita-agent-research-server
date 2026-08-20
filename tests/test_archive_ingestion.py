"""An accepted archive must have its contents parsed, not merely inventoried."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from orbita_mvp.ingestion import ArtifactIngestor, IngestionError


def _zip(tmp_path, name: str, entries: dict[str, str]):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, content in entries.items():
            archive.writestr(member, content)
    return path


def test_archive_members_are_parsed_and_profiled(tmp_path):
    source = _zip(
        tmp_path,
        "export.zip",
        {
            "data/rows.csv": "x,y\n1,2\n3,4\n5,6\n",
            "notes/readme.md": "# Notes\n\nSome prose.\n",
            "conversations.json": json.dumps([{"id": "a", "text": "hello"}]),
        },
    )
    result = ArtifactIngestor().ingest(source, tmp_path / "out")

    assert result["artifact_kind"] == "archive"
    assert result["parse_status"] == "parsed"

    profile = result["profile"]
    assert profile["member_count"] == 3
    assert profile["parsed_member_count"] == 3

    by_name = {member["name"].replace("\\", "/"): member for member in profile["members"]}
    csv_member = by_name["data/rows.csv"]
    assert csv_member["artifact_kind"] == "table"
    assert csv_member["profile"]["rows"] == 3
    assert [column["name"] for column in csv_member["profile"]["column_profiles"]] == ["x", "y"]
    assert csv_member["sha256"]

    assert by_name["notes/readme.md"]["artifact_kind"] == "text"
    assert by_name["conversations.json"]["artifact_kind"] == "table"


def test_a_nested_archive_is_preserved_but_never_expanded(tmp_path):
    inner = _zip(tmp_path, "inner.zip", {"deep.csv": "a\n1\n"})
    source = _zip(
        tmp_path,
        "outer.zip",
        {"inner.zip": inner.read_bytes().decode("latin-1"), "top.csv": "a,b\n1,2\n3,4\n"},
    )
    result = ArtifactIngestor().ingest(source, tmp_path / "out")

    by_name = {member["name"].replace("\\", "/"): member for member in result["profile"]["members"]}
    assert by_name["inner.zip"]["parse_status"] == "skipped"
    assert "nested archives" in by_name["inner.zip"]["skip_reason"]
    assert by_name["top.csv"]["artifact_kind"] == "table"


def test_member_parsing_is_capped(tmp_path):
    source = _zip(tmp_path, "many.zip", {f"f{i}.csv": "a\n1\n" for i in range(12)})
    result = ArtifactIngestor(max_members_parsed=5).ingest(source, tmp_path / "out")

    profile = result["profile"]
    assert profile["parsed_member_count"] == 5
    assert profile["skipped_member_count"] == 7
    assert all(
        "only the first 5 members" in member["skip_reason"]
        for member in profile["members"]
        if member["parse_status"] == "skipped"
    )


def test_archive_budget_prioritizes_research_logic_before_bulk_tables(tmp_path):
    source = _zip(
        tmp_path,
        "programme.zip",
        {
            "bulk.csv": "a\n" + "1\n" * 5_000,
            "method.py": "# frozen method\nprint('audit')\n",
        },
    )
    result = ArtifactIngestor(max_members_parsed=1).ingest(source, tmp_path / "out")

    by_name = {member["name"]: member for member in result["profile"]["members"]}
    assert by_name["method.py"]["artifact_kind"] == "text"
    assert by_name["bulk.csv"]["parse_status"] == "skipped"


def test_long_inherited_paths_are_flattened_but_preserved_in_the_manifest(tmp_path):
    long_name = "/".join(["very_long_research_stage"] * 20) + "/method.py"
    source = _zip(tmp_path, "long.zip", {long_name: "print('preserved')\n"})
    result = ArtifactIngestor().ingest(source, tmp_path / "out")

    member = result["profile"]["members"][0]
    assert member["name"] == long_name
    assert Path(member["extracted_path"]).is_file()
    assert len(Path(member["extracted_path"]).name) < 120


def test_an_oversized_member_is_skipped_rather_than_parsed(tmp_path):
    source = _zip(tmp_path, "big.zip", {"big.csv": "a\n" + "1\n" * 5_000, "small.csv": "a\n1\n"})
    result = ArtifactIngestor(max_member_bytes=1_000).ingest(source, tmp_path / "out")

    by_name = {member["name"]: member for member in result["profile"]["members"]}
    assert by_name["big.csv"]["parse_status"] == "skipped"
    assert "exceeds" in by_name["big.csv"]["skip_reason"]
    assert by_name["small.csv"]["artifact_kind"] == "table"


def test_one_unparseable_member_does_not_fail_the_archive(tmp_path):
    source = _zip(
        tmp_path,
        "mixed.zip",
        {"good.csv": "a,b\n1,2\n3,4\n", "broken.json": "{not valid json"},
    )
    result = ArtifactIngestor().ingest(source, tmp_path / "out")

    by_name = {member["name"]: member for member in result["profile"]["members"]}
    assert by_name["good.csv"]["artifact_kind"] == "table"
    assert by_name["broken.json"]["parse_status"] in {"failed", "partially_parsed"}
    assert by_name["broken.json"]["error"]


def test_the_working_set_guard_inventories_but_never_inflates_a_bomb(tmp_path):
    source = _zip(tmp_path, "bomb.zip", {"huge.txt": "x" * 200_000})
    result = ArtifactIngestor(max_unpacked_bytes=1_000).ingest(source, tmp_path / "out")

    assert result["artifact_kind"] == "archive"
    assert result["profile"]["declared_unpacked_bytes"] == 200_000
    assert result["profile"]["extracted_working_set_bytes"] == 0
    assert result["profile"]["parsed_member_count"] == 0
    assert "working-set budget" in result["profile"]["members"][0]["skip_reason"]
    assert Path(result["profile"]["manifest_path"]).is_file()


def test_zip_slip_is_still_refused(tmp_path):
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escaped.csv", "a\n1\n")
    result = ArtifactIngestor().ingest(path, tmp_path / "out")

    assert result["parse_status"] in {"failed", "partially_parsed"}
    assert "unsafe path" in result["error"]
    assert not (tmp_path / "escaped.csv").exists()


def test_ingestion_error_is_importable():
    assert issubclass(IngestionError, RuntimeError)


@pytest.mark.parametrize("suffix", [".zip", ".gz", ".tar", ".7z"])
def test_every_nested_archive_suffix_is_refused_expansion(suffix):
    assert suffix in ArtifactIngestor.NESTED_ARCHIVE_SUFFIXES
