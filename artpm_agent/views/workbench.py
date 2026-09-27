"""三栏工作台（PRD §7）：①任务 / ②过程 / ③成果。

① 读 `JobService`，按状态分组；② 展示选中 Job 的状态机、输入输出，并允许执行
已接线的办公流技能；③ 列该 Job 的交付物，只有 `verification=passed` 提供下载。

这一页是「任务为中心」的第一个可见表面：对话仍是执行通道之一，但导航对象是 Job。
"""

from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

from artpm_agent.ui_state import (
    Config,
    _active_ui_profile_id,
    _trusted_ui_tenant_context,
    get_artifact_generator,
    get_job_service,
    get_ui_runtime_factory,
)

_COLUMN_HEIGHT = 620

_LIFECYCLE = ("draft", "queued", "running", "succeeded")

_STATUS_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("进行中", ("queued", "running", "waiting", "needs_input")),
    ("计划中", ("draft",)),
    ("已结束", ("succeeded", "failed", "cancelled")),
)

_EXECUTABLE_JOB_TYPES = {"office_weekly"}
_NOTICE_KEY = "wb_notice"
_STATUS_LABELS = {
    "draft": "草稿",
    "queued": "排队中",
    "running": "执行中",
    "waiting": "等待输入",
    "needs_input": "需要补充",
    "succeeded": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def _label(job: dict[str, Any]) -> str:
    name = str(job.get("job_name") or "未命名任务").strip()
    status = str(job.get("status") or "unknown")
    return f"{name} · {_STATUS_LABELS.get(status, status)}"


def _render_task_column(jobs: Any) -> dict[str, Any] | None:
    with st.container(height=_COLUMN_HEIGHT, key="wb_tasks", autoscroll=False):
        with st.expander("新建任务", expanded=False):
            name = st.text_input("任务名称", key="wb_new_name")
            job_type = st.selectbox(
                "类型",
                sorted(_EXECUTABLE_JOB_TYPES | {"manual"}),
                key="wb_new_type",
            )
            if st.button("创建", key="wb_create"):
                if not name.strip():
                    st.warning("任务名称不能为空")
                else:
                    jobs.create(name.strip(), job_type=job_type, trigger="manual")
                    st.rerun()

        selected_id = st.session_state.get("wb_selected_job_id")
        options: list[dict[str, Any]] = []
        labels: list[str] = []
        for group_name, statuses in _STATUS_GROUPS:
            group_jobs = jobs.list(statuses=statuses)
            if not group_jobs:
                continue
            st.markdown(f"**{group_name}**")
            for job in group_jobs:
                options.append(job)
                labels.append(_label(job))
        if not options:
            st.info("还没有任务。新建一个，或在对话里说「生成这周周报」。")
            return None
        index = 0
        for position, job in enumerate(options):
            if job.get("id") == selected_id:
                index = position
                break
        choice = st.radio(
            "任务列表",
            labels,
            index=index,
            key="wb_job_radio",
            label_visibility="collapsed",
        )
        selected = options[labels.index(choice)]
        st.session_state.wb_selected_job_id = selected.get("id")
        return selected


def _run_office_skill(job: dict[str, Any]) -> None:
    from artpm_agent.skills.skill_router import SkillRouter

    factory = get_ui_runtime_factory()
    config = Config() if Config is not None else None
    context = {"database": factory.storage.business}
    tenant_context = _trusted_ui_tenant_context()
    if tenant_context is None:
        st.error("当前租户上下文不可用，无法安全执行周报任务。")
        return
    profile_id = _active_ui_profile_id(tenant_context)
    context.update(
        {
            "tenant_context": tenant_context,
            "profile_id": profile_id,
            "artifact_generator": get_artifact_generator(),
        }
    )
    if config is not None:
        context["config"] = config.get_all()
    router = SkillRouter(context)
    result = router.for_tenant(tenant_context).execute_skill(
        "weekly_report", {"trigger": "manual", "job_id": int(job["id"])}
    )
    if result.get("success"):
        st.session_state[_NOTICE_KEY] = {
            "kind": "success",
            "message": str(result.get("summary") or "周报已生成"),
        }
    else:
        st.session_state[_NOTICE_KEY] = {
            "kind": "error",
            "message": str(result.get("error") or "周报生成失败"),
        }


def _render_process_column(jobs: Any, job: dict[str, Any] | None) -> None:
    with st.container(height=_COLUMN_HEIGHT, key="wb_process", autoscroll=False):
        if job is None:
            st.info("在①选择或新建一个任务。")
            return
        job_name = escape(str(job.get("job_name") or "未命名任务"))
        status = str(job.get("status") or "")
        st.markdown(
            f'<div class="wb-process-title"><strong>{job_name}</strong>'
            f"<span>{escape(_STATUS_LABELS.get(status, status or '未知状态'))}</span></div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"{job.get('job_type') or '未分类'} · 由 {job.get('trigger') or '手动'} 触发"
        )
        current = job.get("status")
        steps: list[str] = [str(step) for step in _LIFECYCLE]
        if current not in steps:
            steps.append(str(current))
        reached = steps.index(current) if current in steps else -1
        for position, step in enumerate(steps):
            state = (
                "完成"
                if position < reached
                else ("当前" if position == reached else "待处理")
            )
            st.markdown(
                f'<div class="wb-step wb-step-{state}"><span aria-hidden="true"></span>'
                f"<strong>{escape(_STATUS_LABELS.get(step, step))}</strong>"
                f"<small>{state}</small></div>",
                unsafe_allow_html=True,
            )
        if job.get("inputs"):
            with st.expander("输入", expanded=False):
                st.json(job["inputs"])
        if job.get("outputs"):
            with st.expander("输出", expanded=False):
                st.json(job["outputs"])
        if job.get("job_type") in _EXECUTABLE_JOB_TYPES and current in {
            "draft",
            "queued",
            "failed",
        }:
            if st.button("执行周报技能", key="wb_run_skill"):
                _run_office_skill(job)
                st.rerun()
        elif job.get("job_type") not in _EXECUTABLE_JOB_TYPES:
            st.caption("该类型暂无执行器；定时巡检与派单在 M4 接入。")


def _render_artifact_column(jobs: Any, job: dict[str, Any] | None) -> None:
    with st.container(height=_COLUMN_HEIGHT, key="wb_artifacts", autoscroll=False):
        if job is None:
            st.info("选中任务后这里显示它的交付物。")
            return
        artifacts = jobs.list_artifacts(job.get("id"))
        if not artifacts:
            st.info("该任务还没有交付物。")
            return
        generator = get_artifact_generator()
        for artifact in artifacts:
            status = str(artifact.get("verification_status") or "pending")
            st.markdown(f"**{artifact.get('filename')}**")
            st.caption(
                f"核验 `{status}` · {artifact.get('size_bytes') or '?'} 字节 · "
                f"v{artifact.get('version') or 1}"
            )
            if status != "passed":
                st.warning("核验未通过，不提供下载。")
                continue
            stored = str(artifact.get("artifact_path") or "")
            if generator is None or not stored:
                st.code(stored or "（无存储路径）")
                continue
            path = generator.root / stored
            if path.is_file():
                st.download_button(
                    "下载",
                    data=path.read_bytes(),
                    file_name=str(artifact.get("filename") or path.name),
                    key=f"wb_dl_{artifact.get('id')}",
                )
            else:
                st.error(f"文件缺失：{stored}")


def workbench_page() -> None:
    """三栏工作台主入口。"""

    notice = st.session_state.pop(_NOTICE_KEY, None)
    if isinstance(notice, dict):
        message = str(notice.get("message") or "")
        if notice.get("kind") == "success":
            st.success(message)
        elif notice.get("kind") == "error":
            st.error(message)

    st.markdown('<div class="workbench-page-marker"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wb-page-head"><div><div class="page-kicker">项目执行</div>'
        "<h1>工作台</h1><p>从任务状态到可下载成果，所有关键动作集中在同一条工作流里。</p></div>"
        '<div class="wb-page-meta"><span>任务驱动</span><small>当前工作区</small></div></div>',
        unsafe_allow_html=True,
    )

    jobs = get_job_service()
    if jobs is None:
        st.warning("Job 服务不可用，工作台无法读取任务列表。")
        return
    col_task, col_process, col_artifact = st.columns([2.4, 3.4, 2.2], gap="medium")
    with col_task:
        st.markdown(
            '<div class="wb-column-caption"><b>01</b><strong>任务</strong>'
            "<span>选择或创建工作项</span></div>",
            unsafe_allow_html=True,
        )
        selected = _render_task_column(jobs)
    with col_process:
        st.markdown(
            '<div class="wb-column-caption"><b>02</b><strong>过程</strong>'
            "<span>查看状态与执行动作</span></div>",
            unsafe_allow_html=True,
        )
        _render_process_column(jobs, selected)
    with col_artifact:
        st.markdown(
            '<div class="wb-column-caption"><b>03</b><strong>成果</strong>'
            "<span>核验并下载交付物</span></div>",
            unsafe_allow_html=True,
        )
        _render_artifact_column(jobs, selected)


__all__ = ["workbench_page"]
