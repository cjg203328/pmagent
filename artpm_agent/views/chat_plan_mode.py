"""Plan mode for the chat composer.

Mirrors the interaction logic EvoFlow documents for plan mode:

1. A mode selector lives next to the composer (ask / agent / plan).
2. Once a plan exists, a confirmation bar appears **above** the composer with a
   kicker, an authorization status and a step summary.
3. Nothing executes before the host authorizes it — the bar is the gate.
4. There is no separate "revise" button; revisions arrive through the composer.

The projection helpers are pure so the copy and the state mapping can be tested
without Streamlit. Only the ``render_*`` helpers touch the Streamlit runtime,
and every state transition is returned to the caller instead of being applied
here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

ASK_MODE = "ask"
AGENT_MODE = "agent"
PLAN_MODE = "plan"

CHAT_MODES: tuple[str, ...] = (ASK_MODE, AGENT_MODE, PLAN_MODE)
CHAT_MODE_LABELS: dict[str, str] = {
    ASK_MODE: "提问",
    AGENT_MODE: "执行",
    PLAN_MODE: "计划",
}
CHAT_MODE_HELP: dict[str, str] = {
    ASK_MODE: "只问答，不产生副作用",
    AGENT_MODE: "直接执行，需要写操作时再确认",
    PLAN_MODE: "先定稿计划，授权后才开始执行",
}
LABEL_TO_MODE: dict[str, str] = {
    label: mode for mode, label in CHAT_MODE_LABELS.items()
}
DEFAULT_CHAT_MODE = AGENT_MODE

MODE_WIDGET_KEY = "chat_mode_pills"
MODE_CONTAINER_KEY = "chat_mode_pills_row"
MODE_STATE_KEY = "chat_mode"
PLAN_ID_STATE_KEY = "active_plan_id"
PLAN_BAR_DISMISSED_KEY = "plan_bar_dismissed_for"

#: Composer placeholder per mode, following the same wording contract EvoFlow
#: uses so the mode change is visible in the input itself.
MODE_PLACEHOLDERS: dict[str, str] = {
    ASK_MODE: "输入问题，只做解答",
    AGENT_MODE: "输入消息或添加附件",
    PLAN_MODE: "描述多步骤目标；将先对齐需求再规划…",
}

#: State copy for the confirmation bar. ``tone`` drives the bar styling and
#: ``primary`` names the single authorization action available in that state.
PLAN_STATE_COPY: dict[str, dict[str, str]] = {
    "draft": {
        "kicker": "计划草稿",
        "status": "待确认步骤",
        "tone": "draft",
        "primary": "提交定稿",
    },
    "proposed": {
        "kicker": "计划已定稿",
        "status": "待授权开始执行",
        "tone": "proposed",
        "primary": "授权执行",
    },
    "approved": {
        "kicker": "计划已授权",
        "status": "待接入执行器后开始跑步骤",
        "tone": "approved",
        "primary": "",
    },
    "executed": {
        "kicker": "计划已执行",
        "status": "已完成",
        "tone": "executed",
        "primary": "",
    },
}

#: Step status copy for the plan review dialog.
STEP_STATUS_LABELS: dict[str, str] = {
    "pending": "待执行",
    "in_progress": "执行中",
    "completed": "已完成",
    "failed": "失败",
    "skipped": "已跳过",
    "blocked": "待确认",
}


def normalize_mode(value: Any) -> str:
    """Return a supported chat mode, falling back to the default."""
    if isinstance(value, str) and value in CHAT_MODES:
        return value
    return DEFAULT_CHAT_MODE


def mode_label(value: Any) -> str:
    """Return the user-facing label for a mode."""
    return CHAT_MODE_LABELS[normalize_mode(value)]


def placeholder_for_mode(value: Any) -> str:
    """Return the composer placeholder that matches the active mode."""
    return MODE_PLACEHOLDERS[normalize_mode(value)]


def plan_state_copy(status: Any) -> dict[str, str]:
    """Return the confirmation-bar copy for a plan status."""

    key = str(status or "").strip().lower()
    return dict(PLAN_STATE_COPY.get(key, PLAN_STATE_COPY["draft"]))


def plan_summary(plan: Mapping[str, Any] | None) -> str:
    """Return the step summary shown in the confirmation bar."""

    if not isinstance(plan, Mapping):
        return "0 个步骤"
    steps = plan.get("steps") or []
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        return "0 个步骤"
    return f"{len(steps)} 个步骤"


def plan_bar_view(plan: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Project one stored plan into the confirmation bar's view model."""

    if not isinstance(plan, Mapping):
        return None
    plan_id = str(plan.get("plan_id") or "").strip()
    if not plan_id:
        return None
    copy = plan_state_copy(plan.get("status"))
    feedback = str(plan.get("feedback") or "").strip()
    return {
        "plan_id": plan_id,
        "title": str(plan.get("title") or "未命名计划"),
        "objective": str(plan.get("objective") or ""),
        "status": str(plan.get("status") or "draft"),
        "kicker": copy["kicker"],
        "status_label": copy["status"],
        "tone": copy["tone"],
        "primary_label": copy["primary"],
        "summary": plan_summary(plan),
        "feedback": feedback,
    }


