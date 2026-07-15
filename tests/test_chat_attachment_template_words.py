from artpm_agent.utils.chat_attachments import select_conversation_attachments


def test_template_format_words_reuse_recent_user_attachment():
    recent = [{"id": "recent", "stored_path": "c/recent.xlsx"}]
    messages = [
        {"role": "user", "metadata": {"attachments": recent}},
    ]

    assert (
        select_conversation_attachments(
            "学习这个格式并保存为模板",
            [],
            messages,
        )
        == recent
    )
