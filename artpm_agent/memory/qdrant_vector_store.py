"""Network-native Qdrant vector storage adapter."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Optional
from uuid import NAMESPACE_URL, uuid5


class QdrantVectorStore:
    METRIC = "cosine"

    def __init__(
        self,
        *,
        url: str,
        collection: str,
        dimension: int,
        embedding_fingerprint: str,
        api_key: str | None = None,
        timeout: float = 5.0,
        client: Any = None,
    ) -> None:
        self.url = str(url).rstrip("/")
        self.collection = collection
        self.dimension = int(dimension)
        self.embedding_fingerprint = embedding_fingerprint
        self.last_error: Optional[str] = None
        self.needs_rebuild = False
        if client is None:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=self.url, api_key=api_key or None, timeout=timeout)
        self.client = client
        self._ensure_collection()
        self.available = True

    @staticmethod
    def _point_id(logical_id: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"artpm:{logical_id}"))

    def _ensure_collection(self) -> None:
        from qdrant_client import models

        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=self.dimension,
                    distance=models.Distance.COSINE,
                ),
            )
            return
        info = self.client.get_collection(self.collection)
        vectors = info.config.params.vectors
        if int(vectors.size) != self.dimension:
            raise ValueError(
                f"Qdrant collection dimension is {vectors.size}, expected {self.dimension}"
            )

    @property
    def count(self) -> int:
        result = self.client.count(self.collection, exact=True)
        return int(result.count)

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "backend": "qdrant",
            "count": self.count,
            "dimension": self.dimension,
            "metric": self.METRIC,
            "embedding_fingerprint": self.embedding_fingerprint,
            "needs_rebuild": self.needs_rebuild,
            "last_error": self.last_error,
            "url": self.url,
            "collection": self.collection,
        }

    def add(self, id: str, vector: list[float], metadata: Mapping[str, Any]) -> None:
        self.upsert(id=id, vector=vector, metadata=metadata)

    def upsert(self, *, id: str, vector: list[float], metadata: Mapping[str, Any]) -> None:
        self.sync([{"id": id, "vector": vector, "metadata": metadata}])

    def sync(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        delete_ids: Iterable[str] = (),
    ) -> None:
        from qdrant_client import models

        prepared = list(entries)
        if prepared:
            points = []
            seen: set[str] = set()
            for item in prepared:
                logical_id = str(item.get("id") or "").strip()
                vector = list(item.get("vector") or [])
                if not logical_id or logical_id in seen:
                    raise ValueError("vector entry id must be unique and non-empty")
                seen.add(logical_id)
                if len(vector) != self.dimension:
                    raise ValueError("invalid Qdrant vector entry")
                metadata = json.loads(json.dumps(item.get("metadata") or {}))
                points.append(
                    models.PointStruct(
                        id=self._point_id(logical_id),
                        vector=vector,
                        payload={"logical_id": logical_id, "metadata": metadata},
                    )
                )
            self.client.upsert(self.collection, points=points, wait=True)
        self.delete(delete_ids)

    def replace(self, entries: Iterable[Mapping[str, Any]]) -> None:
        self.clear()
        self.sync(entries)

    def delete(self, ids: Iterable[str]) -> int:
        from qdrant_client import models

        point_ids = [self._point_id(str(value)) for value in ids]
        if not point_ids:
            return 0
        self.client.delete(
            self.collection,
            points_selector=models.PointIdsList(points=point_ids),
            wait=True,
        )
        return len(point_ids)

    def list_entries(self) -> list[dict[str, Any]]:
        entries = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                entries.append(
                    {
                        "vector_id": str(point.id),
                        "id": payload.get("logical_id"),
                        "metadata": dict(payload.get("metadata") or {}),
                    }
                )
            if offset is None:
                return entries

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        *,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        from qdrant_client import models

        conditions = []
        for key, expected in (filters or {}).items():
            conditions.append(
                models.FieldCondition(
                    key=f"metadata.{key}",
                    match=models.MatchAny(any=list(expected))
                    if isinstance(expected, (set, frozenset, list, tuple))
                    else models.MatchValue(value=expected),
                )
            )
        query_filter = models.Filter(must=conditions) if conditions else None
        response = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=query_filter,
            limit=max(0, int(top_k)),
            with_payload=True,
        )
        return [
            {
                "id": (point.payload or {}).get("logical_id"),
                "score": float(point.score),
                "metadata": dict((point.payload or {}).get("metadata") or {}),
            }
            for point in response.points
        ]

    def clear(self) -> None:
        from qdrant_client import models

        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(filter=models.Filter()),
            wait=True,
        )


__all__ = ["QdrantVectorStore"]
