"""
ArtPM Agent - 智能项目管理助手
完整重构版本 - 修复空白问题
"""
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

# 初始化日志系统
from utils.logger import setup_logging, get_logger
from utils.chat_intent import chat_processing_label
from utils.chat_attachments import (
    ChatAttachmentStore,
    normalize_chat_submission,
    select_conversation_attachments,
)
setup_logging()
logger = get_logger(__name__)

try:
    from agent import ArtPMAgent
    from config import Config
    from database.models import DatabaseManager
    from parsers.excel_parser import ExcelQuoteParser
    AVAILABLE = True
    logger.info("核心模块导入成功")
except Exception as e:
    logger.error(f"核心模块导入失败: {e}")
    print(f"Import error: {e}")
    AVAILABLE = False

CONVERSATION_STORE_AVAILABLE = False
try:
    from memory import WorkspaceKnowledgeStore
    from memory.conversation_store import ConversationStore

    CONVERSATION_STORE_AVAILABLE = True
except Exception as error:
    ConversationStore = None
    WorkspaceKnowledgeStore = None
    logger.error(f"会话存储模块导入失败: {error}")

WORKFLOW_RUNTIME_AVAILABLE = False
try:
    from workflows import (
        WorkflowCoordinator,
        WorkflowOverride,
        WorkflowStore,
        format_workflow_result,
    )

    WORKFLOW_RUNTIME_AVAILABLE = True
except Exception as error:
    WorkflowCoordinator = None
    WorkflowOverride = None
    WorkflowStore = None
    format_workflow_result = None
    logger.error(f"工作流运行时导入失败: {error}")

PROFILE_RUNTIME_AVAILABLE = False
try:
    from profiles import (
        AgentIdentity,
        AgentIdentityPatch,
        AgentProfilePatch,
        AgentProfileStore,
        ProfileChangeParseError,
        QuotePolicy,
        format_profile_changes,
        parse_profile_change,
    )

    PROFILE_RUNTIME_AVAILABLE = True
except Exception as error:
    AgentIdentity = None
    AgentIdentityPatch = None
    AgentProfilePatch = None
    AgentProfileStore = None
    ProfileChangeParseError = ValueError
    QuotePolicy = None
    format_profile_changes = None
    parse_profile_change = None
    logger.error(f"Agent Profile 模块导入失败: {error}")

ARTIFACT_RUNTIME_AVAILABLE = False
try:
    from artifacts import ArtifactCoordinator, WorkspaceArtifactGenerator

    ARTIFACT_RUNTIME_AVAILABLE = True
except Exception as error:
    ArtifactCoordinator = None
    WorkspaceArtifactGenerator = None
    logger.error(f"文件生成模块导入失败: {error}")

MODEL_CATALOG_AVAILABLE = False
MODEL_CATALOG_IMPORT_ERROR = None
try:
    from utils.model_catalog import (
        ModelCatalogError,
        fetch_openai_compatible_models,
        parse_cached_models,
        serialize_cached_models,
    )
    MODEL_CATALOG_AVAILABLE = True
except Exception as error:
    MODEL_CATALOG_IMPORT_ERROR = str(error)
    ModelCatalogError = RuntimeError
    fetch_openai_compatible_models = None
    parse_cached_models = None
    serialize_cached_models = None
    logger.error(f"模型同步模块导入失败: {error}")

# 页面配置
st.set_page_config(
    page_title="ArtPM Agent",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="auto"
)

