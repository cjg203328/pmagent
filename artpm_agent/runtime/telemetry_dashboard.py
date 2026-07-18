"""Telemetry operations dashboard (运营看板).

Reads ``AgentTelemetry`` aggregates and renders a self-contained HTML report
(light theme, no external assets) plus a CLI to export it. A dedicated
Streamlit entrypoint (``telemetry_dashboard_app.py``) reuses the same data so
the dashboard can be viewed in-app or as a static file — without touching the
main ``app.py`` routing.

Everything here is additive and read-only against ``telemetry.db``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from artpm_agent.utils.logger import get_logger

logger = get_logger(__name__)


def _esc(text: Any) -> str:
    s = "" if text is None else str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def collect_dashboard(telemetry: Any, window: int = 500) -> Dict[str, Any]:
    """Gather all aggregates the dashboard needs from a telemetry store."""
    overall = telemetry.summarize(window) if hasattr(telemetry, "summarize") else {"samples": 0}
    by_task = telemetry.by_task_type(window) if hasattr(telemetry, "by_task_type") else {}
    by_model = telemetry.by_model(window) if hasattr(telemetry, "by_model") else {}
    trend = telemetry.latency_trend(window) if hasattr(telemetry, "latency_trend") else {}

    # ── Observability: token + connection signals (additive) ──
    token_summary = (
        telemetry.token_summary(window)
        if hasattr(telemetry, "token_summary")
        else {"samples": 0}
    )
    connection_summary = (
        telemetry.connection_summary(window)
        if hasattr(telemetry, "connection_summary")
        else {"samples": 0}
    )
    by_provider = (
        telemetry.by_provider(window) if hasattr(telemetry, "by_provider") else {}
    )
    endpoint_health = (
        telemetry.endpoint_health(window)
        if hasattr(telemetry, "endpoint_health")
        else []
    )
    connection_trend = (
        telemetry.connection_trend(window)
        if hasattr(telemetry, "connection_trend")
        else []
    )
    # ── Observability: evolution-loop events (additive) ──
    evolution_summary = (
        telemetry.evolution_summary(window)
        if hasattr(telemetry, "evolution_summary")
        else {"events": 0, "errors": 0, "by_stage": {}}
    )
    evolution_events = (
        telemetry.recent_events(window)
        if hasattr(telemetry, "recent_events")
        else []
    )
    return {
        "overall": overall,
        "by_task_type": by_task,
        "by_model": by_model,
        "trend": trend,
        "token_summary": token_summary,
        "connection_summary": connection_summary,
        "by_provider": by_provider,
        "endpoint_health": endpoint_health,
        "connection_trend": connection_trend,
        "evolution_summary": evolution_summary,
        "evolution_events": evolution_events,
        "window": window,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def _rate_bar(value: float, color: str) -> str:
    pct = max(0.0, min(1.0, float(value))) * 100.0
    return (
        f'<div class="bar"><div class="bar-fill" style="width:{pct:.1f}%;'
        f'background:{color}"></div></div>'
        f'<span class="bar-val">{pct:.1f}%</span>'
    )


def _latency_sparkline(trend: List[Dict[str, Any]], width: int = 600, height: int = 120) -> str:
    if not trend:
        return '<p class="muted">暂无延迟数据</p>'
    lats = [max(0.0, float(t.get("latency_ms") or 0)) for t in trend]
    max_lat = max(lats) or 1.0
    n = len(lats)
    pad = 8
    step_x = (width - 2 * pad) / max(1, n - 1) if n > 1 else 0
    points = []
    for i, lat in enumerate(lats):
        x = pad + (i * step_x if n > 1 else width / 2)
        y = height - pad - (lat / max_lat) * (height - 2 * pad)
        points.append((x, y, lat, trend[i].get("success", True)))
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in points)
    circles = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{("#d9534f" if not ok else "#2f7ed8")}"/>'
        for x, y, _, ok in points
    )
    # baseline + max label
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="none" '
        f'role="img" aria-label="延迟趋势">'
        f'<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#ddd"/>'
        f'<polyline fill="none" stroke="#2f7ed8" stroke-width="2" points="{path}"/>'
        f"{circles}"
        f'<text x="{pad}" y="{pad-0}" fill="#888" font-size="10">峰值 {max_lat:.0f}ms</text>'
        f"</svg>"
    )


def render_html(dashboard: Dict[str, Any]) -> str:
    """Render the dashboard as a self-contained HTML document (light theme)."""
    overall = dashboard.get("overall", {}) or {}
    samples = overall.get("samples", 0)
    by_task = dashboard.get("by_task_type", {}) or {}
    by_model = dashboard.get("by_model", {}) or {}
    trend = dashboard.get("trend", []) or []
    token_summary = dashboard.get("token_summary", {}) or {}
    connection_summary = dashboard.get("connection_summary", {}) or {}
    by_provider = dashboard.get("by_provider", {}) or {}
    endpoint_health = dashboard.get("endpoint_health", []) or []
    connection_trend = dashboard.get("connection_trend", []) or []
    evolution_summary = dashboard.get("evolution_summary", {}) or {}
    evolution_events = dashboard.get("evolution_events", []) or []

    sections: List[str] = []

    if not samples:
        sections.append(
            '<p class="muted">暂无遥测数据。开启遥测（默认开）并运行若干回合后，此处将展示性能看板。</p>'
        )
    else:
        avg = overall.get("avg_latency_ms", 0)
        p95 = overall.get("p95_latency_ms", 0)
        sections.append(
            f"""
        <div class="cards">
          {_card("样本数", samples)}
          {_card("平均延迟", f"{avg:.0f} ms")}
          {_card("P95 延迟", f"{p95:.0f} ms")}
          {_card("回退率", f"{overall.get('fallback_rate',0)*100:.1f}%")}
          {_card("缓存命中率", f"{overall.get('cache_hit_rate',0)*100:.1f}%")}
          {_card("失败率", f"{overall.get('failure_rate',0)*100:.1f}%")}
        </div>

        <h2>延迟趋势</h2>
        {_latency_sparkline(trend)}

        <h2>按任务类型</h2>
        {_table(by_task)}

        <h2>按模型</h2>
        {_table(by_model)}
        """
        )

    # ── Token 消耗面板 ──
    if token_summary.get("samples", 0):
        sections.append(
            f"""
        <h2>Token 消耗</h2>
        <div class="cards">
          {_card("总 Tokens", token_summary.get("total_tokens", 0))}
          {_card("输入 Tokens", token_summary.get("prompt_tokens", 0))}
          {_card("输出 Tokens", token_summary.get("completion_tokens", 0))}
          {_card("缓存命中 Tokens", token_summary.get("cached_tokens", 0))}
          {_card("估算成本", f"${token_summary.get('cost_usd', 0):.4f}")}
        </div>

        <h3>Token 趋势</h3>
        {_token_trend_sparkline(trend)}

        <h3>按模型 Token / 成本</h3>
        {_token_by_model_table(by_model)}

        <h3>按 Provider</h3>
        {_by_provider_table(by_provider)}
        """
        )

    # ── 连接 / 链接情况面板 ──
    if connection_summary.get("samples", 0):
        eb = connection_summary.get("error_breakdown", {}) or {}
        sections.append(
            f"""
        <h2>连接 / 链接情况</h2>
        <div class="cards">
          {_card("连接尝试", connection_summary.get("samples", 0))}
          {_card("成功率", f"{connection_summary.get('success_rate',0)*100:.1f}%")}
          {_card("平均延迟", f"{connection_summary.get('avg_latency_ms',0):.0f} ms")}
          {_card("失败次数", connection_summary.get("failed", 0))}
        </div>

        <h3>连接趋势</h3>
        {_connection_sparkline(connection_trend)}

        <h3>错误类型分布</h3>
        {_error_breakdown(eb)}

        <h3>端点健康</h3>
        {_endpoint_health_table(endpoint_health)}
        """
        )

    # ── 进化闭环面板 ──
    ev_events = int(evolution_summary.get("events", 0))
    if ev_events:
        ev_errors = int(evolution_summary.get("errors", 0))
        by_stage = evolution_summary.get("by_stage", {}) or {}
        stage_label = {
            "user_feedback": "用户反馈",
            "outcome_record": "回合结果记录",
            "auto_reflect": "自动复盘",
            "auto_consolidate": "自动知识炼化",
        }
        stage_rows = "".join(
            f"<tr><td>{_esc(stage_label.get(k, k))}</td><td>{v}</td></tr>"
            for k, v in sorted(by_stage.items(), key=lambda kv: -kv[1])
        )
        sections.append(
            f"""
        <h2>进化闭环</h2>
        <div class="cards">
          {_card("闭环事件", ev_events)}
          {_card("最近异常", ev_errors)}
          {_card("最后运行", evolution_summary.get("last_run") or "—")}
        </div>
        <h3>按阶段分布</h3>
        <table><thead><tr><th>阶段</th><th>事件数</th></tr></thead>
        <tbody>{stage_rows}</tbody></table>
        <h3>最近事件</h3>
        {_evolution_table(evolution_events)}
        """
        )

    body = "\n".join(sections)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ArtPM Agent · 遥测运营看板</title>
<style>
  :root {{ --bg:#f7f8fa; --card:#ffffff; --ink:#1f2329; --muted:#8a9099;
          --line:#e6e8eb; --accent:#2f7ed8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
         padding:28px; line-height:1.5; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:20px; }}
  h2 {{ font-size:16px; margin:28px 0 10px; border-left:3px solid var(--accent);
        padding-left:8px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
           gap:12px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:14px 16px; }}
  .card .k {{ color:var(--muted); font-size:12px; }}
  .card .v {{ font-size:22px; font-weight:600; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
  th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line);
           font-size:13px; }}
  th {{ background:#fafbfc; color:var(--muted); font-weight:600; }}
  tr:last-child td {{ border-bottom:none; }}
  .bar {{ height:8px; background:#eef0f3; border-radius:6px; overflow:hidden;
          display:inline-block; width:120px; vertical-align:middle; }}
  .bar-fill {{ height:100%; }}
  .bar-val {{ margin-left:8px; font-size:12px; color:var(--muted); }}
  .muted {{ color:var(--muted); }}
  svg {{ background:var(--card); border:1px solid var(--line); border-radius:12px; }}
  h3 {{ font-size:14px; margin:22px 0 8px; color:var(--muted); font-weight:600; }}
  .eb {{ margin-top:6px; }}
  .eb-row {{ display:flex; align-items:center; gap:8px; padding:4px 0; font-size:13px; }}
  .eb-label {{ min-width:170px; color:var(--ink); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>ArtPM Agent · 遥测运营看板</h1>
  <div class="sub">生成于 {_esc(dashboard.get('generated_at',''))} · 窗口 {_esc(dashboard.get('window',''))} 样本</div>
  {body}
</div>
</body>
</html>"""
    return html


