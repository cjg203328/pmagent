# ruff: noqa: E402,F405 - Streamlit may execute this page as a standalone script
"""可观测系统面板（集成进主应用导航，复用既有设计系统）。

本页面把「Token 消耗」与「连接 / 链接情况」两套看板原生渲染进主应用，
而非使用独立的 HTML 报告，从而与 对话 / 设置 面板共享导航、主题与控件风格。
数据全部读取自 ``telemetry.db``，且复用 ``telemetry_dashboard.collect_dashboard``
这一单一数据源，保证与独立看板口径一致。所有读取均为只读、best-effort。
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保项目根目录在 Python 路径中（Streamlit 多页面方式加载本文件时也能找到包）
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st
import pandas as pd

from artpm_agent.ui_helpers import render_page_header, get_logger  # noqa: F401
from artpm_agent.runtime.telemetry import AgentTelemetry, default_telemetry_db_path
from artpm_agent.runtime.telemetry_dashboard import collect_dashboard

logger = get_logger(__name__)

VIEW_NAME = "可观测"


# ───────────────────────────────────────────────────────────────
# 小型渲染助手（贴合 ui_style.py 的设计令牌）
# ───────────────────────────────────────────────────────────────
def _metric_rail(items: List[Dict[str, Any]]) -> None:
    """渲染一排 KPI 卡片，使用 .metric-rail / .metric-item 设计系统。

    items: [{"label", "value", "tone": "blue|amber|teal", "note": optional}]
    """
    cells: List[str] = []
    for it in items:
        tone = it.get("tone", "blue")
        note = it.get("note")
        note_html = f'<div class="metric-note">{note}</div>' if note else ""
        cells.append(
            '<div class="metric-item tone-{tone}">'
            '<div class="metric-label">{label}</div>'
            '<div class="metric-value">{value}</div>'
            "{note}</div>".format(tone=tone, label=it["label"], value=it["value"], note=note_html)
        )
    st.markdown(
        f'<div class="metric-rail">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def _fmt_money(v: Any) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v <= 0:
        return "¥0.00"
    if v < 0.01:
        return f"¥{v:.4f}"
    return f"¥{v:,.4f}"


def _fmt_pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _section(title: str, meta: str = "") -> None:
    st.markdown(
        f'<div class="section-heading"><strong>{title}</strong>'
        f"<span>{meta}</span></div>",
        unsafe_allow_html=True,
    )


def _empty_hint() -> None:
    st.info(
        "暂无遥测数据。开启遥测（默认已开启）并运行若干回合后，此处将展示 "
        "Token 消耗、连接链路与进化闭环的可观测指标。数据实时读取自 `telemetry.db`。"
    )


# 进化闭环阶段中文标签（与 chat_harness_integration 的 stage 取值对应）
_EVOLUTION_STAGE_LABELS = {
    "user_feedback": "用户反馈",
    "outcome_record": "回合结果记录",
    "auto_reflect": "自动复盘",
    "auto_consolidate": "自动知识炼化",
    "loop_run": "闭环运行",
}

# 健康条样式：复用设计系统 CSS 变量令牌，随主题一致（不再硬编码色值）
_HEALTH_STRIP_STYLE = """
<style>
.obs-health-strip{display:flex;gap:14px;align-items:center;flex-wrap:wrap;
  background:var(--pm-canvas);border:1px solid var(--pm-line-light);
  border-radius:12px;padding:8px 14px;margin-bottom:18px;}
.obs-health-strip .obs-title{font-size:13px;font-weight:600;color:var(--pm-ink);
  margin-right:2px;}
.obs-health-pill{display:inline-flex;align-items:center;gap:6px;
  font-size:12px;color:var(--pm-ink-secondary);}
