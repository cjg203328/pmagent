"""
Memory Manager - Unified interface for short-term and long-term memory
"""
from hashlib import sha256
import json
from math import isfinite
import re
from collections.abc import Mapping
from typing import Dict, Any, List, Optional

from artpm_agent.memory.embeddings import DeterministicEmbeddingProvider, EmbeddingProvider
from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.memory.vector_store import VectorStore
from artpm_agent.runtime.counters import increment_counter
from artpm_agent.tenancy import (
    TenantContext,
    TenantContextManager,
    WorkspaceAccessDenied,
)
from artpm_agent.utils import generate_uuid


class MemoryManager:
    """Memory Manager for ArtPM Agent"""

    DEFAULT_TENANT_ID = "local"
    DEFAULT_WORKSPACE_ID = "local-default"
    MAX_RETRIEVE_QUERY_CHARS = 4_000
    MAX_RETRIEVE_TOP_K = 100

    def __init__(
        self,
        db_path: str,
        vector_db_path: str,
        llm_client=None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        workspace_knowledge_store: Any = None,
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
        self.workspace_knowledge_store = workspace_knowledge_store
        self._sync_vector_index()

    def bind_workspace_knowledge_store(self, store: Any) -> None:
        """Route future document facts to the canonical workspace authority."""

        self.workspace_knowledge_store = store

    def save_document(
        self,
        doc_data: Dict[str, Any],
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        tenant_context: Optional[TenantContext] = None,
    ) -> str:
        """
        Save document to long-term memory

        Args:
            doc_data: Document data with extracted information
            workspace_id: Optional explicit workspace boundary. When a tenant
                context is active it must match that context.
            tenant_context: Optional server-owned tenant context.

        Returns:
            Document ID
        """
        if not isinstance(doc_data, dict):
            raise TypeError("doc_data must be a mapping")

        requested_document_workspace = doc_data.get("workspace_id")
        requested_document_tenant = doc_data.get("tenant_id")
        if (
            requested_document_workspace is not None
            and not str(requested_document_workspace).strip()
        ):
            requested_document_workspace = None
        if workspace_id is not None:
            if (
                requested_document_workspace is not None
                and str(requested_document_workspace).strip()
                != str(workspace_id).strip()
            ):
                raise WorkspaceAccessDenied("workspace_id values conflict")
            requested_document_workspace = workspace_id
        if tenant_id is not None:
            if (
                requested_document_tenant is not None
                and str(requested_document_tenant).strip()
                != str(tenant_id).strip()
            ):
                raise WorkspaceAccessDenied("tenant_id values conflict")
            requested_document_tenant = tenant_id

        current_context = TenantContextManager.get_current()
        if (
            tenant_context is not None
            and current_context is not None
            and (
                tenant_context.tenant_id != current_context.tenant_id
                or tenant_context.workspace_id != current_context.workspace_id
            )
        ):
            raise WorkspaceAccessDenied("tenant contexts conflict")
        context = tenant_context or current_context
        if context is not None:
            if not isinstance(context, TenantContext):
                raise TypeError("tenant_context must be a TenantContext")
            resolved_workspace = context.require_workspace(
                requested_document_workspace
            )
            if (
                requested_document_tenant is not None
                and str(requested_document_tenant).strip()
                != context.tenant_id
            ):
                raise WorkspaceAccessDenied("tenant_id values conflict")
            resolved_tenant = context.tenant_id
        else:
            resolved_tenant = str(
                requested_document_tenant or self.DEFAULT_TENANT_ID
            ).strip()
            if not resolved_tenant:
                resolved_tenant = self.DEFAULT_TENANT_ID
            resolved_workspace = str(
                requested_document_workspace or self.DEFAULT_WORKSPACE_ID
            ).strip()
            if not resolved_workspace:
                resolved_workspace = self.DEFAULT_WORKSPACE_ID

        # Keep one trusted copy for every persistence backend. This prevents a
        # caller-provided workspace value from diverging between SQLite and the
        # vector metadata after the boundary has been resolved.
        document = dict(doc_data)
        document["tenant_id"] = resolved_tenant
        document["workspace_id"] = resolved_workspace
        doc_id = document.get("id") or generate_uuid()
        increment_counter("legacy.memory_manager.save_document")

        knowledge_store = self.workspace_knowledge_store
        if knowledge_store is not None:
            resource = knowledge_store.ingest_resource(
                resource_id=str(doc_id),
                title=str(
                    document.get("title")
                    or document.get("file_name")
                    or doc_id
                ),
                searchable_text=str(document.get("raw_text") or ""),
                resource_type=str(document.get("document_type") or "document"),
                source_type=str(document.get("source") or "memory-manager-facade"),
                source_uri=str(document.get("file_path") or "") or None,
                source_id=str(doc_id),
                structured_data=document.get("extracted_data", {}),
                metadata={"compatibility_facade": "MemoryManager.save_document"},
                tenant_id=resolved_tenant,
                workspace_id=resolved_workspace,
                created_by="memory-manager-facade",
            )
            return str(resource["id"])

        # Save to structured database
        self.db.insert("documents", {
            "id": doc_id,
            "tenant_id": resolved_tenant,
            "workspace_id": resolved_workspace,
            "document_type": document.get("document_type", "unknown"),
            "source": document.get("source", "local"),
            "file_path": document.get("file_path", ""),
            "file_hash": document.get("file_hash", ""),
            "extracted_data": json.dumps(document.get("extracted_data", {}), ensure_ascii=False),
            "raw_text": document.get("raw_text", "")[:10000],  # Truncate
            "confidence": document.get("confidence", 0.0)
        })

        # Generate embedding and save to vector store
        if self.vector_db.available:
            try:
                embedding_text = self._build_embedding_text(document)
                embedding = self._get_embedding(embedding_text)

                self.vector_db.add(
                    id=doc_id,
                    vector=embedding,
                    metadata=self._vector_metadata(document, embedding_text),
                )
            except Exception as e:
                print(f"[Warning] Failed to create vector embedding: {e}")

        return doc_id

    def retrieve(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        tenant_context: Optional[TenantContext] = None,
    ) -> List[Dict]:
        """
        Retrieve documents from memory

        Args:
            query: Search query
            filters: Filter conditions
            top_k: Number of results

        Returns:
            List of matching documents
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        query = query.strip()
        if len(query) > self.MAX_RETRIEVE_QUERY_CHARS:
            raise ValueError(
                "query cannot exceed "
                f"{self.MAX_RETRIEVE_QUERY_CHARS} characters"
            )
        if (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= self.MAX_RETRIEVE_TOP_K
        ):
            raise ValueError(
                "top_k must be between 1 and "
                f"{self.MAX_RETRIEVE_TOP_K}"
            )
        tenant_scope, scope, scoped_filters = self._resolve_retrieval_scope(
            filters,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            tenant_context=tenant_context,
        )
        authenticated_scope = tenant_context or TenantContextManager.get_current()
        if (
            authenticated_scope is not None
            and tenant_scope != self.DEFAULT_TENANT_ID
            and self._claim_legacy_scope(tenant_scope, scope)
        ):
            self._sync_vector_index()
        results = []

        # Vector search if available
        if self.vector_db.available:
            try:
                query_embedding = self._get_embedding(query)
                try:
                    vector_results = self.vector_db.search(
                        query_embedding,
                        top_k=top_k * 2,
                        filters=scoped_filters,
                    )
                except TypeError:
                    # Keep compatibility with injected vector stores that only
                    # implement the historical ``search(vector, top_k)`` API.
                    vector_results = self.vector_db.search(
                        query_embedding,
                        top_k=top_k * 2,
                    )

                # Apply filters again after the vector backend.  This protects
                # against stale/malformed metadata and keeps compatibility
                # backends from returning an unscoped row.
                filtered = []
                for r in vector_results or []:
                    if not isinstance(r, Mapping):
                        continue
                    meta = r.get("metadata") or {}
                    if not isinstance(meta, Mapping):
                        continue
                    if all(
                        meta.get(key) == value
                        for key, value in scoped_filters.items()
                    ):
                        raw_score = r.get("score")
                        if isinstance(raw_score, bool):
                            continue
                        try:
                            if not isfinite(float(raw_score)):
                                continue
                        except (TypeError, ValueError, OverflowError):
                            continue
                        if not r.get("id"):
                            continue
                        filtered.append(r)
                vector_results = filtered

                # Hydrate the ranked vector hits in one SQLite query. Keep a
                # fallback for injected legacy databases that only implement
                # the historical get_by_id() helper.
                vector_candidates = vector_results[:top_k]
                doc_ids = list(dict.fromkeys(item["id"] for item in vector_candidates))
                batch_get = getattr(self.db, "get_by_ids", None)
                if callable(batch_get):
                    hydrated = batch_get("documents", doc_ids)
                    documents_by_id = {
                        str(item.get("id")): item
                        for item in hydrated
                        if isinstance(item, dict) and item.get("id") is not None
                    }
                else:
                    documents_by_id = {
                        str(doc_id): self.db.get_by_id("documents", doc_id)
                        for doc_id in doc_ids
                    }

                for r in vector_candidates:
                    doc_id = r["id"]
                    doc_record = documents_by_id.get(str(doc_id))
                    if (
                        doc_record
                        and doc_record.get("tenant_id") == tenant_scope
                        and doc_record.get("workspace_id") == scope
                    ):
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
            docs = self.db.query("documents", scoped_filters)
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

    def _claim_legacy_scope(self, tenant_id: str, workspace_id: str) -> bool:
        """Bind unowned legacy documents on the first trusted workspace read."""
        if tenant_id == self.DEFAULT_TENANT_ID:
            return False
        with self.db.get_connection() as connection:
            explicit = connection.execute(
                """
                SELECT 1 FROM documents
                WHERE workspace_id = ? AND tenant_id NOT IN (?, ?)
                LIMIT 1
                """,
                (workspace_id, self.DEFAULT_TENANT_ID, tenant_id),
            ).fetchone()
            if explicit is not None:
                return False
            cursor = connection.execute(
                """
                UPDATE documents
                SET tenant_id = ?
                WHERE tenant_id = ? AND workspace_id = ?
                """,
                (tenant_id, self.DEFAULT_TENANT_ID, workspace_id),
            )
            return cursor.rowcount > 0

    def _resolve_retrieval_scope(
        self,
        filters: Optional[Dict[str, Any]],
        *,
        workspace_id: Optional[str],
        tenant_id: Optional[str],
        tenant_context: Optional[TenantContext],
    ) -> tuple[str, str, Dict[str, Any]]:
        """Resolve trusted tenant and workspace boundaries for retrieval.

        The old API accepted arbitrary filters and, when omitted, searched the
        entire shared memory database.  A request-scoped ``TenantContext`` is
        authoritative whenever one exists; explicit workspace values are only
        accepted when they match that context.  Calls outside a request retain
        the historical local workspace default.
        """

        requested_filters = dict(filters or {})
        requested_workspace = requested_filters.get("workspace_id")
        requested_tenant = requested_filters.get("tenant_id")
        if workspace_id is not None:
            if (
                requested_workspace is not None
                and str(requested_workspace).strip()
                and str(requested_workspace).strip() != str(workspace_id).strip()
            ):
                raise WorkspaceAccessDenied("workspace_id filters conflict")
            requested_workspace = workspace_id
        if tenant_id is not None:
            if (
                requested_tenant is not None
                and str(requested_tenant).strip()
                and str(requested_tenant).strip() != str(tenant_id).strip()
            ):
                raise WorkspaceAccessDenied("tenant_id filters conflict")
            requested_tenant = tenant_id

        current_context = TenantContextManager.get_current()
        if (
            tenant_context is not None
            and current_context is not None
            and (
                tenant_context.tenant_id != current_context.tenant_id
                or tenant_context.workspace_id != current_context.workspace_id
            )
        ):
            raise WorkspaceAccessDenied("tenant contexts conflict")
        context = tenant_context or current_context
        if context is not None:
            if not isinstance(context, TenantContext):
                raise TypeError("tenant_context must be a TenantContext")
            scope = context.require_workspace(requested_workspace)
            if (
                requested_tenant is not None
                and str(requested_tenant).strip()
                and str(requested_tenant).strip() != context.tenant_id
            ):
                raise WorkspaceAccessDenied("tenant_id filters conflict")
            tenant_scope = context.tenant_id
        else:
            tenant_scope = str(
                requested_tenant or self.DEFAULT_TENANT_ID
            ).strip()
            if not tenant_scope:
                tenant_scope = self.DEFAULT_TENANT_ID
            scope = str(requested_workspace or self.DEFAULT_WORKSPACE_ID).strip()
            if not scope:
                scope = self.DEFAULT_WORKSPACE_ID

        requested_filters["tenant_id"] = tenant_scope
        requested_filters["workspace_id"] = scope
        return tenant_scope, scope, requested_filters

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
            "tenant_id": str(doc_data.get("tenant_id") or "local"),
            "workspace_id": str(doc_data.get("workspace_id") or "local-default"),
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