def _card(key: str, value: Any) -> str:
    return f'<div class="card"><div class="k">{_esc(key)}</div><div class="v">{_esc(value)}</div></div>'


def _table(groups: Dict[str, Dict[str, Any]]) -> str:
    if not groups:
        return '<p class="muted">暂无数据</p>'
    rows = []
    for name, m in sorted(groups.items(), key=lambda kv: -(kv[1].get("samples", 0))):
        rows.append(
            "<tr>"
            f"<td>{_esc(name)}</td>"
            f"<td>{m.get('samples',0)}</td>"
            f"<td>{m.get('avg_latency_ms',0):.0f} ms</td>"
            f"<td>{m.get('p95_latency_ms',0):.0f} ms</td>"
            f"<td>{_rate_bar(m.get('fallback_rate',0), '#f0ad4e')}</td>"
            f"<td>{_rate_bar(m.get('cache_hit_rate',0), '#5cb85c')}</td>"
            f"<td>{_rate_bar(m.get('failure_rate',0), '#d9534f')}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>名称</th><th>样本</th><th>平均延迟</th><th>P95</th>"
        "<th>回退率</th><th>缓存命中</th><th>失败率</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


# ── Observability render helpers (token + connection) ──

_ERROR_LABELS = {
    "auth": "鉴权失败",
    "not_found": "资源不存在",
    "rate_limit": "限流",
    "server_error": "服务端错误",
    "timeout": "超时/连接失败",
    "vision_unsupported": "不支持视觉",
    "other": "其他",
}


def _token_trend_sparkline(trend: List[Dict[str, Any]], width: int = 600, height: int = 120) -> str:
    if not trend:
        return '<p class="muted">暂无 Token 数据</p>'
    vals = [max(0, int(t.get("total_tokens") or 0)) for t in trend]
    max_v = max(vals) or 1
    n = len(vals)
    pad = 8
    step_x = (width - 2 * pad) / max(1, n - 1) if n > 1 else 0
    points = []
    for i, v in enumerate(vals):
        x = pad + (i * step_x if n > 1 else width / 2)
        y = height - pad - (v / max_v) * (height - 2 * pad)
        points.append((x, y, v))
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in points)
    circles = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="#7e57c2"/>' for x, y, _ in points
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="none" '
        f'role="img" aria-label="Token 趋势">'
        f'<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#ddd"/>'
        f'<polyline fill="none" stroke="#7e57c2" stroke-width="2" points="{path}"/>'
        f"{circles}"
        f'<text x="{pad}" y="{pad}" fill="#888" font-size="10">峰值 {max_v} tokens</text>'
        f"</svg>"
    )


