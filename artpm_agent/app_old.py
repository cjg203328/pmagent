"""
ArtPM - Professional Project Management Tool
Inspired by Cursor/Linear design philosophy
"""
import streamlit as st
from datetime import datetime, timedelta
from pathlib import Path
import sys
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent))

try:
    from database.models import DatabaseManager
    from parsers.excel_parser import ExcelQuoteParser
    from core.token_monitor import TokenMonitor, TokenBudgetManager
    AVAILABLE = True
except:
    AVAILABLE = False

st.set_page_config(page_title="ArtPM", page_icon="◆", layout="wide", initial_sidebar_state="collapsed")

# Professional CSS - Cursor/Linear inspired
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

    * {
        font-family: 'Inter', -apple-system, sans-serif;
        -webkit-font-smoothing: antialiased;
    }

    .main {
        background: #000;
        padding: 0;
        padding-top: 64px !important;
    }

    .block-container {
        padding-top: 64px !important;
        max-width: 100% !important;
    }

    #MainMenu, footer, header {display: none;}

    /* Top bar */
    .topbar {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 48px;
        background: rgba(0,0,0,0.95);
        backdrop-filter: blur(20px);
        border-bottom: 1px solid rgba(255,255,255,0.08);
        z-index: 9999;
        display: flex;
        align-items: center;
        padding: 0 1.5rem;
        z-index: 1000;
    }

    .logo {
        font-size: 0.9rem;
        font-weight: 600;
        color: #fff;
        letter-spacing: -0.02em;
    }

    .nav {
        display: flex;
        gap: 0.5rem;
        margin-left: 2rem;
    }

    .nav-item {
        padding: 0.375rem 0.75rem;
        color: rgba(255,255,255,0.55);
        font-size: 0.8125rem;
        font-weight: 500;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.15s;
    }

    .nav-item:hover {
        background: rgba(255,255,255,0.05);
        color: rgba(255,255,255,0.85);
    }

    .nav-item.active {
        background: rgba(255,255,255,0.1);
        color: #fff;
    }

    /* Content */
    .content {
        margin-top: 48px;
        padding: 2rem 1.5rem;
        max-width: 1400px;
    }

    /* Page title */
    .page-title {
        font-size: 1.5rem;
        font-weight: 600;
        color: #fff;
        margin-bottom: 1.5rem;
    }

    /* Cards */
    .card {
        background: rgba(255,255,255,0.02);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        transition: all 0.15s;
    }

    .card:hover {
        background: rgba(255,255,255,0.04);
        border-color: rgba(255,255,255,0.1);
    }

    /* Metric */
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 1rem;
        margin-bottom: 2rem;
    }

    .metric-card {
        background: rgba(255,255,255,0.02);
        border: 1px solid rgba(255,255,255,0.05);
        border-radius: 8px;
        padding: 1rem;
    }

    .metric-label {
        font-size: 0.75rem;
        color: rgba(255,255,255,0.4);
        font-weight: 500;
        margin-bottom: 0.5rem;
    }

    .metric-value {
        font-size: 1.75rem;
        font-weight: 600;
        color: #fff;
    }

    .metric-delta {
        font-size: 0.8125rem;
        color: rgba(255,255,255,0.45);
        margin-top: 0.25rem;
    }

    /* Table */
    .project-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.875rem 1rem;
        background: rgba(255,255,255,0.02);
        border: 1px solid rgba(255,255,255,0.05);
        border-radius: 6px;
        margin-bottom: 0.5rem;
        transition: all 0.15s;
    }

    .project-row:hover {
        background: rgba(255,255,255,0.04);
        border-color: rgba(255,255,255,0.08);
    }

    .project-name {
        font-weight: 500;
        color: #fff;
        font-size: 0.875rem;
    }

    .project-client {
        font-size: 0.8125rem;
        color: rgba(255,255,255,0.4);
        margin-top: 0.125rem;
    }

    /* Status pill */
    .status {
        padding: 0.25rem 0.625rem;
        border-radius: 4px;
        font-size: 0.6875rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.02em;
    }

    .status-active {
        background: rgba(52, 211, 153, 0.1);
        color: #34d399;
    }

    .status-pending {
        background: rgba(251, 191, 36, 0.1);
        color: #fbbf24;
    }

    /* Buttons */
    .stButton > button {
        background: rgba(255,255,255,0.06);
        color: #fff;
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 6px;
        font-weight: 500;
        padding: 0.5rem 1rem;
        font-size: 0.8125rem;
        transition: all 0.15s;
    }

    .stButton > button:hover {
        background: rgba(255,255,255,0.1);
        border-color: rgba(255,255,255,0.15);
    }

    /* Input */
    .stTextInput > div > div > input {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 6px;
        color: #fff;
        font-size: 0.875rem;
    }

    .stTextInput > div > div > input:focus {
        border-color: rgba(255,255,255,0.15);
    }

    /* File uploader */
    .stFileUploader {
        background: rgba(255,255,255,0.02);
        border: 1px dashed rgba(255,255,255,0.1);
        border-radius: 8px;
        padding: 2rem;
    }
