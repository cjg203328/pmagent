from __future__ import annotations

from artpm_agent.harness.knowledge_handler import (
    _build_knowledge_ingestion_resources,
)
from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore


def test_mineru_result_survives_confirmed_knowledge_ingestion(tmp_path):
    class Agent:
        def process_document(self, _path, _prompt):
            return {
                "success": True,
                "document_type": "PDF document",
                "raw_text": "# Contract\n\nPayment term: net 30 days.",
                "markdown": "# Contract\n\nPayment term: net 30 days.",
                "confidence": None,
                "extracted_data": {
                    "page_count": 1,
                    "mineru": {
                        "integration_schema": "artpm-mineru-v1",
                        "runtime_version": "3.4.4",
                        "backend": "pipeline",
                        "summary": {
                            "page_count": 1,
                            "block_counts": {"text": 2},
                        },
                        "content_list": [
                            {
                                "type": "text",
                                "text": "Payment term: net 30 days.",
                                "page_idx": 0,
                            }
                        ],
                    },
                },
                "preprocessor": {
                    "name": "mineru",
                    "integration_schema": "artpm-mineru-v1",
                    "runtime_version": "3.4.4",
                    "backend": "pipeline",
                },
            }

    resources = _build_knowledge_ingestion_resources(
        Agent(),
        [
            {
                "name": "contract.pdf",
                "extension": "pdf",
                "sha256": "source-sha",
                "stored_path": "conversation/contract.pdf",
                "mime_type": "application/pdf",
                "size": 100,
            }
        ],
        [str(tmp_path / "contract.pdf")],
        "add this file to the knowledge base",
    )

    assert resources[0]["metadata"]["conversion_backend"] == "mineru"
    assert resources[0]["metadata"]["conversion_schema"] == "artpm-mineru-v1"
    assert resources[0]["structured_data"]["mineru"]["backend"] == "pipeline"

    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        enable_vector_search=False,
    )
    proposal = store.propose_ingestion(
        "conversation-1",
        "turn-1",
        resources,
        "mineru-ingestion-1",
    )
    assert store.list_resources() == []

    confirmed = store.confirm_ingestion(
        proposal["id"],
        actor="local-user",
        confirmation_token="mineru-confirmation",
        expected_state_version=proposal["state_version"],
    )

    assert confirmed["status"] == "confirmed"
    stored = store.list_resources()[0]
    assert stored["current_version"] == 1
    result = store.search("net 30 days", limit=5)[0]
    assert result["version"]["structured_data"]["mineru"]["runtime_version"] == "3.4.4"
