"""Capability metadata must describe the runtime that actually executes."""

from artpm_agent.skills.skill_router import ReminderBot, SKILL_METADATA


def test_reminder_preview_is_reported_as_offline_and_deterministic():
    result = ReminderBot({}).run(
        {"task_id": "task-1", "recipients": ["owner"], "tone": "formal"}
    )

    assert result["success"] is True
    assert result["sent_status"] == "dry_run"
    assert ReminderBot.requires_llm is False
    assert SKILL_METADATA["reminder_bot"]["requires_llm"] is False
