# ruff: noqa: E402 - bootstrap adjusts sys.path before package imports
"""
共享层：导入、可用性标志、常量与全部 UI 助手函数。
由 app.py 启动器与 pages/* 通过 `from ui_helpers import *` 复用，
保证 set_page_config / 样式只在启动器执行一次。
"""
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中（任何入口加载本模块时都能找到 artpm_agent 包）
_project_root = Path(__file__).parent.parent.resolve()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from datetime import datetime
from contextlib import nullcontext
from html import escape
import json
import os
import time
from pathlib import Path
import re
from uuid import uuid4
import pandas as pd
import streamlit as st
from artpm_agent.utils.logger import setup_logging, get_logger
from artpm_agent.utils.chat_intent import chat_processing_label, is_local_fast_intent
from artpm_agent.utils.chat_attachments import (
    ChatAttachmentStore,
    normalize_chat_submission,
    select_conversation_attachments,
)
logger = get_logger(__name__)
try:
    from artpm_agent.agent import ArtPMAgent
    from artpm_agent.config import Config
    from artpm_agent.database.models import DatabaseManager
    AVAILABLE = True
    logger.info("核心模块导入成功")
except Exception as e:
    logger.error(f"核心模块导入失败: {e}")
    print(f"Import error: {e}")
    AVAILABLE = False
CONVERSATION_STORE_AVAILABLE = False
try:
    from artpm_agent.memory import WorkspaceKnowledgeStore, create_embedding_provider
    from artpm_agent.memory.conversation_store import ConversationStore

    CONVERSATION_STORE_AVAILABLE = True
except Exception as error:
    ConversationStore = None
    WorkspaceKnowledgeStore = None
    create_embedding_provider = None
    logger.error(f"会话存储模块导入失败: {error}")