def plan_steps_view(plan: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Project the steps of one stored plan for the review dialog."""

    if not isinstance(plan, Mapping):
        return ()
    steps = plan.get("steps") or []
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        return ()
    confirmed = plan.get("confirmed_steps") or []
    confirmed_set = {str(item) for item in confirmed} if confirmed else set()
    projected: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if isinstance(step, Mapping):
            title = str(step.get("title") or f"步骤 {index}")
            description = str(step.get("description") or "")
            status = str(step.get("status") or "pending")
            requires_approval = bool(step.get("requires_approval"))
            result = str(step.get("result") or "")
        else:
            title = str(step)
            description = ""
            status = "pending"
            requires_approval = False
            result = ""
        projected.append(
            {
                "index": f"{index:02d}",
                "title": title,
                "description": description,
                "status": status,
                "status_label": STEP_STATUS_LABELS.get(status, status),
                "requires_approval": requires_approval,
                "confirmed": title in confirmed_set,
                "result": result,
            }
        )
    return tuple(projected)


def draft_plan_payload(prompt: str) -> dict[str, Any] | None:
    """Build a draft plan from a composer submission.

    The model is not asked to invent steps here: the submitted text becomes the
    objective and each non-empty line becomes an editable candidate step. The
    host reviews and rewrites them in the dialog before anything is authorized,
    which keeps "先定稿再执行" true even when the planner is not wired.
    """

    text = str(prompt or "").strip()
    if not text:
        return None
    lines = [line.strip(" \t-—*") for line in text.splitlines()]
    steps = [line for line in lines if line]
    if not steps:
        steps = [text]
    title = steps[0]
    if len(title) > 60:
        title = title[:60].rstrip() + "…"
    return {"title": title, "objective": text, "steps": steps}


def render_mode_pills(st: Any, *, disabled: bool = False) -> str:
    """Render the mode selector and return the mode selected this run."""

    current = normalize_mode(st.session_state.get(MODE_STATE_KEY))
    if st.session_state.get(MODE_WIDGET_KEY) != current:
        st.session_state[MODE_WIDGET_KEY] = current
    with st.container(key=MODE_CONTAINER_KEY):
        selected = st.pills(
            "对话模式",
            options=list(CHAT_MODES),
            format_func=mode_label,
            selection_mode="single",
            key=MODE_WIDGET_KEY,
            default=current,
            disabled=disabled,
            help="先选模式再输入：提问只看，执行会动手，计划先定稿后授权。",
            label_visibility="collapsed",
        )
    selected_mode = normalize_mode(selected or current)
    st.session_state[MODE_STATE_KEY] = selected_mode
    return selected_mode


def render_plan_confirmation_bar(
    st: Any,
    view: Mapping[str, Any] | None,
    *,
    key_suffix: str = "",
) -> str | None:
    """Render the authorization bar and return the chosen action, if any.

    Returns ``"view"``, ``"primary"`` or ``"dismiss"``; the caller owns every
    state transition so the plan store is never mutated from a render helper.
    """

    if not view:
        return None
    tone = str(view.get("tone") or "draft")
    primary_label = str(view.get("primary_label") or "")
    with st.container(key=f"plan_confirmation_bar{key_suffix}", border=True):
        st.markdown(
            f"""
            <div class="pm-plan-bar pm-plan-bar-{tone}">
                <span class="pm-plan-bar-kicker">{view.get("kicker")}</span>
                <strong class="pm-plan-bar-title">{view.get("title")}</strong>
                <span class="pm-plan-bar-status">{view.get("status_label")}</span>
                <span class="pm-plan-bar-summary">{view.get("summary")}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if view.get("feedback"):
            st.caption(f"上一轮修改意见：{view['feedback']}")
        columns = st.columns(
            [1, 1, 1, 1] if primary_label else [1, 1, 1],
            gap="small",
        )
        with columns[0]:
            if st.button(
                "查看计划",
                key=f"plan_bar_view{key_suffix}",
                icon=":material/description:",
                width="stretch",
            ):
                return "view"
        action: str | None = None
        next_column = 1
        if primary_label:
            with columns[next_column]:
                if st.button(
                    primary_label,
                    key=f"plan_bar_primary{key_suffix}",
                    type="primary",
                    icon=":material/play_arrow:",
                    width="stretch",
                ):
                    action = "primary"
            next_column += 1
        with columns[next_column]:
            if st.button(
                "不再提示",
                key=f"plan_bar_dismiss{key_suffix}",
                width="stretch",
            ):
                return "dismiss"
        return action


def render_plan_dialog(
    st: Any, view: Mapping[str, Any] | None, steps: Sequence[Mapping[str, Any]]
) -> None:
    """Render the "任务计划" review dialog body."""

    if not view:
        st.caption("没有可查看的计划。")
        return
    st.markdown(f"**目标**：{view.get('objective') or view.get('title')}")
    st.caption(f"状态：{view.get('kicker')} · {view.get('status_label')}")
    if not steps:
        st.caption("这个计划还没有步骤。")
        return
    for step in steps:
        marker = (
            "⚠" if step.get("requires_approval") and not step.get("confirmed") else "·"
        )
        st.markdown(
            f"{marker} **{step.get('index')} {step.get('title')}**"
            f"  \n<small>{step.get('status_label')}</small>",
            unsafe_allow_html=True,
        )
        if step.get("description"):
            st.caption(step["description"])
        if step.get("result"):
            st.caption(f"结果：{step['result']}")
    st.caption("要改计划，直接在下方输入框说明修改意见即可；没有独立的修改按钮。")


__all__ = [
    "AGENT_MODE",
    "ASK_MODE",
    "CHAT_MODES",
    "CHAT_MODE_HELP",
    "CHAT_MODE_LABELS",
    "DEFAULT_CHAT_MODE",
    "LABEL_TO_MODE",
    "MODE_CONTAINER_KEY",
    "MODE_PLACEHOLDERS",
    "MODE_STATE_KEY",
    "MODE_WIDGET_KEY",
    "PLAN_ID_STATE_KEY",
    "PLAN_MODE",
    "PLAN_STATE_COPY",
    "STEP_STATUS_LABELS",
    "draft_plan_payload",
    "mode_label",
    "normalize_mode",
    "placeholder_for_mode",
    "plan_bar_view",
    "plan_state_copy",
    "plan_steps_view",
    "plan_summary",
    "render_mode_pills",
    "render_plan_confirmation_bar",
    "render_plan_dialog",
]
