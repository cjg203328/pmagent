"""
Artifact generation handler for Phase 3 Stage 3.

Detects requests for structured artifact generation (e.g., reports, manifests).
Migrated from pages/chat.py lines 350-377.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .turn_service import TurnContext, TurnResult

logger = logging.getLogger(__name__)


def _should_preparse_artifact_attachments(prompt: str) -> bool:
    text = str(prompt or "").casefold()
    return any(
        term in text
        for term in (
            "excel",
            "xlsx",
            "word",
            "docx",
            "spreadsheet",
            "document",
            "table",
            "generate",
            "create",
            "export",
            "template",
            "表格",
            "文档",
            "文件",
            "生成",
            "创建",
            "导出",
            "模板",
        )
    )


def _attachment_context_for_artifact(ctx: "TurnContext") -> str:
    file_paths = ctx.extra.get("file_paths", [])
    if not file_paths or not _should_preparse_artifact_attachments(ctx.user_input):
        return str(ctx.extra.get("attachment_context") or "")
    # A preceding handler may already have normalized the same attachments.
    # Reusing that snapshot avoids a second MinerU/OCR invocation during one
    # turn and keeps artifact generation deterministic.
    if ctx.extra.get("parsed_files") and ctx.extra.get("attachment_context"):
        return str(ctx.extra.get("attachment_context") or "")
    runtime = ctx.runtime
    if runtime is None or not runtime.capabilities.attachment_parsing:
        return str(ctx.extra.get("attachment_context") or "")
    try:
        parsed_files, attachment_context = runtime.parse_attachments(
            ctx.user_input,
            {**ctx.extra, "file_paths": file_paths},
        )
        ctx.extra["parsed_files"] = parsed_files
        ctx.extra["attachment_context"] = attachment_context
        return str(attachment_context or "")
    except Exception:
        logger.exception("Artifact attachment preprocessing failed")
        return str(ctx.extra.get("attachment_context") or "")


def try_artifact_generation(
    ctx: "TurnContext",
    artifact_coordinator: Optional[Any],
    request_conversation_id: Optional[str],
) -> Optional["TurnResult"]:
    """
    Check if this turn should generate a structured artifact.

    Args:
        ctx: Turn context with user input
        artifact_coordinator: ArtifactCoordinator instance
            (from get_artifact_coordinator())
        request_conversation_id: Current conversation ID

    Returns:
        TurnResult if this is an artifact generation request, None otherwise

    Logic:
        1. Requires artifact_coordinator and conversation_id
        2. Calls artifact_coordinator.process(prompt)
        3. If matched, returns response with artifact metadata
        4. If not matched, returns None (not handled)

    The artifact coordinator determines if the prompt matches known artifact
    patterns (e.g., "生成报告", "创建清单"). If so, it generates the artifact
    and returns the file reference and message.
    """

    if artifact_coordinator is None:
        return None

    if not request_conversation_id:
        return None

    try:
        attachment_context = _attachment_context_for_artifact(ctx)
        artifact_outcome = artifact_coordinator.process(
            ctx.user_input,
            attachments=ctx.attachments,
            file_paths=ctx.extra.get("file_paths", []),
            attachment_context=attachment_context,
        )

        if not artifact_outcome.matched:
            # Not an artifact generation request
            return None

        # Artifact was generated
        from .turn_service import TurnResult

        artifacts = []
        if artifact_outcome.artifact is not None:
            artifact = artifact_outcome.artifact
            # Extract artifact metadata for UI rendering
            artifacts.append(
                {
                    key: artifact[key]
                    for key in (
                        "id",
                        "name",
                        "stored_path",
                        "format",
                        "mime_type",
                        "size",
                        "sha256",
                        "version",
                        "rows",
                        "columns",
                        "paragraphs",
                        "sheet_name",
                        "template_id",
                        "template_name",
                        "preview_markdown",
                        "export_formats",
                        "source_artifact",
                    )
                    if key in artifact
                }
            )

        return TurnResult(
            response=artifact_outcome.message,
            success=True,
            artifacts=artifacts,
            handled_by="artifact_generation",
            metadata={
                "turn_id": ctx.turn_id,
                "conversation_id": request_conversation_id,
                "artifact_count": len(artifacts),
            },
        )

    except Exception as error:
        logger.exception("Artifact generation failed")
        from .turn_service import TurnResult

        return TurnResult(
            response=f"产物生成失败：{error}",
            success=False,
            error=str(error),
            handled_by="artifact_generation_exception",
        )
