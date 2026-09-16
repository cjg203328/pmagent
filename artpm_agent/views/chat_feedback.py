"""Feedback-specific pure contracts for the chat page."""

from collections.abc import Mapping
from typing import Any

from .chat_state import feedback_was_saved, preceding_user_prompt


def feedback_key(message: Mapping[str, Any], index: int) -> str:
    return str(message.get("id", index))


def render_turn_feedback(
    st: Any,
    message: dict[str, Any],
    index: int,
    *,
    messages: list[Mapping[str, Any]],
    tenant_context: Any,
    feedback_store: Any,
    episode_store: Any,
    record_feedback: Any,
    categories: tuple[str, ...],
) -> None:
    """Render and persist feedback for one completed assistant message."""
    message_id = feedback_key(message, index)
    turn_id = message.get("turn_id") or message_id
    given = (st.session_state.get("feedback_given") or {}).get(message_id)
    if given:
        st.caption(
            "已记录你的反馈（%s），我会记住并在下次改进。"
            % ("👍" if given == "up" else "👎")
        )
        return

    user_prompt = preceding_user_prompt(messages, index)
    up_col, down_col, _ = st.columns([1, 1, 8], gap="small")
    with up_col:
        if st.button(
            "",
            key=f"fb_up_{message_id}",
            icon=":material/thumb_up:",
            help="这个回答有帮助",
        ):
            result = record_feedback(
                turn_id,
                True,
                user_prompt=user_prompt,
                assistant_content=str(message.get("content", "")),
                tenant_context=tenant_context,
                feedback_store=feedback_store,
                episode_store=episode_store,
            )
            if feedback_was_saved(result):
                st.session_state.setdefault("feedback_given", {})[message_id] = "up"
                st.toast("👍 已记录，我会延续这个方向")
                st.rerun()
            else:
                st.error("反馈未能保存，请稍后重试。")
    with down_col:
        if st.button(
            "",
            key=f"fb_down_{message_id}",
            icon=":material/thumb_down:",
            help="这个回答有问题",
        ):
            st.session_state.setdefault("feedback_pending", {})[message_id] = True
            st.rerun()

    if not (st.session_state.get("feedback_pending") or {}).get(message_id):
        return
    labels = {
        "too_verbose": "太啰嗦",
        "too_brief": "太简短",
        "not_direct": "没直接回答",
        "ignored_context": "忽略上下文",
        "factual_error": "事实错误",
        "wrong_format": "格式不对",
        "wrong_tool": "用错工具",
        "other": "其他",
    }
    with st.container(border=True):
        st.markdown("**这次哪里不好？** 选一个分类，补充描述会变成我的长期偏好。")
        category = st.selectbox(
            "反馈分类",
            options=list(categories),
            index=len(categories) - 1,
            format_func=lambda value: labels.get(value, value),
            key=f"fb_cat_{message_id}",
            label_visibility="collapsed",
        )
        reason = st.text_area(
            "可选：具体描述问题",
            key=f"fb_reason_{message_id}",
            placeholder="例如：回答太啰嗦 / 漏掉了报价金额 / 应该用表格而不是文字",
        )
        submit_col, cancel_col = st.columns(2)
        with submit_col:
            if st.button(
                "提交反馈",
                key=f"fb_submit_{message_id}",
                type="primary",
                width="stretch",
            ):
                result = record_feedback(
                    turn_id,
                    False,
                    user_prompt=user_prompt,
                    assistant_content=str(message.get("content", "")),
                    correction=reason,
                    category=category,
                    tenant_context=tenant_context,
                    feedback_store=feedback_store,
                    episode_store=episode_store,
                )
                if feedback_was_saved(result):
                    st.session_state.setdefault("feedback_given", {})[message_id] = (
                        "down"
                    )
                    st.session_state.get("feedback_pending", {}).pop(message_id, None)
                    st.toast("👎 已记录，下次我会注意")
                    st.rerun()
                else:
                    st.error("反馈未能保存，请稍后重试。")
        with cancel_col:
            if st.button("取消", key=f"fb_cancel_{message_id}", width="stretch"):
                st.session_state.get("feedback_pending", {}).pop(message_id, None)
                st.rerun()


__all__ = [
    "feedback_key",
    "feedback_was_saved",
    "preceding_user_prompt",
    "render_turn_feedback",
]
