"""Persistent FAISS vector storage with compatibility and recovery checks."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
import time
from typing import Any, Iterable, Mapping, Optional
from uuid import uuid4

import numpy as np


_LOCKS_GUARD = Lock()
_STORE_LOCKS: dict[str, RLock] = {}


def _store_lock(path: Path) -> RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, RLock())


class VectorStore:
    """A small, durable FAISS cosine-similarity index.

    String document IDs are mapped to stable FAISS int64 IDs. The manifest
    records the embedding fingerprint and vector dimension so an incompatible
    index is never queried silently.
    """

    SCHEMA_VERSION = 2
    METRIC = "cosine"
    LOCK_TIMEOUT_SECONDS = 15.0
    STALE_LOCK_SECONDS = 120.0

    def __init__(
        self,
        store_path: str | Path,
        *,
        dimension: int = 1536,
        embedding_fingerprint: str = "unspecified:1536",
    ):
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise TypeError("dimension must be an integer")
        if dimension <= 0:
            raise ValueError("dimension must be positive")

        self.store_path = Path(store_path).expanduser().resolve()
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.index_file = self.store_path / "index.faiss"
        self.legacy_index_file = self.store_path / "index.bin"
        self.metadata_file = self.store_path / "metadata.json"
        self.manifest_file = self.store_path / "manifest.json"
        self.write_lock_file = self.store_path / "write.lock"
        self.dimension = dimension
        self.embedding_fingerprint = str(embedding_fingerprint or "unspecified")
        self.index = None
        self.metadata: dict[str, dict[str, Any]] = {}
        self.available = False
        self.needs_rebuild = False
        self.last_error: Optional[str] = None
        self._next_vector_id = 1
        self._generation = 0
        self._faiss = None
        self._lock = _store_lock(self.store_path)
        self._load_or_create()

    @property
    def count(self) -> int:
        return int(self.index.ntotal) if self.index is not None else 0

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "count": self.count,
            "dimension": self.dimension,
            "metric": self.METRIC,
            "embedding_fingerprint": self.embedding_fingerprint,
            "needs_rebuild": self.needs_rebuild,
            "last_error": self.last_error,
            "generation": self._generation,
            "path": str(self.store_path),
        }

    def _load_or_create(self) -> None:
        try:
            import faiss
        except (ImportError, OSError) as error:
            self.last_error = f"FAISS unavailable: {error}"
            self.index = None
            return

        self._faiss = faiss
        self.available = True
        with self._lock:
            with self._disk_write_lock():
                self._reload_from_disk()

    def _reload_from_disk(self) -> None:
        """Refresh this instance while the process and disk locks are held."""
        files_exist = any(
            path.exists()
            for path in (
                self.index_file,
                self.legacy_index_file,
                self.metadata_file,
                self.manifest_file,
            )
        )
        if not files_exist:
            self.index = self._new_index()
            self.metadata = {}
            self._next_vector_id = 1
            self._generation = 0
            return

        try:
            manifest = self._read_json(self.manifest_file)
            self._validate_manifest(manifest)
            metadata = self._read_json(self.metadata_file)
            if not isinstance(metadata, dict):
                raise ValueError("vector metadata must be an object")
            serialized = np.frombuffer(self.index_file.read_bytes(), dtype=np.uint8)
            index = self._faiss.deserialize_index(serialized)
            if int(index.d) != self.dimension:
                raise ValueError("FAISS index dimension does not match manifest")
            if not hasattr(index, "add_with_ids") or not hasattr(index, "remove_ids"):
                raise ValueError("legacy FAISS index type requires rebuild")
            if int(index.ntotal) != len(metadata):
                raise ValueError("FAISS index and metadata counts differ")
            self.index = index
            self.metadata = metadata
            self._next_vector_id = max(
                int(manifest.get("next_vector_id", 1)),
                max((int(key) for key in metadata), default=0) + 1,
            )
            self._generation = max(0, int(manifest.get("generation", 0)))
            self.needs_rebuild = False
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            self.last_error = f"Vector index rebuilt after compatibility check: {error}"
            self.needs_rebuild = True
            self._quarantine_files()
            self.index = self._new_index()
            self.metadata = {}
            self._next_vector_id = 1
            self._generation = 0

    def _new_index(self):
        if self._faiss is None:
            return None
        return self._faiss.IndexIDMap2(
            self._faiss.IndexFlatIP(self.dimension)
        )

    def _validate_manifest(self, manifest: Any) -> None:
        if not isinstance(manifest, dict):
            raise ValueError("vector manifest must be an object")
        expected = {
            "schema_version": self.SCHEMA_VERSION,
            "dimension": self.dimension,
            "metric": self.METRIC,
            "embedding_fingerprint": self.embedding_fingerprint,
        }
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise ValueError(
                    f"vector manifest {key} is {manifest.get(key)!r}, expected {value!r}"
                )

    def add(self, id: str, vector: list[float], metadata: Mapping[str, Any]) -> None:
        """Insert or replace one logical vector entry."""
        self.upsert(id=id, vector=vector, metadata=metadata)

    def upsert(
        self,
        *,
        id: str,
        vector: list[float],
        metadata: Mapping[str, Any],
    ) -> None:
        self.sync(
            [{"id": id, "vector": vector, "metadata": metadata}]
        )

    def sync(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        delete_ids: Iterable[str] = (),
    ) -> None:
        """Apply a batch of upserts and removals with one durable write."""
        if self.index is None:
            raise RuntimeError(self.last_error or "FAISS vector index is unavailable")
        prepared = []
        seen: set[str] = set()
        for item in entries:
            logical_id = str(item.get("id") or "").strip()
            if not logical_id:
                raise ValueError("vector entry id must be a non-empty string")
            if logical_id in seen:
                raise ValueError(f"duplicate vector entry id: {logical_id}")
            seen.add(logical_id)
            prepared.append(
                (
                    logical_id,
                    list(item.get("vector") or []),
                    self._json_mapping(item.get("metadata") or {}),
                )
            )
        vectors = (
            self._normalized_matrix([item[1] for item in prepared])
            if prepared
            else None
        )
        logical_ids_to_remove = {
            str(value) for value in delete_ids
        } | {item[0] for item in prepared}

        with self._lock, self._disk_write_lock():
            self._reload_from_disk()
            vector_ids_to_remove = [
                int(vector_id)
                for vector_id, entry in self.metadata.items()
                if entry.get("id") in logical_ids_to_remove
            ]
            if vector_ids_to_remove:
                self.index.remove_ids(
                    np.asarray(vector_ids_to_remove, dtype=np.int64)
                )
                for vector_id in vector_ids_to_remove:
                    self.metadata.pop(str(vector_id), None)

            if prepared and vectors is not None:
                vector_ids = np.arange(
                    self._next_vector_id,
                    self._next_vector_id + len(prepared),
                    dtype=np.int64,
                )
                self._next_vector_id += len(prepared)
                self.index.add_with_ids(vectors, vector_ids)
                for vector_id, (logical_id, _, metadata) in zip(
                    vector_ids, prepared
                ):
                    self.metadata[str(int(vector_id))] = {
                        "id": logical_id,
                        "metadata": metadata,
                    }
            if prepared or vector_ids_to_remove:
                self.needs_rebuild = False
                self._save()

    def replace(self, entries: Iterable[Mapping[str, Any]]) -> None:
        """Atomically replace the logical contents of the index."""
        if self.index is None:
            raise RuntimeError(self.last_error or "FAISS vector index is unavailable")
        prepared = []
        seen: set[str] = set()
        for item in entries:
            logical_id = str(item.get("id") or "").strip()
            if not logical_id:
                raise ValueError("vector entry id must be a non-empty string")
            if logical_id in seen:
                raise ValueError(f"duplicate vector entry id: {logical_id}")
            seen.add(logical_id)
            prepared.append(
                (
                    logical_id,
                    list(item.get("vector") or []),
                    self._json_mapping(item.get("metadata") or {}),
                )
            )

        vectors = self._normalized_matrix([item[1] for item in prepared]) if prepared else None
        with self._lock, self._disk_write_lock():
            self._reload_from_disk()
            self.index = self._new_index()
            self.metadata = {}
            self._next_vector_id = 1
            if prepared and vectors is not None:
                vector_ids = np.arange(1, len(prepared) + 1, dtype=np.int64)
                self.index.add_with_ids(vectors, vector_ids)
                for vector_id, (logical_id, _, metadata) in zip(vector_ids, prepared):
                    self.metadata[str(int(vector_id))] = {
                        "id": logical_id,
                        "metadata": metadata,
                    }
                self._next_vector_id = len(prepared) + 1
            self.needs_rebuild = False
            self.last_error = None
            self._save()

    def delete(self, ids: Iterable[str]) -> int:
        if self.index is None:
            return 0
        logical_ids = {str(value) for value in ids}
        with self._lock, self._disk_write_lock():
            self._reload_from_disk()
            vector_ids = [
                int(vector_id)
                for vector_id, entry in self.metadata.items()
                if entry.get("id") in logical_ids
            ]
            if not vector_ids:
                return 0
            self.index.remove_ids(np.asarray(vector_ids, dtype=np.int64))
            for vector_id in vector_ids:
                self.metadata.pop(str(vector_id), None)
            self._save()
            return len(vector_ids)

    def list_entries(self) -> list[dict[str, Any]]:
        with self._lock, self._disk_write_lock():
            self._reload_from_disk()
            return [
                {
                    "vector_id": int(vector_id),
                    "id": entry.get("id"),
                    "metadata": dict(entry.get("metadata") or {}),
                }
                for vector_id, entry in sorted(
                    self.metadata.items(), key=lambda item: int(item[0])
                )
            ]

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        *,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []
        vector = self._normalized_matrix([query_vector])
        with self._lock, self._disk_write_lock():
            self._reload_from_disk()
            if self.index is None or self.count == 0:
                return []
            # Metadata filters are evaluated after FAISS ranking. Search the whole
            # small local index so workspace isolation cannot discard valid hits.
            search_k = self.count if filters else min(top_k, self.count)
            distances, indices = self.index.search(vector, search_k)
            results = []
            for distance, vector_id in zip(distances[0], indices[0]):
                if int(vector_id) < 0:
                    continue
                entry = self.metadata.get(str(int(vector_id)))
                if not entry:
                    continue
                metadata = dict(entry.get("metadata") or {})
                if filters and not self._metadata_matches(metadata, filters):
                    continue
                results.append(
                    {
                        "id": entry.get("id"),
                        "score": float(distance),
                        "metadata": metadata,
                    }
                )
                if len(results) >= top_k:
                    break
            return results

    def clear(self) -> None:
        if self.index is None:
            return
        with self._lock, self._disk_write_lock():
            self._reload_from_disk()
            self.index = self._new_index()
            self.metadata = {}
            self._next_vector_id = 1
            self.needs_rebuild = False
            self._save()

    def _normalized_matrix(self, vectors: list[list[float]]) -> np.ndarray:
        if not vectors:
            return np.empty((0, self.dimension), dtype=np.float32)
        for vector in vectors:
            if len(vector) != self.dimension:
                raise ValueError(
                    f"Expected vector dimension {self.dimension}, got {len(vector)}"
                )
        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        np.divide(matrix, norms, out=matrix, where=norms > 0)
        return np.ascontiguousarray(matrix, dtype=np.float32)

    @staticmethod
    def _metadata_matches(
        metadata: Mapping[str, Any], filters: Mapping[str, Any]
    ) -> bool:
        for key, expected in filters.items():
            actual = metadata.get(key)
            if isinstance(expected, (set, frozenset, list, tuple)):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _json_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ValueError("vector metadata must be JSON serializable") from error
        if not isinstance(decoded, dict):
            raise ValueError("vector metadata must be an object")
        return decoded

    def _save(self) -> None:
        if self.index is None or self._faiss is None:
            return
        suffix = f".tmp-{os.getpid()}-{time.time_ns()}"
        index_temp = self.index_file.with_name(self.index_file.name + suffix)
        metadata_temp = self.metadata_file.with_name(self.metadata_file.name + suffix)
        manifest_temp = self.manifest_file.with_name(self.manifest_file.name + suffix)
        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "dimension": self.dimension,
            "metric": self.METRIC,
            "embedding_fingerprint": self.embedding_fingerprint,
            "count": self.count,
            "next_vector_id": self._next_vector_id,
            "generation": self._generation + 1,
        }
        try:
            serialized = self._faiss.serialize_index(self.index)
            with index_temp.open("wb") as stream:
                stream.write(serialized.tobytes())
            self._write_json(metadata_temp, self.metadata)
            self._write_json(manifest_temp, manifest)
            os.replace(index_temp, self.index_file)
            os.replace(metadata_temp, self.metadata_file)
            os.replace(manifest_temp, self.manifest_file)
            self._generation = int(manifest["generation"])
        finally:
            for path in (index_temp, metadata_temp, manifest_temp):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _quarantine_files(self) -> None:
        marker = f".incompatible-{time.time_ns()}"
        for path in (
            self.index_file,
            self.legacy_index_file,
            self.metadata_file,
            self.manifest_file,
        ):
            if not path.exists():
                continue
            try:
                os.replace(path, path.with_name(path.name + marker))
            except OSError:
                # A later atomic save will replace the unusable live file.
                pass

    @contextmanager
    def _disk_write_lock(self):
        """Coordinate index snapshots across application processes."""
        deadline = time.monotonic() + self.LOCK_TIMEOUT_SECONDS
        token = uuid4().hex
        payload = json.dumps(
            {"pid": os.getpid(), "token": token, "created_at": time.time()}
        ).encode("utf-8")
        while True:
            try:
                descriptor = os.open(
                    self.write_lock_file,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._disk_lock_is_stale():
                    try:
                        self.write_lock_file.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for vector index lock")
                time.sleep(0.05)
                continue
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
            break
        try:
            yield
        finally:
            try:
                current = self._read_json(self.write_lock_file)
                if current.get("token") == token:
                    self.write_lock_file.unlink(missing_ok=True)
            except (OSError, AttributeError, json.JSONDecodeError):
                pass

    def _disk_lock_is_stale(self) -> bool:
        try:
            payload = self._read_json(self.write_lock_file)
            pid = int(payload.get("pid", 0))
            created_at = float(payload.get("created_at", 0.0))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            try:
                return time.time() - self.write_lock_file.stat().st_mtime > 2.0
            except OSError:
                return False
        if time.time() - created_at > self.STALE_LOCK_SECONDS:
            return True
        if pid <= 0:
            return True
        if pid == os.getpid():
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OSError:
            return True
        return False

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        with path.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)


__all__ = ["VectorStore"]
