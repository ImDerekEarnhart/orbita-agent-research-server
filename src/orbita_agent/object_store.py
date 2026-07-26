"""Where large archives actually live.

A Railway volume is block storage: a virtual hard disk sized once, in advance, shared by
every tenant. That is the most expensive way to hold a 900 MB archive nobody reads for
weeks, and it fails badly — a full volume does not merely refuse the next upload, it
breaks the service for everyone. At 4.6 GB the ceiling arrives at roughly three users.

Archives belong in object storage, which has no fixed size, charges by what is used, and
cannot be filled. Once they move, the volume holds only databases and the search index,
which today is twelve megabytes.

Two implementations sit behind one interface. The local one keeps tests fast and offline
and keeps single-operator deployments working with no accounts to create. The R2 one is
what production uses. Nothing above this module knows which is in play.

Keys are namespaced by tenant. That is defence in depth rather than the isolation itself
— tenants already have separate databases, so a key is never constructed from another
tenant's identifier — but a bucket listing should still be legible about who owns what.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Iterator, Protocol


class ObjectStoreError(RuntimeError):
    pass


def object_key(tenant: str | None, case_id: str, file_id: str, filename: str) -> str:
    """Build the storage key for one uploaded file.

    The final segment is taken after splitting on both separators, so a name carrying a
    path is reduced to its last component on any platform. Leading dots are then dropped:
    a key containing `..` is caught downstream by the containment check, but it should
    never be constructed in the first place.
    """
    scope = tenant or "operator"
    last = filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe = "".join(ch for ch in last if ch.isalnum() or ch in "._-").lstrip(".") or "upload"
    return f"tenants/{scope}/cases/{case_id}/{file_id}/{safe}"


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    backend: str

    def public(self) -> dict[str, Any]:
        return {"key": self.key, "size_bytes": self.size_bytes, "backend": self.backend}


class ObjectStore(Protocol):
    backend: str

    def put_stream(self, key: str, chunks: Iterator[bytes], *, max_bytes: int) -> StoredObject: ...
    def open(self, key: str) -> IO[bytes]: ...
    def delete(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def exists(self, key: str) -> bool: ...
    def total_bytes(self, prefix: str) -> int: ...


class LocalObjectStore:
    """Filesystem-backed store, for tests and single-operator deployments."""

    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # A key is data. Resolve it and confirm containment before touching the disk.
        candidate = (self.root / key).resolve()
        if self.root.resolve() not in candidate.parents:
            raise ObjectStoreError(f"key escapes the object store root: {key}")
        return candidate

    def put_stream(self, key: str, chunks: Iterator[bytes], *, max_bytes: int) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with path.open("wb") as handle:
                for chunk in chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise ObjectStoreError(f"object exceeded {max_bytes} bytes")
                    handle.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StoredObject(key=key, size_bytes=written, backend=self.backend)

    def open(self, key: str) -> IO[bytes]:
        path = self._path(key)
        if not path.is_file():
            raise ObjectStoreError(f"no such object: {key}")
        return path.open("rb")

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def delete_prefix(self, prefix: str) -> int:
        directory = self._path(prefix)
        if not directory.is_dir():
            return 0
        removed = sum(1 for item in directory.rglob("*") if item.is_file())
        shutil.rmtree(directory, ignore_errors=False)
        return removed

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def total_bytes(self, prefix: str) -> int:
        directory = self._path(prefix)
        if not directory.is_dir():
            return 0
        return sum(item.stat().st_size for item in directory.rglob("*") if item.is_file())


class R2ObjectStore:
    """Cloudflare R2 over its S3-compatible API.

    R2 is used rather than a bigger volume because it has no fixed size to exhaust and no
    egress charge for reading archives back, which is the operation this service performs
    most.
    """

    backend = "r2"

    def __init__(self, *, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ObjectStoreError(
                "boto3 is required for R2 storage; install the package extras"
            ) from exc

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
            region_name="auto",
        )

    def put_stream(self, key: str, chunks: Iterator[bytes], *, max_bytes: int) -> StoredObject:
        # Buffer to a temporary file rather than memory: an archive is far larger than
        # this process should ever hold at once, and the size ceiling must be enforced
        # while receiving rather than after.
        import tempfile

        written = 0
        with tempfile.TemporaryFile() as buffer:
            for chunk in chunks:
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    raise ObjectStoreError(f"object exceeded {max_bytes} bytes")
                buffer.write(chunk)
            buffer.seek(0)
            self._client.upload_fileobj(buffer, self.bucket, key)
        return StoredObject(key=key, size_bytes=written, backend=self.backend)

    def open(self, key: str) -> IO[bytes]:
        import tempfile

        handle = tempfile.TemporaryFile()
        try:
            self._client.download_fileobj(self.bucket, key, handle)
        except Exception as exc:  # noqa: BLE001 - surfaced as a store error
            handle.close()
            raise ObjectStoreError(f"could not read object {key}: {type(exc).__name__}") from exc
        handle.seek(0)
        return handle

    def delete(self, key: str) -> bool:
        if not self.exists(key):
            return False
        self._client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def _iter_keys(self, prefix: str) -> Iterator[dict[str, Any]]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            yield from page.get("Contents", []) or []

    def delete_prefix(self, prefix: str) -> int:
        removed = 0
        batch: list[dict[str, str]] = []
        for item in self._iter_keys(prefix):
            batch.append({"Key": item["Key"]})
            if len(batch) == 1000:
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})
                removed += len(batch)
                batch = []
        if batch:
            self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})
            removed += len(batch)
        return removed

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except Exception:  # noqa: BLE001 - any failure to head means "treat as absent"
            return False
        return True

    def total_bytes(self, prefix: str) -> int:
        return sum(int(item.get("Size", 0)) for item in self._iter_keys(prefix))


def build_object_store(local_root: Path) -> ObjectStore:
    """Return R2 when it is fully configured, otherwise the local store.

    Partial configuration is refused rather than silently falling back. A deployment that
    meant to use R2 and has a typo in one variable must not quietly start writing
    archives to a volume that cannot hold them.
    """
    settings = {
        name: os.getenv(name, "").strip()
        for name in (
            "ORBITA_R2_ENDPOINT",
            "ORBITA_R2_BUCKET",
            "ORBITA_R2_ACCESS_KEY_ID",
            "ORBITA_R2_SECRET_ACCESS_KEY",
        )
    }
    present = [name for name, value in settings.items() if value]
    if not present:
        return LocalObjectStore(local_root)
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise ObjectStoreError(
            "R2 storage is partially configured; these are missing: " + ", ".join(missing)
        )
    return R2ObjectStore(
        endpoint=settings["ORBITA_R2_ENDPOINT"],
        bucket=settings["ORBITA_R2_BUCKET"],
        access_key_id=settings["ORBITA_R2_ACCESS_KEY_ID"],
        secret_access_key=settings["ORBITA_R2_SECRET_ACCESS_KEY"],
    )
