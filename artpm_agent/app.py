"""
ArtPM Agent - 智能项目管理助手
瘦启动器：负责日志初始化、页面配置、样式注入与主路由。
所有 UI 助手函数见 ui_helpers.py，页面见 pages/。
"""
from utils.logger import setup_logging, get_logger
import streamlit as st
from ui_helpers import *  # noqa: F401,F403
from ui_style import STYLE_CSS
from pages.chat import chat_page
from pages.settings import settings_page
# 向后兼容：拆分前 persist_settings 直接挂在 app 模块上，用户 WIP 代码/测试仍按 app.persist_settings 调用。
from pages.settings import persist_settings  # noqa: F401

# 日志系统只需初始化一次
setup_logging()
logger = get_logger(__name__)

# 页面配置：每个 Streamlit 脚本运行只能调用一次，且须在任何其它 st.* 之前
st.set_page_config(
    page_title="ArtPM Agent",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="auto"
)

# 视觉系统：以项目台账为原型，使用克制的分隔线和数据带建立层级
st.markdown(STYLE_CSS, unsafe_allow_html=True)


def main():
    """主入口"""
    init_session()
    render_sidebar()

    # 路由
    view = st.session_state.view

    if view == "设置":
        settings_page()
    else:
        chat_page()


if __name__ == "__main__":
    main()
