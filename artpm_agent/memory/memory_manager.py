"""
Memory Manager - Unified interface for short-term and long-term memory
"""
from hashlib import sha256
import json
import re
from typing import Dict, Any, List, Optional

from artpm_agent.memory.embeddings import DeterministicEmbeddingProvider, EmbeddingProvider
from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.memory.vector_store import VectorStore
from artpm_agent.utils import generate_uuid


class MemoryManager:
    """Memory Manager for ArtPM Agent"""

    def __init__(
        self,
        db_path: str,
        vector_db_path: str,
        llm_client=None,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        """
        Initialize memory manager

        Args:
            db_path: SQLite database path
            vector_db_path: Vector store path
            llm_client: LLM client for generating embeddings
        """
        self.embedding_provider = (
            embedding_provider or DeterministicEmbeddingProvider()
        )
        self.db = SQLiteManager(db_path)
        self.vector_db = VectorStore(
            vector_db_path,
            dimension=self.embedding_provider.dimension,
            embedding_fingerprint=self.embedding_provider.fingerprint,
        )
        self.llm_client = llm_client
        self._sync_vector_index()

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
        if self.vector_db.available:
            try:
                embedding_text = self._build_embedding_text(doc_data)
                embedding = self._get_embedding(embedding_text)

                self.vector_db.add(
                    id=doc_id,
                    vector=embedding,
                    metadata=self._vector_metadata(doc_data, embedding_text),
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
        if self.vector_db.available:
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
        # Keep the full phrase as the strongest signal, but also match
        # whitespace/punctuation-separated terms so a long conversational query
        # does not become an all-or-nothing substring lookup.
        if not results:
            docs = self.db.query("documents", filters or {})
            query_text = " ".join(str(query or "").split()).casefold()
            if query_text:
                terms = [
                    term.casefold()
                    for term in re.split(r"[\s,，。；;、:：/\\]+", query_text)
                    if len(term.strip()) >= 2
                ]
                if not terms:
                    terms = [query_text]
                ranked = []
                for doc in docs:
                    haystack = " ".join(
                        [
                            str(doc.get("document_type", "")),
                            str(doc.get("raw_text", "")),
                            str(doc.get("extracted_data", "")),
                        ]
                    ).casefold()
                    exact = 1.0 if query_text in haystack else 0.0
                    term_hits = sum(term in haystack for term in terms)
                    if not exact and term_hits == 0:
                        continue
                    score = exact + term_hits / max(1, len(terms))
                    ranked.append((score, doc))
                ranked.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
                docs = [doc for _score, doc in ranked]
            results = [
                {"id": doc["id"], "score": 1.0, "data": doc}
                for doc in docs[:top_k]
            ]

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
        parts = [
            str(doc_data.get("document_type", "")),
            str(doc_data.get("source", "")),
        ]

        raw_text = doc_data.get("raw_text", "")
        if raw_text:
            parts.append(str(raw_text)[:2000])

        extracted = doc_data.get("extracted_data", {})
        if isinstance(extracted, str):
            try:
                extracted = json.loads(extracted)
            except json.JSONDecodeError:
                extracted = extracted[:2000]
        if extracted:
            try:
                parts.append(json.dumps(extracted, ensure_ascii=False, sort_keys=True)[:2000])
            except (TypeError, ValueError):
                parts.append(str(extracted)[:2000])

        return " ".join(part for part in parts if part).strip()

    def _sync_vector_index(self) -> None:
        """Reconcile persisted documents after FAISS installation or rebuild."""
        if not self.vector_db.available:
            return
        current = {
            item["id"]: item["metadata"]
            for item in self.vector_db.list_entries()
        }
        documents = self.db.query("documents", {})
        specs = []
        for document in documents:
            embedding_text = self._build_embedding_text(document)
            specs.append(
                {
                    "id": document["id"],
                    "text": embedding_text,
                    "metadata": self._vector_metadata(document, embedding_text),
                }
            )
        desired = {item["id"]: item["metadata"] for item in specs}
        if self.vector_db.needs_rebuild:
            self.vector_db.replace(
                [
                    {
                        "id": item["id"],
                        "vector": self._get_embedding(item["text"]),
                        "metadata": item["metadata"],
                    }
                    for item in specs
                ]
            )
            return
        changed = [
            item for item in specs
            if current.get(item["id"]) != item["metadata"]
        ]
        removed_ids = set(current) - set(desired)
        if changed or removed_ids:
            self.vector_db.sync(
                [
                    {
                        "id": item["id"],
                        "vector": self._get_embedding(item["text"]),
                        "metadata": item["metadata"],
                    }
                    for item in changed
                ],
                delete_ids=removed_ids,
            )

    @staticmethod
    def _vector_metadata(
        doc_data: Dict[str, Any], embedding_text: str
    ) -> Dict[str, Any]:
        extracted = doc_data.get("extracted_data", {})
        if isinstance(extracted, str):
            try:
                extracted = json.loads(extracted)
            except json.JSONDecodeError:
                extracted = {}
        project_info = (
            extracted.get("project_info", {})
            if isinstance(extracted, dict)
            else {}
        )
        return {
            "document_type": doc_data.get("document_type", "unknown"),
            "project_name": project_info.get("project_name", ""),
            "client_name": project_info.get("client_name", ""),
            "date": project_info.get("document_date", ""),
            "embedding_hash": sha256(
                embedding_text.encode("utf-8")
            ).hexdigest(),
        }

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
        return self.embedding_provider.embed(text)