# 视觉系统：以项目台账为原型，使用克制的分隔线和数据带建立层级。
st.markdown("""
<style>
    :root {
        --pm-paper: #ffffff;
        --pm-canvas: #f4f6f8;
        --pm-ink: #18202e;
        --pm-muted: #667085;
        --pm-line: #dde2e8;
        --pm-accent: #2458c6;
        --pm-accent-soft: #eaf1ff;
        --pm-success: #147d64;
        --pm-success-soft: #eaf7f3;
        --pm-warning: #b7680b;
        --pm-warning-soft: #fff4df;
        --pm-danger: #b93830;
        --pm-danger-soft: #fff0ef;
        --pm-radius: 6px;
        --pm-space-1: 4px;
        --pm-space-2: 8px;
        --pm-space-3: 12px;
        --pm-space-4: 16px;
        --pm-space-5: 20px;
        --pm-space-6: 24px;
        --pm-space-8: 32px;
        --pm-chat-shell-width: 800px;
        --pm-chat-thread-width: 720px;
    }

    *, *::before, *::after {
        box-sizing: border-box;
        letter-spacing: 0 !important;
    }

    html, body {
        font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif;
        color: var(--pm-ink);
    }

    #MainMenu, footer, header, .stDeployButton {
        display: none !important;
    }

    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        background: var(--pm-canvas);
    }

    [data-testid="stMainBlockContainer"] {
        width: 100%;
        max-width: 1240px;
        padding: 28px 40px 48px;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-page-marker) {
        max-width: calc(var(--pm-chat-shell-width) + 40px);
        padding-left: 20px;
        padding-right: 20px;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
        padding-top: clamp(176px, 25vh, 260px);
        text-align: center;
    }

    h1, h2, h3, p {
        margin-top: 0;
    }

    h1 {
        color: var(--pm-ink);
        font-size: 28px !important;
        line-height: 1.25 !important;
        font-weight: 680 !important;
        margin: 0 0 4px !important;
    }

    h2 {
        color: var(--pm-ink);
        font-size: 18px !important;
        line-height: 1.4 !important;
        font-weight: 650 !important;
    }

    h3 {
        color: var(--pm-ink);
        font-size: 15px !important;
        line-height: 1.45 !important;
        font-weight: 650 !important;
    }

    p, li, label, [data-testid="stMarkdownContainer"] {
        font-size: 14px;
        line-height: 1.65;
    }

    .page-kicker,
    .section-label,
    .sidebar-section-label {
        color: var(--pm-accent);
        font-size: 12px;
        font-weight: 650;
        line-height: 1.4;
    }

    .page-kicker {
        margin-bottom: 6px;
    }

    .page-meta {
        color: var(--pm-muted);
        font-size: 13px;
        margin: 0 0 20px;
    }

    .section-heading {
        align-items: baseline;
        border-bottom: 1px solid var(--pm-line);
        display: flex;
        justify-content: space-between;
        margin: 24px 0 12px;
        padding-bottom: 8px;
        position: relative;
    }

    .section-heading::after {
        background: var(--pm-accent);
        bottom: -1px;
        content: "";
        height: 2px;
        left: 0;
        position: absolute;
        width: 44px;
    }

    .section-heading strong {
        color: var(--pm-ink);
        font-size: 15px;
        font-weight: 650;
    }

    .section-heading span {
        color: var(--pm-muted);
        font-size: 12px;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background: #f0f3f2;
        border-right: 1px solid var(--pm-line);
        min-width: 280px !important;
        width: 280px !important;
    }

    [data-testid="stSidebarContent"] {
        padding: 24px 16px 20px;
    }

    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0;
    }

    .sidebar-brand {
        align-items: center;
        display: flex;
        gap: 10px;
        margin: 2px 4px 22px;
    }

    .brand-mark {
        background: var(--pm-accent);
        display: block;
        height: 14px;
        transform: rotate(45deg);
        width: 14px;
    }

    .brand-name {
        color: var(--pm-ink);
        font-size: 16px;
        font-weight: 700;
        line-height: 1;
    }

    .sidebar-section-label {
        margin: 0 8px 8px;
        padding-bottom: 8px;
    }

    [data-testid="stSidebar"] .stButton {
        margin-bottom: 4px;
    }

    [data-testid="stSidebar"] .stButton button {
        border: 1px solid transparent;
        border-radius: var(--pm-radius);
        box-shadow: none;
        justify-content: flex-start;
        min-height: 44px;
        padding: 0 12px;
        transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
        width: 100%;
    }

    [data-testid="stSidebar"] .stButton button p {
        text-align: left;
        width: 100%;
    }

    [data-testid="stSidebar"] .stButton button[kind="secondary"] {
        background: transparent;
        color: #4b5565;
    }

    [data-testid="stSidebar"] .stButton button[kind="secondary"]:hover {
        background: rgba(255, 255, 255, 0.72);
        border-color: #d5dae1;
        color: var(--pm-ink);
    }

    [data-testid="stSidebar"] .stButton button[kind="primary"] {
        background: var(--pm-accent-soft);
        border-color: transparent;
        box-shadow: inset 3px 0 0 var(--pm-accent);
        color: var(--pm-ink);
        font-weight: 650;
    }

    .st-key-conversation_panel {
        margin-top: 4px;
    }

    .st-key-conversation_panel .sidebar-section-label {
        margin-left: 4px;
    }

    .st-key-conversation_list {
        margin: 4px 0 8px;
        overflow-x: hidden;
    }

    .st-key-conversation_list .stButton {
        margin-bottom: 2px !important;
    }

    .st-key-conversation_list .stButton button {
        min-height: 36px !important;
        padding: 0 10px !important;
    }

    .st-key-conversation_list .stButton button p {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .st-key-conversation_list [class*="st-key-conversation_row_"] [data-testid="stHorizontalBlock"] {
        gap: 4px;
    }

    .st-key-conversation_list [class*="st-key-conversation_row_"] [data-testid="stColumn"]:last-child {
        min-width: 36px;
        flex: 0 0 36px;
    }

    .st-key-conversation_list [class*="st-key-conversation_row_"] [data-testid="stColumn"]:last-child button {
        width: 36px !important;
        min-width: 36px !important;
        padding: 0 !important;
        justify-content: center !important;
    }

    .st-key-conversation_list [class*="st-key-conversation_row_"] [data-testid="stColumn"]:last-child button p {
        display: none;
    }

    .st-key-conversation_list [class*="st-key-conversation_delete_confirm_"] {
        border-left: 2px solid var(--pm-danger);
        margin: 2px 0 6px;
        padding-left: 8px;
    }

    .st-key-new_conversation button {
        justify-content: center !important;
        min-height: 38px !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] {
        background: rgba(255, 255, 255, 0.48);
        border-color: var(--pm-line);
        border-radius: var(--pm-radius);
        margin-top: 6px;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton button {
        justify-content: center;
        min-height: 38px;
    }

    .st-key-sidebar_modes {
        border-top: 1px solid var(--pm-line);
        margin-top: 14px;
        padding-top: 12px;
    }

    .st-key-sidebar_modes [data-testid="stHorizontalBlock"] {
        gap: 8px;
    }

    .st-key-sidebar_modes .stButton button {
        justify-content: center !important;
        min-height: 38px !important;
    }

    .sidebar-ledger {
        border-top: 1px solid var(--pm-line);
        margin-top: 24px;
        padding: 18px 4px 0;
    }

    .ledger-row {
        align-items: center;
        display: flex;
        justify-content: space-between;
        min-height: 30px;
    }

    .ledger-row span {
        color: var(--pm-muted);
        font-size: 12px;
    }

    .ledger-row strong {
        color: var(--pm-ink);
        font-family: Bahnschrift, "Segoe UI", sans-serif;
        font-size: 13px;
        font-weight: 600;
    }

    .service-state {
        align-items: center;
        color: var(--pm-muted);
        display: flex;
        font-size: 12px;
        gap: 7px;
        margin: 20px 4px 0;
    }

    .state-dot {
        background: var(--pm-success);
        border-radius: 50%;
        height: 7px;
        width: 7px;
    }

    .state-dot.offline {
        background: var(--pm-danger);
    }

    /* Main controls */
    [data-testid="stMain"] .stButton button,
    [data-testid="stFormSubmitButton"] button,
    [data-testid="stDownloadButton"] button {
        border-radius: var(--pm-radius);
        box-shadow: none;
        font-size: 14px;
        font-weight: 600;
        min-height: 44px;
        padding: 0 16px;
        transition: background-color 120ms ease, border-color 120ms ease;
    }

    [data-testid="stMain"] button[kind="primary"] {
        background: var(--pm-accent) !important;
        border-color: var(--pm-accent) !important;
    }

    [data-testid="stMain"] button[kind="primary"]:hover {
        background: #1d49b2 !important;
        border-color: #1d49b2 !important;
    }

    button:focus-visible,
    input:focus-visible,
    textarea:focus-visible,
    [role="tab"]:focus-visible {
        outline: 3px solid rgba(36, 88, 211, 0.24) !important;
        outline-offset: 2px !important;
    }

    .stTextInput input,
    .stTextArea textarea,
    [data-baseweb="select"] > div {
        background: var(--pm-paper) !important;
        border-color: #cfd5dd !important;
        border-radius: var(--pm-radius) !important;
        box-shadow: none !important;
        min-height: 44px;
    }

    .stTextInput input:focus,
    .stTextArea textarea:focus {
        border-color: var(--pm-accent) !important;
        box-shadow: 0 0 0 3px rgba(36, 88, 211, 0.12) !important;
    }

    [data-testid="stFileUploaderDropzone"] {
        background: transparent;
        border: 1px dashed #b8c0cc;
        border-radius: var(--pm-radius);
        min-height: 132px;
        padding: 20px;
    }

    [data-testid="stFileUploaderDropzone"] button {
        min-height: 40px;
    }

    [data-testid="stDataFrame"] {
        border: 1px solid var(--pm-line);
        border-radius: var(--pm-radius);
        overflow: hidden;
    }

    [data-testid="stAlert"] {
        border-radius: var(--pm-radius);
        box-shadow: none;
    }

    [data-baseweb="tab-list"] {
        border-bottom: 1px solid var(--pm-line);
        gap: 20px;
    }

    [data-baseweb="tab"] {
        font-size: 14px;
        min-height: 44px;
        padding-left: 2px;
        padding-right: 2px;
    }

    [data-baseweb="tab-highlight"] {
        background-color: var(--pm-accent);
    }

    /* Project ledger metric band */
    .metric-rail {
        background: var(--pm-paper);
        border-bottom: 1px solid var(--pm-line);
        border-top: 1px solid var(--pm-line);
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin: 16px 0 0;
    }

    .metric-item {
        min-width: 0;
        padding: 16px 20px;
    }

    .metric-item.tone-blue {
        background: var(--pm-accent-soft);
        box-shadow: inset 0 3px 0 var(--pm-accent);
    }

    .metric-item.tone-amber {
        background: var(--pm-warning-soft);
        box-shadow: inset 0 3px 0 var(--pm-warning);
    }

    .metric-item.tone-teal {
        background: var(--pm-success-soft);
        box-shadow: inset 0 3px 0 var(--pm-success);
    }

    .metric-item.tone-blue .metric-label {
        color: var(--pm-accent);
    }

    .metric-item.tone-amber .metric-label {
        color: var(--pm-warning);
    }

    .metric-item.tone-teal .metric-label {
        color: var(--pm-success);
    }

    .metric-item:not(:last-child) {
        border-right: 1px solid var(--pm-line);
    }

    .metric-label {
        color: var(--pm-muted);
        font-size: 12px;
        font-weight: 600;
        margin-bottom: 5px;
    }

    .metric-value {
        color: var(--pm-ink);
        font-family: Bahnschrift, "Segoe UI", sans-serif;
        font-size: 24px;
        font-weight: 600;
        line-height: 1.2;
        overflow-wrap: anywhere;
    }

    .metric-note {
        color: var(--pm-muted);
        font-size: 12px;
        margin-top: 4px;
    }

    .metric-note.positive {
        color: var(--pm-success);
    }

    .definition-grid {
        background: var(--pm-paper);
        border-bottom: 1px solid var(--pm-line);
        border-top: 1px solid var(--pm-line);
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin-top: 12px;
    }

    .definition-item {
        border-bottom: 1px solid var(--pm-line);
        display: grid;
        gap: 8px;
        grid-template-columns: 96px 1fr;
        min-height: 48px;
        padding: 12px 16px;
    }

    .definition-item:nth-child(odd) {
        border-right: 1px solid var(--pm-line);
    }

    .definition-item dt {
        color: var(--pm-muted);
        font-size: 12px;
    }

    .definition-item dd {
        color: var(--pm-ink);
        font-size: 14px;
        font-weight: 600;
        margin: 0;
        overflow-wrap: anywhere;
    }

    .step-rail {
        border-bottom: 1px solid var(--pm-line);
        border-top: 1px solid var(--pm-line);
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        margin: 16px 0 20px;
    }

    .step {
        color: var(--pm-muted);
        font-size: 13px;
        padding: 12px 16px;
    }

    .step.current {
        background: var(--pm-accent-soft);
        color: var(--pm-accent);
    }

    .step.pending {
        background: var(--pm-warning-soft);
        color: var(--pm-warning);
    }

    .step + .step {
        border-left: 1px solid var(--pm-line);
    }

    .step strong {
        color: var(--pm-ink);
        font-family: Bahnschrift, "Segoe UI", sans-serif;
        margin-right: 8px;
    }

    /* Conversation */
    .chat-empty-marker {
        height: 0;
        overflow: hidden;
    }

    .chat-ready-state {
        align-items: center;
        color: var(--pm-muted);
        display: flex;
        font-size: 13px;
        gap: 8px;
        justify-content: center;
        margin-top: 10px;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) h1 {
        font-size: 30px !important;
        text-align: center;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) .page-meta,
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) .page-kicker {
        display: none;
    }

    [data-testid="stChatMessage"] {
        background: transparent;
        border-bottom: 0;
        border-radius: 0;
        gap: 12px;
        padding: 8px 0;
        width: 100%;
    }

    .st-key-chat_thread {
        margin-left: auto;
        margin-right: auto;
        max-width: var(--pm-chat-thread-width);
        width: 100%;
    }

    .chat-role-marker,
    [data-testid="stElementContainer"]:has(.chat-role-marker) {
        display: none;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user) {
        background: transparent;
        border-radius: 0;
        justify-content: flex-end;
        margin: 0;
        max-width: none;
        padding: 8px 0;
        width: 100%;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageAvatarCustom"] {
        display: none;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] {
        background: var(--pm-accent-soft);
        border-radius: 8px;
        flex: 0 1 auto;
        margin: 0;
        max-width: 74%;
        min-height: 36px;
        min-width: 0;
        overflow-wrap: anywhere;
        padding: 7px 12px;
        width: fit-content;
    }

    [data-testid="stChatMessageContent"]
    [data-testid="stMarkdownContainer"] {
        margin-bottom: 0 !important;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] {
        min-width: 0;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] li {
        font-size: 14px !important;
        line-height: 1.45 !important;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] > :first-child {
        margin-top: 0 !important;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] > :last-child {
        margin-bottom: 0 !important;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] ul,
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] ol {
        margin-block: 4px;
        padding-inline-start: 20px;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] pre {
        max-width: 100%;
        overflow-x: auto;
    }

    [data-testid="stChatMessage"]:has(.chat-role-assistant) {
        margin-bottom: 8px;
    }

    [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarAssistant"] {
        border: 1px solid var(--pm-line);
        height: 32px;
        width: 32px;
    }

    [data-testid="stChatMessageContent"] p:last-child {
        margin-bottom: 0 !important;
    }

    .chat-attachments {
        color: var(--pm-muted);
        display: flex;
        flex-wrap: wrap;
        font-size: 12px;
        gap: 4px 10px;
        margin-top: 6px;
    }

    .chat-attachment-name {
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    [data-testid="stChatInput"] {
        background: var(--pm-paper);
        border: 1px solid #cfd5dd;
        border-radius: 16px;
        box-shadow: 0 10px 30px rgba(24, 32, 46, 0.10);
    }

    [data-testid="stChatInput"] textarea {
        background: transparent !important;
        font-size: 14px;
        min-height: 46px;
    }

    [data-testid="stChatInput"] textarea:focus {
        box-shadow: none !important;
        outline: none !important;
    }

    [data-testid="stChatInput"] button {
        color: var(--pm-accent);
        min-height: 44px;
        min-width: 44px;
    }

    .st-key-clear_chat {
        align-items: flex-end;
        display: flex;
    }

    .st-key-clear_chat button {
        min-width: 44px !important;
        margin-left: auto;
        padding: 0 !important;
        width: 44px !important;
    }

    .st-key-clear_chat button p {
        border: 0;
        clip: rect(0 0 0 0);
        clip-path: inset(50%);
        height: 1px;
        margin: -1px;
        overflow: hidden;
        padding: 0;
        position: absolute;
        white-space: nowrap;
        width: 1px;
    }

    .st-key-chat_header [data-testid="stHorizontalBlock"] {
        align-items: flex-end;
        flex-wrap: nowrap;
    }

    .st-key-chat_header [data-testid="stColumn"]:first-child {
        flex: 1 1 auto;
        min-width: 0;
    }

    .st-key-chat_header [data-testid="stColumn"]:last-child {
        flex: 0 0 44px;
        min-width: 44px;
        width: 44px;
    }

    [data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] {
        margin-left: auto;
        margin-right: auto;
        max-width: var(--pm-chat-shell-width);
    }

    [data-testid="stBottom"] {
        background: transparent !important;
    }

    [data-testid="stAppScrollToBottomContainer"]:has(.chat-empty-marker)
    [data-testid="stChatInput"] textarea {
        min-height: 72px;
        padding-top: 14px;
    }

    .stSpinner > div {
        border-top-color: var(--pm-accent) !important;
    }

    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            scroll-behavior: auto !important;
            transition-duration: 0.01ms !important;
        }
    }

    @media (max-width: 760px) {
        [data-testid="stSidebar"] {
            min-width: min(88vw, 320px) !important;
            width: min(88vw, 320px) !important;
        }

        [data-testid="stMainBlockContainer"] {
            padding: 20px 16px 32px;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-page-marker) {
            padding-left: 16px;
            padding-right: 16px;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
            padding-top: 176px;
        }

        [data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] {
            max-width: calc(100vw - 32px);
        }

        .st-key-chat_thread {
            max-width: 100%;
        }

        [data-testid="stChatMessage"]:has(.chat-role-user)
        [data-testid="stChatMessageContent"] {
            max-width: 88%;
        }

        h1 {
            font-size: 24px !important;
        }

        .metric-rail {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .metric-item:nth-child(2) {
            border-right: 0;
        }

        .metric-item:nth-child(-n+2) {
            border-bottom: 1px solid var(--pm-line);
        }

        .metric-item {
            padding: 14px 12px;
        }

        .metric-value {
            font-size: 21px;
        }

        .definition-grid {
            grid-template-columns: 1fr;
        }

        .definition-item:nth-child(odd) {
            border-right: 0;
        }

        .section-heading {
            align-items: flex-start;
            gap: 8px;
        }
    }
</style>
""", unsafe_allow_html=True)


EMPTY_STATS = {
    "total_projects": 0,
    "in_progress": 0,
    "completed": 0,
    "total_revenue": 0,
    "avg_profit_rate": 0,
}


def format_cn_date(value, include_time=False):
    """以中文产品格式显示日期，避免将 ISO 时间暴露到界面。"""
    if not value:
        return "—"
    if include_time:
        return (
            f"{value.year}年{value.month}月{value.day}日 "
            f"{value.hour:02d}:{value.minute:02d}"
        )
    return f"{value.month}月{value.day}日"


def format_money(value, compact=False):
    amount = float(value or 0)
    if compact and abs(amount) >= 10000:
        return f"¥{amount / 10000:,.1f}万"
    return f"¥{amount:,.0f}"


def render_page_header(section, title, meta=""):
    st.markdown(f'<div class="page-kicker">{section}</div>', unsafe_allow_html=True)
    st.title(title)
    if meta:
        st.markdown(f'<div class="page-meta">{meta}</div>', unsafe_allow_html=True)


def render_section_heading(title, meta=""):
    st.markdown(
        f'<div class="section-heading"><strong>{title}</strong><span>{meta}</span></div>',
        unsafe_allow_html=True,
    )


def get_project_stats():
    if not AVAILABLE or not st.session_state.get("db"):
        return EMPTY_STATS.copy(), "数据库未连接"
    try:
        return st.session_state.db.get_project_stats(), None
    except Exception as error:
        logger.warning(f"项目统计加载失败: {error}")
        return EMPTY_STATS.copy(), "项目数据加载失败，请检查数据库连接"