WORKFLOW_RUNTIME_AVAILABLE = False
try:
    from artpm_agent.workflows import (
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
    from artpm_agent.profiles import (
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
    from artpm_agent.artifacts import ArtifactCoordinator, WorkspaceArtifactGenerator

    ARTIFACT_RUNTIME_AVAILABLE = True
except Exception as error:
    ArtifactCoordinator = None
    WorkspaceArtifactGenerator = None
    logger.error(f"文件生成模块导入失败: {error}")
MODEL_CATALOG_AVAILABLE = False
MODEL_CATALOG_IMPORT_ERROR = None
try:
    from artpm_agent.utils.model_catalog import (
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
        return title or getattr(ConversationStore, "DEFAULT_TITLE", "新对话")
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


class _UnavailableArtifactPlanner:
    """Small local stand-in so deterministic artifact paths still work offline."""

    def chat(self, *_args, **_kwargs):
        raise RuntimeError("模型服务暂时不可用")


def get_artifact_coordinator():
    generator = get_artifact_generator()
    agent = st.session_state.get("agent")
    llm_client = getattr(agent, "llm_client", None)
    if generator is None or ArtifactCoordinator is None:
        return None
    if not callable(getattr(llm_client, "chat", None)):
        llm_client = st.session_state.setdefault(
            "artifact_local_planner",
            _UnavailableArtifactPlanner(),
        )
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
        rules = store.get_active_rules(limit=8)
        try:
            resources = store.search(
                str(prompt),
                limit=5,
                include_rules=False,
                max_text_chars=1200,
                use_confidence=True,
                confidence_floor=0.05,
            )
        except TypeError:
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
            excerpt = str(
                resource.get("text") or resource.get("searchable_text") or ""
            ).strip()
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
                f"{name}：附件暂未提取到可检索正文；对话中会自动尝试多模态解析，暂不写入知识库"
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
                vector_root = Path(
                    Config().get("database.vector_db_path", "./data/vector_store")
                )
                embedding_provider = create_embedding_provider(
                    Config().get("memory", {})
                )
                st.session_state.knowledge_store = WorkspaceKnowledgeStore(
                    conversation_store.db_path,
                    vector_store_path=vector_root / "workspace_knowledge",
                    embedding_provider=embedding_provider,
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
        # 仅在切换会话后首次渲染时从 DB 载入，避免每次 rerun 都打一次库。
        if st.session_state.get("messages_loaded_for") != conversation["id"]:
            load_active_messages()
            st.session_state.messages_loaded_for = conversation["id"]
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
            '<div class="sidebar-section-label">会话</div>',
            unsafe_allow_html=True,
        )
        if st.button(
            "新建会话",
            key="new_conversation",
            icon=":material/add_circle_outline:",
            use_container_width=True,
            disabled=request_pending,
        ):
            conversation = store.create_conversation()
            activate_conversation(conversation["id"])
            st.rerun()

        # 30 秒内的重复 rerun 复用缓存，避免高频刷新反复打库。
        _conv_cache = st.session_state.get("_conv_list_cache")
        if not _conv_cache or (time.monotonic() - _conv_cache["ts"]) > 30:
            conversations = store.list_conversations(limit=20)
            st.session_state._conv_list_cache = {
                "ts": time.monotonic(),
                "data": conversations,
            }
        else:
            conversations = _conv_cache["data"]
        with st.container(key="conversation_list", height=220, border=False):
            for conversation in conversations:
                conversation_id = conversation["id"]
                is_active = conversation_id == active_id
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
                    st.rerun()
def render_sidebar():
    """渲染侧边栏：品牌区、会话列表和导航切换。"""
    with st.sidebar:
        # ── 品牌区（渐变菱形 Logo + 品牌名）──
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

        # ── 底部导航模式切换（对话 / 设置）──
        with st.container(key="sidebar_modes"):
            chat_col, settings_col = st.columns(2)
            with chat_col:
                if st.button(
                    "对话",
                    key="nav_chat",
                    icon=":material/chat:",
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


def render_global_navigation():
    """Render compact navigation that remains reachable when the sidebar is hidden."""
    with st.container(key="global_modes"):
        chat_col, settings_col = st.columns(2)
        with chat_col:
            if st.button(
                "对话",
                key="global_nav_chat",
                icon=":material/chat:",
                type="primary" if st.session_state.view == "对话" else "secondary",
                use_container_width=True,
            ):
                if st.session_state.view != "对话":
                    st.session_state.view = "对话"
                    st.rerun()
        with settings_col:
            if st.button(
                "设置",
                key="global_nav_settings",
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
def stream_agent_response(agent, prompt, context):
    """Render incremental model output and return the complete persisted text."""
    stream_chat = getattr(agent, "stream_chat", None)
    if not callable(stream_chat):
        return normalize_agent_response(agent.chat(prompt, context=context))

    received = []

    def _chunks():
        for chunk in stream_chat(prompt, context=context):
            if not isinstance(chunk, str) or not chunk:
                continue
            received.append(chunk)
            yield chunk

    rendered = st.write_stream(_chunks())
    if isinstance(rendered, str) and rendered.strip():
        return rendered.strip()
    return normalize_agent_response("".join(received))
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
def _response_model_id(agent):
    """Prefer the model that actually completed this response over the default."""
    if hasattr(agent, "last_response_model"):
        model_id = getattr(agent, "last_response_model")
        return model_id.strip() if isinstance(model_id, str) and model_id.strip() else None
    return _current_model_id(agent)
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
        for marker in (
            "resourceexhausted",
            "resource exhausted",
            "rate limit",
            "too many requests",
            "worker local total request limit",
            "overloaded",
            "服务繁忙",
        )
    ):
        return (
            f"模型 {display_model} 当前服务繁忙，未收到回答。"
            "请稍后重新生成；本地功能和已配置的工作流仍可继续使用。"
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
@st.cache_data(show_spinner=False)
def _read_artifact_bytes(root, stored_path, max_size):
    """读取生成文件字节（带路径与大小校验），按路径缓存避免每帧重读全部附件。"""
    path = (root / stored_path).resolve()
    path.relative_to(root)
    if path.parent != root or not path.is_file():
        return None
    if path.stat().st_size > max_size:
        return None
    return path.read_bytes()


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
            data = _read_artifact_bytes(
                generator.root, stored_path, generator.max_file_size
            )
            if data is None:
                continue
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
        preview_markdown = str(artifact.get("preview_markdown") or "").strip()
        if not preview_markdown:
            try:
                preview = generator.preview_artifact(stored_path, max_chars=4000)
                preview_markdown = str(preview.get("preview_markdown") or "").strip()
            except (OSError, ValueError, RuntimeError):
                preview_markdown = ""
        if preview_markdown:
            with st.expander(f"预览 {name}", expanded=False):
                st.markdown(preview_markdown)

        export_formats = artifact.get("export_formats")
        if not isinstance(export_formats, list):
            export_formats = generator.available_export_formats(
                str(artifact.get("format") or "")
            )
        export_formats = [
            str(item).lower()
            for item in export_formats
            if str(item).lower() in {"csv", "docx", "md", "txt", "xlsx"}
            and str(item).lower() != str(artifact.get("format") or "").lower()
        ][:4]
        if export_formats:
            export_columns = st.columns(len(export_formats), gap="small")
            for export_index, target_format in enumerate(export_formats):
                with export_columns[export_index]:
                    try:
                        exported = generator.export_artifact_bytes(
                            stored_path,
                            target_format,
                        )
                    except (OSError, ValueError, RuntimeError):
                        continue
                    st.download_button(
                        f"保存为 {target_format.upper()}",
                        data=exported["data"],
                        file_name=exported["filename"],
                        mime=exported["mime_type"],
                        key=(
                            f"artifact_export_{message_key}_{index}_"
                            f"{artifact.get('id', '')}_{target_format}"
                        ),
                        icon=":material/save_alt:",
                        use_container_width=True,
                    )

        coordinator = get_artifact_coordinator()
        if coordinator is None:
            continue
        source_format = str(artifact.get("format") or "").lower()
        if source_format not in {"xlsx", "docx"}:
            continue
        edit_state_key = (
            f"artifact_edit_result_{message_key}_{index}_{artifact.get('id', '')}"
        )
        edit_result = st.session_state.get(edit_state_key)
        if isinstance(edit_result, dict):
            if edit_result.get("artifact"):
                edited = edit_result["artifact"]
                st.success(edit_result.get("message") or "已生成编辑版本。")
                try:
                    edited_data = _read_artifact_bytes(
                        generator.root,
                        edited["stored_path"],
                        generator.max_file_size,
                    )
                except (KeyError, OSError, ValueError):
                    edited_data = None
                if edited_data is not None:
                    st.download_button(
                        f"下载 {edited.get('name', '编辑版本')}",
                        data=edited_data,
                        file_name=edited.get("name", "edited-artifact"),
                        mime=edited.get("mime_type") or "application/octet-stream",
                        key=f"artifact_edit_download_{message_key}_{index}",
                        icon=":material/download:",
                    )
            elif edit_result.get("message"):
                st.warning(edit_result["message"])

        with st.expander("一句话编辑", expanded=False):
            instruction = st.text_input(
                "编辑要求",
                key=f"artifact_edit_input_{message_key}_{index}_{artifact.get('id', '')}",
                placeholder="例如：把金额列改成美元，并新增风险说明",
            )
            target_options = [source_format]
            if source_format == "xlsx":
                target_options.append("docx")
            target_format = st.selectbox(
                "保存格式",
                target_options,
                key=f"artifact_edit_format_{message_key}_{index}_{artifact.get('id', '')}",
            )
            if st.button(
                "生成新版本",
                key=f"artifact_edit_submit_{message_key}_{index}_{artifact.get('id', '')}",
                icon=":material/auto_fix_high:",
                use_container_width=True,
            ):
                outcome = coordinator.edit_artifact(
                    stored_path,
                    instruction,
                    target_format=target_format,
                )
                st.session_state[edit_state_key] = outcome.to_dict()
                st.rerun()

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
def _cached_workflow_preview(run_id):
    """缓存待审批工作流的预览渲染结果，避免每条待审批运行每帧重算。"""
    coordinator = get_workflow_coordinator()
    agent = st.session_state.get("agent")
    if coordinator is None:
        return ""
    return format_workflow_result(agent, coordinator.engine.result(run_id))


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
        preview = _cached_workflow_preview(run.id)
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
