"""
ArtPM - Simple Working Version
"""
import streamlit as st
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

try:
    from database.models import DatabaseManager
    from parsers.excel_parser import ExcelQuoteParser
    AVAILABLE = True
except:
    AVAILABLE = False

# Simple page config
st.set_page_config(
    page_title="ArtPM",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Simple dark theme
st.markdown("""
<style>
    .main {
        background-color: #0e0e0e;
        color: #ffffff;
    }
    .stButton button {
        background-color: #1a1a1a;
        color: #ffffff;
        border: 1px solid #333;
    }
    h1, h2, h3 {
        color: #ffffff;
    }
</style>
""", unsafe_allow_html=True)


def init():
    """Initialize session state"""
    if "view" not in st.session_state:
        st.session_state.view = "overview"

    if AVAILABLE:
        if "db" not in st.session_state:
            st.session_state.db = DatabaseManager()
        if "parser" not in st.session_state:
            st.session_state.parser = ExcelQuoteParser()


def navigation():
    """Simple navigation"""
    st.title("◆ ArtPM")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("📊 Overview", use_container_width=True):
            st.session_state.view = "overview"
            st.rerun()

    with col2:
        if st.button("📁 Projects", use_container_width=True):
            st.session_state.view = "projects"
            st.rerun()

    with col3:
        if st.button("📤 Upload", use_container_width=True):
            st.session_state.view = "upload"
            st.rerun()

    with col4:
        if st.button("📈 Analytics", use_container_width=True):
            st.session_state.view = "analytics"
            st.rerun()

    st.divider()


def overview():
    """Overview page"""
    st.header("Overview")

    if AVAILABLE and st.session_state.db:
        try:
            stats = st.session_state.db.get_project_stats()
        except Exception as e:
            st.error(f"Database error: {e}")
            stats = {"total_projects": 0, "in_progress": 0, "completed": 0, "total_revenue": 0, "avg_profit_rate": 0}
    else:
        stats = {"total_projects": 0, "in_progress": 0, "completed": 0, "total_revenue": 0, "avg_profit_rate": 0}

    # Metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Projects", stats['total_projects'], f"{stats['in_progress']} active")

    with col2:
        st.metric("Revenue", f"¥{stats['total_revenue']/1000:.0f}k", f"{stats['completed']} completed")

    with col3:
        st.metric("Profit Rate", f"{stats.get('avg_profit_rate', 0)*100:.0f}%", "Average")

    with col4:
        st.metric("Tokens", "-", "Not available")

    st.subheader("Recent Projects")

    if AVAILABLE and st.session_state.db:
        projects = st.session_state.db.list_projects(limit=10)

        if projects:
            for proj in projects:
                with st.expander(f"📋 {proj.project_name} - {proj.client}"):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.write(f"**Status:** {proj.status}")
                    with col2:
                        st.write(f"**Amount:** ¥{proj.quote_amount:,.0f}")
                    with col3:
                        if proj.profit_rate:
                            st.write(f"**Profit:** {proj.profit_rate*100:.1f}%")
        else:
            st.info("No projects yet")
    else:
        st.info("Database not available")


def projects():
    """Projects page"""
    st.header("Projects")

    if AVAILABLE and st.session_state.db:
        projects = st.session_state.db.list_projects(limit=50)

        if projects:
            st.write(f"Total: {len(projects)} projects")

            for proj in projects:
                with st.expander(f"{proj.project_name} - {proj.client}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**Status:** {proj.status}")
                        st.write(f"**Amount:** ¥{proj.quote_amount:,.0f}")
                    with col2:
                        if proj.deadline:
                            st.write(f"**Deadline:** {proj.deadline.strftime('%Y-%m-%d')}")
                        if proj.profit_rate:
                            st.write(f"**Profit Rate:** {proj.profit_rate*100:.1f}%")
        else:
            st.info("No projects yet")
    else:
        st.error("Database not available")


def upload():
    """Upload page"""
    st.header("Upload Document")

    uploaded_file = st.file_uploader("Choose an Excel file", type=['xlsx', 'xls'])

    if uploaded_file:
        st.success(f"File uploaded: {uploaded_file.name}")

        if st.button("Parse Document"):
            if AVAILABLE and st.session_state.parser:
                with st.spinner("Parsing..."):
                    try:
                        result = st.session_state.parser.parse(uploaded_file)

                        if result.get("success"):
                            st.success("Parsed successfully!")
                            st.json(result)
                        else:
                            st.error(result.get("error", "Unknown error"))
                    except Exception as e:
                        st.error(f"Parse error: {e}")
            else:
                st.error("Parser not available")


def analytics():
    """Analytics page"""
    st.header("Analytics")
    st.info("Analytics dashboard coming soon")


def main():
    """Main entry point"""
    init()
    navigation()

    # Route to views
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
