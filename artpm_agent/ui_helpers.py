# ruff: noqa: E402 - bootstrap adjusts sys.path before package imports
"""
共享层：UI 助手函数。
由 app.py 启动器与 pages/* 通过 `from ui_helpers import *` 复用，
保证 set_page_config / 样式只在启动器执行一次。

共享状态、常量、getter 已提取到 artpm_agent.ui_state。
"""
from datetime import datetime
from contextlib import nullcontext
from collections.abc import Mapping
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
    chat_submission_audio,
    normalize_chat_submission,
    select_conversation_attachments,
)
from artpm_agent.tenancy import TenantContext
from artpm_agent.ui_feedback import (
    build_error_info,
    probe_backend_health,
    render_action_callback,
    render_backend_link_status,
    render_error_callback,
)
from artpm_agent.ui_state import (  # noqa: F401 — re-exported for wildcard consumers
    _NAV_EVENT_KEY,
    _NAV_OPTIONS,
    _NAV_WIDGET_KEY,
    _queue_sidebar_navigation,
    _UnavailableArtifactPlanner,
    AgentIdentity,
    AgentIdentityPatch,
    AgentProfilePatch,
    AgentProfileStore,
    ArtifactCoordinator,
    ARTIFACT_RUNTIME_AVAILABLE,
    AVAILABLE,
    CONVERSATION_STORE_AVAILABLE,
    ConversationStore,
    create_embedding_provider,
    EMPTY_STATS,
    fetch_openai_compatible_models,
    format_profile_changes,
    format_workflow_result,
    get_artifact_coordinator,
    get_artifact_generator,
    get_chat_attachment_store,
    get_conversation_store,
    get_session_store,
    get_current_profile,
    get_knowledge_store,
    get_wiki_store,
    get_permission_store,
    get_profile_store,
    get_workflow_store,
    MODEL_CATALOG_AVAILABLE,
    MODEL_CATALOG_IMPORT_ERROR,
    ModelCatalogError,
    parse_cached_models,
    parse_profile_change,
    PermissionConflictError,
    PERMISSION_RUNTIME_AVAILABLE,
    PermissionStore,
    ProfileChangeParseError,
    PROFILE_RUNTIME_AVAILABLE,
    QuotePolicy,
    record_runtime_init_failure,
    redact_sensitive,
    serialize_cached_models,
    WorkflowCoordinator,
    WorkflowOverride,
    WorkflowStore,
    WORKFLOW_RUNTIME_AVAILABLE,
    WorkspaceArtifactGenerator,
    WorkspaceKnowledgeStore,
    WorkspaceWikiStore,
)

logger = get_logger(__name__)

# Pure helpers are implemented in a dependency-free module. The assignments
# preserve the historical import surface while allowing focused unit tests.
from artpm_agent import ui_formatters as _ui_formatters  # noqa: E402

try:
    from artpm_agent.agent import ArtPMAgent
    from artpm_agent.config import Config
    from artpm_agent.database.models import DatabaseManager
    logger.info("核心模块导入成功")
