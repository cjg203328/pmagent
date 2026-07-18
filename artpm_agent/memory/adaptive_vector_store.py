"""Enhanced vector store with adaptive indexing strategy.

Automatically selects optimal FAISS index type based on vector count:
- < 1K vectors: Flat index (exact search)
- 1K-100K: IVF index (10-50x faster)
- > 100K: HNSW index (best for large scale)
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class AdaptiveVectorStore:
    """Vector store with automatic index type selection."""

    # Thresholds for index type switching
    FLAT_THRESHOLD = 1000
    IVF_THRESHOLD = 100000

    def __init__(
        self,
        store_path: str | Path,
        *,
        dimension: int = 1536,
        embedding_fingerprint: str = "unspecified:1536",
        force_index_type: str | None = None
    ):
        """Initialize adaptive vector store.

        Args:
            store_path: Directory for index storage
            dimension: Vector dimension
            embedding_fingerprint: Embedding model identifier
            force_index_type: Force specific index type (flat/ivf/hnsw)
        """
        self.store_path = Path(store_path).expanduser().resolve()
        self.store_path.mkdir(parents=True, exist_ok=True)

        self.dimension = dimension
        self.embedding_fingerprint = embedding_fingerprint
        self.force_index_type = force_index_type

        self.index = None
        self.index_type: str | None = None
        self.metadata: dict[str, dict[str, Any]] = {}
        self.available = False

        self._load_or_create()

    def _select_index_type(self, count: int) -> str:
        """Select optimal index type based on vector count.

        Args:
            count: Number of vectors

        Returns:
            Index type: flat, ivf, or hnsw
        """
        if self.force_index_type:
            return self.force_index_type

        if count < self.FLAT_THRESHOLD:
            return "flat"
        elif count < self.IVF_THRESHOLD:
            return "ivf"
        else:
            return "hnsw"

    def _create_flat_index(self) -> Any:
        """Create flat (exact) index for small datasets.

        Returns:
            FAISS IndexFlatL2
        """
        import faiss
        index = faiss.IndexFlatL2(self.dimension)
        logger.info(f"Created flat index (dimension={self.dimension})")
        return index

    def _create_ivf_index(self, nlist: int | None = None) -> Any:
        """Create IVF index for medium-scale datasets.

        Args:
            nlist: Number of clusters (default: sqrt(count))

        Returns:
            FAISS IndexIVFFlat
        """
        import faiss

        if nlist is None:
            # Rule of thumb: sqrt(n) clusters for good balance
            current_count = self.count if self.index else 1000
            nlist = max(int(math.sqrt(current_count)), 8)

        quantizer = faiss.IndexFlatL2(self.dimension)
        index = faiss.IndexIVFFlat(
            quantizer,
            self.dimension,
            nlist,
            faiss.METRIC_L2
        )

        logger.info(
            f"Created IVF index (dimension={self.dimension}, nlist={nlist})"
        )
        return index

    def _create_hnsw_index(self, m: int = 32) -> Any:
        """Create HNSW index for large-scale datasets.

        Args:
            m: Number of connections per layer

        Returns:
            FAISS IndexHNSWFlat
        """
        import faiss
        index = faiss.IndexHNSWFlat(self.dimension, m)
        logger.info(
            f"Created HNSW index (dimension={self.dimension}, m={m})"
        )
        return index

    def _create_pq_index(
        self,
        m: int = 8,
        nbits: int = 8
    ) -> Any:
        """Create Product Quantization index for memory efficiency.

        Args:
            m: Number of sub-quantizers
            nbits: Bits per sub-quantizer

        Returns:
            FAISS IndexPQ
        """
        import faiss

        # Ensure dimension is divisible by m
        if self.dimension % m != 0:
            logger.warning(
                f"Dimension {self.dimension} not divisible by m={m}, "
                f"using m={self.dimension // 8}"
            )
            m = max(self.dimension // 8, 1)

        index = faiss.IndexPQ(self.dimension, m, nbits)
        logger.info(
            f"Created PQ index (dimension={self.dimension}, m={m}, nbits={nbits})"
        )
        return index

    def _load_or_create(self) -> None:
        """Load existing index or create new one."""
        index_file = self.store_path / "index.faiss"
        metadata_file = self.store_path / "metadata.json"
        manifest_file = self.store_path / "manifest.json"

        # Try loading existing index
        if index_file.exists() and manifest_file.exists():
            try:
                import faiss

                self.index = faiss.read_index(str(index_file))

                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)

                # Verify compatibility
                if manifest.get("dimension") != self.dimension:
                    logger.warning(
                        f"Dimension mismatch: expected {self.dimension}, "
                        f"got {manifest.get('dimension')}"
                    )
                    self._create_new_index()
                    return

                self.index_type = manifest.get("index_type", "flat")
                self.available = True

                if metadata_file.exists():
                    with open(metadata_file, "r", encoding="utf-8") as f:
                        self.metadata = json.load(f)

                logger.info(
                    f"Loaded {self.index_type} index with {self.count} vectors"
                )
                return

            except Exception as e:
                logger.warning(f"Failed to load index: {e}")

        # Create new index
        self._create_new_index()

    def _create_new_index(self) -> None:
        """Create new index based on current vector count."""
        count = len(self.metadata)
        desired_type = self._select_index_type(count)

        if desired_type == "flat":
            self.index = self._create_flat_index()
        elif desired_type == "ivf":
            self.index = self._create_ivf_index()
        elif desired_type == "hnsw":
            self.index = self._create_hnsw_index()
        else:
            logger.warning(f"Unknown index type: {desired_type}, using flat")
            self.index = self._create_flat_index()
            desired_type = "flat"

        self.index_type = desired_type
        self.available = True

    def _maybe_reindex(self) -> None:
        """Check if index type should be upgraded based on vector count."""
        current_count = self.count
        current_type = self.index_type or "flat"
        desired_type = self._select_index_type(current_count)

        if desired_type != current_type:
            logger.info(
                f"Upgrading index from {current_type} to {desired_type} "
                f"(count={current_count})"
            )

            # Save current vectors
            if current_count > 0:
                import faiss
                vectors = faiss.vector_to_array(self.index.reconstruct_n(0, current_count))
                vectors = vectors.reshape(current_count, self.dimension)
            else:
                vectors = None

            # Create new index
            if desired_type == "ivf":
                self.index = self._create_ivf_index()
            elif desired_type == "hnsw":
                self.index = self._create_hnsw_index()
            else:
                self.index = self._create_flat_index()

            self.index_type = desired_type

            # Re-add vectors if any
            if vectors is not None:
                # Train IVF index if needed
                if desired_type == "ivf" and not self.index.is_trained:
                    logger.info("Training IVF index...")
                    self.index.train(vectors)

                self.index.add(vectors)
                logger.info(f"Re-indexed {current_count} vectors")

            self.save()

    @property
    def count(self) -> int:
        """Get number of vectors in index."""
        if self.index is None:
            return 0
        return int(self.index.ntotal)

    def add(
        self,
        doc_id: str,
        vector: list[float] | np.ndarray,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """Add vector to index.

        Args:
            doc_id: Document identifier
            vector: Embedding vector
            metadata: Optional metadata
        """
        if not self.available or self.index is None:
            raise RuntimeError("Index not available")

        # Convert to numpy array
        if isinstance(vector, list):
            vector = np.array(vector, dtype=np.float32)
        elif not isinstance(vector, np.ndarray):
            raise TypeError("Vector must be list or numpy array")

        if vector.shape[-1] != self.dimension:
            raise ValueError(
                f"Vector dimension mismatch: expected {self.dimension}, "
                f"got {vector.shape[-1]}"
            )

        # Reshape if needed
        if vector.ndim == 1:
            vector = vector.reshape(1, -1)

        # Train IVF index if not trained
        if self.index_type == "ivf" and not self.index.is_trained:
            logger.info("Training IVF index with first batch...")
            # Need at least nlist vectors to train, accumulate if needed
            if self.count < self.index.nlist:
                logger.warning(
                    f"Not enough vectors to train IVF ({self.count} < {self.index.nlist}), "
                    "using flat index temporarily"
                )
            else:
                self.index.train(vector)

        # Add to index
        self.index.add(vector)

        # Store metadata
        self.metadata[doc_id] = metadata or {}

        # Check if reindexing needed (every 1000 additions)
        if self.count % 1000 == 0:
            self._maybe_reindex()

    def search(
        self,
        query: list[float] | np.ndarray,
        top_k: int = 5,
        *,
        nprobe: int | None = None
    ) -> list[tuple[str, float]]:
        """Search for similar vectors.

        Args:
            query: Query vector
            top_k: Number of results
            nprobe: Number of clusters to search (IVF only)

        Returns:
            List of (doc_id, distance) tuples
        """
        if not self.available or self.index is None or self.count == 0:
            return []

        # Convert query
        if isinstance(query, list):
            query = np.array(query, dtype=np.float32)

        if query.ndim == 1:
            query = query.reshape(1, -1)

        # Set nprobe for IVF index
        if self.index_type == "ivf" and nprobe is not None:
            self.index.nprobe = nprobe
        elif self.index_type == "ivf":
            # Default: search sqrt(nlist) clusters
            self.index.nprobe = max(int(math.sqrt(self.index.nlist)), 1)

        # Search
        distances, indices = self.index.search(query, min(top_k, self.count))

        # Convert to doc_ids
        doc_ids = list(self.metadata.keys())
        results = []

        for dist, idx in zip(distances[0], indices[0]):
            if idx >= 0 and idx < len(doc_ids):
                results.append((doc_ids[idx], float(dist)))

        return results

    def save(self) -> None:
        """Save index and metadata to disk."""
        if not self.available or self.index is None:
            logger.warning("Index not available, skipping save")
            return

        import faiss

        index_file = self.store_path / "index.faiss"
        metadata_file = self.store_path / "metadata.json"
        manifest_file = self.store_path / "manifest.json"

        # Save index
        faiss.write_index(self.index, str(index_file))

        # Save metadata
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

        # Save manifest
        manifest = {
            "dimension": self.dimension,
            "index_type": self.index_type,
            "count": self.count,
            "embedding_fingerprint": self.embedding_fingerprint
        }

        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        logger.info(f"Saved {self.index_type} index with {self.count} vectors")

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics.

        Returns:
            Dictionary with stats
        """
        stats = {
            "available": self.available,
            "count": self.count,
            "dimension": self.dimension,
            "index_type": self.index_type,
        }

        if self.index_type == "ivf" and hasattr(self.index, "nlist"):
            stats["nlist"] = self.index.nlist
            stats["nprobe"] = getattr(self.index, "nprobe", None)

        if self.index_type == "hnsw" and hasattr(self.index, "hnsw"):
            stats["m"] = self.index.hnsw.efConstruction

        return stats
