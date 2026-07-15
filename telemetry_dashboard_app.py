"""Standalone Streamlit telemetry dashboard for ArtPM Agent.

Run with:  streamlit run telemetry_dashboard_app.py

This is intentionally a SEPARATE entrypoint from the main ``app.py`` so the
operations dashboard can be opened without modifying the primary app's routing.
It reuses ``telemetry_dashboard.collect_dashboard`` / ``render_html``.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st

from artpm_agent.runtime.telemetry import AgentTelemetry, default_telemetry_db_path
from artpm_agent.runtime.telemetry_dashboard import collect_dashboard, render_html
from artpm_agent.utils.logger import get_logger

logger = get_logger(__name__)

st.set_page_config(page_title="ArtPM 遥测看板", page_icon="📊", layout="wide")
st.markdown("# 📊 ArtPM Agent · 遥测运营看板")


@st.cache_data(ttl=30)
def _load(window: int) -> str:
    try:
        telemetry = AgentTelemetry(db_path=default_telemetry_db_path(), enabled=True)
        return render_html(collect_dashboard(telemetry, window=window))
    except Exception as error:  # noqa: BLE001
        logger.warning("Telemetry dashboard load failed: %s", error)
        return f"<p>加载遥测数据失败：{error}</p>"


window = st.sidebar.slider("样本窗口", min_value=50, max_value=2000, value=500, step=50)
if st.sidebar.button("刷新"):
    _load.clear()

html = _load(window)
st.markdown(html, unsafe_allow_html=True)
