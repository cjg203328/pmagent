"""Workspace-scoped retrieval contracts.

The retrieval package is deliberately small: it normalizes scope and result
metadata around the existing ``WorkspaceKnowledgeStore`` instead of creating
another knowledge database or vector backend.
"""

from .contracts import RetrievalHit, RetrievalPlan, SearchTarget
from .workspace import WorkspaceRetriever

__all__ = ["RetrievalHit", "RetrievalPlan", "SearchTarget", "WorkspaceRetriever"]
