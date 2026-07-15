"""
Knowledge ingestion proposal handler for Phase 3 Stage 2.

Detects knowledge base ingestion requests and creates approval proposals.
Migrated from pages/chat.py lines 268-310.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List, Optional

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
        resources = _build_knowledge_ingestion_resources(
            ctx.agent, attachments, file_paths, ctx.user_input
        )

        if knowledge_store is None:
            raise RuntimeError("资料库存储未就绪")

        # Create approval proposal
        knowledge_store.propose_ingestion(
            request_conversation_id,
            ctx.turn_id,
            resources,
            f"knowledge-turn-{ctx.turn_id}",
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


def _build_knowledge_ingestion_resources(
    agent: Any, attachments: List[Any], file_paths: List[str], prompt: str
) -> List[dict]:
    """
    Parse conversation files into bounded, parser-neutral knowledge payloads.

    This is a direct migration from ui_helpers.build_knowledge_ingestion_resources().

    Args:
        agent: ArtPMAgent instance with process_document() method
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

    for attachment, file_path in zip(attachments, file_paths):
        result = agent.process_document(file_path, prompt)
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
        elif extension in {"png", "jpg", "jpeg", "webp"}:
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
        resources.append(
            {
                "title": name,
                "searchable_text": raw_text[:32768],  # Bounded for indexing
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
                    "preprocessor": result.get("preprocessor") or None,
                },
            }
        )

    if failures:
        raise ValueError("；".join(failures))

    if not resources:
        raise ValueError("没有可加入资料库的有效附件")

    return resources