except Exception as e:
    logger.error(f"核心模块导入失败: {e}")
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
        width="stretch",
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
# Shared UI state getters, constants, and availability flags now live in
# artpm_agent.ui_state and are re-exported through the import above.
def build_knowledge_context(prompt, *, max_chars=6000):
    """Build bounded, confirmed Workspace knowledge for one model request."""
    store = get_knowledge_store()
    if store is None or not str(prompt).strip():
        return ""
    try:
        tenant_context = _trusted_knowledge_context()
        scope = {
            "tenant_id": tenant_context.tenant_id,
            "workspace_id": tenant_context.workspace_id,
        }
        rules = store.get_active_rules(limit=8, **scope)
        try:
            resources = store.search(
                str(prompt),
                limit=5,
                **scope,
                include_rules=False,
                max_text_chars=1200,
                use_confidence=True,
                confidence_floor=0.05,
            )
        except TypeError:
            resources = store.search(
                str(prompt),
                limit=5,
                **scope,
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
    """Use the harness ingestion contract for every UI and agent path."""
    from artpm_agent.harness.knowledge_handler import (
        _build_knowledge_ingestion_resources,
    )

    return _build_knowledge_ingestion_resources(
        agent,
        attachments,
        file_paths,
        prompt,
    )
def get_workflow_coordinator():
    """Return a coordinator bound to the current Agent, if it supports Skills."""
    if not WORKFLOW_RUNTIME_AVAILABLE:
        return None
    store = get_workflow_store()
    agent = st.session_state.get("agent")
    if store is None or agent is None or not hasattr(agent, "router"):
        return None
    tenant_context = st.session_state.get("tenant_context") or TenantContext.local()
    if not isinstance(tenant_context, TenantContext):
        return None
    workspace_id = tenant_context.require_workspace()
    profile = get_current_profile()
    profile_id = str(getattr(profile, "profile_id", None) or "local-default")
    try:
        store.ensure_builtins(workspace_id=workspace_id, profile_id=profile_id)
    except Exception:
        logger.exception("工作流内置定义初始化失败")
        return None
    coordinator = st.session_state.get("workflow_coordinator")
    if (
        coordinator is None
        or getattr(coordinator, "agent", None) is not agent
        or getattr(coordinator, "workspace_id", None) != workspace_id
        or getattr(coordinator, "profile_id", None) != profile_id
    ):
        try:
            coordinator = WorkflowCoordinator(
                store,
                agent,
                workspace_id=workspace_id,
                profile_id=profile_id,
            )
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
    if store is None:
        logger.error("activate_conversation 失败: conversation_store 未初始化")
        render_error_callback(
            {
                "message": "会话系统未就绪，请刷新页面重试",
                "suggestions": ["刷新页面后重试", "若问题持续，请检查数据库状态"],
                "severity": "error",
                "error_id": "conversation-store-unavailable",
            },
            key="conversation_store_unavailable",
            retry=False,
        )
        return
    try:
        conversation = store.get_conversation(conversation_id)
    except Exception as exc:
        logger.error("activate_conversation 查询失败: %s", exc)
        render_error_callback(
            build_error_info(exc, context={"operation": "conversation_switch"}),
            key=f"conversation_switch_error_{conversation_id}",
            retry=False,
        )
        return
    if conversation is None:
        logger.error("activate_conversation: 会话不存在 %s", conversation_id)
        render_error_callback(
            {
                "message": "目标会话不存在，可能已被删除",
                "suggestions": ["刷新会话列表", "选择其他会话继续工作"],
                "severity": "warning",
                "error_id": "conversation-not-found",
            },
            key=f"conversation_not_found_{conversation_id}",
            retry=False,
        )
        return
    st.session_state.active_conversation_id = conversation_id
    st.session_state.view = "对话"
    st.session_state.pop("pending_prompt", None)
    # 离开编辑模式（如有），避免新会话残留旧编辑状态
    st.session_state.pop("edit_mode", None)
    load_active_messages()
    # 同步 messages_loaded_for，避免 _init_session_state 重复加载
    st.session_state.messages_loaded_for = conversation_id
def delete_conversation_and_activate_next(store, conversation_id):
    """Delete one conversation and keep the workspace on a valid active thread."""
    active_id = st.session_state.get("active_conversation_id")
    deleted = store.delete_conversation(conversation_id)
    if not deleted:
        raise KeyError(f"Unknown conversation: {conversation_id}")

    # Permission grants are conversation-scoped and must not survive deletion
    # of the conversation they were issued for.
    grants = st.session_state.get("conversation_permission_grants")
    if isinstance(grants, dict):
        grants.pop(str(conversation_id), None)

    # Database deletion is authoritative. Attachment cleanup is best-effort so
    # an orphaned file never prevents the conversation from disappearing.
    attachment_store = get_chat_attachment_store()
    if attachment_store is not None:
        try:
            attachment_store.remove_conversation(conversation_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Conversation attachment cleanup failed: %s", exc)

    st.session_state.pop("pending_conversation_delete", None)
    st.session_state.pop("_conv_list_cache", None)

    current = store.get_conversation(active_id) if active_id else None
    if active_id == conversation_id or current is None:
        remaining = store.list_conversations(limit=1)
        next_conversation = (
            remaining[0] if remaining else store.create_conversation()
        )
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
# _RUNTIME_COMPONENT_LABELS and _record_runtime_init_failure are now in
# artpm_agent.ui_state and imported as record_runtime_init_failure.


def render_runtime_init_status() -> None:
    """Surface storage/runtime degradation instead of silently losing state."""
    failures = st.session_state.get("runtime_init_errors", {})
    if not isinstance(failures, Mapping) or not failures:
        return
    labels = [str(label) for label in failures.values() if str(label).strip()]
    critical = any(key in failures for key in ("conversation_store", "agent", "database"))
    suggestions = ["检查数据目录的读写权限和可用空间后重启服务"]
    if "conversation_store" in failures:
        suggestions.insert(0, "当前会话可能无法持久保存，请先不要关闭页面")
    if "permission_store" in failures:
        suggestions.append("审批记录恢复前不要执行需要授权的操作")
    render_error_callback(
        {
            "message": f"部分服务未就绪：{'、'.join(labels)}。",
            "suggestions": suggestions,
            "severity": "error" if critical else "warning",
            "error_id": "runtime-init-" + "-".join(sorted(failures)),
        },
        key="runtime_init_status",
        retry=False,
    )


def init_session():
    """初始化应用状态，并从持久化存储恢复当前会话。"""
    if "runtime_init_errors" not in st.session_state:
        st.session_state.runtime_init_errors = {}
    if "tenant_context" not in st.session_state:
        st.session_state.tenant_context = TenantContext.local()
    elif not isinstance(st.session_state.tenant_context, TenantContext):
        raise RuntimeError("tenant_context must be created by the application host")
    if "view" not in st.session_state:
        st.session_state.view = "对话"
    elif st.session_state.view not in _NAV_OPTIONS:
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
            except Exception as error:
                record_runtime_init_failure("conversation_store", error)

    if "permission_store" not in st.session_state:
        st.session_state.permission_store = None
        conversation_store = get_conversation_store()
        if PERMISSION_RUNTIME_AVAILABLE and conversation_store is not None:
            try:
                st.session_state.permission_store = PermissionStore(
                    conversation_store.db_path
                )
            except Exception as error:
                record_runtime_init_failure("permission_store", error)

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
            except Exception as error:
                record_runtime_init_failure("chat_attachment_store", error)

    if "artifact_generator" not in st.session_state:
        st.session_state.artifact_generator = None
        if ARTIFACT_RUNTIME_AVAILABLE and get_conversation_store() is not None:
            try:
                conversation_path = Path(get_conversation_store().db_path)
                artifact_root = conversation_path.parent / "artifacts" / "local-default"
                st.session_state.artifact_generator = WorkspaceArtifactGenerator(
                    artifact_root
                )
            except Exception as error:
                record_runtime_init_failure("artifact_generator", error)

    if "workflow_store" not in st.session_state:
        st.session_state.workflow_store = None
        if WORKFLOW_RUNTIME_AVAILABLE and get_conversation_store() is not None:
            try:
                st.session_state.workflow_store = WorkflowStore(
                    get_conversation_store().db_path
                )
            except Exception as error:
                record_runtime_init_failure("workflow_store", error)

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
            except Exception as error:
                record_runtime_init_failure("profile_store", error)

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
            except Exception as error:
                record_runtime_init_failure("knowledge_store", error)

    if "wiki_store" not in st.session_state:
        st.session_state.wiki_store = None
        knowledge_store = get_knowledge_store()
        if WorkspaceWikiStore is not None and knowledge_store is not None:
            try:
                wiki_store = WorkspaceWikiStore(knowledge_store)
                tenant_context = st.session_state.get("tenant_context")
                workspace_id = str(
                    getattr(
                        tenant_context,
                        "workspace_id",
                        WorkspaceKnowledgeStore.DEFAULT_WORKSPACE_ID,
                    )
                )
                wiki_store.reconcile(workspace_id=workspace_id)
                st.session_state.wiki_store = wiki_store
            except Exception as error:
                record_runtime_init_failure("wiki_store", error)

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
            record_runtime_init_failure("agent", e)

    if "db" not in st.session_state and AVAILABLE:
        try:
            agent = st.session_state.get("agent")
            st.session_state.db = agent.database if agent is not None else DatabaseManager()
            logger.info("数据库管理器初始化成功")
        except Exception as e:
            st.session_state.db = None
            record_runtime_init_failure("database", e)
def render_conversation_sidebar():
    store = get_conversation_store()
    if store is None:
        return

    active_id = st.session_state.get("active_conversation_id")
    request_pending = bool(st.session_state.get("pending_prompt"))
    pending_delete_id = st.session_state.get("pending_conversation_delete")
    with st.container(key="conversation_panel"):
        if st.button(
            "新建会话",
            key="new_conversation",
            icon=":material/add_circle_outline:",
            width="stretch",
            disabled=request_pending or bool(pending_delete_id),
        ):
            try:
                conversation = store.create_conversation()
                activate_conversation(conversation["id"])
                st.session_state.pop("_conv_list_cache", None)
                st.rerun()
            except KeyError as exc:
                logger.error("新建会话失败(workspace/DB): %s", exc)
                render_error_callback(
                    build_error_info(exc, context={"operation": "conversation_create"}),
                    key="conversation_create_key_error",
                    retry=False,
                )
            except Exception as exc:
                logger.exception("新建会话异常")
                render_error_callback(
                    build_error_info(exc, context={"operation": "conversation_create"}),
                    key="conversation_create_error",
                    retry=False,
                )

        st.markdown(
            '<div class="sidebar-section-label">最近会话</div>',
            unsafe_allow_html=True,
        )

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
        list_height = min(220, max(78, len(conversations) * 42))
        if pending_delete_id:
            list_height = max(list_height, 184)
        with st.container(key="conversation_list", height=list_height, border=False):
            for conversation in conversations:
                conversation_id = conversation["id"]
                is_active = conversation_id == active_id
                row_title, row_delete = st.columns(
                    [0.84, 0.16], gap="small", vertical_alignment="center"
                )
                with row_title:
                    if st.button(
                        conversation["title"],
                        key=f"conversation_{conversation_id}",
                        type="primary" if is_active else "secondary",
                        help=conversation["title"],
                        width="stretch",
                        disabled=(
                            request_pending
                            or bool(pending_delete_id)
                            or (is_active and st.session_state.view == "对话")
                        ),
                    ):
                        activate_conversation(conversation_id)
                        st.rerun()
                with row_delete:
                    if st.button(
                        "",
                        key=f"quick_delete_conversation_{conversation_id}",
                        icon=":material/delete_outline:",
                        help="删除会话",
                        width="stretch",
                        disabled=request_pending or bool(pending_delete_id),
                    ):
                        st.session_state.pending_conversation_delete = conversation_id
                        st.rerun()

                if pending_delete_id == conversation_id:
                    st.warning("确认删除这个会话？消息、附件和运行记录会一并删除，且无法撤销。")
                    confirm_col, cancel_col = st.columns(2)
                    with confirm_col:
                        if st.button(
                            "确认删除",
                            key=f"confirm_quick_delete_{conversation_id}",
                            type="primary",
                            width="stretch",
                        ):
                            try:
                                delete_conversation_and_activate_next(
                                    store, conversation_id
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.exception("Conversation deletion failed")
                                render_error_callback(
                                    build_error_info(
                                        exc,
                                        context={"operation": "conversation_delete"},
                                    ),
                                    key=f"conversation_delete_error_{conversation_id}",
                                    retry=False,
                                )
                            else:
                                st.rerun()
                    with cancel_col:
                        if st.button(
                            "取消",
                            key=f"cancel_quick_delete_{conversation_id}",
                            width="stretch",
                        ):
                            st.session_state.pop("pending_conversation_delete", None)
                            st.rerun()
def _render_backend_link_status():
    """Show a cached, non-blocking API gateway link indicator."""
    state = st.session_state.get("_backend_link_status")
    now = time.monotonic()
    if not state or now - float(state.get("checked_at", 0)) > 15:
        status = probe_backend_health()
        status["checked_at"] = now
        st.session_state._backend_link_status = status
    else:
        status = state
    render_backend_link_status(status)
def render_sidebar():
    """渲染侧边栏：品牌区、会话列表和导航切换。"""
    with st.sidebar:
        # ── 品牌区（渐变菱形 Logo + 品牌名）──
        st.markdown(
            """
            <div class="sidebar-brand">
                <span class="brand-mark" aria-hidden="true"></span>
                <span class="brand-copy">
                    <strong class="brand-name">ArtPM Agent</strong>
                    <small class="brand-tagline">项目协作工作台</small>
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        render_conversation_sidebar()

        # ── 导航模式切换（对话 / 设置 / 可观测）──
        _current_view = (
            st.session_state.view
            if st.session_state.view in _NAV_OPTIONS
            else _NAV_OPTIONS[0]
        )

        # Keyed widgets can retain a stale browser value after reconnect/restart.
        # Only the on_change callback is allowed to change the canonical route;
        # unrelated reruns always realign the widget to the current view.
        nav_event = st.session_state.pop(_NAV_EVENT_KEY, None)
        if nav_event in _NAV_OPTIONS:
            _current_view = nav_event
            st.session_state.view = nav_event
        if st.session_state.get(_NAV_WIDGET_KEY) != _current_view:
            st.session_state[_NAV_WIDGET_KEY] = _current_view

        with st.container(key="sidebar_footer"):
            st.markdown(
                '<div class="sidebar-section-label">工作区</div>',
                unsafe_allow_html=True,
            )
            st.pills(
                "导航",
                _NAV_OPTIONS,
                selection_mode="single",
                key=_NAV_WIDGET_KEY,
                on_change=_queue_sidebar_navigation,
                label_visibility="collapsed",
            )
            _render_backend_link_status()


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
        source = iter(stream_chat(prompt, context=context))
        try:
            for chunk in source:
                if not isinstance(chunk, str) or not chunk:
                    continue
                received.append(chunk)
                yield chunk
        finally:
            # A Streamlit rerun can detach the rendering window while the
            # provider stream is still open. Explicitly close it so sockets,
            # callbacks and AgentRuntime state settle immediately.
            close = getattr(source, "close", None)
            if callable(close):
                close()

    chunks = _chunks()
    try:
        rendered = st.write_stream(chunks)
    finally:
        chunks.close()
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

    # 获取可用的候选模型列表（用于错误提示）
    def get_available_models_hint() -> str:
        """生成候选模型提示文本。"""
        try:
            import json
            import os
            available = json.loads(os.getenv("LLM_AVAILABLE_MODELS", "[]"))
            if available and isinstance(available, list):
                models_str = "、".join(available[:3])  # 最多显示3个
                if len(available) > 3:
                    models_str += f" 等 {len(available)} 个模型"
                return f"\n\n💡 你可以尝试切换到其他模型：{models_str}"
            return ""
        except Exception:
            return ""

    available_hint = get_available_models_hint()

    if "模型服务未返回有效回答" in signal_text:
        return f"❌ 未收到有效回答。请重新生成，或检查模型连接。{available_hint}"

    # 认证错误
    if any(
        marker in signal_text
        for marker in (
            "invalid api key",
            "authentication",
            "unauthorized",
            "401",
            "403",
            "forbidden",
        )
    ):
        return (
            f"❌ 模型 {display_model} 认证失败。\n\n"
            "**可能原因：**\n"
            "- API Key 无效或已过期\n"
            "- API Base URL 配置错误\n"
            "- 该模型不可用于当前账户\n\n"
            "**解决方案：**\n"
            "1. 检查设置页的 API Key 和 API Base URL\n"
            "2. 确认 API Key 对应的服务支持该模型\n"
            "3. 尝试切换到其他模型（对话页右上角选择器）"
            f"{available_hint}"
        )

    # 超时错误
    if any(marker in signal_text for marker in ("timeout", "timed out", "超时")):
        return (
            f"⏱️ 模型 {display_model} 响应超时。\n\n"
            "**可能原因：**\n"
            "- 网络不稳定或 API 服务繁忙\n"
            "- 该模型响应速度较慢\n\n"
            "**解决方案：**\n"
            "1. 点击「重新生成」按钮重试\n"
            "2. 尝试切换到响应更快的模型\n"
            "3. 检查网络连接和 API Base URL"
            f"{available_hint}"
        )

    # 服务繁忙/限流
    if any(
        marker in signal_text
        for marker in (
            "resourceexhausted",
            "resource exhausted",
            "rate limit",
            "too many requests",
            "429",
            "worker local total request limit",
            "overloaded",
            "服务繁忙",
            "capacity",
        )
    ):
        return (
            f"🚦 模型 {display_model} 当前服务繁忙。\n\n"
            "**可能原因：**\n"
            "- API 速率限制（请求过于频繁）\n"
            "- 服务负载过高\n"
            "- 账户配额不足\n\n"
            "**解决方案：**\n"
            "1. 稍等片刻后重试\n"
            "2. 切换到其他可用模型\n"
            "3. 检查账户配额和使用限制"
            f"{available_hint}"
        )

    # 连接失败
    if any(
        marker in signal_text
        for marker in ("connection", "connecterror", "连接失败", "无法连接", "reset")
    ):
        return (
            f"🔌 模型 {display_model} 连接失败。\n\n"
            "**可能原因：**\n"
            "- API Base URL 错误或无法访问\n"
            "- 网络连接问题\n"
            "- API 服务暂时不可用\n\n"
            "**解决方案：**\n"
            "1. 检查设置页的 API Base URL 是否正确\n"
            "2. 确认网络连接正常\n"
            "3. 尝试访问 API Base URL（浏览器测试）\n"
            "4. 切换到其他可用模型"
            f"{available_hint}"
        )

    # 模型不存在/不支持
    if any(
        marker in signal_text
        for marker in (
            "model not found",
            "unsupported model",
            "404",
            "不支持",
            "not supported",
        )
    ):
        return (
            f"❓ 模型 {display_model} 不存在或不支持。\n\n"
            "**可能原因：**\n"
            "- 模型 ID 拼写错误\n"
            "- 该 API 服务不提供此模型\n"
            "- 模型已下线或更名\n\n"
            "**解决方案：**\n"
            "1. 点击「同步模型」按钮获取可用模型列表\n"
            "2. 切换到其他可用模型\n"
            "3. 检查 API 文档确认模型 ID"
            f"{available_hint}"
        )

    # 通用错误
    return (
        f"❌ 模型请求失败。\n\n"
        "**解决方案：**\n"
        "1. 点击「重新生成」按钮重试\n"
        "2. 检查设置页的模型配置\n"
        "3. 尝试切换到其他可用模型\n"
        "4. 查看应用日志获取详细错误信息"
        f"{available_hint}"
    )
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


def _fmt_size(num_bytes):
    """把字节数格式化为人类可读的字符串（B / KB / MB）。"""
    if not isinstance(num_bytes, int) or num_bytes <= 0:
        return ""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _artifact_subtitle(artifact):
    """生成卡片头部右侧的元信息：行/列、段落数、文件大小。"""
    fmt = str(artifact.get("format") or "").lower()
    parts = []
    if fmt == "xlsx":
        rows = artifact.get("rows")
        cols = artifact.get("columns")
        if rows is not None and cols is not None:
            parts.append(f"{rows} 行 · {cols} 列")
        elif rows is not None:
            parts.append(f"{rows} 行")
    elif fmt == "docx":
        paras = artifact.get("paragraphs")
        if paras is not None:
            parts.append(f"{paras} 段")
    size = _fmt_size(artifact.get("size"))
    if size:
        parts.append(size)
    if not parts and fmt:
        parts.append(fmt.upper())
    return " · ".join(parts)


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

        # ── Kimi 风格文件产物卡片 ──
        with st.container():
            # 锚点：供全局 CSS 选中该卡片并套用 Kimi 卡片样式
            st.markdown('<span class="pm-artifact-anchor"></span>', unsafe_allow_html=True)

            # 卡片头部：类型图标 + 文件名 + 元信息（行/列/大小）
            fmt = str(artifact.get("format") or "").lower()
            icon = {
                "xlsx": "📊", "csv": "📋", "docx": "📄",
                "md": "📝", "txt": "📃",
            }.get(fmt, "📁")
            head_html = (
                '<div class="pm-artifact-head">'
                f'<div class="pm-artifact-icon">{icon}</div>'
                '<div class="pm-artifact-meta">'
                f'<div class="pm-artifact-name" title="{escape(name)}">{escape(name)}</div>'
                f'<div class="pm-artifact-sub">{escape(_artifact_subtitle(artifact))}</div>'
                '</div></div>'
            )
            st.markdown(head_html, unsafe_allow_html=True)

            # 计算预览内容（docx 走 markdown；xlsx 优先渲染真正表格）
            preview_markdown = str(artifact.get("preview_markdown") or "").strip()
            if not preview_markdown:
                try:
                    preview = generator.preview_artifact(stored_path, max_chars=4000)
                    preview_markdown = str(preview.get("preview_markdown") or "").strip()
                except (OSError, ValueError, RuntimeError):
                    preview_markdown = ""

            # 主操作行：下载 + 预览并排（对标 Kimi）
            preview_open_key = (
                f"preview_open_{message_key}_{index}_{artifact.get('id', '')}"
            )
            dl_col, prev_col = st.columns([1.4, 1])
            with dl_col:
                st.download_button(
                    f"⬇ 下载 {name}",
                    data=data,
                    file_name=name,
                    mime=artifact.get("mime_type") or "application/octet-stream",
                    key=f"dl_main_{message_key}_{index}_{artifact.get('id', '')}",
                    width="stretch",
                )
            with prev_col:
                if st.button(
                    "▸ 预览",
                    key=f"artifact_preview_{message_key}_{index}_{artifact.get('id', '')}",
                    icon=":material/visibility:",
                    width="stretch",
                ):
                    st.session_state[preview_open_key] = not st.session_state.get(
                        preview_open_key, False
                    )

            # 预览区（下方全宽：xlsx 渲染真正表格，docx 渲染 markdown）
            if st.session_state.get(preview_open_key, False):
                st.markdown('<hr class="pm-artifact-hr">', unsafe_allow_html=True)
                if fmt == "xlsx":
                    try:
                        df = pd.read_excel(path, sheet_name=0, nrows=200)
                        st.dataframe(
                            df,
                            width="stretch",
                            height=min(360, 40 * (len(df) + 1) + 20),
                        )
                    except Exception:
                        if preview_markdown:
                            st.markdown(preview_markdown)
                        else:
                            st.info("（暂无可预览的表格内容）")
                elif preview_markdown:
                    st.markdown(preview_markdown)
                st.markdown('<hr class="pm-artifact-hr">', unsafe_allow_html=True)

            # 多格式导出
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
                st.markdown(
                    '<div class="pm-artifact-export-label">另存为其他格式</div>',
                    unsafe_allow_html=True,
                )
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
                            f"⬇ 保存为 {target_format.upper()}",
                            data=exported["data"],
                            file_name=exported["filename"],
                            mime=exported["mime_type"],
                            key=(
                                f"artifact_export_{message_key}_{index}_"
                                f"{artifact.get('id', '')}_{target_format}"
                            ),
                            width="stretch",
                        )

            # 一句话编辑（仅 xlsx / docx 支持）
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
                            f"⬇ 下载 {edited.get('name', '编辑版本')}",
                            data=edited_data,
                            file_name=edited.get("name", "edited-artifact"),
                            mime=edited.get("mime_type") or "application/octet-stream",
                            key=f"artifact_edit_download_{message_key}_{index}",
                            width="stretch",
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
                    width="stretch",
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


_PERMISSION_RISK_LABELS = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
    "critical": "严重风险",
    "untrusted": "未受信任",
}
_PERMISSION_ELEVATED_RISKS = frozenset({"high", "critical", "untrusted"})
_PERMISSION_CONFIRMATION_LABELS = {
    "low": "单次确认",
    "medium": "单次确认",
    "high": "二次确认",
    "critical": "管理员二次确认",
    "untrusted": "管理员二次确认",
}
_PERMISSION_ACTION_LABELS = {
    "quality_control": "更新质量评审与返工状态",
    "requirements_assessment": "写入需求评估结果",
    "cost_control": "更新成本与预算数据",
    "quote_scheduling": "写入报价排期数据",
    "progress_management": "更新项目进度数据",
    "delivery": "写入交付与验收记录",
    "retrospective": "沉淀复盘与经验记录",
    "reminder_dispatch": "发送外部催办消息",
}
_PERMISSION_IMPACT_LABELS = {
    "quality_control": "将更新质量评审、验收或返工状态",
    "requirements_assessment": "将把需求评估结果写入当前工作区",
    "cost_control": "将更新成本、预算或超支记录",
    "quote_scheduling": "将写入排期、工期或里程碑数据",
    "progress_management": "将更新项目进度、卡点或站会记录",
    "delivery": "将写入交付、验收或资产版本记录",
    "retrospective": "将沉淀复盘结果或经验规则",
    "reminder_dispatch": "将向工作区外部发送催办消息",
}
_PERMISSION_PARAM_LABELS = {
    "action": "操作",
    "project_id": "项目",
    "asset_id": "资产",
    "task_id": "任务",
    "delivery_no": "交付单号",
    "version": "版本",
    "recipient": "接收方",
    "path": "路径",
    "command": "命令",
}


def _permission_parameter_summary(payload_preview):
    """Build a bounded summary from the already-redacted public payload."""
    if not isinstance(payload_preview, Mapping):
        return "无附加参数"
    values = payload_preview.get("inputs")
    if not isinstance(values, Mapping):
        values = payload_preview.get("arguments")
    if not isinstance(values, Mapping):
        return "无附加参数"

    parts = []
    for key, value in values.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        label = _PERMISSION_PARAM_LABELS.get(str(key), str(key))
        if isinstance(value, bool):
            rendered = "是" if value else "否"
        elif isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(
                _thaw_permission_json(value),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        else:
            rendered = str(value)
        rendered = rendered.replace("\n", " ").strip()
        if len(rendered) > 56:
            rendered = f"{rendered[:53]}..."
        parts.append(f"{label}={rendered}")
        if len(parts) >= 4:
            break
    return " · ".join(parts) if parts else "无附加参数"


def _permission_impact(request, skill_name):
    impact = _PERMISSION_IMPACT_LABELS.get(skill_name)
    if impact:
        return impact
    if request.risk in {"critical", "untrusted"}:
        return "可能访问外部系统或敏感资源"
    if request.risk == "high":
        return "可能产生外部影响或不可逆修改"
    return "可能修改当前工作区中的数据"


def _thaw_permission_json(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw_permission_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_permission_json(item) for item in value]
    return value


def _permission_actor():
    """Resolve the authenticated principal shape used by local and cloud hosts."""
    tenant_context = st.session_state.get("tenant_context")
    if isinstance(tenant_context, TenantContext):
        actor_role = "admin" if "admin" in tenant_context.roles else "user"
        return tenant_context.principal_id, actor_role

    # Legacy local hosts may not have installed TenantContext yet. Keep them at
    # user privilege unless their host explicitly supplies a principal mapping.
    principal = st.session_state.get("permission_principal")
    if isinstance(principal, Mapping):
        actor_id = str(principal.get("id") or "local-default")
        actor_role = str(principal.get("role") or "user").casefold()
    else:
        actor_id = str(st.session_state.get("user_id") or "local-default")
        actor_role = str(st.session_state.get("user_role") or "user").casefold()
    if actor_role not in {"user", "admin"}:
        actor_role = "user"
    return actor_id, actor_role


def _trusted_knowledge_context() -> TenantContext:
    """Return the current UI workspace from the host-created tenant context."""

    tenant_context = st.session_state.get("tenant_context")
    if tenant_context is None:
        tenant_context = TenantContext.local()
    if not isinstance(tenant_context, TenantContext):
        raise RuntimeError("tenant_context must be created by the application host")
    tenant_context.require_workspace()
    return tenant_context


def _trusted_knowledge_workspace_id() -> str:
    return _trusted_knowledge_context().workspace_id


def _permission_feedback_state_key(conversation_id):
    return f"permission_feedback_state_{conversation_id or 'unknown'}"


def _set_permission_feedback(conversation_id, feedback):
    st.session_state[_permission_feedback_state_key(conversation_id)] = dict(feedback)


def _render_permission_feedback(conversation_id):
    state_key = _permission_feedback_state_key(conversation_id)
    feedback = st.session_state.get(state_key)
    if not isinstance(feedback, Mapping):
        return
    reference = str(feedback.get("reference") or "status")
    callback_key = f"permission_feedback_{conversation_id}_{reference}"
    if st.session_state.get(f"{callback_key}_dismissed"):
        st.session_state.pop(state_key, None)
        st.session_state.pop(f"{callback_key}_dismissed", None)
        return
    if render_action_callback(feedback, key=callback_key) == "dismiss":
        st.session_state.pop(state_key, None)
        st.rerun()


def has_pending_permission_requests(conversation_id):
    permission_store = get_permission_store()
    if permission_store is None or not conversation_id:
        return False
    tenant_context = st.session_state.get("tenant_context")
    if tenant_context is None:
        tenant_context = TenantContext.local()
    if not isinstance(tenant_context, TenantContext):
        logger.error("租户上下文无效，保持权限输入锁定")
        return True
    workspace_id = tenant_context.workspace_id
    try:
        return bool(
            permission_store.list_pending(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                limit=1,
            )
        )
    except Exception:
        logger.exception("Failed to check pending permission requests")
        # The composer is a second line of defence. A permission-store outage
        # must not make a pending protected action look safe to continue past.
        return True


def _persist_permission_response(request, content, *, status="complete"):
    """Persist one terminal response for the originating permission turn."""
    store = get_conversation_store()
    if store is None or not request.conversation_id or not request.turn_id:
        return
    existing = next(
        (
            message
            for message in store.list_messages(request.conversation_id)
            if message.get("role") == "assistant"
            and message.get("turn_id") == request.turn_id
        ),
        None,
    )
    if existing is None:
        store.add_message(
            request.conversation_id,
            "assistant",
            content,
            status=status,
            turn_id=request.turn_id,
            model_id=_current_model_id(st.session_state.get("agent")),
            metadata={
                "permission_request_id": request.id,
                "permission_status": request.status,
                "permission_source": request.source,
            },
        )
    load_active_messages()


def _execute_approved_permission(request, approved):
    """Claim and execute the exact server-owned continuation once."""
    permission_store = get_permission_store()
    agent = st.session_state.get("agent")
    if permission_store is None or agent is None:
        raise RuntimeError("权限服务或 Agent 当前不可用")
    tenant_context = st.session_state.get("tenant_context")
    if tenant_context is None:
        tenant_context = TenantContext.local()
    if not isinstance(tenant_context, TenantContext):
        raise RuntimeError("租户上下文无效，已阻止恢复执行")
    if request.workspace_id != tenant_context.workspace_id:
        raise RuntimeError("权限请求不属于当前工作区，已阻止恢复执行")

    execution_id = f"permission-{uuid4().hex}"
    claimed = permission_store.claim_execution(
        request.id,
        execution_id=execution_id,
        expected_version=approved.state_version,
        expected_payload_sha256=request.payload_sha256,
        expected_action_sha256=request.action_sha256,
        workspace_id=tenant_context.workspace_id,
    )

    try:
        if claimed.source == "tool":
            payload = _thaw_permission_json(claimed.payload)
            if not isinstance(payload, dict):
                raise RuntimeError("permission payload is invalid")
            tool_name = str(payload.get("tool_name") or "")
            raw_arguments = payload.get("arguments")
            if (
                not tool_name
                or claimed.action != f"tool.{tool_name}"
                or not isinstance(raw_arguments, dict)
            ):
                raise RuntimeError("permission request does not match the tool")
            registry = getattr(agent, "tool_registry", None)
            tool = registry.get(tool_name) if registry is not None else None
            if tool is None:
                raise RuntimeError(f"tool is no longer available: {tool_name}")

            # Revalidate the approved arguments against the current registry.
            arguments = dict(raw_arguments)
            arguments.pop("approved", None)
            arguments.pop("confirmation_token", None)
            prepared = dict(tool.prepare(arguments))
            router = getattr(agent, "router", None)
            if tool_name in getattr(router, "skills", {}):
                prepared = tenant_context.bind_inputs(prepared)
                prepared["approved"] = True
                prepared["confirmation_token"] = (
                    f"permission:{request.id}:{execution_id}"
                )
                prepared.setdefault("idempotency_key", f"permission:{request.id}")
                scoped_router = (
                    router.for_tenant(tenant_context)
                    if callable(getattr(router, "for_tenant", None))
                    else router
                )
                result = scoped_router.execute_skill(tool_name, prepared)
                if not isinstance(result, dict) or result.get("success") is False:
                    raise RuntimeError(
                        str(
                            result.get("error", "tool failed")
                            if isinstance(result, dict)
                            else "tool returned an invalid result"
                        )
                    )
            else:
                from threading import Event

                tool_result = tool.invoke(execution_id, prepared, Event())
                if getattr(tool_result, "is_error", False):
                    raise RuntimeError(
                        str(getattr(tool_result, "content", "tool failed"))
                    )
                result = (
                    tool_result.to_dict()
                    if callable(getattr(tool_result, "to_dict", None))
                    else dict(tool_result)
                )
            completed = permission_store.complete_execution(
                request.id,
                execution_id=execution_id,
                success=True,
                expected_version=claimed.state_version,
                result=result,
                workspace_id=tenant_context.workspace_id,
            )
            safe_result = (
                redact_sensitive(result) if callable(redact_sensitive) else result
            )
            content = str(
                safe_result.get("content")
                or f"Tool completed: {tool_name}"
            )
            return completed, content, "complete"

        if claimed.source != "skill":
            raise RuntimeError(f"暂不支持恢复权限来源：{claimed.source}")
        payload = _thaw_permission_json(claimed.payload)
        if not isinstance(payload, dict):
            raise RuntimeError("权限请求的恢复参数无效")
        skill_name = str(payload.get("skill_name") or "")
        raw_inputs = payload.get("inputs")
        if (
            not skill_name
            or claimed.action != f"skill.{skill_name}"
            or not isinstance(raw_inputs, dict)
        ):
            raise RuntimeError("权限请求与待执行能力不匹配")
        if not hasattr(agent, "router") or skill_name not in agent.router.skills:
            raise RuntimeError(f"待执行能力不可用：{skill_name}")

        inputs = dict(raw_inputs)
        inputs = tenant_context.bind_inputs(inputs)
        inputs.pop("approved", None)
        inputs.pop("confirmation_token", None)
        inputs["approved"] = True
        inputs["confirmation_token"] = f"permission:{request.id}:{execution_id}"
        inputs.setdefault("idempotency_key", f"permission:{request.id}")
        router = getattr(agent, "router", None)
        scoped_router = (
            router.for_tenant(tenant_context)
            if callable(getattr(router, "for_tenant", None))
            else router
        )
        if scoped_router is None:
            raise RuntimeError("Skill 路由器不可用")
        result = scoped_router.execute_skill(skill_name, inputs)
        if not isinstance(result, dict):
            raise TypeError("Skill 必须返回结构化结果")
        if result.get("success") is False:
            raise RuntimeError(str(result.get("error") or "Skill 执行失败"))

        completed = permission_store.complete_execution(
            request.id,
            execution_id=execution_id,
            success=True,
            expected_version=claimed.state_version,
            result=result,
            workspace_id=tenant_context.workspace_id,
        )
        safe_result = redact_sensitive(result) if callable(redact_sensitive) else result
        formatter = getattr(agent, "_format_skill_result", None)
        content = formatter(skill_name, safe_result) if callable(formatter) else ""
        if not str(content or "").strip():
            content = f"已完成：{_PERMISSION_ACTION_LABELS.get(skill_name, skill_name)}。"
        return completed, str(content), "complete"
    except Exception as error:
        try:
            failed = permission_store.complete_execution(
                request.id,
                execution_id=execution_id,
                success=False,
                expected_version=claimed.state_version,
                result=None,
                error=str(error),
                workspace_id=tenant_context.workspace_id,
            )
        except Exception:
            logger.exception("Failed to finalize permission execution")
            failed = (
                permission_store.get(
                    request.id,
                    workspace_id=tenant_context.workspace_id,
                )
                or claimed
            )
        logger.warning("Approved permission %s did not complete: %s", request.id, error)
        return (
            failed,
            "操作执行未完成。为避免重复修改，系统没有自动重试。",
            "error",
        )


def _render_permission_approvals(conversation_id):
    """Render persistent, one-shot permission requests in their conversation."""
    _render_permission_feedback(conversation_id)
    permission_store = get_permission_store()
    if permission_store is None or not conversation_id:
        return
    tenant_context = st.session_state.get("tenant_context")
    if tenant_context is None:
        tenant_context = TenantContext.local()
    if not isinstance(tenant_context, TenantContext):
        logger.error("租户上下文无效，拒绝加载待确认权限")
        render_error_callback(
            {
                "message": "当前工作区身份无效，已暂停权限操作。",
                "suggestions": ["刷新页面重新建立工作区会话", "确认登录状态仍然有效"],
                "severity": "error",
                "error_id": "permission-identity",
            },
            key="permission_identity_error",
            retry=False,
            dismissible=False,
        )
        return
    workspace_id = tenant_context.workspace_id
    try:
        requests = permission_store.list_pending(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            limit=20,
        )
    except Exception:
        logger.exception("Failed to load pending permission requests")
        action = render_error_callback(
            {
                "message": "权限服务暂时不可用，已暂停新的操作。",
                "suggestions": ["刷新状态后重试", "如果问题持续，请检查权限数据库连接"],
                "severity": "error",
                "error_id": "permission-store",
            },
            key="permission_store_load_error",
            retry=True,
            dismissible=False,
            retry_label="刷新状态",
        )
        if action == "retry":
            st.rerun()
        return

    actor_id, actor_role = _permission_actor()
    for request in reversed(requests):
        public = request.to_dict()
        payload_preview = public.get("redacted_arguments") or {}
        resource_preview = public.get("resource") or {}
        skill_name = str(
            payload_preview.get("skill_name")
            if isinstance(payload_preview, dict)
            else ""
        )
        action_label = _PERMISSION_ACTION_LABELS.get(
            skill_name,
            (
                resource_preview.get("label")
                if isinstance(resource_preview, dict)
                else None
            )
            or request.action,
        )
        risk_label = _PERMISSION_RISK_LABELS.get(request.risk, request.risk)
        impact_label = _permission_impact(request, skill_name)
        parameter_summary = _permission_parameter_summary(payload_preview)
        can_approve = request.required_role == "user" or actor_role == "admin"
        elevated = request.risk in _PERMISSION_ELEVATED_RISKS
        confirmation_label = _PERMISSION_CONFIRMATION_LABELS.get(
            request.risk,
            "单次确认",
        )
        role_label = "管理员" if request.required_role == "admin" else "当前用户"
        review_key = f"permission_review_{request.id}_{request.state_version}"
        review_open = bool(st.session_state.get(review_key))

        with st.container(key=f"permission_approval_{request.id}", border=True):
            st.markdown(
                '<span class="pm-permission-anchor" aria-hidden="true"></span>',
                unsafe_allow_html=True,
            )
            st.markdown(
                (
                    '<div class="pm-permission-heading">'
                    '<div class="pm-permission-title">'
                    '<h3>需要你的确认</h3>'
                    f'<p>{escape(request.agent_id)} 请求执行以下操作</p></div>'
                    f'<span class="pm-risk pm-risk-{escape(request.risk)}">'
                    f'{escape(risk_label)}</span></div>'
                    f'<div class="pm-permission-action">{escape(action_label)}</div>'
                    '<div class="pm-permission-facts" role="list">'
                    '<div class="pm-permission-fact" role="listitem">'
                    '<span>影响</span>'
                    f'<strong>{escape(impact_label)}</strong></div>'
                    '<div class="pm-permission-fact" role="listitem">'
                    '<span>作用域</span>'
                    f'<strong>工作区 {escape(request.workspace_id)} · 仅此操作</strong>'
                    '</div><div class="pm-permission-fact" role="listitem">'
                    '<span>确认级别</span>'
                    f'<strong>{escape(confirmation_label)} · {escape(role_label)}</strong>'
                    '</div></div>'
                    '<div class="pm-permission-params">'
                    '<span>参数</span>'
                    f'<code>{escape(parameter_summary)}</code></div>'
                ),
                unsafe_allow_html=True,
            )
            with st.expander("查看脱敏后的完整参数", expanded=False):
                st.caption("敏感字段已隐藏；本次授权只适用于这里显示的参数。")
                st.json(payload_preview, expanded=1)
            if not can_approve:
                st.warning("此操作需要管理员确认；当前账号只能拒绝或等待管理员处理。")
            approve = False
            reject = False
            back = False
            with st.container(key=f"permission_actions_{request.id}"):
                if elevated and review_open:
                    st.warning(
                        "这是高风险操作。请再次核对影响、作用域和脱敏参数；确认后会立即执行一次。"
                    )
                    acknowledged = st.checkbox(
                        "我已核对影响范围和参数",
                        key=f"acknowledge_permission_{request.id}",
                    )
                    approve_col, back_col, reject_col = st.columns([1.4, 1, 1])
                    with approve_col:
                        approve = st.button(
                            "确认并执行",
                            key=f"confirm_permission_{request.id}",
                            type="primary",
                            icon=":material/check:",
                            width="stretch",
                            disabled=not can_approve or not acknowledged,
                            help="仅执行上方显示的这一项操作",
                        )
                    with back_col:
                        back = st.button(
                            "返回",
                            key=f"back_permission_{request.id}",
                            width="stretch",
                        )
                    with reject_col:
                        reject = st.button(
                            "拒绝",
                            key=f"reject_permission_{request.id}",
                            icon=":material/close:",
                            width="stretch",
                            help="拒绝该请求并保持数据不变",
                        )
                else:
                    approve_col, reject_col = st.columns(2)
                    with approve_col:
                        if elevated:
                            advance = st.button(
                                "继续确认",
                                key=f"review_permission_{request.id}",
                                type="primary",
                                icon=":material/arrow_forward:",
                                width="stretch",
                                disabled=not can_approve,
                                help="进入第二层复核，不会立即执行",
                            )
                            if advance:
                                st.session_state[review_key] = True
                                st.rerun()
                        else:
                            approve = st.button(
                                "允许一次",
                                key=f"allow_once_{request.id}",
                                type="primary",
                                icon=":material/check:",
                                width="stretch",
                                disabled=not can_approve,
                                help=(
                                    "只允许当前请求使用上方参数"
                                    if can_approve
                                    else "当前账号没有管理员权限"
                                ),
                            )
                    with reject_col:
                        reject = st.button(
                            "拒绝",
                            key=f"reject_permission_{request.id}",
                            icon=":material/close:",
                            width="stretch",
                            help="拒绝该请求并保持数据不变",
                        )

            if back:
                st.session_state.pop(review_key, None)
                st.rerun()

            if approve:
                try:
                    approved = permission_store.decide(
                        request.id,
                        decision="approved",
                        actor_id=actor_id,
                        actor_role=actor_role,
                        expected_version=request.state_version,
                        workspace_id=workspace_id,
                        acknowledged_risk=request.risk if elevated else None,
                    )
                    with st.spinner("正在执行已授权操作…"):
                        terminal, content, response_status = _execute_approved_permission(
                            request,
                            approved,
                        )
                    _persist_permission_response(
                        terminal,
                        content,
                        status=response_status,
                    )
                    st.session_state.pop(review_key, None)
                    if response_status == "complete":
                        _set_permission_feedback(
                            conversation_id,
                            {
                                "title": "已按本次授权执行",
                                "message": "操作已完成，本次授权随即失效，不会用于其他请求。",
                                "severity": "success",
                                "reference": request.id[:8],
                            },
                        )
                    else:
                        _set_permission_feedback(
                            conversation_id,
                            {
                                "title": "操作执行未完成",
                                "message": "为避免重复修改，系统没有自动重试。请检查运行状态后重新发起。",
                                "severity": "error",
                                "reference": request.id[:8],
                            },
                        )
                    st.rerun()
                except PermissionConflictError:
                    action = render_error_callback(
                        {
                            "message": "该权限请求已在其他窗口处理。",
                            "suggestions": ["刷新状态查看最新结果"],
                            "severity": "warning",
                            "error_id": f"permission-conflict-{request.id[:8]}",
                        },
                        key=f"permission_conflict_{request.id}",
                        retry=True,
                        dismissible=False,
                        retry_label="刷新状态",
                    )
                    if action == "retry":
                        st.rerun()
                except Exception as error:
                    logger.exception("Permission approval failed")
                    render_error_callback(
                        build_error_info(error, context={"operation": "permission_approve"}),
                        key=f"permission_approve_error_{request.id}",
                        retry=False,
                    )
            if reject:
                try:
                    rejected = permission_store.decide(
                        request.id,
                        decision="rejected",
                        actor_id=actor_id,
                        actor_role=actor_role,
                        expected_version=request.state_version,
                        workspace_id=workspace_id,
                    )
                    _persist_permission_response(
                        rejected,
                        "已拒绝该操作，未执行任何修改。",
                    )
                    st.session_state.pop(review_key, None)
                    _set_permission_feedback(
                        conversation_id,
                        {
                            "title": "已拒绝操作",
                            "message": "没有执行该请求，也没有修改工作区数据。",
                            "severity": "info",
                            "reference": request.id[:8],
                        },
                    )
                    st.rerun()
                except PermissionConflictError:
                    action = render_error_callback(
                        {
                            "message": "该权限请求已在其他窗口处理。",
                            "suggestions": ["刷新状态查看最新结果"],
                            "severity": "warning",
                            "error_id": f"permission-conflict-{request.id[:8]}",
                        },
                        key=f"permission_reject_conflict_{request.id}",
                        retry=True,
                        dismissible=False,
                        retry_label="刷新状态",
                    )
                    if action == "retry":
                        st.rerun()
                except Exception as error:
                    logger.exception("Permission rejection failed")
                    render_error_callback(
                        build_error_info(error, context={"operation": "permission_reject"}),
                        key=f"permission_reject_error_{request.id}",
                        retry=False,
                    )
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
                    width="stretch",
                )
            with reject_col:
                reject = st.button(
                    "保留原设置",
                    key=f"reject_profile_{proposal.id}",
                    icon=":material/close:",
                    width="stretch",
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
                    render_error_callback(
                        build_error_info(error, context={"operation": "profile_approve"}),
                        key=f"profile_approve_error_{proposal.id}",
                        retry=False,
                    )
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
                    render_error_callback(
                        build_error_info(error, context={"operation": "profile_reject"}),
                        key=f"profile_reject_error_{proposal.id}",
                        retry=False,
                    )
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
        tenant_context = _trusted_knowledge_context()
        workspace_id = tenant_context.workspace_id
        tenant_id = tenant_context.tenant_id
    except Exception:
        logger.exception("当前工作区身份无效，拒绝加载资料入库提案")
        return
    try:
        proposals = knowledge_store.list_ingestion_proposals(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
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
                    width="stretch",
                )
            with reject_col:
                reject = st.button(
                    "取消",
                    key=f"reject_ingestion_{proposal['id']}",
                    icon=":material/close:",
                    width="stretch",
                )
            if approve:
                try:
                    confirmed = knowledge_store.confirm_ingestion(
                        proposal["id"],
                        actor="本地用户（会话确认）",
                        confirmation_token=(
                            f"{conversation_id}:{proposal['id']}:confirmed"
                        ),
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
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
                    render_error_callback(
                        build_error_info(error, context={"operation": "knowledge_ingest"}),
                        key=f"knowledge_ingest_error_{proposal['id']}",
                        retry=False,
                    )
            if reject:
                try:
                    rejected = knowledge_store.reject_ingestion(
                        proposal["id"],
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
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
                    render_error_callback(
                        build_error_info(error, context={"operation": "knowledge_ingest_reject"}),
                        key=f"knowledge_ingest_reject_error_{proposal['id']}",
                        retry=False,
                    )
def _render_knowledge_approvals(conversation_id):
    knowledge_store = get_knowledge_store()
    if knowledge_store is None or not conversation_id:
        return
    try:
        tenant_context = _trusted_knowledge_context()
        workspace_id = tenant_context.workspace_id
        tenant_id = tenant_context.tenant_id
    except Exception:
        logger.exception("当前工作区身份无效，拒绝加载知识规则提案")
        return
    try:
        rules = [
            rule
            for rule in knowledge_store.list_rules(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="proposed",
                limit=100,
            )
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
                    width="stretch",
                )
            with reject_col:
                reject = st.button(
                    "不采纳",
                    key=f"reject_knowledge_{rule['id']}",
                    icon=":material/close:",
                    width="stretch",
                )
            if approve:
                try:
                    accepted = knowledge_store.confirm_rule(
                        rule["id"],
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
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
                    render_error_callback(
                        build_error_info(error, context={"operation": "knowledge_rule_approve"}),
                        key=f"knowledge_rule_approve_error_{rule['id']}",
                        retry=False,
                    )
            if reject:
                try:
                    rejected = knowledge_store.reject_rule(
                        rule["id"],
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        rejected_by="本地用户（会话拒绝）",
                    )
                    _persist_knowledge_response(
                        rejected,
                        "已忽略这条知识提案，后续会话不会使用它。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("拒绝知识规则失败")
                    render_error_callback(
                        build_error_info(error, context={"operation": "knowledge_rule_reject"}),
                        key=f"knowledge_rule_reject_error_{rule['id']}",
                        retry=False,
                    )
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
        action = render_error_callback(
            {
                "message": "暂时无法读取待确认工作流。",
                "suggestions": ["刷新状态后重试", "确认工作流存储连接正常"],
                "severity": "error",
                "error_id": "workflow-approval-load",
            },
            key="workflow_approval_load_error",
            retry=True,
            dismissible=False,
            retry_label="刷新状态",
        )
        if action == "retry":
            st.rerun()
        return

    actor_id, actor_role = _permission_actor()
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
                    width="stretch",
                )
            with reject_col:
                reject = st.button(
                    "取消",
                    key=f"reject_workflow_{run.id}_{run.current_step}",
                    icon=":material/close:",
                    width="stretch",
                )

            if approve:
                try:
                    result = coordinator.engine.decide_approval(
                        run.id,
                        run.current_step,
                        decision="approved",
                        actor=actor_id,
                        actor_level=actor_role,
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
                    render_error_callback(
                        build_error_info(error, context={"operation": "workflow_approve"}),
                        key=f"workflow_approve_error_{run.id}",
                        retry=False,
                    )
            if reject:
                try:
                    result = coordinator.engine.decide_approval(
                        run.id,
                        run.current_step,
                        decision="rejected",
                        actor=actor_id,
                        actor_level=actor_role,
                    )
                    _persist_workflow_response(
                        result.run,
                        "已取消该工作流，未执行后续外部操作。",
                    )
                    st.rerun()
                except Exception as error:
                    logger.exception("取消工作流失败")
                    render_error_callback(
                        build_error_info(error, context={"operation": "workflow_reject"}),
                        key=f"workflow_reject_error_{run.id}",
                        retry=False,
                    )


# New code can import the pure implementations directly from ui_formatters;
# these aliases keep the historical ui_helpers surface stable during migration.
_history_limits = _ui_formatters.history_limits
_current_model_id = _ui_formatters.current_model_id
_response_model_id = _ui_formatters.response_model_id
_fmt_size = _ui_formatters.format_size
_artifact_subtitle = _ui_formatters.artifact_subtitle
