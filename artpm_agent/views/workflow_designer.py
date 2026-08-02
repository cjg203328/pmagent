"""Lightweight visual workflow authoring inside Streamlit settings."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
import json
from typing import Any

import streamlit as st

from artpm_agent.workflows.designer import (
    WorkflowDraftConflictError,
    list_capability_options,
    save_workflow_draft,
)
from artpm_agent.workflows.risk_policy import DEFAULT_SKILL_CAPABILITIES
from artpm_agent.workflows.selector import CapabilityAllowlist


_NEW_WORKFLOW = "__new__"


def _editor_rows(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        try:
            value = value.to_dict(orient="records")
        except TypeError:
            value = value.to_dict("records")
    if not isinstance(value, (list, tuple)):
        return []
    rows = [dict(row) for row in value if isinstance(row, Mapping)]

    def order_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        index, row = item
        try:
            order = int(row.get("order") or index + 1)
        except (TypeError, ValueError):
            order = index + 1
        return order, index

    return [row for _index, row in sorted(enumerate(rows), key=order_key)]


def _definition_rows(definition: Any) -> list[dict[str, Any]]:
    return [
        {
            "order": index + 1,
            "id": step.id,
            "skill_id": step.skill_id,
            "capability": step.capability,
            "input_map": json.dumps(
                step.input_map,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        for index, step in enumerate(definition.steps)
    ]


def _load_editor(definition: Any | None, first_option: Any) -> None:
    # The data editor owns a separate widget state; clear it before the widget
    # is instantiated so switching definitions loads the selected snapshot.
    st.session_state.pop("workflow_designer_steps", None)
    if definition is None:
        values = {
            "workflow_designer_id": "",
            "workflow_designer_name": "",
            "workflow_designer_description": "",
            "workflow_designer_keywords": "",
            "workflow_designer_always": False,
            "workflow_designer_enabled": True,
            "workflow_designer_priority": 0,
            "workflow_designer_rows": [
                {
                    "order": 1,
                    "id": "step_1",
                    "skill_id": first_option.skill_id,
                    "capability": first_option.capability,
                    "input_map": "{}",
                }
            ],
            "workflow_designer_base_version": None,
        }
    else:
        values = {
            "workflow_designer_id": definition.id,
            "workflow_designer_name": definition.name,
            "workflow_designer_description": definition.description,
            "workflow_designer_keywords": ", ".join(definition.trigger.keywords),
            "workflow_designer_always": definition.trigger.always,
            "workflow_designer_enabled": definition.enabled,
            "workflow_designer_priority": definition.priority,
            "workflow_designer_rows": _definition_rows(definition),
            "workflow_designer_base_version": definition.version,
        }
    for key, value in values.items():
        st.session_state[key] = value


def _risk_label(risk: str, approval: str) -> str:
    if approval == "admin":
        return "管理员确认"
    if approval == "user":
        return "用户确认"
    return "只读"


def _render_canvas(
    rows: list[dict[str, Any]],
    option_by_pair: Mapping[tuple[str, str], Any],
) -> None:
    nodes: list[str] = []
    for index, row in enumerate(rows[:8]):
        skill_id = str(row.get("skill_id") or "")
        capability_id = str(row.get("capability") or "")
        step_id = str(row.get("id") or f"step_{index + 1}")
        option = option_by_pair.get((skill_id, capability_id))
        capability = option.capability if option is not None else "未选择能力"
        policy = (
            _risk_label(option.risk, option.approval)
            if option is not None
            else "不可用"
        )
        if index:
            nodes.append(
                '<span class="pm-workflow-edge" aria-hidden="true">'
                '&rarr;</span>'
            )
        nodes.append(
            '<div class="pm-workflow-node" role="listitem">'
            f'<span class="pm-workflow-node-index">{index + 1}</span>'
            f'<strong>{escape(step_id)}</strong>'
            f'<span>{escape(skill_id or "未选择 Skill")}</span>'
            f'<small>{escape(capability)} · {escape(policy)}</small>'
            '</div>'
        )
    if not nodes:
        nodes.append('<div class="pm-workflow-empty">暂无步骤</div>')
    # The stylesheet hides the marker's Streamlit element container. Render the
    # graph separately so that selector cannot hide the actual canvas with it.
    st.markdown(
        '<div class="workflow-designer-marker" aria-hidden="true"></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="pm-workflow-canvas" role="list" aria-label="工作流步骤">'
        + "".join(nodes)
        + "</div>",
        unsafe_allow_html=True,
    )


def render_workflow_designer(
    store: Any,
    *,
    workspace_id: str = "local-default",
    profile_id: str = "local-default",
    capability_allowlist: CapabilityAllowlist | None = None,
) -> None:
    """Render a functional linear graph editor backed by immutable versions."""

    options = list_capability_options(
        capability_allowlist=capability_allowlist
        if capability_allowlist is not None
        else DEFAULT_SKILL_CAPABILITIES
    )
    option_by_pair = {
        (option.skill_id, option.capability): option for option in options
    }
    skill_ids = sorted({option.skill_id for option in options})
    capability_ids = sorted({option.capability for option in options})
    if not skill_ids:
        st.error("没有可用于编排的受信 Skill。")
        return

    definitions = store.list_definitions(
        workspace_id=workspace_id,
        profile_id=profile_id,
    )
    custom = {item.id: item for item in definitions if item.source == "custom"}
    pending_selection = st.session_state.pop(
        "workflow_designer_pending_selection",
        None,
    )
    if pending_selection is not None:
        st.session_state["workflow_designer_selected"] = pending_selection
    selected = st.selectbox(
        "工作流",
        [_NEW_WORKFLOW, *sorted(custom)],
        format_func=lambda item: "新建工作流" if item == _NEW_WORKFLOW else custom[item].name,
        key="workflow_designer_selected",
    )
    loaded_key = f"{workspace_id}:{profile_id}:{selected}"
    if st.session_state.get("workflow_designer_loaded_key") != loaded_key:
        _load_editor(custom.get(selected), options[0])
        st.session_state.workflow_designer_loaded_key = loaded_key

    identity_col, priority_col = st.columns([3, 1])
    with identity_col:
        workflow_id = st.text_input(
            "工作流 ID",
            key="workflow_designer_id",
            disabled=selected != _NEW_WORKFLOW,
            placeholder="asset_delivery_review",
        ).strip()
    with priority_col:
        priority = int(
            st.number_input(
                "优先级",
                min_value=-1000,
                max_value=1000,
                step=1,
                key="workflow_designer_priority",
            )
        )
    name = st.text_input("名称", key="workflow_designer_name").strip()
    description = st.text_area(
        "说明",
        key="workflow_designer_description",
        max_chars=4000,
    ).strip()
    trigger_col, mode_col = st.columns([3, 1])
    with trigger_col:
        keywords = st.text_input(
            "触发关键词",
            key="workflow_designer_keywords",
            placeholder="交付, 验收",
        )
    with mode_col:
        trigger_always = st.toggle(
            "始终触发",
            key="workflow_designer_always",
        )

    rows_value = st.data_editor(
        st.session_state.get("workflow_designer_rows", []),
        key="workflow_designer_steps",
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        column_order=("order", "id", "skill_id", "capability", "input_map"),
        column_config={
            "order": st.column_config.NumberColumn(
                "顺序",
                min_value=1,
                max_value=8,
                step=1,
                required=True,
            ),
            "id": st.column_config.TextColumn("步骤 ID", required=True),
            "skill_id": st.column_config.SelectboxColumn(
                "Skill",
                options=skill_ids,
                required=True,
            ),
            "capability": st.column_config.SelectboxColumn(
                "能力",
                options=capability_ids,
                required=True,
            ),
            "input_map": st.column_config.TextColumn(
                "输入映射(JSON)",
                default="{}",
            ),
        },
    )
    rows = _editor_rows(rows_value)
    st.session_state.workflow_designer_rows = rows
    _render_canvas(rows, option_by_pair)

    state_col, action_col = st.columns([2, 3], vertical_alignment="bottom")
    with state_col:
        enabled = st.toggle(
            "发布后启用",
            key="workflow_designer_enabled",
        )
    with action_col:
        save_col, clear_col = st.columns(2)
        with save_col:
            save_clicked = st.button(
                "保存新版本",
                key="save_visual_workflow",
                type="primary",
                icon=":material/save:",
                width="stretch",
            )
        with clear_col:
            clear_clicked = st.button(
                "新建",
                key="clear_visual_workflow",
                icon=":material/add:",
                width="stretch",
            )

    if clear_clicked:
        st.session_state.workflow_designer_pending_selection = _NEW_WORKFLOW
        st.session_state.workflow_designer_loaded_key = None
        st.rerun()
    if save_clicked:
        try:
            saved = save_workflow_draft(
                store,
                workflow_id=workflow_id,
                name=name,
                description=description,
                rows=rows,
                trigger_keywords=keywords,
                trigger_always=trigger_always,
                enabled=enabled,
                priority=priority,
                workspace_id=workspace_id,
                profile_id=profile_id,
                expected_base_version=st.session_state.get(
                    "workflow_designer_base_version"
                ),
                capability_allowlist=(
                    capability_allowlist
                    if capability_allowlist is not None
                    else DEFAULT_SKILL_CAPABILITIES
                ),
            )
            st.session_state.workflow_designer_pending_selection = saved.id
            st.session_state.workflow_designer_loaded_key = None
            st.toast(f"已保存 {saved.name} v{saved.version}", icon=":material/check:")
            st.rerun()
        except WorkflowDraftConflictError as error:
            st.warning(str(error))
        except Exception as error:
            st.error(f"工作流未保存：{error}")


__all__ = ["render_workflow_designer"]