def _connection_sparkline(conns: List[Dict[str, Any]], width: int = 600, height: int = 120) -> str:
    if not conns:
        return '<p class="muted">暂无连接数据</p>'
    n = len(conns)
    pad = 8
    step_x = (width - 2 * pad) / max(1, n - 1) if n > 1 else 0
    bars = []
    for i, c in enumerate(conns):
        x = pad + (i * step_x if n > 1 else width / 2)
        color = "#5cb85c" if bool(c.get("ok")) else "#d9534f"
        bars.append(
            f'<rect x="{x-3:.1f}" y="{pad}" width="6" height="{height-2*pad}" '
            f'fill="{color}" opacity="0.85"/>'
        )
    ok_n = sum(1 for c in conns if c.get("ok"))
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="none" '
        f'role="img" aria-label="连接趋势">'
        f'<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#ddd"/>'
        f'{"".join(bars)}'
        f'<text x="{pad}" y="{pad}" fill="#888" font-size="10">成功 {ok_n}/{n}</text>'
        f"</svg>"
    )


def _error_breakdown(breakdown: Dict[str, int]) -> str:
    if not breakdown:
        return '<p class="muted">窗口内无连接错误</p>'
    total = sum(breakdown.values()) or 1
    rows = []
    for et, cnt in sorted(breakdown.items(), key=lambda kv: -kv[1]):
        pct = cnt / total
        label = _ERROR_LABELS.get(et, et)
        rows.append(
            f'<div class="eb-row"><span class="eb-label">{_esc(label)} ({_esc(et)})</span>'
            f"{_rate_bar(pct, '#d9534f')}</div>"
        )
    return '<div class="eb">' + "".join(rows) + "</div>"