def get_projects(limit=100, status=None):
    if not AVAILABLE or not st.session_state.get("db"):
        return [], "数据库未连接，请检查配置并重启服务"
    try:
        return st.session_state.db.list_projects(status=status, limit=limit), None
    except Exception as error:
        logger.warning(f"项目列表加载失败: {error}")
        return [], "项目列表加载失败，请检查数据库连接后重试"


def project_rows(projects):
    return [
        {
            "项目": project.project_name,
            "客户": project.client,
            "状态": project.status,
            "报价": float(project.quote_amount or 0),
            "利润率": (
                f"{project.profit_rate * 100:.1f}%"
                if project.profit_rate is not None
                else "—"
            ),
            "截止日期": format_cn_date(project.deadline),
        }
        for project in projects
    ]


def render_project_table(projects, key):
    rows = project_rows(projects)
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        height=min(420, 38 + max(len(rows), 1) * 36),
        column_config={
            "项目": st.column_config.TextColumn(width="large"),
            "客户": st.column_config.TextColumn(width="medium"),
            "状态": st.column_config.TextColumn(width="small"),
            "报价": st.column_config.NumberColumn(format="¥ %.0f", width="medium"),
            "利润率": st.column_config.TextColumn(width="small"),
            "截止日期": st.column_config.TextColumn(width="small"),
        },
        key=key,
    )


def _conversation_title_from_prompt(prompt, max_length=28):
    """Create a compact local title without spending another model request."""
    title = " ".join(str(prompt).split())
    if len(title) <= max_length:
        return title or ConversationStore.DEFAULT_TITLE
    return f"{title[:max_length].rstrip()}…"


def _message_for_ui(message):
    """Keep storage metadata nested while exposing legacy fields used by the UI."""
    projected = dict(message)
    metadata = projected.get("metadata") or {}
    if metadata.get("retry_prompt"):
        projected["retry_prompt"] = metadata["retry_prompt"]
    return projected


def get_conversation_store():
    return st.session_state.get("conversation_store")


def get_chat_attachment_store():
    return st.session_state.get("chat_attachment_store")


def get_artifact_generator():
    return st.session_state.get("artifact_generator")


def get_artifact_coordinator():
    generator = get_artifact_generator()
    agent = st.session_state.get("agent")
    llm_client = getattr(agent, "llm_client", None)
    if generator is None or llm_client is None or ArtifactCoordinator is None:
        return None
    coordinator = st.session_state.get("artifact_coordinator")
    if (
        coordinator is None
        or getattr(coordinator, "generator", None) is not generator
        or getattr(coordinator, "llm", None) is not llm_client
    ):
        coordinator = ArtifactCoordinator(generator, llm_client)
        st.session_state.artifact_coordinator = coordinator
    return coordinator


def get_workflow_store():
    return st.session_state.get("workflow_store")


def get_profile_store():
    return st.session_state.get("profile_store")


def get_knowledge_store():
    return st.session_state.get("knowledge_store")


def get_current_profile():
    store = get_profile_store()
    if store is None:
        return None
    try:
        return store.get_effective_profile()
    except Exception:
        logger.exception("读取当前 Agent Profile 失败")
        return None


def build_knowledge_context(prompt, *, max_chars=6000):
    """Build bounded, confirmed Workspace knowledge for one model request."""
    store = get_knowledge_store()
    if store is None or not str(prompt).strip():
        return ""
    try:
        rules = store.get_active_rules(limit=20)
        resources = store.search(
            str(prompt),
            limit=5,
            include_rules=False,
            max_text_chars=1200,
        )
    except Exception:
        logger.exception("检索 Workspace 知识失败")
        return ""

    lines = []
    if rules:
        lines.append("已采纳规则：")
        lines.extend(f"- {rule['statement']}" for rule in rules)
    if resources:
        lines.append("相关资料：")
        for resource in resources:
            title = resource.get("title", "未命名资料")
            version = resource.get("current_version") or resource.get("version")
            excerpt = str(resource.get("text", "")).strip()
            lines.append(f"- {title}（v{version}）：{excerpt}")
    return "\n".join(lines)[:max_chars]


def extract_knowledge_rule(prompt):
    """Return an explicit long-term rule request, never an inferred preference."""
    text = " ".join(str(prompt or "").strip().split())
    if not text or any(
        phrase in text
        for phrase in ("记住这份附件", "附件加入知识库", "附件加入资料库")
    ):
        return None
    explicit = re.search(
        r"^(?:请)?记住(?:这条)?(?:规则|偏好)?[：:，,\s]+(.{2,1000})$",
        text,
        re.IGNORECASE,
    )
    if explicit:
        return explicit.group(1).strip()
    add_rule = re.search(
        r"^(?:把|将)(.{2,1000}?)(?:作为|设为)?(?:规则|偏好)?"
        r"(?:加入|写入)(?:知识库|资料库)$",
        text,
        re.IGNORECASE,
    )
    if add_rule:
        return add_rule.group(1).strip(" ，,：:")
    if text.startswith(("以后", "今后")) and any(
        marker in text
        for marker in ("统一", "一律", "默认", "必须", "不要", "请", "按", "使用", "采用")
    ):
        return text
    return None


def is_knowledge_ingestion_request(prompt):
    text = str(prompt or "").casefold()
    return (
        any(target in text for target in ("知识库", "资料库"))
        and any(
            action in text
            for action in ("加入", "写入", "保存", "收录", "沉淀", "学习")
        )
        and any(subject in text for subject in ("附件", "文件", "这份", "这些"))
    )


def build_knowledge_ingestion_resources(agent, attachments, file_paths, prompt):
    """Parse conversation files into bounded, parser-neutral knowledge payloads."""
    resources = []
    failures = []
    for attachment, file_path in zip(attachments, file_paths):
        result = agent.process_document(file_path, prompt)
        name = str(attachment.get("name") or Path(file_path).name)
        if not result.get("success"):
            failures.append(f"{name}：{result.get('error', '无法解析')}")
            continue
        structured = result.get("extracted_data")
        if not isinstance(structured, dict):
            structured = {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "success",
                    "source",
                    "file_path",
                    "raw_text",
                    "confidence",
                }
            }
        structured = json.loads(
            json.dumps(structured, ensure_ascii=False, default=str)
        )
        extension = str(attachment.get("extension", "")).lower()
        if extension in {"xlsx", "xls", "csv"}:
            resource_type = "table"
        elif extension in {"png", "jpg", "jpeg", "webp"}:
            resource_type = "image"
        else:
            resource_type = "document"
        raw_text = str(result.get("raw_text", "")).strip()
        if resource_type in {"image", "document"} and not raw_text:
            failures.append(
                f"{name}：附件没有可检索正文；扫描件请先启用并连接 OCR 服务"
            )
            continue
        resources.append(
            {
                "title": name,
                "searchable_text": raw_text[:32768],
                "resource_type": resource_type,
                "source_type": "conversation_attachment",
                "source_uri": str(
                    attachment.get("stored_path") or attachment.get("name") or name
                ),
                "source_id": str(
                    attachment.get("sha256") or attachment.get("id") or name
                ),
                "mime_type": str(attachment.get("mime_type") or "") or None,
                "structured_data": structured,
                "metadata": {
                    "document_type": result.get("document_type", "未知"),
                    "original_name": name,
                    "size": attachment.get("size"),
                    "confidence": result.get("confidence"),
                },
            }
        )
    if failures:
        raise ValueError("；".join(failures))
    if not resources:
        raise ValueError("没有可加入资料库的有效附件")
    return resources


def get_workflow_coordinator():
    """Return a coordinator bound to the current Agent, if it supports Skills."""
    if not WORKFLOW_RUNTIME_AVAILABLE:
        return None
    store = get_workflow_store()
    agent = st.session_state.get("agent")
    if store is None or agent is None or not hasattr(agent, "router"):
        return None
    coordinator = st.session_state.get("workflow_coordinator")
    if coordinator is None or getattr(coordinator, "agent", None) is not agent:
        try:
            coordinator = WorkflowCoordinator(store, agent)
        except (TypeError, ValueError):
            logger.exception("工作流协调器初始化失败")
            return None
        st.session_state.workflow_coordinator = coordinator
    return coordinator


def load_active_messages():
    store = get_conversation_store()
    conversation_id = st.session_state.get("active_conversation_id")
    if store is None or not conversation_id:
        return st.session_state.get("messages", [])
    st.session_state.messages = [
        _message_for_ui(message)
        for message in store.list_messages(conversation_id)
    ]
    return st.session_state.messages


def activate_conversation(conversation_id):
    store = get_conversation_store()
    if store is None or store.get_conversation(conversation_id) is None:
        raise KeyError(f"未知会话: {conversation_id}")
    st.session_state.active_conversation_id = conversation_id
    st.session_state.view = "对话"
    st.session_state.pop("pending_prompt", None)
    load_active_messages()


def delete_conversation_and_activate_next(store, conversation_id):
    """Delete one conversation and keep the workspace on a valid active thread."""
    attachment_store = get_chat_attachment_store()
    if attachment_store is not None:
        attachment_store.remove_conversation(conversation_id)
    store.delete_conversation(conversation_id)
    remaining = store.list_conversations(limit=1)
    next_conversation = remaining[0] if remaining else store.create_conversation()
    st.session_state.pop("pending_conversation_delete", None)
    activate_conversation(next_conversation["id"])


def _migrate_legacy_messages(store, messages):
    """Preserve messages from a live pre-upgrade Streamlit session once."""
    if not messages or store.list_conversations(limit=1):
        return None
    conversation = store.create_conversation()
    current_turn_id = None
    first_prompt = None
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        if role == "user" or current_turn_id is None:
            current_turn_id = uuid4().hex
        if role == "user" and first_prompt is None:
            first_prompt = content
        metadata = {}
        if message.get("retry_prompt"):
            metadata["retry_prompt"] = message["retry_prompt"]
        store.add_message(
            conversation["id"],
            role,
            content,
            status=message.get("status", "complete"),
            turn_id=current_turn_id,
            metadata=metadata,
        )
    if first_prompt:
        conversation = store.rename_conversation(
            conversation["id"], _conversation_title_from_prompt(first_prompt)
        )
    return conversation


