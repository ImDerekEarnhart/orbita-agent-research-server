"""An accepted archive must have its contents parsed, not merely inventoried."""

from __future__ import annotations

import json
import zipfile

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


def test_the_unpacked_size_guard_still_refuses_a_bomb(tmp_path):
    source = _zip(tmp_path, "bomb.zip", {"huge.txt": "x" * 200_000})
    result = ArtifactIngestor(max_unpacked_bytes=1_000).ingest(source, tmp_path / "out")

    # The guard raises inside ingest, which records it rather than crashing the upload.
    assert result["parse_status"] in {"failed", "partially_parsed"}
    assert "safe unpacked-size limit" in result["error"]


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
