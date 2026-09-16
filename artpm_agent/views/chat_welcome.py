"""Welcome-page suggestions independent of Streamlit rendering."""

from __future__ import annotations

from typing import TypedDict


class WelcomeSuggestion(TypedDict, total=False):
    icon: str
    text: str
    prompt: str
    mode: str


WELCOME_SUGGESTIONS: tuple[WelcomeSuggestion, ...] = (
    {
        "icon": ":material/analytics:",
        "text": "分析利润率",
        "prompt": "帮我分析当前项目的利润率和成本结构",
    },
    {
        "icon": ":material/request_quote:",
        "text": "创建报价",
        "prompt": "帮我创建一个新的项目报价，包含客户、报价金额和工期",
    },
    {
        "icon": ":material/query_stats:",
        "text": "项目概览",
        "prompt": "查看所有项目的整体经营概览和统计数据",
    },
    {
        "icon": ":material/rule:",
        "text": "评估需求",
        "prompt": "我有一个新的产品需求，帮我评估技术可行性和成本",
    },
    {
        "icon": ":material/summarize:",
        "text": "生成周报",
        "prompt": "根据近期项目数据，生成一份本周工作总结报告",
    },
    {
        "icon": ":material/tune:",
        "text": "优化流程",
        "prompt": "根据现有工作流，给出优化项目管理的具体建议",
    },
    {"icon": ":material/edit_document:", "text": "智能编辑文件", "mode": "edit"},
)

WELCOME_TITLE = "今天先推进哪件事？"
WELCOME_DESCRIPTION = "查项目、算成本、起草报价，或上传文件直接处理。"


def welcome_suggestions() -> tuple[WelcomeSuggestion, ...]:
    """Return an immutable copy so callers cannot mutate shared page state."""
    return tuple(dict(item) for item in WELCOME_SUGGESTIONS)  # type: ignore[return-value]


def render_welcome_intro(st) -> None:
    """Render the chat landing header without owning any session state."""
    st.markdown(
        f"""
        <section class="pm-welcome" aria-labelledby="pm-welcome-title">
            <div class="pm-welcome-kicker">
                <span class="pm-welcome-mark" aria-hidden="true">◆</span>
                <span>ARTPM 工作区</span>
            </div>
            <h1 id="pm-welcome-title">{WELCOME_TITLE}</h1>
            <p>{WELCOME_DESCRIPTION}</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_welcome_suggestions(st, *, on_prompt, on_edit) -> None:
    """Render suggestion controls while leaving state transitions to the host."""
    with st.container(key="welcome_suggestions", border=False):
        for row_index in range(0, len(WELCOME_SUGGESTIONS), 3):
            row = WELCOME_SUGGESTIONS[row_index : row_index + 3]
            columns = st.columns(len(row), gap="small")
            for column_index, suggestion in enumerate(row):
                with columns[column_index]:
                    if st.button(
                        suggestion["text"],
                        key=f"suggest_{row_index}_{column_index}",
                        icon=suggestion["icon"],
                        help=(
                            "上传 Excel 或 Word，并用一句话生成编辑版本"
                            if suggestion.get("mode") == "edit"
                            else f"点击发送：{suggestion['prompt']}"
                        ),
                        width="stretch",
                    ):
                        if suggestion.get("mode") == "edit":
                            on_edit()
                        else:
                            on_prompt(suggestion["prompt"])
                        st.rerun()


__all__ = [
    "WELCOME_DESCRIPTION",
    "WELCOME_SUGGESTIONS",
    "WELCOME_TITLE",
    "WelcomeSuggestion",
    "render_welcome_intro",
    "render_welcome_suggestions",
    "welcome_suggestions",
]