def init_session():
    """初始化应用状态，并从持久化存储恢复当前会话。"""
    if "view" not in st.session_state:
        st.session_state.view = "对话"
    elif st.session_state.view not in {"对话", "设置"}:
        st.session_state.view = "对话"

    title_sync = st.session_state.pop("pending_conversation_title_sync", None)
    if isinstance(title_sync, dict):
        conversation_id = title_sync.get("conversation_id")
        title = title_sync.get("title")
        if conversation_id and title:
            st.session_state[f"rename_input_{conversation_id}"] = title

    legacy_messages = list(st.session_state.get("messages", []))
    if "conversation_store" not in st.session_state:
        st.session_state.conversation_store = None
        if CONVERSATION_STORE_AVAILABLE and AVAILABLE:
            try:
                conversation_path = Config().get(
                    "database.conversation_db_path", "./data/conversations.db"
                )
                store = ConversationStore(conversation_path)
                st.session_state.conversation_store = store
                _migrate_legacy_messages(store, legacy_messages)
            except Exception:
                logger.exception("会话存储初始化失败")

    if "chat_attachment_store" not in st.session_state:
        st.session_state.chat_attachment_store = None
        if AVAILABLE:
            try:
                conversation_path = Path(
                    Config().get(
                        "database.conversation_db_path",
                        "./data/conversations.db",
                    )
                )
                attachment_root = conversation_path.parent / "chat_attachments"
                st.session_state.chat_attachment_store = ChatAttachmentStore(
                    attachment_root
                )
            except Exception:
                logger.exception("会话附件存储初始化失败")

    if "artifact_generator" not in st.session_state:
        st.session_state.artifact_generator = None
        if ARTIFACT_RUNTIME_AVAILABLE and get_conversation_store() is not None:
            try:
                conversation_path = Path(get_conversation_store().db_path)
                artifact_root = conversation_path.parent / "artifacts" / "local-default"
                st.session_state.artifact_generator = WorkspaceArtifactGenerator(
                    artifact_root
                )
            except Exception:
                logger.exception("工作区文件生成器初始化失败")

    if "workflow_store" not in st.session_state:
        st.session_state.workflow_store = None
        if WORKFLOW_RUNTIME_AVAILABLE and get_conversation_store() is not None:
            try:
                st.session_state.workflow_store = WorkflowStore(
                    get_conversation_store().db_path
                )
            except Exception:
                logger.exception("工作流存储初始化失败")

    if "profile_store" not in st.session_state:
        st.session_state.profile_store = None
        conversation_store = get_conversation_store()
        if PROFILE_RUNTIME_AVAILABLE and conversation_store is not None:
            try:
                runtime_config = Config()
                default_policy = QuotePolicy(
                    overhead_rate=float(
                        runtime_config.get("cost_config.overhead_rate", 0.15)
                    ),
                    tax_rate=float(runtime_config.get("cost_config.tax_rate", 0.06)),
                    currency=str(
                        runtime_config.get("cost_config.currency", "CNY")
                    ).upper(),
                )
                st.session_state.profile_store = AgentProfileStore(
                    conversation_store.db_path,
                    default_identity=AgentIdentity(),
                    default_quote_policy=default_policy,
                )
                st.session_state.profile_store.get_effective_profile()
            except Exception:
                logger.exception("Agent Profile 存储初始化失败")

    if "knowledge_store" not in st.session_state:
        st.session_state.knowledge_store = None
        conversation_store = get_conversation_store()
        if WorkspaceKnowledgeStore is not None and conversation_store is not None:
            try:
                st.session_state.knowledge_store = WorkspaceKnowledgeStore(
                    conversation_store.db_path
                )
            except Exception:
                logger.exception("Workspace 知识库存储初始化失败")

    store = get_conversation_store()
    if store is not None:
        conversation_id = st.session_state.get("active_conversation_id")
        conversation = (
            store.get_conversation(conversation_id) if conversation_id else None
        )
        if conversation is None:
            conversations = store.list_conversations(limit=1)
            conversation = (
                conversations[0] if conversations else store.create_conversation()
            )
            st.session_state.active_conversation_id = conversation["id"]
        load_active_messages()
    elif "messages" not in st.session_state:
        st.session_state.messages = []

    if "agent" not in st.session_state and AVAILABLE:
        try:
            st.session_state.agent = ArtPMAgent()
            logger.info("Agent实例化成功")
        except Exception as e:
            st.session_state.agent = None
            logger.error(f"Agent初始化失败: {e}")
            print(f"Agent init error: {e}")

    if "db" not in st.session_state and AVAILABLE:
        try:
            agent = st.session_state.get("agent")
            st.session_state.db = agent.database if agent is not None else DatabaseManager()
            logger.info("数据库管理器初始化成功")
        except Exception as e:
            st.session_state.db = None
            logger.error(f"数据库初始化失败: {e}")


def render_conversation_sidebar():
    store = get_conversation_store()
    if store is None:
        return

    active_id = st.session_state.get("active_conversation_id")
    request_pending = bool(st.session_state.get("pending_prompt"))
    with st.container(key="conversation_panel"):
        st.markdown(
            '<div class="sidebar-section-label">最近会话</div>',
            unsafe_allow_html=True,
        )
        if st.button(
            "新建对话",
            key="new_conversation",
            icon=":material/add:",
            use_container_width=True,
            disabled=request_pending,
        ):
            conversation = store.create_conversation()
            activate_conversation(conversation["id"])
            st.rerun()

        conversations = store.list_conversations(limit=20)
        with st.container(key="conversation_list", height=220, border=False):
            for conversation in conversations:
                conversation_id = conversation["id"]
                is_active = conversation_id == active_id
                with st.container(key=f"conversation_row_{conversation_id}"):
                    title_col, delete_col = st.columns(
                        [1, 0.16],
                        gap="small",
                        vertical_alignment="center",
                    )
                    with title_col:
                        if st.button(
                            conversation["title"],
                            key=f"conversation_{conversation_id}",
                            type="primary" if is_active else "secondary",
                            help=conversation["title"],
                            use_container_width=True,
                            disabled=(
                                request_pending
                                or (is_active and st.session_state.view == "对话")
                            ),
                        ):
                            activate_conversation(conversation_id)
                            st.session_state.pop("pending_conversation_delete", None)
                            st.rerun()
                    with delete_col:
                        if st.button(
                            "删除",
                            key=f"quick_delete_conversation_{conversation_id}",
                            icon=":material/delete_outline:",
                            help=f"删除会话：{conversation['title']}",
                            disabled=request_pending,
                        ):
                            st.session_state.pending_conversation_delete = conversation_id

                if (
                    st.session_state.get("pending_conversation_delete")
                    == conversation_id
                ):
                    with st.container(
                        key=f"conversation_delete_confirm_{conversation_id}"
                    ):
                        st.caption("确认删除此会话？")
                        confirm_col, cancel_col = st.columns(2, gap="small")
                        with confirm_col:
                            confirm_delete = st.button(
                                "删除",
                                key=f"confirm_quick_delete_{conversation_id}",
                                type="primary",
                                use_container_width=True,
                            )
                        with cancel_col:
                            cancel_delete = st.button(
                                "取消",
                                key=f"cancel_quick_delete_{conversation_id}",
                                use_container_width=True,
                            )
                        if confirm_delete:
                            delete_conversation_and_activate_next(
                                store,
                                conversation_id,
                            )
                            st.rerun()
                        if cancel_delete:
                            st.session_state.pop("pending_conversation_delete", None)
                            st.rerun()

        current = store.get_conversation(active_id) if active_id else None
        if current is not None:
            with st.expander("管理当前对话", icon=":material/edit_note:"):
                title = st.text_input(
                    "会话名称",
                    value=current["title"],
                    max_chars=ConversationStore.MAX_TITLE_LENGTH,
                    key=f"rename_input_{active_id}",
                    disabled=request_pending,
                )
                if st.button(
                    "保存名称",
                    key=f"rename_conversation_{active_id}",
                    use_container_width=True,
                    disabled=request_pending or not title.strip(),
                ):
                    store.rename_conversation(active_id, title.strip())
                    st.rerun()

                delete_confirmed = st.checkbox(
                    "确认删除此会话",
                    key=f"confirm_delete_conversation_{active_id}",
                    disabled=request_pending,
                )
                if st.button(
                    "删除会话",
                    key=f"delete_conversation_{active_id}",
                    icon=":material/delete_outline:",
                    use_container_width=True,
                    disabled=request_pending or not delete_confirmed,
                ):
                    delete_conversation_and_activate_next(store, active_id)
                    st.rerun()


def render_sidebar():
    """渲染侧边栏"""
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
                <span class="brand-mark" aria-hidden="true"></span>
                <span class="brand-name">ArtPM Agent</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        render_conversation_sidebar()

        with st.container(key="sidebar_modes"):
            chat_col, settings_col = st.columns(2)
            with chat_col:
                if st.button(
                    "对话",
                    key="nav_chat",
                    icon=":material/chat_bubble_outline:",
                    type="primary" if st.session_state.view == "对话" else "secondary",
                    use_container_width=True,
                ):
                    if st.session_state.view != "对话":
                        st.session_state.view = "对话"
                        st.rerun()
            with settings_col:
                if st.button(
                    "设置",
                    key="nav_settings",
                    icon=":material/settings:",
                    type="primary" if st.session_state.view == "设置" else "secondary",
                    use_container_width=True,
                ):
                    if st.session_state.view != "设置":
                        st.session_state.view = "设置"
                        st.rerun()


def normalize_agent_response(response):
    """Return displayable assistant text or fail loudly instead of rendering blank."""
    if not isinstance(response, str) or not response.strip():
        raise ValueError("模型服务未返回有效回答")
    return response.strip()


def _pending_request(raw_pending):
    if isinstance(raw_pending, dict):
        return raw_pending
    if not isinstance(raw_pending, str) or not raw_pending.strip():
        return None

    prompt = raw_pending.strip()
    conversation_id = st.session_state.get("active_conversation_id")
    user_message = next(
        (
            message
            for message in reversed(st.session_state.get("messages", []))
            if message.get("role") == "user" and message.get("content") == prompt
        ),
        {},
    )
    return {
        "prompt": prompt,
        "conversation_id": conversation_id,
        "user_message_id": user_message.get("id"),
        "turn_id": user_message.get("turn_id", uuid4().hex),
    }


def _history_limits(agent):
    config = getattr(agent, "config", None)
    if config is None or not hasattr(config, "get"):
        return 12, 8000
    return (
        int(config.get("llm.history_max_messages", 12)),
        int(config.get("llm.history_max_chars", 8000)),
    )


def _current_model_id(agent):
    config = getattr(agent, "config", None)
    if config is None or not hasattr(config, "get"):
        return None
    model_id = config.get("llm.model")
    return str(model_id).strip() if model_id else None


def _chat_error_message(error, model_id=None):
    """Return a useful, bounded message without exposing provider internals."""
    signals = []
    current = error
    seen = set()
    while current is not None and id(current) not in seen and len(signals) < 5:
        seen.add(id(current))
        signals.append(f"{type(current).__name__}: {current}".casefold())
        current = current.__cause__ or current.__context__
    signal_text = " ".join(signals)
    display_model = " ".join(str(model_id or "当前模型").split())[:80]

    if any(marker in signal_text for marker in ("timeout", "timed out", "超时")):
        return (
            f"模型 {display_model} 响应超时，未收到回答。请重新生成；"
            "若持续超时，请在设置中确认该模型支持聊天接口。"
        )
    if any(
        marker in signal_text
        for marker in ("connection", "connecterror", "连接失败", "无法连接")
    ):
        return (
            f"模型 {display_model} 连接失败，未收到回答。"
            "请检查 API Base URL 和服务状态后重试。"
        )
    return "未收到有效回答。请重新生成，或检查模型连接。"


def _render_message_attachments(attachments):
    names = [
        escape(str(attachment.get("name", "附件")))
        for attachment in attachments or []
        if isinstance(attachment, dict)
    ]
    if not names:
        return
    items = "".join(
        f'<span class="chat-attachment-name">附件 · {name}</span>'
        for name in names
    )
    st.markdown(
        f'<div class="chat-attachments">{items}</div>',
        unsafe_allow_html=True,
    )


def _render_message_artifacts(metadata, message_key):
    generator = get_artifact_generator()
    artifacts = metadata.get("artifacts", []) if isinstance(metadata, dict) else []
    if generator is None or not isinstance(artifacts, list):
        return
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            continue
        stored_path = artifact.get("stored_path")
        name = str(artifact.get("name") or stored_path or "生成文件")
        if not isinstance(stored_path, str) or not stored_path.strip():
            continue
        try:
            path = (generator.root / stored_path).resolve()
            path.relative_to(generator.root)
            if path.parent != generator.root or not path.is_file():
                continue
            if path.stat().st_size > generator.max_file_size:
                continue
            data = path.read_bytes()
        except (OSError, ValueError):
            logger.warning("忽略无效的生成文件记录: %s", stored_path)
            continue
        st.download_button(
            f"下载 {name}",
            data=data,
            file_name=name,
            mime=artifact.get("mime_type") or "application/octet-stream",
            key=f"artifact_{message_key}_{index}_{artifact.get('id', '')}",
            icon=":material/download:",
        )


def _persist_workflow_response(run, content, *, status="complete"):
    """Persist one final assistant message for a workflow turn, idempotently."""
    store = get_conversation_store()
    if store is None or not run.conversation_id or not run.turn_id:
        return
    existing = next(
        (
            message
            for message in store.list_messages(run.conversation_id)
            if message.get("role") == "assistant"
            and message.get("turn_id") == run.turn_id
        ),
        None,
    )
    if existing is None:
        store.add_message(
            run.conversation_id,
            "assistant",
            content,
            status=status,
            turn_id=run.turn_id,
            model_id=_current_model_id(st.session_state.get("agent")),
            metadata={"workflow_run_id": run.id, "workflow_id": run.workflow_id},
        )
    load_active_messages()


