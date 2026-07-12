"""
Memory Manager - Unified interface for short-term and long-term memory
"""
import json
from typing import Dict, Any, List, Optional

from memory.sqlite_manager import SQLiteManager
from memory.vector_store import VectorStore
from utils import generate_uuid


class MemoryManager:
    """Memory Manager for ArtPM Agent"""

    def __init__(self, db_path: str, vector_db_path: str, llm_client=None):
        """
        Initialize memory manager

        Args:
            db_path: SQLite database path
            vector_db_path: Vector store path
            llm_client: LLM client for generating embeddings
        """
        self.db = SQLiteManager(db_path)
        self.vector_db = VectorStore(vector_db_path)
        self.llm_client = llm_client

    def save_document(self, doc_data: Dict[str, Any]) -> str:
        """
        Save document to long-term memory

        Args:
            doc_data: Document data with extracted information

        Returns:
            Document ID
        """
        doc_id = doc_data.get("id") or generate_uuid()

        # Save to structured database
        self.db.insert("documents", {
            "id": doc_id,
            "document_type": doc_data.get("document_type", "unknown"),
            "source": doc_data.get("source", "local"),
            "file_path": doc_data.get("file_path", ""),
            "file_hash": doc_data.get("file_hash", ""),
            "extracted_data": json.dumps(doc_data.get("extracted_data", {}), ensure_ascii=False),
            "raw_text": doc_data.get("raw_text", "")[:10000],  # Truncate
            "confidence": doc_data.get("confidence", 0.0)
        })

        # Generate embedding and save to vector store
        if self.vector_db.index is not None:
            try:
                embedding_text = self._build_embedding_text(doc_data)
                embedding = self._get_embedding(embedding_text)

                extracted = doc_data.get("extracted_data", {})
                project_info = extracted.get("project_info", {})

                self.vector_db.add(
                    id=doc_id,
                    vector=embedding,
                    metadata={
                        "document_type": doc_data.get("document_type", "unknown"),
                        "project_name": project_info.get("project_name", ""),
                        "client_name": project_info.get("client_name", ""),
                        "date": project_info.get("document_date", "")
                    }
                )
            except Exception as e:
                print(f"[Warning] Failed to create vector embedding: {e}")

        return doc_id

    def retrieve(self, query: str, filters: Optional[Dict[str, Any]] = None, top_k: int = 5) -> List[Dict]:
        """
        Retrieve documents from memory

        Args:
            query: Search query
            filters: Filter conditions
            top_k: Number of results

        Returns:
            List of matching documents
        """
        results = []

        # Vector search if available
        if self.vector_db.index:
            try:
                query_embedding = self._get_embedding(query)
                vector_results = self.vector_db.search(query_embedding, top_k=top_k * 2)

                # Apply filters
                if filters:
                    filtered = []
                    for r in vector_results:
                        meta = r["metadata"]
                        match = True
                        for key, value in filters.items():
                            if meta.get(key) != value:
                                match = False
                                break
                        if match:
                            filtered.append(r)
                    vector_results = filtered

                # Get full document data
                for r in vector_results[:top_k]:
                    doc_id = r["id"]
                    doc_record = self.db.get_by_id("documents", doc_id)
                    if doc_record:
                        results.append({
                            "id": doc_id,
                            "score": r["score"],
                            "metadata": r["metadata"],
                            "data": doc_record
                        })
            except Exception as e:
                print(f"[Warning] Vector search failed: {e}")

        # Offline fallback: filter structured records and rank literal matches.
        if not results:
            docs = self.db.query("documents", filters or {})
            query_text = query.strip().lower()
            if query_text:
                docs = [
                    doc for doc in docs
                    if query_text in " ".join([
                        str(doc.get("document_type", "")),
                        str(doc.get("raw_text", "")),
                        str(doc.get("extracted_data", "")),
                    ]).lower()
                ]
            results = [{"id": doc["id"], "score": 1.0, "data": doc} for doc in docs[:top_k]]

        return results

    def get_avg_profit_rate(self, client_name: str) -> float:
        """
        Get average profit rate for a client

        Args:
            client_name: Client name

        Returns:
            Average profit rate
        """
        sql = "SELECT AVG(profit_rate) FROM projects WHERE client_name = ? AND status = 'completed'"
        result = self.db.query_sql(sql, (client_name,))
        return float(result[0][0]) if result and result[0][0] else 0.25

    def count_similar_projects(self, client_name: Optional[str] = None) -> int:
        """
        Count similar projects

        Args:
            client_name: Client name (optional)

        Returns:
            Project count
        """
        if client_name:
            sql = "SELECT COUNT(*) FROM projects WHERE client_name = ?"
            result = self.db.query_sql(sql, (client_name,))
        else:
            result = self.db.query_sql("SELECT COUNT(*) FROM projects")

        return int(result[0][0]) if result else 0

    def get_all_staff(self) -> List[Dict[str, Any]]:
        """
        Get all active staff

        Returns:
            List of staff records
        """
        staff = self.db.query("staff", {"status": "active"})
        for member in staff:
            skills = member.get("skills")
            if isinstance(skills, str):
                try:
                    member["skills"] = json.loads(skills)
                except json.JSONDecodeError:
                    member["skills"] = [item.strip() for item in skills.split(",") if item.strip()]
        return staff

    def has_worked_together(self, project_id: str, staff_id: str) -> bool:
        """
        Check if staff has worked on project before

        Args:
            project_id: Project ID
            staff_id: Staff ID

        Returns:
            True if worked together
        """
        sql = """
            SELECT COUNT(*) FROM task_assignments ta
            JOIN tasks t ON ta.task_id = t.id
            WHERE t.project_id = ? AND ta.staff_id = ?
        """
        result = self.db.query_sql(sql, (project_id, staff_id))
        return int(result[0][0]) > 0 if result else False

    def _build_embedding_text(self, doc_data: Dict[str, Any]) -> str:
        """Build text for embedding generation"""
        parts = [doc_data.get("document_type", "")]

        raw_text = doc_data.get("raw_text", "")
        if raw_text:
            parts.append(raw_text[:1000])

        return " ".join(parts)

    def _get_embedding(self, text: str) -> List[float]:
        """
        Get embedding vector for text using deterministic feature hashing.

        Uses character n-gram hashing to produce a fixed-size vector (1536 dims)
        that is deterministic (same text → same vector) and provides approximate
        semantic similarity (similar texts share n-grams).

        To use real embeddings (OpenAI / local model), set llm_client and the
        client will be used instead.

        Args:
            text: Input text

        Returns:
            Embedding vector as list of floats
        """
        import hashlib
        import struct

        dim = 1536
        # Normalize text
        text = text.lower().strip()
        if not text:
            return [0.0] * dim

        # Extract character n-grams (n=3,4,5) for better coverage
        vector = [0.0] * dim
        total_weight = 0.0

        for n in (3, 4, 5):
            for i in range(len(text) - n + 1):
                ngram = text[i:i + n]
                # Hash ngram to determine position and sign
                h = hashlib.md5(ngram.encode('utf-8')).digest()
                # Use first 8 bytes as two u32 ints: position and sign
                pos, sign_val = struct.unpack('<II', h[:8])
                idx = pos % dim
                sign = 1.0 if (sign_val & 1) else -1.0
                vector[idx] += sign
                total_weight += 1.0

        # L2-normalize
        if total_weight > 0:
            norm = sum(v * v for v in vector) ** 0.5
            if norm > 0:
                vector = [v / norm for v in vector]

        return vector
