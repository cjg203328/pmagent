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
    trend = telemetry.latency_trend(window) if hasattr(telemetry, "latency_trend") else []
    return {
        "overall": overall,
        "by_task_type": by_task,
        "by_model": by_model,
        "trend": trend,
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

    if not samples:
        body = '<p class="muted">暂无遥测数据。开启遥测（默认开）并运行若干回合后，此处将展示性能看板。</p>'
    else:
        avg = overall.get("avg_latency_ms", 0)
        p95 = overall.get("p95_latency_ms", 0)
        body = f"""
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