def _persist_profile_response(proposal, content):
    """Persist one profile decision response for the originating chat turn."""
    store = get_conversation_store()
    if (
        store is None
        or not proposal.conversation_id
        or not proposal.turn_id
    ):
        return
    existing = next(
        (
            message
            for message in store.list_messages(proposal.conversation_id)
            if message.get("role") == "assistant"
            and message.get("turn_id") == proposal.turn_id
        ),
        None,
    )
    if existing is None:
        store.add_message(
            proposal.conversation_id,
            "assistant",
            content,
            turn_id=proposal.turn_id,
            model_id=_current_model_id(st.session_state.get("agent")),
            metadata={"agent_profile_proposal_id": proposal.id},
        )
    load_active_messages()


def _render_profile_approvals(conversation_id):
    """Render pending Agent Profile changes inside their source conversation."""
    profile_store = get_profile_store()
    if profile_store is None or not conversation_id:
        return
    try:
        proposals = profile_store.list_pending(conversation_id=conversation_id)
    except Exception:
        logger.exception("读取待确认 Agent Profile 提案失败")
        return

    for proposal in reversed(proposals):
        with st.container(key=f"profile_approval_{proposal.id}", border=True):
            st.markdown("#### 需要确认：调整 Agent 设置")
            st.markdown(proposal.summary)
            st.caption("确认后应用到当前工作区；不会改变工具权限或安全规则。")
            approve_col, reject_col = st.columns(2)
            with approve_col:
                approve = st.button(
                    "确认应用",
                    key=f"approve_profile_{proposal.id}",
                    type="primary",
                    icon=":material/check:",
                    use_container_width=True,
                )
            with reject_col:
                reject = st.button(
                    "保留原设置",
                    key=f"reject_profile_{proposal.id}",
                    icon=":material/close:",
                    use_container_width=True,
                )
            if approve:
                try:
                    updated = profile_store.confirm_change(
                        proposal.id,
                        actor="本地用户（会话确认）",
                    )
                    _persist_profile_response(
                        proposal,
                        f"Agent 设置已更新为 v{updated.revision}，后续回答将使用新配置。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("确认 Agent Profile 提案失败")
                    st.error(f"配置未更新：{error}")
            if reject:
                try:
                    rejected = profile_store.reject_change(
                        proposal.id,
                        actor="本地用户（会话拒绝）",
                    )
                    _persist_profile_response(
                        rejected,
                        "已保留原 Agent 设置，本次提案未生效。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("拒绝 Agent Profile 提案失败")
                    st.error(f"操作失败：{error}")


def _persist_knowledge_response(rule, content):
    store = get_conversation_store()
    conversation_id = rule.get("source_conversation_id")
    turn_id = rule.get("source_message_id")
    if store is None or not conversation_id or not turn_id:
        return
    existing = next(
        (
            message
            for message in store.list_messages(conversation_id)
            if message.get("role") == "assistant"
            and message.get("turn_id") == turn_id
        ),
        None,
    )
    if existing is None:
        store.add_message(
            conversation_id,
            "assistant",
            content,
            turn_id=turn_id,
            model_id=_current_model_id(st.session_state.get("agent")),
            metadata={"knowledge_rule_id": rule["id"]},
        )
    load_active_messages()


def _persist_ingestion_response(proposal, content):
    store = get_conversation_store()
    conversation_id = proposal.get("conversation_id")
    turn_id = proposal.get("turn_id")
    if store is None or not conversation_id or not turn_id:
        return
    existing = next(
        (
            message
            for message in store.list_messages(conversation_id)
            if message.get("role") == "assistant"
            and message.get("turn_id") == turn_id
        ),
        None,
    )
    if existing is None:
        store.add_message(
            conversation_id,
            "assistant",
            content,
            turn_id=turn_id,
            model_id=_current_model_id(st.session_state.get("agent")),
            metadata={"knowledge_ingestion_proposal_id": proposal["id"]},
        )
    load_active_messages()


def _render_knowledge_ingestion_approvals(conversation_id):
    knowledge_store = get_knowledge_store()
    if knowledge_store is None or not conversation_id:
        return
    try:
        proposals = knowledge_store.list_ingestion_proposals(
            status="pending",
            conversation_id=conversation_id,
        )
    except Exception:
        logger.exception("读取待确认资料入库提案失败")
        return

    for proposal in reversed(proposals):
        titles = [resource["title"] for resource in proposal["resources"]]
        with st.container(key=f"ingestion_approval_{proposal['id']}", border=True):
            st.markdown("#### 需要确认：加入工作区资料库")
            st.markdown("\n".join(f"- {title}" for title in titles))
            st.caption("确认后写入不可变版本；当前尚未进入 Agent 检索上下文。")
            approve_col, reject_col = st.columns(2)
            with approve_col:
                approve = st.button(
                    "确认入库",
                    key=f"approve_ingestion_{proposal['id']}",
                    type="primary",
                    icon=":material/check:",
                    use_container_width=True,
                )
            with reject_col:
                reject = st.button(
                    "取消",
                    key=f"reject_ingestion_{proposal['id']}",
                    icon=":material/close:",
                    use_container_width=True,
                )
            if approve:
                try:
                    confirmed = knowledge_store.confirm_ingestion(
                        proposal["id"],
                        actor="本地用户（会话确认）",
                        confirmation_token=(
                            f"{conversation_id}:{proposal['id']}:confirmed"
                        ),
                        expected_state_version=proposal["state_version"],
                    )
                    ingested = confirmed.get("ingested_resources", [])
                    _persist_ingestion_response(
                        confirmed,
                        f"已将 {len(ingested)} 份资料加入工作区资料库。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("确认资料入库失败")
                    st.error(f"资料未入库：{error}")
            if reject:
                try:
                    rejected = knowledge_store.reject_ingestion(
                        proposal["id"],
                        actor="本地用户（会话拒绝）",
                        expected_state_version=proposal["state_version"],
                    )
                    _persist_ingestion_response(
                        rejected,
                        "已取消资料入库，本次附件不会进入工作区知识。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("取消资料入库失败")
                    st.error(f"操作失败：{error}")


def _render_knowledge_approvals(conversation_id):
    knowledge_store = get_knowledge_store()
    if knowledge_store is None or not conversation_id:
        return
    try:
        rules = [
            rule
            for rule in knowledge_store.list_rules(status="proposed", limit=100)
            if rule.get("source_conversation_id") == conversation_id
        ]
    except Exception:
        logger.exception("读取待确认知识规则失败")
        return

    for rule in reversed(rules):
        with st.container(key=f"knowledge_approval_{rule['id']}", border=True):
            st.markdown("#### 需要确认：采纳为工作区知识")
            st.markdown(f"> {rule['statement']}")
            st.caption("确认后会用于后续会话；不会改变 Agent 权限或安全规则。")
            approve_col, reject_col = st.columns(2)
            with approve_col:
                approve = st.button(
                    "采纳",
                    key=f"approve_knowledge_{rule['id']}",
                    type="primary",
                    icon=":material/check:",
                    use_container_width=True,
                )
            with reject_col:
                reject = st.button(
                    "不采纳",
                    key=f"reject_knowledge_{rule['id']}",
                    icon=":material/close:",
                    use_container_width=True,
                )
            if approve:
                try:
                    accepted = knowledge_store.confirm_rule(
                        rule["id"],
                        confirmed_by="本地用户（会话确认）",
                        confirmation_token=(
                            f"{conversation_id}:{rule['id']}:accepted"
                        ),
                    )
                    _persist_knowledge_response(
                        accepted,
                        "这条规则已加入工作区知识，后续会话会参考它。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("采纳知识规则失败")
                    st.error(f"规则未采纳：{error}")
            if reject:
                try:
                    rejected = knowledge_store.reject_rule(
                        rule["id"],
                        rejected_by="本地用户（会话拒绝）",
                    )
                    _persist_knowledge_response(
                        rejected,
                        "已忽略这条知识提案，后续会话不会使用它。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("拒绝知识规则失败")
                    st.error(f"操作失败：{error}")


def _render_workflow_approvals(conversation_id):
    """Render only decision-relevant workflow state inside the conversation."""
    workflow_store = get_workflow_store()
    coordinator = get_workflow_coordinator()
    if workflow_store is None or coordinator is None or not conversation_id:
        return
    try:
        waiting_runs = workflow_store.list_runs(
            conversation_id,
            statuses=("awaiting_approval",),
            limit=5,
        )
    except Exception:
        logger.exception("读取待审批工作流失败")
        return

    for run in reversed(waiting_runs):
        definition = run.definition_snapshot
        step = definition.steps[run.current_step]
        preview = format_workflow_result(
            st.session_state.get("agent"),
            coordinator.engine.result(run.id),
        )
        with st.container(key=f"workflow_approval_{run.id}", border=True):
            st.markdown(f"#### 需要确认：{definition.name}")
            st.markdown(preview)
            action_label = (
                "发送催办消息"
                if step.skill_id == "reminder_dispatch"
                else definition.name
            )
            st.caption(f"下一步：{action_label} · 批准前不会执行外部操作")
            approve_col, reject_col = st.columns(2)
            with approve_col:
                approve = st.button(
                    "批准执行",
                    key=f"approve_workflow_{run.id}_{run.current_step}",
                    type="primary",
                    icon=":material/check:",
                    use_container_width=True,
                )
            with reject_col:
                reject = st.button(
                    "取消",
                    key=f"reject_workflow_{run.id}_{run.current_step}",
                    icon=":material/close:",
                    use_container_width=True,
                )

            if approve:
                try:
                    result = coordinator.engine.decide_approval(
                        run.id,
                        run.current_step,
                        decision="approved",
                        actor="local-default",
                        actor_level="user",
                    )
                    content = format_workflow_result(
                        st.session_state.get("agent"), result
                    )
                    response_status = (
                        "complete" if result.run.status == "succeeded" else "error"
                    )
                    _persist_workflow_response(
                        result.run,
                        content,
                        status=response_status,
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("工作流审批执行失败")
                    st.error(f"执行失败：{error}")
            if reject:
                try:
                    result = coordinator.engine.decide_approval(
                        run.id,
                        run.current_step,
                        decision="rejected",
                        actor="local-default",
                        actor_level="user",
                    )
                    _persist_workflow_response(
                        result.run,
                        "已取消该工作流，未执行后续外部操作。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("取消工作流失败")
                    st.error(f"取消失败：{error}")


def chat_page():
    """对话页面"""
    st.markdown('<div class="chat-page-marker"></div>', unsafe_allow_html=True)
    store = get_conversation_store()
    active_id = st.session_state.get("active_conversation_id")
    active_conversation = (
        store.get_conversation(active_id) if store is not None and active_id else None
    )

    if not st.session_state.messages:
        st.markdown('<div class="chat-empty-marker"></div>', unsafe_allow_html=True)
        st.title("今天要推进什么？")
    else:
        with st.container(key="chat_header"):
            header_col, action_col = st.columns([1, 0.08], vertical_alignment="bottom")
            with header_col:
                conversation_title = (
                    active_conversation["title"] if active_conversation else "智能助手"
                )
                st.title(conversation_title)
            with action_col:
                if st.button(
                    "清空",
                    key="clear_chat",
                    icon=":material/delete_outline:",
                    help="清空当前对话",
                    width="content",
                ):
                    st.session_state[f"confirm_clear_chat_{active_id}"] = True

            clear_state_key = f"confirm_clear_chat_{active_id}"
            if st.session_state.get(clear_state_key):
                with st.container(key="clear_chat_confirmation", border=True):
                    st.markdown("**确认清空当前对话？**")
                    st.caption("这会删除消息、附件和运行记录，且无法撤销。")
                    confirm_col, cancel_col = st.columns(2)
                    with confirm_col:
                        confirm_clear = st.button(
                            "确认清空",
                            key=f"confirm_clear_chat_action_{active_id}",
                            type="primary",
                            use_container_width=True,
                        )
                    with cancel_col:
                        cancel_clear = st.button(
                            "取消",
                            key=f"cancel_clear_chat_action_{active_id}",
                            use_container_width=True,
                        )
                    if confirm_clear:
                        if store is not None and active_id:
                            store.clear_messages(active_id)
                        workflow_store = get_workflow_store()
                        if workflow_store is not None and active_id:
                            workflow_store.clear_conversation_runs(active_id)
                        attachment_store = get_chat_attachment_store()
                        if attachment_store is not None and active_id:
                            attachment_store.remove_conversation(active_id)
                        st.session_state.messages = []
                        st.session_state.pop("pending_prompt", None)
                        st.session_state.pop(clear_state_key, None)
                        st.rerun()
                    if cancel_clear:
                        st.session_state.pop(clear_state_key, None)
                        st.rerun()

    pending_request = _pending_request(st.session_state.get("pending_prompt"))
    with st.container(key="chat_thread"):
        for index, msg in enumerate(st.session_state.messages):
            role = msg["role"]
            content = msg["content"]
            metadata = msg.get("metadata") or {}
            avatar = ":material/person:" if role == "user" else ":material/neurology:"
            with st.chat_message(role, avatar=avatar):
                st.markdown(
                    f'<span class="chat-role-marker chat-role-{role}"></span>',
                    unsafe_allow_html=True,
                )
                if msg.get("status") == "error":
                    st.error(content)
                    retry_prompt = msg.get("retry_prompt") or metadata.get("retry_prompt")
                    message_id = msg.get("id", index)
                    if retry_prompt and st.button(
                        "重新生成",
                        key=f"retry_{active_id or 'legacy'}_{message_id}",
                    ):
                        if store is not None and msg.get("id"):
                            store.delete_message(msg["id"])
                            user_message = next(
                                (
                                    item
                                    for item in reversed(store.list_messages(active_id))
                                    if item.get("role") == "user"
                                    and item.get("turn_id") == msg.get("turn_id")
                                ),
                                None,
                            )
                            load_active_messages()
                        else:
                            st.session_state.messages.pop(index)
                            user_message = next(
                                (
                                    item
                                    for item in reversed(st.session_state.messages)
                                    if item.get("role") == "user"
                                ),
                                None,
                            )
                        st.session_state.pending_prompt = {
                            "prompt": retry_prompt,
                            "conversation_id": active_id,
                            "user_message_id": (
                                user_message.get("id") if user_message else None
                            ),
                            "turn_id": msg.get("turn_id", uuid4().hex),
                            "attachments": metadata.get("attachments", []),
                        }
                        st.rerun()
                else:
                    st.markdown(content)
                    if role == "user":
                        _render_message_attachments(metadata.get("attachments", []))
                    else:
                        _render_message_artifacts(
                            metadata,
                            msg.get("id", index),
                        )

        _render_profile_approvals(active_id)
        _render_knowledge_ingestion_approvals(active_id)
        _render_knowledge_approvals(active_id)
        _render_workflow_approvals(active_id)

        if pending_request:
            prompt = pending_request["prompt"]
            request_conversation_id = pending_request.get("conversation_id") or active_id
            if active_id and request_conversation_id != active_id:
                st.session_state.pop("pending_prompt", None)
                st.rerun()
            if AVAILABLE and st.session_state.get("agent"):
                agent = st.session_state.agent
                attachments = pending_request.get("attachments", [])
                with st.chat_message("assistant", avatar=":material/neurology:"):
                    st.markdown(
                        '<span class="chat-role-marker chat-role-assistant"></span>',
                        unsafe_allow_html=True,
                    )
                    try:
                        history = []
                        if store is not None and request_conversation_id:
                            max_messages, max_chars = _history_limits(agent)
                            history = store.build_context(
                                request_conversation_id,
                                max_messages=max_messages,
                                max_chars=max_chars,
                                before_message_id=pending_request.get("user_message_id"),
                            )
                        attachments = select_conversation_attachments(
                            prompt,
                            pending_request.get("attachments", []),
                            st.session_state.messages,
                        )
                        attachment_store = get_chat_attachment_store()
                        file_paths = (
                            attachment_store.resolve_paths(attachments)
                            if attachment_store is not None and attachments
                            else []
                        )
                        agent_context = {
                            "conversation_id": request_conversation_id,
                            "workspace_id": ConversationStore.DEFAULT_WORKSPACE_ID,
                            "conversation_history": history,
                            "attachments": attachments,
                            "file_path": file_paths[0] if file_paths else None,
                            "file_paths": file_paths,
                            "agent_profile": get_current_profile(),
                            "knowledge_context": build_knowledge_context(prompt),
                        }
                        workflow_result = None
                        workflow_metadata = {}
                        awaiting_approval = False
                        handled_response = False
                        with st.spinner(
                            chat_processing_label(prompt, _current_model_id(agent))
                        ):
                            profile_store = get_profile_store()
                            current_profile = get_current_profile()
                            if (
                                profile_store is not None
                                and current_profile is not None
                                and parse_profile_change is not None
                                and request_conversation_id
                            ):
                                try:
                                    profile_patch = parse_profile_change(
                                        prompt,
                                        current_profile,
                                    )
                                    if profile_patch is not None:
                                        preview = format_profile_changes(
                                            current_profile,
                                            profile_patch,
                                        )
                                        profile_store.propose_change(
                                            request_conversation_id,
                                            profile_patch,
                                            turn_id=pending_request["turn_id"],
                                            summary=preview,
                                            idempotency_key=(
                                                f"profile-turn-"
                                                f"{pending_request['turn_id']}"
                                            ),
                                        )
                                        response = (
                                            f"{preview}\n\n"
                                            "请在当前会话中确认后再应用。"
                                        )
                                        awaiting_approval = True
                                        handled_response = True
                                except ProfileChangeParseError as error:
                                    response = f"这个配置变更暂不能应用：{error}"
                                    handled_response = True
                                except Exception:
                                    logger.exception("创建 Agent Profile 提案失败")
                                    response = "配置提案未能保存，请检查服务日志后重试。"
                                    handled_response = True

                            if (
                                not handled_response
                                and request_conversation_id
                                and is_knowledge_ingestion_request(prompt)
                            ):
                                if not attachments:
                                    response = (
                                        "请先在当前消息中附上需要加入资料库的文件。"
                                    )
                                    handled_response = True
                                else:
                                    try:
                                        resources = build_knowledge_ingestion_resources(
                                            agent,
                                            attachments,
                                            file_paths,
                                            prompt,
                                        )
                                        knowledge_store = get_knowledge_store()
                                        if knowledge_store is None:
                                            raise RuntimeError("资料库存储未就绪")
                                        knowledge_store.propose_ingestion(
                                            request_conversation_id,
                                            pending_request["turn_id"],
                                            resources,
                                            (
                                                f"knowledge-turn-"
                                                f"{pending_request['turn_id']}"
                                            ),
                                        )
                                        names = "、".join(
                                            resource["title"] for resource in resources
                                        )
                                        response = (
                                            f"已解析待入库资料：{names}。\n\n"
                                            "当前尚未写入资料库，请确认后再入库。"
                                        )
                                        awaiting_approval = True
                                        handled_response = True
                                    except Exception as error:
                                        logger.exception("创建资料入库提案失败")
                                        response = f"资料暂不能入库：{error}"
                                        handled_response = True

                            if not handled_response and request_conversation_id:
                                rule_statement = extract_knowledge_rule(prompt)
                                knowledge_store = get_knowledge_store()
                                if rule_statement and knowledge_store is not None:
                                    try:
                                        knowledge_store.propose_rule(
                                            rule_statement,
                                            proposed_by="agent",
                                            source_conversation_id=(
                                                request_conversation_id
                                            ),
                                            source_message_id=pending_request["turn_id"],
                                            rule_id=(
                                                f"rule-{pending_request['turn_id']}"
                                            ),
                                            metadata={
                                                "origin": "conversation",
                                                "requires_confirmation": True,
                                            },
                                        )
                                        response = (
                                            "我可以把下面这条规则加入工作区知识：\n\n"
                                            f"> {rule_statement}\n\n"
                                            "当前尚未生效，请确认是否采纳。"
                                        )
                                        awaiting_approval = True
                                        handled_response = True
                                    except Exception:
                                        logger.exception("创建知识规则提案失败")
                                        response = (
                                            "知识提案未能保存，请检查服务日志后重试。"
                                        )
                                        handled_response = True

                            if not handled_response:
                                artifact_coordinator = get_artifact_coordinator()
                                if artifact_coordinator is not None:
                                    artifact_outcome = artifact_coordinator.process(
                                        prompt
                                    )
                                    if artifact_outcome.matched:
                                        response = artifact_outcome.message
                                        handled_response = True
                                        if artifact_outcome.artifact is not None:
                                            artifact = artifact_outcome.artifact
                                            workflow_metadata = {
                                                "artifacts": [
                                                    {
                                                        key: artifact[key]
                                                        for key in (
                                                            "id",
                                                            "name",
                                                            "stored_path",
                                                            "format",
                                                            "mime_type",
                                                            "size",
                                                            "sha256",
                                                            "version",
                                                        )
                                                        if key in artifact
                                                    }
                                                ]
                                            }

                            if not handled_response:
                                coordinator = get_workflow_coordinator()
                                outcome = None
                                if coordinator is not None and request_conversation_id:
                                    try:
                                        outcome = coordinator.process(
                                            prompt,
                                            conversation_id=request_conversation_id,
                                            turn_id=pending_request["turn_id"],
                                            agent_context=agent_context,
                                            attachments=attachments,
                                        )
                                    except Exception:
                                        logger.exception(
                                            "工作流匹配或只读执行失败，回退普通对话"
                                        )
                                if outcome is not None and outcome.matched:
                                    workflow_result = outcome.execution
                                    awaiting_approval = (
                                        workflow_result.run.status
                                        == "awaiting_approval"
                                    )
                                    if workflow_result.run.status == "failed":
                                        logger.warning(
                                            "工作流 %s 未完成，回退普通对话: %s",
                                            workflow_result.run.workflow_id,
                                            workflow_result.run.error,
                                        )
                                        workflow_result = None
                                    else:
                                        response = normalize_agent_response(
                                            format_workflow_result(
                                                agent,
                                                workflow_result,
                                            )
                                        )
                                        workflow_metadata = {
                                            "workflow_run_id": workflow_result.run.id,
                                            "workflow_id": workflow_result.run.workflow_id,
                                        }
                            if workflow_result is None and not handled_response:
                                response = normalize_agent_response(
                                    agent.chat(
                                        prompt,
                                        context=agent_context,
                                    )
                                )
                        st.markdown(response)
                        _render_message_artifacts(
                            workflow_metadata,
                            pending_request["turn_id"],
                        )
                        if (
                            store is not None
                            and request_conversation_id
                            and not awaiting_approval
                        ):
                            store.add_message(
                                request_conversation_id,
                                "assistant",
                                response,
                                turn_id=pending_request["turn_id"],
                                model_id=_current_model_id(agent),
                                metadata=workflow_metadata,
                            )
                            load_active_messages()
                        elif store is None and not awaiting_approval:
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": response,
                                "time": format_cn_date(datetime.now(), include_time=True),
                            })
                    except Exception as error:
                        logger.exception("对话处理失败")
                        error_message = _chat_error_message(
                            error,
                            _current_model_id(agent),
                        )
                        st.error(error_message)
                        if store is not None and request_conversation_id:
                            store.add_message(
                                request_conversation_id,
                                "assistant",
                                error_message,
                                status="error",
                                turn_id=pending_request["turn_id"],
                                model_id=_current_model_id(agent),
                                metadata={
                                    "retry_prompt": prompt,
                                    "attachments": attachments,
                                },
                            )
                            load_active_messages()
                        else:
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": error_message,
                                "time": format_cn_date(datetime.now(), include_time=True),
                                "status": "error",
                                "retry_prompt": prompt,
                                "metadata": {"attachments": attachments},
                            })
            else:
                error_message = "Agent 未初始化，请检查设置中的模型配置并重启服务。"
                with st.chat_message("assistant", avatar=":material/neurology:"):
                    st.error(error_message)
                if store is not None and request_conversation_id:
                    store.add_message(
                        request_conversation_id,
                        "assistant",
                        error_message,
                        status="error",
                        turn_id=pending_request["turn_id"],
                        metadata={
                            "retry_prompt": prompt,
                            "attachments": pending_request.get("attachments", []),
                        },
                    )
                    load_active_messages()
                else:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": error_message,
                        "time": format_cn_date(datetime.now(), include_time=True),
                        "status": "error",
                        "retry_prompt": prompt,
                        "metadata": {
                            "attachments": pending_request.get("attachments", [])
                        },
                    })
            st.session_state.pop("pending_prompt", None)
            st.rerun()

    user_submission = st.chat_input(
        "输入消息或添加附件",
        key="chat_input",
        max_chars=4000,
        accept_file="multiple",
        file_type=[
            "xlsx",
            "xls",
            "csv",
            "json",
            "txt",
            "md",
            "pdf",
            "docx",
            "png",
            "jpg",
            "jpeg",
            "webp",
        ],
        disabled=not AVAILABLE or bool(pending_request),
    )

    prompt_text, uploaded_files = normalize_chat_submission(user_submission)
    if prompt_text or uploaded_files:
        attachments = []
        if uploaded_files:
            attachment_store = get_chat_attachment_store()
            if attachment_store is None or not active_id:
                st.error("附件存储未就绪，请刷新页面后重试。")
                return
            try:
                attachments = attachment_store.save_files(active_id, uploaded_files)
            except (OSError, TypeError, ValueError) as error:
                logger.warning("保存会话附件失败: %s", error)
                st.error(f"附件未能保存：{error}")
                return

        prompt = prompt_text or "请分析这些附件，并给出关键结论和下一步建议。"
        turn_id = uuid4().hex
        message_metadata = {"attachments": attachments} if attachments else {}
        if store is not None and active_id:
            user_message = store.add_message(
                active_id,
                "user",
                prompt,
                turn_id=turn_id,
                metadata=message_metadata,
            )
            if (
                active_conversation
                and active_conversation["title"] == ConversationStore.DEFAULT_TITLE
            ):
                new_title = _conversation_title_from_prompt(prompt)
                store.rename_conversation(active_id, new_title)
                st.session_state.pending_conversation_title_sync = {
                    "conversation_id": active_id,
                    "title": new_title,
                }
            load_active_messages()
        else:
            user_message = {
                "role": "user",
                "content": prompt,
                "time": format_cn_date(datetime.now(), include_time=True),
                "turn_id": turn_id,
                "metadata": message_metadata,
            }
            st.session_state.messages.append(user_message)
        st.session_state.pending_prompt = {
            "prompt": prompt,
            "conversation_id": active_id,
            "user_message_id": user_message.get("id"),
            "turn_id": turn_id,
            "attachments": attachments,
        }
        st.rerun()

    if not AVAILABLE:
        st.error("Agent 模块加载失败，请查看服务日志并修复依赖后重试。")


def overview_page():
    """概览页面"""
    render_page_header("工作区 / 概览", "经营概览", "项目组合的当前状态")
    stats, stats_error = get_project_stats()
    profit = stats.get("avg_profit_rate", 0) * 100

    st.markdown(
        f"""
        <div class="metric-rail">
            <div class="metric-item tone-blue">
                <div class="metric-label">全部项目</div>
                <div class="metric-value">{stats['total_projects']}</div>
                <div class="metric-note positive">{stats['in_progress']} 个正在执行</div>
            </div>
            <div class="metric-item tone-amber">
                <div class="metric-label">进行中</div>
                <div class="metric-value">{stats['in_progress']}</div>
                <div class="metric-note">当前工作负载</div>
            </div>
            <div class="metric-item tone-teal">
                <div class="metric-label">已完成营收</div>
                <div class="metric-value">{format_money(stats['total_revenue'], compact=True)}</div>
                <div class="metric-note">{stats['completed']} 个项目已完成</div>
            </div>
            <div class="metric-item tone-blue">
                <div class="metric-label">平均利润率</div>
                <div class="metric-value">{profit:.1f}%</div>
                <div class="metric-note">已核算项目平均值</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if stats_error:
        st.error(stats_error)

    projects, projects_error = get_projects(limit=6)
    render_section_heading("最近项目", f"最近 {len(projects)} 条")
    if projects_error:
        st.error(projects_error)
    elif projects:
        render_project_table(projects, key="overview_projects")
    else:
        st.info("尚无项目。导入报价单后，项目会显示在这里。")


def projects_page():
    """项目页面"""
    render_page_header("工作区 / 项目", "项目台账", "集中查看项目状态、报价与交付日期")

    filter_col, search_col = st.columns([2, 5])
    with filter_col:
        status = st.selectbox(
            "项目状态",
            ["全部", "待开始", "进行中", "已完成", "已取消"],
            label_visibility="collapsed",
            key="project_status",
        )
    with search_col:
        query = st.text_input(
            "搜索项目",
            placeholder="搜索项目或客户",
            label_visibility="collapsed",
            key="project_search",
        )

    projects, projects_error = get_projects(
        limit=100,
        status=None if status == "全部" else status,
    )
    if query.strip():
        keyword = query.strip().casefold()
        projects = [
            project
            for project in projects
            if keyword in project.project_name.casefold()
            or keyword in project.client.casefold()
        ]

    render_section_heading("项目明细", f"{len(projects)} 条记录")
    if projects_error:
        st.error(projects_error)
    elif projects:
        render_project_table(projects, key="all_projects")
    else:
        st.info("没有符合当前条件的项目。调整筛选条件或导入新的报价单。")


def upload_page():
    """上传页面"""
    render_page_header("工作区 / 导入", "导入报价", "Excel 报价单解析")
    st.markdown(
        """
        <div class="step-rail">
            <div class="step current"><strong>01</strong>选择文件</div>
            <div class="step pending"><strong>02</strong>解析确认</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    uploaded_file = st.file_uploader(
        "选择 Excel 文件",
        type=["xlsx", "xls"],
        help="支持 .xlsx 和 .xls，单个文件不超过 50 MB",
        key="quote_uploader",
    )

    if uploaded_file:
        max_file_size = 50 * 1024 * 1024
        if uploaded_file.size > max_file_size:
            st.error("文件超过 50MB 限制，请压缩或拆分后重试")
            return
        file_identity = (uploaded_file.name, uploaded_file.size)
        if st.session_state.get("uploaded_file_identity") != file_identity:
            st.session_state.uploaded_file_identity = file_identity
            st.session_state.pop("upload_result", None)
        st.success(f"文件已就绪：{uploaded_file.name}")

        if st.button("开始解析", type="primary", key="parse_quote"):
            if AVAILABLE:
                parser = ExcelQuoteParser()
                with st.spinner("正在解析报价单"):
                    try:
                        result = parser.parse(uploaded_file)
                        st.session_state.upload_result = result
                    except Exception as error:
                        logger.exception("报价单解析失败")
                        st.session_state.upload_result = {
                            "success": False,
                            "error": str(error),
                        }
            else:
                st.error("解析器未加载，请检查服务依赖后重试")

        result = st.session_state.get("upload_result")
        if result:
            if result.get("success"):
                st.success("解析完成，结果已生成")
                extracted = result.get("extracted_data", {})
                proj_info = extracted.get("project_info", {})
                render_section_heading("解析结果", "待确认")
                project_name = escape(str(proj_info.get("project_name", "未知")))
                client_name = escape(str(proj_info.get("client_name", "未知")))
                st.markdown(
                    f"""
                    <dl class="definition-grid">
                        <div class="definition-item"><dt>项目名称</dt><dd>{project_name}</dd></div>
                        <div class="definition-item"><dt>客户</dt><dd>{client_name}</dd></div>
                        <div class="definition-item"><dt>总金额</dt><dd>{format_money(extracted.get('total_amount', 0))}</dd></div>
                        <div class="definition-item"><dt>资产数量</dt><dd>{len(extracted.get('assets', []))} 项</dd></div>
                    </dl>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                st.error(f"解析失败：{result.get('error', '文件内容无法识别')}。请检查模板后重试。")


MANUAL_MODEL_OPTION = "手动输入模型 ID"


def persist_settings(config, env_path=None):
    """只更新受管理的配置项，保留 .env 中的注释和其它设置。"""
    from dotenv import set_key, unset_key

    env_path = (
        Path(env_path)
        if env_path
        else Path(__file__).resolve().parent.parent / ".env"
    )
    env_path.touch(exist_ok=True)
    keys_to_unset = set()
    values = {
        "LLM_PROVIDER": config["provider"],
        "LLM_MODEL": config["model"],
        "MCP_ENABLED": str(config["mcp_enabled"]).lower(),
        "SKILLS_FORGE_KEY": config["mcp_key"],
        "SKILLS_FORGE_URL": config["mcp_url"],
    }
    # Legacy callers may still persist seed values. The settings page no longer
    # writes them because quote rules are managed by confirmed conversation changes.
    if "overhead_rate" in config:
        values["OVERHEAD_RATE"] = str(config["overhead_rate"])
    if "tax_rate" in config:
        values["TAX_RATE"] = str(config["tax_rate"])
    available_models = config.get("available_models") or []
    models_synced_at = (config.get("models_synced_at") or "").strip()
    if MODEL_CATALOG_AVAILABLE:
        if available_models:
            values["LLM_AVAILABLE_MODELS"] = serialize_cached_models(available_models)
        else:
            keys_to_unset.add("LLM_AVAILABLE_MODELS")
        if models_synced_at:
            values["LLM_MODELS_SYNCED_AT"] = models_synced_at
        else:
            keys_to_unset.add("LLM_MODELS_SYNCED_AT")

    provider_key = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "zhipu": "ZHIPU_API_KEY",
        "custom": "OPENAI_API_KEY",
    }[config["provider"]]
    provider_base = {
        "openai": "OPENAI_API_BASE",
        "anthropic": "ANTHROPIC_API_BASE",
        "zhipu": "ZHIPU_API_BASE",
        "custom": "OPENAI_API_BASE",
    }[config["provider"]]

    if config["api_key"]:
        values[provider_key] = config["api_key"]
    if config["api_base_url"]:
        values[provider_base] = config["api_base_url"]
    else:
        keys_to_unset.add(provider_base)

    for key, value in values.items():
        set_key(str(env_path), key, str(value), quote_mode="auto")
        os.environ[key] = str(value)
    for key in keys_to_unset:
        unset_key(str(env_path), key)
        os.environ.pop(key, None)


def settings_page():
    """设置页面"""
    render_page_header("系统 / 设置", "设置", "模型、工具与业务参数")

    mcp_skills = []
    mcp_error = None
    if AVAILABLE and st.session_state.get("agent"):
        try:
            mcp_client = getattr(st.session_state.agent, "mcp_client", None)
            if mcp_client and mcp_client.is_enabled():
                mcp_skills = mcp_client.list_skills()
        except Exception as error:
            logger.warning(f"MCP 状态检查失败: {error}")
            mcp_error = "Skills Forge 状态读取失败"

    provider_options = ["openai", "anthropic", "zhipu", "custom"]
    configured_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    provider_index = (
        provider_options.index(configured_provider)
        if configured_provider in provider_options
        else 0
    )

    model_tab, mcp_tab, workflow_tab, knowledge_tab, business_tab = st.tabs(
        ["模型", "工具与连接", "工作流", "知识", "Agent"]
    )
    with model_tab:
        llm_provider = st.selectbox(
            "LLM 提供商",
            provider_options,
            index=provider_index,
            help="支持官方服务和 OpenAI 兼容接口",
            key="llm_provider",
        )
        provider_key_names = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "zhipu": "ZHIPU_API_KEY",
            "custom": "OPENAI_API_KEY",
        }
        provider_base_names = {
            "openai": "OPENAI_API_BASE",
            "anthropic": "ANTHROPIC_API_BASE",
            "zhipu": "ZHIPU_API_BASE",
            "custom": "OPENAI_API_BASE",
        }
        defaults = {
            "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
            "anthropic": ("https://api.anthropic.com", "claude-3-5-sonnet-20241022"),
            "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4"),
            "custom": ("https://api.openai.com/v1", "gpt-4o-mini"),
        }
        default_base_url, default_model = defaults[llm_provider]
        configured_model = (
            os.getenv("LLM_MODEL", "")
            if llm_provider == configured_provider
            else default_model
        ) or default_model
        api_key = st.text_input(
            "API Key",
            type="password",
            value=os.getenv(provider_key_names[llm_provider], ""),
            placeholder="输入 API Key",
            key=f"api_key_{llm_provider}",
        )
        api_base_url = st.text_input(
            "API Base URL",
            value=os.getenv(provider_base_names[llm_provider], ""),
            placeholder=default_base_url,
            key=f"api_base_{llm_provider}",
        ).strip()

        catalog_key = f"available_models_{llm_provider}"
        synced_at_key = f"models_synced_at_{llm_provider}"
        if catalog_key not in st.session_state:
            st.session_state[catalog_key] = (
                parse_cached_models(os.getenv("LLM_AVAILABLE_MODELS", ""))
                if MODEL_CATALOG_AVAILABLE and llm_provider == configured_provider
                else []
            )
        if synced_at_key not in st.session_state:
            st.session_state[synced_at_key] = (
                os.getenv("LLM_MODELS_SYNCED_AT", "")
                if llm_provider == configured_provider
                else ""
            )

        supports_model_sync = (
            MODEL_CATALOG_AVAILABLE
            and llm_provider in {"openai", "custom", "zhipu"}
        )
        sync_feedback = None
        sync_col, sync_meta_col = st.columns([1.2, 3.8], vertical_alignment="bottom")
        with sync_col:
            sync_requested = st.button(
                "同步模型",
                key=f"sync_models_{llm_provider}",
                icon=":material/sync:",
                disabled=not supports_model_sync,
                use_container_width=True,
            )
        if sync_requested:
            try:
                synced_models = fetch_openai_compatible_models(
                    api_key,
                    api_base_url or default_base_url,
                )
                synced_at = datetime.now().astimezone().isoformat(timespec="seconds")
                st.session_state[catalog_key] = synced_models
                st.session_state[synced_at_key] = synced_at
                sync_feedback = ("success", f"已同步 {len(synced_models)} 个模型")
            except ModelCatalogError as error:
                sync_feedback = ("error", str(error))

        with sync_meta_col:
            synced_at = st.session_state.get(synced_at_key, "")
            if synced_at:
                st.caption(f"最近同步：{synced_at}")
            elif not MODEL_CATALOG_AVAILABLE:
                st.caption("模型同步组件不可用，请查看服务日志")
            elif supports_model_sync:
                st.caption("尚未同步模型列表")
            else:
                st.caption("此提供商不使用 OpenAI 兼容 /models")
        if sync_feedback:
            feedback_type, feedback_message = sync_feedback
            if feedback_type == "success":
                st.success(feedback_message)
            else:
                st.error(feedback_message)

        available_models = list(st.session_state.get(catalog_key, []))
        model_options = list(available_models)
        if not model_options and configured_model:
            model_options.append(configured_model)
        model_options.append(MANUAL_MODEL_OPTION)

        model_choice_key = f"model_choice_{llm_provider}"
        current_choice = st.session_state.get(model_choice_key)
        if current_choice not in model_options:
            st.session_state[model_choice_key] = (
                configured_model
                if configured_model in model_options
                else MANUAL_MODEL_OPTION
            )
        model_choice = st.selectbox(
            "模型列表",
            model_options,
            key=model_choice_key,
        )
        if model_choice == MANUAL_MODEL_OPTION:
            manual_model_key = f"manual_model_{llm_provider}"
            if manual_model_key not in st.session_state:
                st.session_state[manual_model_key] = configured_model
            model = st.text_input(
                "手动模型 ID",
                placeholder=default_model,
                key=manual_model_key,
            ).strip()
        else:
            model = model_choice

    with mcp_tab:
        if mcp_error:
            st.error(f"{mcp_error}，请检查服务地址后重试。")
        elif mcp_skills:
            st.success(f"Skills Forge 已连接，{len(mcp_skills)} 个技能可用")
            skill_rows = [
                {
                    "技能": skill.get("name", "未命名"),
                    "说明": skill.get("description", "—"),
                }
                for skill in mcp_skills
            ]
            st.dataframe(
                pd.DataFrame(skill_rows),
                use_container_width=True,
                hide_index=True,
                height=min(280, 38 + len(skill_rows) * 36),
                key="mcp_skills_table",
            )
        else:
            st.info("Skills Forge 当前未连接。保存有效配置并重启服务后生效。")
        mcp_enabled = st.toggle(
            "启用远程 Skills Forge",
            value=os.getenv("MCP_ENABLED", "false").lower() == "true",
        )
        mcp_key = st.text_input(
            "Skills Forge API Key",
            type="password",
            value=os.getenv("SKILLS_FORGE_KEY", ""),
            placeholder="sk_live_...",
        )
        mcp_url = st.text_input(
            "Skills Forge URL",
            value=os.getenv("SKILLS_FORGE_URL", ""),
            placeholder="https://api.skillsforge.xyz",
        ).strip()

    with workflow_tab:
        workflow_store = get_workflow_store()
        if st.session_state.pop("reset_workflow_widget_state", False):
            for state_key in list(st.session_state):
                if state_key.startswith(("workflow_enabled_", "workflow_priority_")):
                    del st.session_state[state_key]
        if workflow_store is None:
            st.error("工作流运行时未就绪，请查看服务日志。")
        else:
            workflow_definitions = workflow_store.list_definitions()
            workflow_values = []
            for definition in workflow_definitions:
                with st.expander(
                    definition.name,
                    expanded=True,
                    icon=":material/account_tree:",
                ):
                    st.caption(definition.description)
                    enabled_col, priority_col = st.columns([2, 1])
                    with enabled_col:
                        enabled = st.toggle(
                            "启用",
                            value=definition.enabled,
                            key=(
                                f"workflow_enabled_{definition.id}_"
                                f"{definition.version}"
                            ),
                        )
                    with priority_col:
                        priority = st.number_input(
                            "优先级",
                            min_value=-1000,
                            max_value=1000,
                            value=definition.priority,
                            step=1,
                            key=(
                                f"workflow_priority_{definition.id}_"
                                f"{definition.version}"
                            ),
                        )
                    st.caption(
                        f"版本 {definition.version} · {len(definition.steps)} 个步骤"
                    )
                    workflow_values.append((definition, enabled, int(priority)))

            save_flow_col, reset_flow_col = st.columns(2)
            with save_flow_col:
                save_workflows = st.button(
                    "保存工作流设置",
                    key="save_workflow_settings",
                    type="primary",
                    use_container_width=True,
                )
            with reset_flow_col:
                reset_workflows = st.button(
                    "恢复默认",
                    key="reset_workflow_settings",
                    use_container_width=True,
                )
            if save_workflows:
                try:
                    for definition, enabled, priority in workflow_values:
                        workflow_store.set_override(
                            WorkflowOverride(
                                workflow_id=definition.id,
                                workflow_version=definition.version,
                                enabled=enabled,
                                priority=priority,
                            )
                        )
                    st.success("工作流设置已保存。")
                except Exception as error:
                    logger.exception("保存工作流设置失败")
                    st.error(f"工作流设置保存失败：{error}")
            if reset_workflows:
                try:
                    for definition in workflow_definitions:
                        workflow_store.clear_override(
                            definition.id,
                            version=definition.version,
                        )
                    st.session_state.reset_workflow_widget_state = True
                    st.rerun()
                except Exception as error:
                    logger.exception("恢复默认工作流失败")
                    st.error(f"恢复失败：{error}")

    with knowledge_tab:
        knowledge_store = get_knowledge_store()
        if knowledge_store is None:
            st.error("工作区知识库未就绪，请查看服务日志。")
        else:
            resources = knowledge_store.list_resources(limit=100)
            active_rules = knowledge_store.get_active_rules(limit=100)
            pending_ingestions = knowledge_store.list_ingestion_proposals(
                status="pending",
                limit=100,
            )
            pending_rules = knowledge_store.list_rules(
                status="proposed",
                limit=100,
            )
            st.caption(
                f"资料 {len(resources)} · 已采纳规则 {len(active_rules)} · "
                f"待确认 {len(pending_ingestions) + len(pending_rules)}"
            )
            if resources:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "资料": resource["title"],
                                "类型": resource["resource_type"],
                                "版本": resource["current_version"],
                                "来源": resource["source_type"],
                            }
                            for resource in resources
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                    key="knowledge_resources_table",
                )
            if active_rules:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "已采纳规则": rule["statement"],
                                "范围": rule["scope"],
                            }
                            for rule in active_rules
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                    key="knowledge_rules_table",
                )
            if not resources and not active_rules:
                st.info("当前工作区还没有已确认的资料或规则。")

    with business_tab:
        current_profile = get_current_profile()
        if current_profile is not None:
            identity_defaults = current_profile.identity
            st.caption(f"当前配置版本：v{current_profile.revision}")
        else:
            identity_defaults = AgentIdentity() if AgentIdentity else None

        render_section_heading("Agent 身份", "当前工作区")
        identity_name = st.text_input(
            "名称",
            value=getattr(identity_defaults, "display_name", "ArtPM Agent"),
        ).strip()
        identity_role = st.text_input(
            "角色",
            value=getattr(identity_defaults, "role", "游戏美术项目管理智能体"),
        ).strip()
        identity_domain = st.text_input(
            "业务领域",
            value=getattr(identity_defaults, "domain", "游戏美术资产项目管理"),
        ).strip()
        style_options = ["concise", "balanced", "detailed"]
        style_labels = {
            "concise": "简洁",
            "balanced": "平衡",
            "detailed": "详细",
        }
        configured_style = getattr(identity_defaults, "response_style", "balanced")
        response_style = st.selectbox(
            "回答风格",
            style_options,
            index=(
                style_options.index(configured_style)
                if configured_style in style_options
                else 1
            ),
            format_func=lambda value: style_labels[value],
        )
        identity_guidance = st.text_area(
            "业务指引",
            value=getattr(
                identity_defaults,
                "guidance",
                "优先给出可核验的结论、假设和下一步动作。",
            ),
            max_chars=1000,
        ).strip()

    render_section_heading("保存", "保存后立即应用")
    if st.button("保存更改", type="primary", key="save_settings"):
        if not model.strip():
            st.error("请从模型列表选择模型，或填写手动模型 ID。")
            return
        if not identity_name or not identity_role or not identity_domain:
            st.error("Agent 名称、角色和业务领域不能为空。")
            return
        profile_patch = None
        if current_profile is not None and AgentProfilePatch is not None:
            identity_changes = {}
            identity_values = {
                "display_name": identity_name,
                "role": identity_role,
                "domain": identity_domain,
                "response_style": response_style,
                "guidance": identity_guidance,
            }
            for key, value in identity_values.items():
                if value != getattr(current_profile.identity, key):
                    identity_changes[key] = value
            if identity_changes:
                profile_patch = AgentProfilePatch(
                    identity=AgentIdentityPatch(**identity_changes),
                )
        settings = {
            "provider": llm_provider,
            "model": model.strip(),
            "api_key": api_key.strip(),
            "api_base_url": api_base_url,
            "available_models": available_models,
            "models_synced_at": st.session_state.get(synced_at_key, ""),
            "mcp_enabled": mcp_enabled,
            "mcp_key": mcp_key.strip(),
            "mcp_url": mcp_url,
        }
        try:
            persist_settings(settings)
        except Exception as error:
            logger.exception("保存配置失败")
            st.error(f"保存失败：{error}。请检查 .env 文件权限后重试。")
            return

        applied_profile = current_profile
        if profile_patch is not None:
            try:
                profile_store = get_profile_store()
                active_conversation_id = st.session_state.get(
                    "active_conversation_id"
                )
                change_id = uuid4().hex
                proposal = profile_store.propose_change(
                    active_conversation_id,
                    profile_patch,
                    turn_id=change_id,
                    summary="通过设置页更新 Agent 身份与业务规则",
                    idempotency_key=f"settings-{change_id}",
                )
                applied_profile = profile_store.confirm_change(
                    proposal.id,
                    actor="本地用户（设置页）",
                )
            except Exception as error:
                logger.exception("保存 Agent Profile 失败")
                st.error(f"模型配置已保存，但 Agent Profile 保存失败：{error}")
                return

        try:
            refreshed_agent = ArtPMAgent(Config())
            st.session_state.agent = refreshed_agent
            st.session_state.db = refreshed_agent.database
            profile_version = (
                f"，Agent 配置 v{applied_profile.revision}"
                if applied_profile is not None
                else ""
            )
            st.success(
                f"配置已保存并应用，当前默认模型：{model.strip()}"
                f"{profile_version}"
            )
        except Exception:
            logger.exception("配置已保存，但当前 Agent 刷新失败")
            st.warning("配置已保存；当前会话刷新失败，重启服务后生效。")


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
