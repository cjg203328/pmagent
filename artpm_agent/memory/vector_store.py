"""
Vector Store for semantic search
"""
import json
from pathlib import Path
from typing import List, Dict, Any
import numpy as np


class VectorStore:
    """Simple vector store using FAISS"""

    def __init__(self, store_path: str):
        """
        Initialize vector store

        Args:
            store_path: Path to store directory
        """
        self.store_path = Path(store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)

        self.index_file = self.store_path / "index.bin"
        self.metadata_file = self.store_path / "metadata.json"

        self.index = None
        self.metadata = {}
        self.dimension = 1536  # Default for text-embedding-3-small

        self._load_or_create()

    def _load_or_create(self):
        """Load existing index or create new one"""
        try:
            import faiss

            if self.index_file.exists():
                self.index = faiss.read_index(str(self.index_file))
                if self.metadata_file.exists():
                    with open(self.metadata_file, 'r', encoding='utf-8') as f:
                        self.metadata = json.load(f)
            else:
                # Create new index
                self.index = faiss.IndexFlatL2(self.dimension)

        except ImportError:
            print("[Warning] FAISS not installed. Vector search disabled.")
            self.index = None

    def add(self, id: str, vector: List[float], metadata: Dict[str, Any]):
        """
        Add vector to index

        Args:
            id: Document ID
            vector: Embedding vector
            metadata: Document metadata
        """
        if self.index is None:
            return
        if len(vector) != self.dimension:
            raise ValueError(f"Expected vector dimension {self.dimension}, got {len(vector)}")


        # Convert to numpy array
        vec = np.array([vector], dtype=np.float32)

        # Add to index
        self.index.add(vec)

        # Store metadata
        idx = self.index.ntotal - 1
        self.metadata[str(idx)] = {
            "id": id,
            "metadata": metadata
        }

        # Save
        self._save()

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Search for similar vectors

        Args:
            query_vector: Query embedding vector
            top_k: Number of results to return

        Returns:
            List of results with scores
        """
        if self.index is None or self.index.ntotal == 0:
            return []
        if len(query_vector) != self.dimension:
            raise ValueError(f"Expected vector dimension {self.dimension}, got {len(query_vector)}")
        if top_k <= 0:
            return []

        # Convert to numpy array
        vec = np.array([query_vector], dtype=np.float32)

        # Search
        distances, indices = self.index.search(vec, min(top_k, self.index.ntotal))

        # Build results
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue

            meta = self.metadata.get(str(idx))
            if meta:
                results.append({
                    "id": meta["id"],
                    "score": float(1.0 / (1.0 + dist)),  # Convert distance to similarity
                    "metadata": meta["metadata"]
                })

        return results

    def _save(self):
        """Save index and metadata to disk"""
        if self.index is None:
            return

        import faiss

        faiss.write_index(self.index, str(self.index_file))

        with open(self.metadata_file, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

    def clear(self):
        """Clear all vectors"""
        if self.index:
            self.index.reset()
            self.metadata = {}
            self._save()
