"""Turn-level UI contracts.

Execution remains owned by ``artpm_agent.harness``; this module only carries
stable names for future rendering adapters.
"""

from typing import Any
from uuid import uuid4


def turn_id_from_result(result: Any) -> str | None:
    run = getattr(result, "run", None)
    turn_id = getattr(run, "turn_id", None) or getattr(result, "turn_id", None)
    return str(turn_id) if turn_id else None


def queue_suggested_prompt(
    prompt: str,
    *,
    store: Any,
    active_id: str | None,
    messages: list[dict[str, Any]],
    default_title: str,
    title_from_prompt: Any,
    load_active_messages: Any,
) -> dict[str, Any]:
    """Persist a suggestion through the same durable path as typed input."""
    turn_id = uuid4().hex
    if store is not None and active_id:
        user_message = store.add_message(
            active_id,
            "user",
            prompt,
            turn_id=turn_id,
            metadata={},
        )
        conversation = store.get_conversation(active_id)
        if conversation and conversation.get("title") == default_title:
            store.rename_conversation(active_id, title_from_prompt(prompt))
        load_active_messages()
        user_message_id = user_message.get("id")
    else:
        user_message_id = None
        messages.append(
            {
                "role": "user",
                "content": prompt,
                "turn_id": turn_id,
                "metadata": {},
            }
        )
    return {
        "prompt": prompt,
        "conversation_id": active_id,
        "user_message_id": user_message_id,
        "turn_id": turn_id,
        "attachments": [],
    }


__all__ = ["queue_suggested_prompt", "turn_id_from_result"]
