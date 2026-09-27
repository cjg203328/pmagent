"""Welcome-page suggestions expressed as an explicit working chain.

The landing actions are grouped into stages so a new user can read the order
of work instead of facing one flat grid of unrelated buttons. Every stage
keeps the same suggestion ids and labels the chat page already renders.
"""

from __future__ import annotations

from typing import TypedDict


class WelcomeSuggestion(TypedDict, total=False):
    icon: str
    text: str
    prompt: str
    mode: str
    stage: str


class WelcomeStage(TypedDict):
    id: str
    index: str
    title: str
    detail: str


WELCOME_STAGES: tuple[WelcomeStage, ...] = (
    {
        "id": "survey",
        "index": "01",
        "title": "看清现状",
        "detail": "先把项目、成本和人天摊开",
    },
    {
        "id": "decide",
        "index": "02",
        "title": "推进决策",
        "detail": "确认需求、定价与流程是否成立",
    },
    {
        "id": "deliver",
        "index": "03",
        "title": "产出交付",
        "detail": "生成可下载、可继续修改的成果",
    },
)

WELCOME_SUGGESTIONS: tuple[WelcomeSuggestion, ...] = (
    {
        "icon": ":material/query_stats:",
        "text": "项目概览",
        "prompt": "查看所有项目的整体经营概览和统计数据",
        "stage": "survey",
    },
    {
        "icon": ":material/analytics:",
        "text": "分析利润率",
        "prompt": "帮我分析当前项目的利润率和成本结构",
        "stage": "survey",
    },
    {
        "icon": ":material/rule:",
        "text": "评估需求",
        "prompt": "我有一个新的产品需求，帮我评估技术可行性和成本",
        "stage": "decide",
    },
    {
        "icon": ":material/request_quote:",
        "text": "创建报价",
        "prompt": "帮我创建一个新的项目报价，包含客户、报价金额和工期",
        "stage": "decide",
    },
    {
        "icon": ":material/tune:",
        "text": "优化流程",
        "prompt": "根据现有工作流，给出优化项目管理的具体建议",
        "stage": "decide",
    },
    {
        "icon": ":material/summarize:",
        "text": "生成周报",
        "prompt": "根据近期项目数据，生成一份本周工作总结报告",
        "stage": "deliver",
    },
    {
        "icon": ":material/edit_document:",
        "text": "智能编辑文件",
        "mode": "edit",
        "stage": "deliver",
    },
)

WELCOME_TITLE = "今天先推进哪件事？"
WELCOME_DESCRIPTION = "查项目、算成本、起草报价，或上传文件直接处理。"


def welcome_suggestions() -> tuple[WelcomeSuggestion, ...]:
    """Return an immutable copy so callers cannot mutate shared page state."""
    return tuple(dict(item) for item in WELCOME_SUGGESTIONS)  # type: ignore[return-value]


def welcome_stages() -> tuple[WelcomeStage, ...]:
    """Return an immutable copy of the ordered working stages."""
    return tuple(dict(stage) for stage in WELCOME_STAGES)  # type: ignore[return-value]


def suggestions_for_stage(stage_id: str) -> tuple[WelcomeSuggestion, ...]:
    """Return the suggestions belonging to one stage, in declaration order."""
    return tuple(
        dict(item) for item in WELCOME_SUGGESTIONS if item.get("stage") == stage_id
    )  # type: ignore[return-value]


def render_welcome_intro(st) -> None:
    """Render the chat landing header without owning any session state."""
    chain = " → ".join(stage["title"] for stage in WELCOME_STAGES)
    st.markdown(
        f"""
        <section class="pm-welcome" aria-labelledby="pm-welcome-title">
            <div class="pm-welcome-kicker">
                <span class="pm-welcome-mark" aria-hidden="true">◆</span>
                <span>ARTPM 工作区</span>
            </div>
            <h1 id="pm-welcome-title">{WELCOME_TITLE}</h1>
            <p>{WELCOME_DESCRIPTION}</p>
            <p class="pm-welcome-chain">{chain}</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_welcome_suggestions(st, *, on_prompt, on_edit) -> None:
    """Render suggestion controls grouped by stage; state stays with the host."""
    with st.container(key="welcome_suggestions", border=False):
        button_index = 0
        for stage in WELCOME_STAGES:
            stage_items = suggestions_for_stage(stage["id"])
            if not stage_items:
                continue
            st.markdown(
                f"""
                <div class="pm-welcome-stage">
                    <span class="pm-welcome-stage-index">{stage["index"]}</span>
                    <span class="pm-welcome-stage-copy">
                        <strong>{stage["title"]}</strong>
                        <small>{stage["detail"]}</small>
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            columns = st.columns(len(stage_items), gap="small")
            for column_index, suggestion in enumerate(stage_items):
                with columns[column_index]:
                    if st.button(
                        suggestion["text"],
                        key=f"suggest_{button_index}",
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
                button_index += 1


__all__ = [
    "WELCOME_DESCRIPTION",
    "WELCOME_STAGES",
    "WELCOME_SUGGESTIONS",
    "WELCOME_TITLE",
    "WelcomeStage",
    "WelcomeSuggestion",
    "render_welcome_intro",
    "render_welcome_suggestions",
    "suggestions_for_stage",
    "welcome_stages",
    "welcome_suggestions",
]
