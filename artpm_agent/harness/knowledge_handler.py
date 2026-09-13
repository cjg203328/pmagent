"""
Knowledge ingestion proposal handler for Phase 3 Stage 2.

Detects knowledge base ingestion requests and creates approval proposals.
Migrated from pages/chat.py lines 268-310.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from inspect import Parameter, signature
from typing import TYPE_CHECKING, Any, List, Optional

from artpm_agent.utils.mineru_adapter import MINERU_IMAGE_SUFFIXES

if TYPE_CHECKING:
    from .turn_service import TurnContext, TurnResult

logger = logging.getLogger(__name__)


def is_knowledge_ingestion_request(prompt: str) -> bool:
    """
    Check if the prompt is requesting to add files to knowledge base.

    Args:
        prompt: User input text

    Returns:
        True if this looks like a knowledge ingestion request

    Logic:
        Requires ALL of:
        - Knowledge target: "知识库" or "资料库"
        - Action verb: "加入", "写入", "保存", "收录", "沉淀", "学习"
        - Subject: "附件", "文件", "这份", "这些"
    """
    text = str(prompt or "").casefold()
    return (
        any(target in text for target in ("知识库", "资料库"))
        and any(
            action in text
            for action in ("加入", "写入", "保存", "收录", "沉淀", "学习")
        )
        and any(subject in text for subject in ("附件", "文件", "这份", "这些"))
    )


def try_knowledge_ingestion(
    ctx: "TurnContext",
    knowledge_store: Optional[Any],
    request_conversation_id: Optional[str],
    attachments: List[Any],
    file_paths: List[str],
) -> Optional["TurnResult"]:
    """
    Check if this turn is a knowledge base ingestion request.

    Args:
        ctx: Turn context with user input
        knowledge_store: WorkspaceKnowledgeStore instance
        request_conversation_id: Current conversation ID
        attachments: List of attachment metadata dicts
        file_paths: List of file paths on disk

    Returns:
        TurnResult if this is an ingestion request, None otherwise

    Logic:
        1. Check if prompt matches ingestion pattern
        2. Require attachments present
        3. Parse each attachment via agent.process_document()
        4. Build resource payloads
        5. Create approval proposal
        6. Return awaiting_approval response
    """

    from .turn_service import TurnResult

    if not request_conversation_id:
        return None

    if not is_knowledge_ingestion_request(ctx.user_input):
        return None

    # Ingestion request detected, but requires attachments
    if not attachments:
        from .turn_service import TurnResult

        return TurnResult(
            response="请先在当前消息中附上需要加入资料库的文件。",
            success=True,
            handled_by="knowledge_ingestion_no_attachments",
        )

    # Parse attachments into resource payloads
    try:
        if ctx.runtime is None or not ctx.runtime.capabilities.document_parsing:
            raise RuntimeError("document parsing runtime is unavailable")
        resources = _build_knowledge_ingestion_resources(
            ctx.runtime,
            attachments,
            file_paths,
            ctx.user_input,
            parsed_files=ctx.extra.get("parsed_files"),
        )

        if knowledge_store is None:
            raise RuntimeError("资料库存储未就绪")

        # Create an approval proposal in the authenticated workspace. Legacy
        # local hosts without a tenant context retain the store's default.
        scope = _trusted_scope(ctx)
        _propose_ingestion(
            knowledge_store,
            request_conversation_id,
            ctx.turn_id,
            resources,
            workspace_id=scope.workspace_id,
            tenant_id=scope.tenant_id,
        )

        names = "、".join(resource["title"] for resource in resources)

        from .turn_service import TurnResult

        return TurnResult(
            response=f"已解析待入库资料：{names}。\n\n当前尚未写入资料库，请确认后再入库。",
            success=True,
            awaiting_approval=True,
            handled_by="knowledge_ingestion",
            metadata={
                "turn_id": ctx.turn_id,
                "conversation_id": request_conversation_id,
                "resource_count": len(resources),
                "resource_names": [r["title"] for r in resources],
            },
        )

    except Exception as error:
        logger.exception("创建资料入库提案失败")
        from .turn_service import TurnResult

        return TurnResult(
            response=f"资料暂不能入库：{error}",
            success=False,
            error=str(error),
            handled_by="knowledge_ingestion_exception",
        )


def _trusted_scope(ctx: "TurnContext") -> Any:
    """Resolve the host-authenticated scope for ingestion persistence."""

    from .turn_service import get_turn_scope

    return get_turn_scope(ctx)


def _trusted_workspace_id(ctx: "TurnContext") -> Optional[str]:
    """Resolve a host-authenticated workspace for compatibility callers."""

    return _trusted_scope(ctx).workspace_id


def _supports_workspace_keyword(method: Any) -> bool:
    try:
        parameters = signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "workspace_id"
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _propose_ingestion(
    knowledge_store: Any,
    conversation_id: str,
    turn_id: str,
    resources: list[dict],
    *,
    workspace_id: str,
    tenant_id: str,
) -> None:
    method = knowledge_store.propose_ingestion
    args = (
        conversation_id,
        turn_id,
        resources,
        f"knowledge-turn-{turn_id}",
    )
    kwargs: dict[str, Any] = {}
    if _supports_workspace_keyword(method):
        kwargs["workspace_id"] = workspace_id
    elif workspace_id != "local-default":
        raise TypeError(
            "knowledge store must support workspace-scoped ingestion"
        )
    if _supports_keyword(method, "tenant_id"):
        kwargs["tenant_id"] = tenant_id
    elif tenant_id != "local":
        raise TypeError("knowledge store must support tenant-scoped ingestion")
    method(*args, **kwargs)


def _supports_keyword(method: Any, name: str) -> bool:
    try:
        parameters = signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _build_knowledge_ingestion_resources(
    runtime: Any,
    attachments: List[Any],
    file_paths: List[str],
    prompt: str,
    *,
    parsed_files: Optional[List[Any]] = None,
) -> List[dict]:
    """
    Parse conversation files into bounded, parser-neutral knowledge payloads.

    This is a direct migration from ui_helpers.build_knowledge_ingestion_resources().

    Args:
        runtime: HarnessRuntime with a process_document() method
        attachments: List of attachment metadata dicts
        file_paths: List of file paths on disk
        prompt: User prompt for context

    Returns:
        List of resource dicts with {title, content, structured, type, source}

    Raises:
        ValueError: If all attachments fail to parse or extract content
    """
    import json
    from pathlib import Path

    resources = []
    failures = []

    if len(attachments) != len(file_paths):
        raise ValueError("附件元数据与文件路径数量不一致")

    cached = parsed_files if isinstance(parsed_files, list) else None
    for index, (attachment, file_path) in enumerate(zip(attachments, file_paths)):
        result = (
            cached[index]
            if cached is not None and index < len(cached) and isinstance(cached[index], Mapping)
            else runtime.process_document(file_path, prompt)
        )
        name = str(attachment.get("name") or Path(file_path).name)

        if not result.get("success"):
            failures.append(f"{name}：{result.get('error', '无法解析')}")
            continue

        # Extract structured data
        structured = result.get("extracted_data")
        if not isinstance(structured, dict):
            structured = {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "success",
                    "source",
                    "file_path",
                    "raw_text",
                    "confidence",
                }
            }

        # Ensure JSON-serializable
        structured = json.loads(json.dumps(structured, ensure_ascii=False, default=str))

        # Determine resource type
        extension = str(attachment.get("extension", "")).lower()
        if extension in {"xlsx", "xls", "csv"}:
            resource_type = "table"
        elif f".{extension}" in MINERU_IMAGE_SUFFIXES:
            resource_type = "image"
        else:
            resource_type = "document"

        # Extract bounded evidence for indexing. Tables and documents can use
        # normalized Markdown; images need real OCR text to avoid memory noise.
        raw_text = str(result.get("raw_text", "")).strip()
        if not raw_text and resource_type in {"table", "document"}:
            raw_text = str(result.get("markdown", "")).strip()
        if resource_type in {"image", "document"} and not raw_text:
            failures.append(
                f"{name}：附件暂未提取到可检索正文；对话中会自动尝试多模态解析，暂不写入知识库"
            )
            continue

        # Build resource payload (matching WorkspaceKnowledgeStore format)
        preprocessor = result.get("preprocessor")
        conversion_backend = (
            str(preprocessor.get("name"))
            if isinstance(preprocessor, dict)
            else "legacy"
        )
        searchable_limit = 131072 if conversion_backend == "mineru" else 32768
        resources.append(
            {
                "title": name,
                "searchable_text": raw_text[:searchable_limit],  # Bounded for indexing
                "resource_type": resource_type,
                "source_type": "conversation_attachment",
                "source_uri": str(
                    attachment.get("stored_path") or attachment.get("name") or name
                ),
                "source_id": str(
                    attachment.get("sha256") or attachment.get("id") or name
                ),
                "mime_type": str(attachment.get("mime_type") or "") or None,
                "structured_data": structured,
                "metadata": {
                    "document_type": result.get("document_type", "未知"),
                    "original_name": name,
                    "size": attachment.get("size"),
                    "confidence": result.get("confidence"),
                    "preprocessor": preprocessor or None,
                    "conversion_backend": conversion_backend,
                    "conversion_schema": (
                        preprocessor.get("integration_schema")
                        if isinstance(preprocessor, dict)
                        else None
                    ),
                },
            }
        )

    if failures:
        raise ValueError("；".join(failures))

    if not resources:
        raise ValueError("没有可加入资料库的有效附件")

    return resources