.obs-health-pill .dot{width:8px;height:8px;border-radius:50%;display:inline-block;}
.obs-health-pill.ok .dot{background:var(--pm-success);}
.obs-health-pill.bad .dot{background:var(--pm-warning);}
</style>
"""


@st.cache_data(ttl=30)
def _load_dashboard(db_path: str, window: int) -> Dict[str, Any]:
    """缓存的看板聚合：避免每次交互（如拖动窗口滑块）重查 DB。

    ``collect_dashboard`` 已包含 token / 连接 / 进化闭环三类聚合，页面直接复用，
    不再重复查询 evolution_summary / recent_events。
    """
    tel = AgentTelemetry(db_path=db_path, enabled=True)
    return collect_dashboard(tel, window=window)


def _health_strip(
    token_ok: bool, conn_ok: bool, ev_ok: bool, ev_errors: int
) -> None:
    """顶部系统健康条：三支柱一目了然，故障无需滚动即可见。"""
    st.markdown(_HEALTH_STRIP_STYLE, unsafe_allow_html=True)
    pills = [
        ("Token", token_ok, "ok"),
        ("连接", conn_ok, "ok"),
        ("进化闭环", ev_ok, "ok" if ev_ok else "bad"),
    ]
    parts = []
    for name, ok, state in pills:
        extra = "" if ok else f" {ev_errors} 异常"
        parts.append(
            f'<span class="obs-health-pill {state}">'
            f'<span class="dot"></span>{name} {"正常" if ok else "异常"}{extra}</span>'
        )
    html = (
        '<div class="obs-health-strip">'
        '<span class="obs-title">系统健康</span>'
        + "".join(parts)
        + "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def _evolution_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        level = ev.get("level") or "info"
        rows.append(
            {
                "时间": ev.get("timestamp") or "—",
                "阶段": _EVOLUTION_STAGE_LABELS.get(
                    ev.get("stage", ""), ev.get("stage", "—")
                ),
                "等级": level,
                "摘要": (ev.get("message") or "")[:160],
            }
        )
    return rows


def _evolution_section(summary: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
    """进化闭环健康：回合结果 / 自动复盘 / 自动知识炼化的可观测性。

    数据由页面统一从缓存看板取（summary + events），本函数不再直接查库。
    """
    errors = int(summary.get("errors", 0))
    total = int(summary.get("events", 0))

    _section("进化闭环", "回合结果 / 自动复盘 / 自动知识炼化 的运行健康")

    if total == 0:
        st.success("进化闭环运行正常 🎉 窗口内暂无事件记录。")
        return

    _metric_rail(
        [
            {
                "label": "最近异常",
                "value": _fmt_int(errors),
                "tone": "amber" if errors else "teal",
                "note": "warning / error 事件数",
            },
            {
                "label": "闭环事件",
                "value": _fmt_int(total),
                "tone": "blue",
                "note": f"窗口内共 {_fmt_int(total)} 条",
            },
            {
                "label": "最后运行",
                "value": (summary.get("last_run") or "—"),
                "tone": "blue",
            },
            {
                "label": "健康状态",
                "value": ("正常" if not errors else f"{errors} 异常"),
                "tone": "teal" if not errors else "amber",
            },
        ]
    )

    rows = _evolution_rows(events)
    if rows:
        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
            height=min(360, 38 + max(len(rows), 1) * 36),
            column_config={
                "时间": st.column_config.TextColumn(width="small"),
                "阶段": st.column_config.TextColumn(width="small"),
                "等级": st.column_config.TextColumn(width="small"),
                "摘要": st.column_config.TextColumn(width="large"),
            },
        )
    else:
        st.caption("暂无事件明细。")


# ───────────────────────────────────────────────────────────────
# 数据转换（dict-of-aggregates → DataFrame）
# ───────────────────────────────────────────────────────────────
def _model_rows(by_model: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for model, agg in by_model.items():
        if not isinstance(agg, dict) or agg.get("samples", 0) == 0:
            continue
        rows.append(
            {
                "模型": model,
                "样本": agg.get("samples", 0),
                "输入 Token": int(agg.get("prompt_tokens", 0)),
                "输出 Token": int(agg.get("completion_tokens", 0)),
                "总 Token": int(agg.get("total_tokens", 0)),
                "成本 (USD)": round(float(agg.get("cost_usd", 0.0)), 4),
                "缓存命中率": _fmt_pct(agg.get("cache_hit_rate", 0)),
                "失败率": _fmt_pct(agg.get("failure_rate", 0)),
            }
        )
    return rows


def _provider_rows(by_provider: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for prov, agg in by_provider.items():
        if not isinstance(agg, dict):
            continue
        rows.append(
            {
                "Provider": prov,
                "总 Token": int(agg.get("total_tokens", 0)),
                "成本 (USD)": round(float(agg.get("cost_usd", 0.0)), 4),
                "回合": int(agg.get("turns", 0)),
                "连接尝试": int(agg.get("attempts", 0)),
                "连接成功率": _fmt_pct(agg.get("conn_success_rate", 0)),
            }
        )
    return rows


def _health_rows(health: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for h in health:
        if not isinstance(h, dict):
            continue
        unavailable = h.get("unavailable")
        if unavailable is True:
            state = "熔断中"
        elif h.get("success_rate", 1) >= 0.999:
            state = "正常"
        else:
            state = "存在失败"
        rows.append(
            {
                "Provider": h.get("provider", ""),
                "模型": h.get("model", ""),
                "端点": h.get("endpoint", "") or "—",
                "尝试": int(h.get("attempts", 0)),
                "成功率": _fmt_pct(h.get("success_rate", 0)),
                "最近错误": h.get("last_error") or "—",
                "状态": state,
                "最近探测": h.get("last_seen") or "—",
            }
        )
    return rows


# ───────────────────────────────────────────────────────────────
# 主页面
# ───────────────────────────────────────────────────────────────
def observability_page() -> None:
    """可观测系统面板：Token 消耗 + 连接 / 链接情况。"""
    render_page_header(
        "可观测系统",
        "Token 消耗与连接链路",
        meta="实时读取自 telemetry.db · 与运营看板口径一致",
    )

    col_window, col_refresh = st.columns([4, 1])
    with col_window:
        window = st.slider(
            "样本窗口（最近 N 条记录）",
            min_value=50,
            max_value=2000,
            value=500,
            step=50,
            key="observability_window",
        )
    with col_refresh:
        st.markdown("<div style='height:26px'></div>", unsafe_allow_html=True)
        if st.button("刷新数据", key="observability_refresh", use_container_width=True):
            _load_dashboard.clear()
            st.rerun()

    db_path = default_telemetry_db_path()
    try:
        dash = _load_dashboard(db_path, window)
    except Exception as error:  # noqa: BLE001
        logger.warning("可观测系统数据加载失败: %s", error)
        st.error(f"加载遥测数据失败：{error}")
        return

    token_summary: Dict[str, Any] = dash.get("token_summary", {"samples": 0})
    conn_summary: Dict[str, Any] = dash.get("connection_summary", {"samples": 0})
    has_token = int(token_summary.get("samples", 0)) > 0
    has_conn = int(conn_summary.get("samples", 0)) > 0

    # 进化闭环健康（独立于 Token/连接 的第三支柱）—— 直接复用缓存看板数据
    ev_summary: Dict[str, Any] = dash.get(
        "evolution_summary", {"events": 0, "errors": 0}
    )
    ev_events: List[Dict[str, Any]] = dash.get("evolution_events", []) or []
    ev_total = int(ev_summary.get("events", 0))
    ev_errors = int(ev_summary.get("errors", 0))

    if not has_token and not has_conn and ev_total == 0:
        _empty_hint()
        return

    # 顶部系统健康条：三支柱一目了然，故障无需滚动即可见
    _health_strip(has_token, has_conn, ev_errors == 0, ev_errors)

    # 端点健康：尽量带上 gateway 熔断器冷却状态（gateway 为实时对象，不进缓存）
    health = dash.get("endpoint_health", []) or []
    agent = st.session_state.get("agent")
    gateway = getattr(agent, "model_gateway", None) if agent is not None else None
    if gateway is not None and hasattr(gateway, "is_model_available"):
        try:
            live_tel = AgentTelemetry(db_path=db_path, enabled=True)
            health = live_tel.endpoint_health(window, gateway=gateway)
        except Exception:  # noqa: BLE001
            logger.debug("端点健康叠加熔断器状态失败，沿用连接事件统计")

    # ════════════ Token 消耗 ════════════
    if has_token:
        _section("Token 消耗", "输入 / 输出 / 缓存命中 与估算成本")
        cached = int(token_summary.get("cached_tokens", 0))
        total = int(token_summary.get("total_tokens", 0))
        cache_save = (cached / total) if total > 0 else 0.0
        _metric_rail(
            [
                {
                    "label": "总 Token",
                    "value": _fmt_int(total),
                    "tone": "blue",
                    "note": f"输入 {_fmt_int(token_summary.get('prompt_tokens', 0))} · 输出 {_fmt_int(token_summary.get('completion_tokens', 0))}",
                },
                {
                    "label": "估算成本",
                    "value": _fmt_money(token_summary.get("cost_usd", 0)),
                    "tone": "teal",
                    "note": "按公开价目表估算",
                },
                {
                    "label": "缓存命中 Token",
                    "value": _fmt_int(cached),
                    "tone": "amber",
                    "note": f"占总 Token {_fmt_pct(cache_save)}",
                },
                {
                    "label": "样本回合",
                    "value": _fmt_int(token_summary.get("samples", 0)),
                    "tone": "blue",
                },
            ]
        )

        # 按模型 / 按 Provider 明细
        by_model = _model_rows(dash.get("by_model", {}) or {})
        by_provider = _provider_rows(dash.get("by_provider", {}) or {})
        if by_model or by_provider:
            m_col, p_col = st.columns(2)
            with m_col:
                _section("按模型", "Token 与成本明细")
                if by_model:
                    st.dataframe(
                        by_model,
                        use_container_width=True,
                        hide_index=True,
                        height=min(360, 38 + max(len(by_model), 1) * 36),
                    )
                else:
                    st.caption("暂无分模型数据")
            with p_col:
                _section("按 Provider", "连接与成本聚合")
                if by_provider:
                    st.dataframe(
                        by_provider,
                        use_container_width=True,
                        hide_index=True,
                        height=min(360, 38 + max(len(by_provider), 1) * 36),
                    )
                else:
                    st.caption("暂无分 Provider 数据")

        # Token 趋势
        trend = dash.get("trend", []) or []
        token_points = [
            {"时间": r.get("timestamp"), "总Token": int(r.get("total_tokens", 0) or 0)}
            for r in trend
            if isinstance(r, dict)
        ]
        if token_points:
            _section("Token 趋势", "逐条请求的累计 Token")
            df = pd.DataFrame(token_points)
            st.line_chart(df.set_index("时间")["总Token"], use_container_width=True)
    else:
        _section("Token 消耗", "暂无记录")
        st.caption("尚未采集到任何成功回合的 Token 数据。")

    # ════════════ 连接 / 链接情况 ════════════
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    if has_conn:
        _section("连接 / 链接情况", "每次 failover 候选尝试的连通性")
        _metric_rail(
            [
                {
                    "label": "连接成功率",
                    "value": _fmt_pct(conn_summary.get("success_rate", 0)),
                    "tone": "teal",
                    "note": f"成功 {_fmt_int(conn_summary.get('ok', 0))} / {_fmt_int(conn_summary.get('samples', 0))}",
                },
                {
                    "label": "连接尝试",
                    "value": _fmt_int(conn_summary.get("samples", 0)),
                    "tone": "blue",
                },
                {
                    "label": "平均延迟",
                    "value": f"{conn_summary.get('avg_latency_ms', 0):.0f} ms",
                    "tone": "blue",
                },
                {
                    "label": "失败次数",
                    "value": _fmt_int(conn_summary.get("failed", 0)),
                    "tone": "amber" if conn_summary.get("failed", 0) else "teal",
                },
            ]
        )

        # 错误类型分布 + 连接趋势
        errors: Dict[str, int] = conn_summary.get("error_breakdown", {}) or {}
        conn_trend = dash.get("connection_trend", []) or []
        e_col, t_col = st.columns(2)
        with e_col:
            _section("错误类型分布", "按失败原因归类")
            if errors:
                err_df = pd.DataFrame(
                    [{"错误类型": k, "次数": v} for k, v in sorted(errors.items(), key=lambda kv: -kv[1])]
                )
                st.bar_chart(err_df.set_index("错误类型")["次数"], use_container_width=True)
            else:
                st.caption("窗口内无失败连接 🎉")
        with t_col:
            _section("连接趋势", "成功(1)/失败(0) 随时间")
            if conn_trend:
                ct_df = pd.DataFrame(
                    [
                        {
                            "时间": r.get("timestamp"),
                            "成功": 1 if r.get("ok") else 0,
                            "延迟(ms)": r.get("latency_ms") or 0,
                        }
                        for r in conn_trend
                        if isinstance(r, dict)
                    ]
                )
                st.line_chart(ct_df.set_index("时间")["成功"], use_container_width=True)
            else:
                st.caption("暂无连接趋势数据")

        # 端点健康表
        _section("端点健康", "按 Provider / 模型 / 端点，含熔断器冷却态")
        health_rows = _health_rows(health)
        if health_rows:
            st.dataframe(
                health_rows,
                use_container_width=True,
                hide_index=True,
                height=min(360, 38 + max(len(health_rows), 1) * 36),
                column_config={
                    "端点": st.column_config.TextColumn(width="large"),
                    "最近错误": st.column_config.TextColumn(width="medium"),
                },
            )
        else:
            st.caption("暂无端点健康数据")
    else:
        _section("连接 / 链接情况", "暂无记录")
        st.caption("尚未采集到任何连接事件。")

    # ════════════ 进化闭环 ════════════
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    _evolution_section(ev_summary, ev_events)


if __name__ == "__main__":
    observability_page()
