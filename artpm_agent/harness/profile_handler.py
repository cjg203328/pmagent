"""
Profile change proposal handler for Phase 3 Stage 2.

Detects profile configuration change requests and creates approval proposals.
Migrated from pages/chat.py lines 222-266.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .turn_service import TurnContext, TurnResult

from artpm_agent.profiles.parser import (
    ProfileChangeParseError,
    format_profile_changes,
    parse_profile_change,
)

logger = logging.getLogger(__name__)


def try_profile_proposal(
    ctx: "TurnContext",
    profile_store: Optional[Any],
    request_conversation_id: Optional[str],
) -> Optional["TurnResult"]:
    """
    Check if this turn is a profile configuration change request.

    Args:
        ctx: Turn context with user input and current profile
        profile_store: ProfileStore instance (from get_profile_store())
        request_conversation_id: Current conversation ID

    Returns:
        TurnResult if this is a profile change request, None otherwise

    Logic:
        1. Requires profile_store, current_profile, and conversation_id
        2. Calls parse_profile_change() to detect change intent
        3. If detected, creates a proposal and returns approval message
        4. If parse error, returns error message
        5. If no change detected, returns None (not handled)
    """

    from .turn_service import TurnResult

    # Require all dependencies
    if profile_store is None:
        return None

    current_profile = ctx.agent_profile
    if current_profile is None:
        return None

    if not request_conversation_id:
        return None

    # Try parsing the input as a profile change request
    try:
        profile_patch = parse_profile_change(ctx.user_input, current_profile)

        if profile_patch is None:
            # Not a profile change request
            return None

        # Generate preview of changes
        preview = format_profile_changes(current_profile, profile_patch)

        # Create approval proposal
        profile_store.propose_change(
            request_conversation_id,
            profile_patch,
            turn_id=ctx.turn_id,
            summary=preview,
            idempotency_key=f"profile-turn-{ctx.turn_id}",
        )

        # Import here to avoid circular dependency at module level
        from .turn_service import TurnResult

        return TurnResult(
            response=f"{preview}\n\n请在当前会话中确认后再应用。",
            success=True,
            awaiting_approval=True,
            handled_by="profile_proposal",
            metadata={
                "turn_id": ctx.turn_id,
                "conversation_id": request_conversation_id,
                "proposal_type": "profile_change",
            },
        )

    except ProfileChangeParseError as error:
        from .turn_service import TurnResult

        return TurnResult(
            response=f"这个配置变更暂不能应用：{error}",
            success=True,  # Not a system error, just invalid request
            handled_by="profile_proposal_error",
            metadata={
                "error_type": "ProfileChangeParseError",
                "error_message": str(error),
            },
        )

    except Exception as error:
        logger.exception("创建 Agent Profile 提案失败")
        from .turn_service import TurnResult

        return TurnResult(
            response="配置提案未能保存，请检查服务日志后重试。",
            success=False,
            error=str(error),
            handled_by="profile_proposal_exception",
        )
