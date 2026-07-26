"""Archives move off the volume, so the store has to be trustworthy about size and keys."""

from __future__ import annotations

import pytest

from orbita_agent.object_store import (
    LocalObjectStore,
    ObjectStoreError,
    build_object_store,
    object_key,
)

R2_VARS = (
    "ORBITA_R2_ENDPOINT",
    "ORBITA_R2_BUCKET",
    "ORBITA_R2_ACCESS_KEY_ID",
    "ORBITA_R2_SECRET_ACCESS_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in R2_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


def _chunks(*parts: bytes):
    return iter(parts)


def test_a_stored_object_round_trips(store):
    stored = store.put_stream("a/b/file.zip", _chunks(b"hello ", b"world"), max_bytes=1000)

    assert stored.size_bytes == 11
    assert stored.backend == "local"
    with store.open("a/b/file.zip") as handle:
        assert handle.read() == b"hello world"


def test_the_size_ceiling_is_enforced_while_writing(store):
    with pytest.raises(ObjectStoreError, match="exceeded"):
        store.put_stream("big.zip", _chunks(b"x" * 600, b"x" * 600), max_bytes=1000)

    # A refused upload must not leave a partial object behind.
    assert store.exists("big.zip") is False


def test_a_failed_write_leaves_nothing(store):
    def exploding():
        yield b"some bytes"
        raise RuntimeError("connection dropped")

    with pytest.raises(RuntimeError):
        store.put_stream("partial.zip", exploding(), max_bytes=1000)
    assert store.exists("partial.zip") is False


@pytest.mark.parametrize("key", ["../escape.zip", "a/../../escape.zip", "../../etc/passwd"])
def test_a_key_cannot_escape_the_store_root(store, key):
    with pytest.raises(ObjectStoreError, match="escapes"):
        store.put_stream(key, _chunks(b"x"), max_bytes=1000)


def test_deleting_an_object(store):
    store.put_stream("gone.zip", _chunks(b"data"), max_bytes=100)

    assert store.delete("gone.zip") is True
    assert store.exists("gone.zip") is False
    assert store.delete("gone.zip") is False


def test_deleting_a_whole_prefix(store):
    store.put_stream("tenants/alice/cases/c1/f1/a.zip", _chunks(b"aaa"), max_bytes=100)
    store.put_stream("tenants/alice/cases/c1/f2/b.zip", _chunks(b"bbb"), max_bytes=100)
    store.put_stream("tenants/bob/cases/c9/f1/c.zip", _chunks(b"ccc"), max_bytes=100)

    removed = store.delete_prefix("tenants/alice")

    assert removed == 2
    assert store.exists("tenants/bob/cases/c9/f1/c.zip") is True


def test_usage_is_measurable_per_prefix(store):
    store.put_stream("tenants/alice/x.zip", _chunks(b"a" * 100), max_bytes=1000)
    store.put_stream("tenants/alice/y.zip", _chunks(b"a" * 50), max_bytes=1000)
    store.put_stream("tenants/bob/z.zip", _chunks(b"a" * 999), max_bytes=1000)

    assert store.total_bytes("tenants/alice") == 150
    assert store.total_bytes("tenants/bob") == 999
    assert store.total_bytes("tenants/nobody") == 0


def test_reading_a_missing_object_is_an_error(store):
    with pytest.raises(ObjectStoreError, match="no such object"):
        store.open("never/written.zip")


# -- key construction ----------------------------------------------------------------


def test_keys_are_namespaced_by_tenant():
    key = object_key("dkscr711", "case_1", "file_1", "export.zip")
    assert key == "tenants/dkscr711/cases/case_1/file_1/export.zip"


def test_a_missing_tenant_becomes_the_operator():
    assert object_key(None, "c", "f", "a.zip").startswith("tenants/operator/")


@pytest.mark.parametrize(
    "hostile,expected",
    [
        ("../../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32", "system32"),
        ("....//....//x.zip", "x.zip"),
        ("...hidden.zip", "hidden.zip"),
        ("/absolute/path.zip", "path.zip"),
    ],
)
def test_a_hostile_filename_cannot_shape_the_key(hostile, expected):
    key = object_key("alice", "c", "f", hostile)

    assert ".." not in key
    assert key == f"tenants/alice/cases/c/f/{expected}"


def test_an_empty_filename_still_produces_a_key():
    assert object_key("alice", "c", "f", "///").endswith("/upload")


# -- selection -------------------------------------------------------------------------


def test_no_configuration_means_local(tmp_path):
    assert build_object_store(tmp_path).backend == "local"


def test_partial_r2_configuration_is_refused_rather_than_falling_back(tmp_path, monkeypatch):
    """A typo in one variable must not quietly send archives to a volume too small for them."""
    monkeypatch.setenv("ORBITA_R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setenv("ORBITA_R2_BUCKET", "orbita-archives")

    with pytest.raises(ObjectStoreError) as excinfo:
        build_object_store(tmp_path)

    assert "ORBITA_R2_ACCESS_KEY_ID" in str(excinfo.value)
    assert "ORBITA_R2_SECRET_ACCESS_KEY" in str(excinfo.value)
    # The refusal names what is missing and never echoes what was supplied.
    assert "example.r2.cloudflarestorage.com" not in str(excinfo.value)