</style>
""", unsafe_allow_html=True)


def init():
    if "view" not in st.session_state:
        st.session_state.view = "overview"

    if AVAILABLE:
        if "db" not in st.session_state:
            st.session_state.db = DatabaseManager()
        if "parser" not in st.session_state:
            st.session_state.parser = ExcelQuoteParser()
        if "monitor" not in st.session_state:
            st.session_state.monitor = TokenMonitor()


def topbar():
    """Cursor-style top bar with real navigation"""
    st.markdown(f"""
    <div class="topbar">
        <div class="logo">ArtPM</div>
    </div>
    <div style="height: 20px;"></div>
    """, unsafe_allow_html=True)

    # Real navigation
    col1, col2, col3, col4, col5 = st.columns([1,1,1,1,8])
    with col1:
        if st.button("Overview", use_container_width=True):
            st.session_state.view = "overview"
            st.rerun()
    with col2:
        if st.button("Projects", use_container_width=True):
            st.session_state.view = "projects"
            st.rerun()
    with col3:
        if st.button("Upload", use_container_width=True):
            st.session_state.view = "upload"
            st.rerun()
    with col4:
        if st.button("Analytics", use_container_width=True):
            st.session_state.view = "analytics"
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)


def overview():
    """Overview page"""

    if AVAILABLE and st.session_state.db:
        stats = st.session_state.db.get_project_stats()
    else:
        stats = {"total_projects": 0, "in_progress": 0, "completed": 0, "total_revenue": 0}

    # Metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">TOTAL PROJECTS</div>
            <div class="metric-value">{stats['total_projects']}</div>
            <div class="metric-delta">{stats['in_progress']} active</div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">REVENUE</div>
            <div class="metric-value">¥{stats['total_revenue']/1000:.0f}k</div>
            <div class="metric-delta">{stats['completed']} completed</div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">PROFIT RATE</div>
            <div class="metric-value">{stats.get('avg_profit_rate', 0)*100:.0f}%</div>
            <div class="metric-delta">Average</div>
        </div>
        """, unsafe_allow_html=True)

    with col4:
        if AVAILABLE and st.session_state.monitor:
            today = st.session_state.monitor.get_today_stats()
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">TOKENS</div>
                <div class="metric-value">{today.get('tokens', 0):,}</div>
                <div class="metric-delta">${today.get('cost', 0):.2f} today</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="metric-card">
                <div class="metric-label">TOKENS</div>
                <div class="metric-value">-</div>
                <div class="metric-delta">Not available</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Recent projects
    st.markdown('<div class="page-title">Recent Projects</div>', unsafe_allow_html=True)

    if AVAILABLE and st.session_state.db:
        projects = st.session_state.db.list_projects(limit=10)

        if projects:
            for p in projects[:8]:
                status_class = "status-active" if p.status == "进行中" else "status-pending"
                status_text = "ACTIVE" if p.status == "进行中" else "PENDING"

                st.markdown(f"""
                <div class="project-row">
                    <div>
                        <div class="project-name">{p.project_name}</div>
                        <div class="project-client">{p.client}</div>
                    </div>
                    <div style="display: flex; gap: 1rem; align-items: center;">
                        <div class="status {status_class}">{status_text}</div>
                        <div style="color: #fff; font-size: 0.875rem; font-weight: 500;">
                            ¥{p.quote_amount:,.0f}
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No projects yet")
    else:
        st.warning("Database not available")


def projects():
    """Projects list"""
    st.markdown('<div class="page-title">Projects</div>', unsafe_allow_html=True)

    # Search bar
    col1, col2 = st.columns([4, 1])
    with col1:
        search = st.text_input("", placeholder="Search projects...", label_visibility="collapsed")
    with col2:
        filter_btn = st.selectbox("", ["All", "Active", "Pending", "Completed"], label_visibility="collapsed")

    st.markdown("<br>", unsafe_allow_html=True)

    if AVAILABLE and st.session_state.db:
        all_projects = st.session_state.db.list_projects(limit=100)

        if all_projects:
            data = []
            for p in all_projects:
                data.append({
                    "Project": p.project_name,
                    "Client": p.client,
                    "Status": p.status,
                    "Amount": f"¥{p.quote_amount:,.0f}",
                    "Deadline": p.deadline.strftime("%Y-%m-%d") if p.deadline else "-"
                })

            df = pd.DataFrame(data)
            st.dataframe(df, use_container_width=True, hide_index=True)


def upload():
    """Upload documents"""
    st.markdown('<div class="page-title">Upload Document</div>', unsafe_allow_html=True)

    uploaded = st.file_uploader("", type=["xlsx", "xls"], label_visibility="collapsed")

    col1, col2 = st.columns([3, 1])
    with col1:
        hint = st.text_input("", placeholder="Client hint (optional)", label_visibility="collapsed")
    with col2:
        if st.button("Parse", use_container_width=True):
            if uploaded and AVAILABLE:
                temp = Path("temp") / uploaded.name
                temp.parent.mkdir(exist_ok=True)

                with open(temp, "wb") as f:
                    f.write(uploaded.getbuffer())

                result = st.session_state.parser.parse(str(temp), client_hint=hint)

                if result["success"]:
                    st.success("Parsed")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Client", result["client"])
                    with col2:
                        st.metric("Project", result.get("project_name") or "Untitled")
                    with col3:
                        st.metric("Total", f"¥{result['total_amount']:,.0f}")

                    st.markdown("<br>", unsafe_allow_html=True)

                    if result["assets"]:
                        for a in result["assets"]:
                            st.markdown(f"• {a['name']}: {a['quantity']}× ¥{a['unit_price']:,.0f}")
                else:
                    st.error(result.get("error"))


def analytics():
    """Analytics page"""
    st.markdown('<div class="page-title">Analytics</div>', unsafe_allow_html=True)
    st.info("Analytics dashboard coming soon")


def main():
    init()
    topbar()

    # Render current view
    if st.session_state.view == "overview":
        overview()
    elif st.session_state.view == "projects":
        projects()
    elif st.session_state.view == "upload":
        upload()
    elif st.session_state.view == "analytics":
        analytics()


if __name__ == "__main__":
    main()