def _token_by_model_table(by_model: Dict[str, Dict[str, Any]]) -> str:
    if not by_model:
        return '<p class="muted">暂无数据</p>'
    rows = []
    for name, m in sorted(
        by_model.items(), key=lambda kv: -(kv[1].get("total_tokens", 0))
    ):
        rows.append(
            "<tr>"
            f"<td>{_esc(name)}</td>"
            f"<td>{m.get('samples',0)}</td>"
            f"<td>{m.get('total_tokens',0)}</td>"
            f"<td>{m.get('prompt_tokens',0)}</td>"
            f"<td>{m.get('completion_tokens',0)}</td>"
            f"<td>${m.get('cost_usd',0):.4f}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>模型</th><th>样本</th><th>总 Tokens</th><th>输入</th><th>输出</th><th>成本</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _by_provider_table(by_provider: Dict[str, Dict[str, Any]]) -> str:
    if not by_provider:
        return '<p class="muted">暂无数据</p>'
    rows = []
    for name, m in sorted(
        by_provider.items(), key=lambda kv: -(kv[1].get("cost_usd", 0))
    ):
        conn_rate = m.get("conn_success_rate")
        conn_rate_s = f"{conn_rate*100:.1f}%" if conn_rate is not None else "—"
        rows.append(
            "<tr>"
            f"<td>{_esc(name)}</td>"
            f"<td>{m.get('turns',0)}</td>"
            f"<td>{m.get('attempts', m.get('turns',0))}</td>"
            f"<td>{m.get('total_tokens',0)}</td>"
            f"<td>${m.get('cost_usd',0):.4f}</td>"
            f"<td>{conn_rate_s}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Provider</th><th>回合数</th><th>连接尝试</th><th>总 Tokens</th>"
        "<th>成本</th><th>连接成功率</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _endpoint_health_table(health: List[Dict[str, Any]]) -> str:
    if not health:
        return '<p class="muted">暂无端点数据</p>'
    rows = []
    for h in health:
        sr = float(h.get("success_rate", 0) or 0)
        color = "#5cb85c" if sr >= 0.99 else ("#f0ad4e" if sr >= 0.8 else "#d9534f")
        unav = h.get("unavailable")
        unav_s = "—" if unav is None else ("熔断中" if unav else "正常")
        rows.append(
            "<tr>"
            f"<td>{_esc(h.get('provider',''))}</td>"
            f"<td>{_esc(h.get('model',''))}</td>"
            f"<td>{_esc(h.get('endpoint','')) or '—'}</td>"
            f"<td>{h.get('attempts',0)}</td>"
            f"<td>{_rate_bar(sr, color)}</td>"
            f"<td>{_esc(h.get('last_error') or '—')}</td>"
            f"<td>{unav_s}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Provider</th><th>模型</th><th>端点</th><th>尝试</th>"
        "<th>成功率</th><th>最近错误</th><th>熔断</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


_EVOLUTION_STAGE_LABELS = {
    "user_feedback": "用户反馈",
    "outcome_record": "回合结果记录",
    "auto_reflect": "自动复盘",
    "auto_consolidate": "自动知识炼化",
}


def _evolution_table(events: List[Dict[str, Any]]) -> str:
    if not events:
        return '<p class="muted">暂无事件</p>'
    rows = []
    for ev in events[:50]:
        if not isinstance(ev, dict):
            continue
        stage = _EVOLUTION_STAGE_LABELS.get(ev.get("stage", ""), ev.get("stage", "—"))
        level = _esc(ev.get("level") or "info")
        msg = _esc((ev.get("message") or "")[:160])
        ts = _esc(ev.get("timestamp") or "—")
        rows.append(
            "<tr>"
            f"<td>{ts}</td><td>{_esc(stage)}</td>"
            f"<td>{level}</td><td>{msg}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>时间</th><th>阶段</th><th>等级</th><th>摘要</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render the ArtPM telemetry dashboard.")
    parser.add_argument("--db", default=None, help="Path to telemetry.db (default: data/telemetry.db)")
    parser.add_argument("--out", default="telemetry_report.html", help="Output HTML path")
    parser.add_argument("--window", type=int, default=500, help="Sample window size")
    args = parser.parse_args(argv)

    try:
        from artpm_agent.runtime.telemetry import AgentTelemetry, default_telemetry_db_path

        db_path = args.db or default_telemetry_db_path()
        telemetry = AgentTelemetry(db_path=db_path, enabled=True)
        dashboard = collect_dashboard(telemetry, window=args.window)
        html = render_html(dashboard)
        out_path = args.out
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"Dashboard written to {out_path} (samples={dashboard['overall'].get('samples',0)})")
        return 0
    except Exception as error:  # noqa: BLE001
        logger.error("Failed to render telemetry dashboard: %s", error)
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
