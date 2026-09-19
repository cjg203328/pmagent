# ruff: noqa: E402 - bootstrap adjusts sys.path before package imports
"""
UI 共享状态层 — 导入、常量、lazy getter、可用性标志。

从 ui_helpers.py 提取，所有 UI 子模块均可安全导入此模块，
不会产生循环引用（此模块不导入任何 ui_helpers 子模块）。

可用性标志在导入期就地确定：每个可选运行时各自 try/except，失败时把
对应符号降级为 None 并留下日志，UI 侧据此进入离线降级路径。
"""
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Any

# 确保项目根目录在 Python 路径中
_project_root = Path(__file__).parent.parent.resolve()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from artpm_agent.runtime.factory import get_runtime_factory as _get_runtime_factory
from artpm_agent.utils.logger import get_logger

logger = get_logger(__name__)

# ── 导航常量 ──
_NAV_OPTIONS = ("对话", "设置", "可观测")
_NAV_WIDGET_KEY = "sidebar_nav_pills"
_NAV_EVENT_KEY = "_sidebar_nav_event"

# ── 可用性标志与可选运行时导入 ──
# 每个可选运行时单独 try/except：任一子系统缺失只降级它自己，其余功能照常。
AVAILABLE = False
# Optional imports remain public compatibility exports.  Their runtime value
# is the imported object or ``None`` when a subsystem is unavailable.
Config: Any = None
DatabaseManager: Any = None
try:
    from artpm_agent.config import Config as _Config
    from artpm_agent.database.models import DatabaseManager as _DatabaseManager

    Config = _Config
    DatabaseManager = _DatabaseManager

    AVAILABLE = True
    logger.info("核心模块导入成功")
except Exception as error:
    logger.error("核心模块导入失败: %s", error)

CONVERSATION_STORE_AVAILABLE = False
ConversationStore: Any = None
SessionStore: Any = None
WorkspaceKnowledgeStore: Any = None
WorkspaceWikiStore: Any = None
create_embedding_provider: Any = None
try:
    from artpm_agent.memory.conversation_store import (
        ConversationStore as _ConversationStore,
    )
    from artpm_agent.memory.embeddings import (
        create_embedding_provider as _create_embedding_provider,
    )
    from artpm_agent.memory.session_store import SessionStore as _SessionStore
    from artpm_agent.memory.wiki_store import WorkspaceWikiStore as _WorkspaceWikiStore
    from artpm_agent.memory.workspace_knowledge_store import (
        WorkspaceKnowledgeStore as _WorkspaceKnowledgeStore,
    )

    ConversationStore = _ConversationStore
    create_embedding_provider = _create_embedding_provider
    SessionStore = _SessionStore
    WorkspaceWikiStore = _WorkspaceWikiStore
    WorkspaceKnowledgeStore = _WorkspaceKnowledgeStore

    CONVERSATION_STORE_AVAILABLE = True
except Exception as error:
    logger.error("会话存储模块导入失败: %s", error)

WORKFLOW_RUNTIME_AVAILABLE = False
ScopedWorkflowAgent: Any = None
WorkflowCoordinator: Any = None
WorkflowOverride: Any = None
WorkflowStore: Any = None
format_workflow_result: Any = None
try:
    from artpm_agent.workflows import ScopedWorkflowAgent as _ScopedWorkflowAgent
    from artpm_agent.workflows import WorkflowCoordinator as _WorkflowCoordinator
    from artpm_agent.workflows import WorkflowOverride as _WorkflowOverride
    from artpm_agent.workflows import WorkflowStore as _WorkflowStore
    from artpm_agent.workflows import format_workflow_result as _format_workflow_result

    ScopedWorkflowAgent = _ScopedWorkflowAgent
    WorkflowCoordinator = _WorkflowCoordinator
    WorkflowOverride = _WorkflowOverride
    WorkflowStore = _WorkflowStore
    format_workflow_result = _format_workflow_result
    WORKFLOW_RUNTIME_AVAILABLE = True
except Exception as error:
    logger.error("工作流运行时导入失败: %s", error)

PROFILE_RUNTIME_AVAILABLE = False
AgentIdentity: Any = None
AgentIdentityPatch: Any = None
AgentProfilePatch: Any = None
AgentProfileStore: Any = None
ProfileChangeParseError: Any = ValueError
QuotePolicy: Any = None
format_profile_changes: Any = None
parse_profile_change: Any = None
try:
    from artpm_agent.profiles import AgentIdentity as _AgentIdentity
    from artpm_agent.profiles import AgentIdentityPatch as _AgentIdentityPatch
    from artpm_agent.profiles import AgentProfilePatch as _AgentProfilePatch
    from artpm_agent.profiles import AgentProfileStore as _AgentProfileStore
    from artpm_agent.profiles import ProfileChangeParseError as _ProfileChangeParseError
    from artpm_agent.profiles import QuotePolicy as _QuotePolicy
    from artpm_agent.profiles import format_profile_changes as _format_profile_changes
    from artpm_agent.profiles import parse_profile_change as _parse_profile_change

    AgentIdentity = _AgentIdentity
    AgentIdentityPatch = _AgentIdentityPatch
    AgentProfilePatch = _AgentProfilePatch
    AgentProfileStore = _AgentProfileStore
    ProfileChangeParseError = _ProfileChangeParseError
    QuotePolicy = _QuotePolicy
    format_profile_changes = _format_profile_changes
    parse_profile_change = _parse_profile_change
    PROFILE_RUNTIME_AVAILABLE = True
except Exception as error:
    logger.error("Agent Profile 模块导入失败: %s", error)

ARTIFACT_RUNTIME_AVAILABLE = False
ArtifactCoordinator = None
WorkspaceArtifactGenerator = None
ARTIFACT_RUNTIME_AVAILABLE = find_spec("artpm_agent.artifacts") is not None

PERMISSION_RUNTIME_AVAILABLE = False
PermissionConflictError: Any = RuntimeError
PermissionStore: Any = None
redact_sensitive: Any = None
try:
    from artpm_agent.security import PermissionConflictError as _PermissionConflictError
    from artpm_agent.security import PermissionStore as _PermissionStore
    from artpm_agent.security import redact_sensitive as _redact_sensitive

    PermissionConflictError = _PermissionConflictError
    PermissionStore = _PermissionStore
    redact_sensitive = _redact_sensitive
    PERMISSION_RUNTIME_AVAILABLE = True
except Exception as error:
    logger.error("审批运行时导入失败: %s", error)

MODEL_CATALOG_AVAILABLE = False
MODEL_CATALOG_IMPORT_ERROR = None
ModelCatalogError: Any = RuntimeError
fetch_openai_compatible_models: Any = None
parse_cached_models: Any = None
serialize_cached_models: Any = None
try:
    from artpm_agent.utils.model_catalog import ModelCatalogError as _ModelCatalogError
    from artpm_agent.utils.model_catalog import (
        fetch_openai_compatible_models as _fetch_openai_compatible_models,
    )
    from artpm_agent.utils.model_catalog import (
        parse_cached_models as _parse_cached_models,
    )
    from artpm_agent.utils.model_catalog import (
        serialize_cached_models as _serialize_cached_models,
    )

    ModelCatalogError = _ModelCatalogError
    fetch_openai_compatible_models = _fetch_openai_compatible_models
    parse_cached_models = _parse_cached_models
    serialize_cached_models = _serialize_cached_models
    MODEL_CATALOG_AVAILABLE = True
except Exception as error:
    MODEL_CATALOG_IMPORT_ERROR = str(error)
    logger.error("模型同步模块导入失败: %s", error)

# ── 默认值 ──
EMPTY_STATS: dict = {
    "total_projects": 0,
    "in_progress": 0,
    "completed": 0,
    "total_revenue": 0,
    "avg_profit_rate": 0,
}


def _queue_sidebar_navigation() -> None:
    """Capture only an explicit pills interaction for the next script run."""
    import streamlit as st
    selected = st.session_state.get(_NAV_WIDGET_KEY)
    if selected in _NAV_OPTIONS:
        st.session_state[_NAV_EVENT_KEY] = selected


# ── 懒加载 session state getter ──
def get_conversation_store():
    import streamlit as st
    return st.session_state.get("conversation_store")


def get_ui_runtime_factory():
    """Return the process runtime while keeping a Streamlit compatibility alias."""

    import streamlit as st

    factory = _get_runtime_factory()
    if st.session_state.get("runtime_factory") is not factory:
        st.session_state.runtime_factory = factory
    return factory


def get_session_store():
    """Return the append-only runtime log bound to the active conversation DB."""

    import streamlit as st

    cached = st.session_state.get("session_store")
    if cached is None:
        try:
            cached = get_ui_runtime_factory().storage.session
        except Exception as error:  # pragma: no cover - optional UI degradation
            logger.warning("Session store unavailable: %s", error)
            return None
        st.session_state.session_store = cached
    return cached


def get_event_bus():
    """Return the event bus shared by all turns in the active UI session."""

    import streamlit as st

    cached = st.session_state.get("event_bus")
    if cached is None:
        try:
            cached = get_ui_runtime_factory().event_bus()
        except Exception as error:  # pragma: no cover - optional UI degradation
            logger.warning("Event bus unavailable: %s", error)
            return None
        st.session_state.event_bus = cached
    return cached


def get_episode_store():
    """Return the outcome store shared by all turns in this UI session."""

    import streamlit as st

    cached = st.session_state.get("episode_store")
    if cached is None:
        try:
            cached = get_ui_runtime_factory().learning_service("episode_store")
            st.session_state.episode_store = cached
        except Exception as error:  # pragma: no cover - optional UI degradation
            logger.warning("Episode store unavailable: %s", error)
    return cached


def get_consolidation_scheduler():
    """Return the memory scheduler shared by all turns in this UI session."""

    import streamlit as st

    cached = st.session_state.get("consolidation_scheduler")
    if cached is None:
        try:
            cached = get_ui_runtime_factory().learning_service(
                "consolidation_scheduler"
            )
            st.session_state.consolidation_scheduler = cached
        except Exception as error:  # pragma: no cover - optional UI degradation
            logger.warning("Consolidation scheduler unavailable: %s", error)
    return cached


def get_chat_attachment_store():
    import streamlit as st
    return st.session_state.get("chat_attachment_store")


def _trusted_ui_tenant_context():
    import streamlit as st

    from artpm_agent.tenancy import TenantContext

    tenant_context = st.session_state.get("tenant_context")
    if tenant_context is None:
        tenant_context = TenantContext.local()
    return tenant_context if isinstance(tenant_context, TenantContext) else None


def _active_ui_profile_id(tenant_context) -> str:
    import streamlit as st

    profile_id = str(st.session_state.get("profile_id") or "").strip()
    if profile_id:
        return profile_id
    store = get_profile_store()
    if store is not None:
        try:
            profile = store.get_effective_profile(scope=tenant_context.to_scope())
            profile_id = str(getattr(profile, "profile_id", "") or "").strip()
        except Exception:
            profile_id = ""
    return profile_id or "local-default"


def get_artifact_generator():
    import streamlit as st

    if not ARTIFACT_RUNTIME_AVAILABLE:
        return None
    tenant_context = _trusted_ui_tenant_context()
    if tenant_context is None:
        return None
    try:
        generator = get_ui_runtime_factory().artifact_generator(
            tenant_context,
            profile_id=_active_ui_profile_id(tenant_context),
        )
    except Exception as error:  # pragma: no cover - optional UI degradation
        logger.warning("Artifact generator unavailable: %s", error)
        return None
    # Compatibility alias for rendering/download helpers. The factory owns it.
    st.session_state.artifact_generator = generator
    return generator


def get_artifact_coordinator():
    import streamlit as st

    if not ARTIFACT_RUNTIME_AVAILABLE:
        return None
    tenant_context = _trusted_ui_tenant_context()
    if tenant_context is None:
        return None
    try:
        coordinator = get_ui_runtime_factory().artifact_coordinator(
            tenant_context,
            profile_id=_active_ui_profile_id(tenant_context),
        )
    except Exception as error:  # pragma: no cover - optional UI degradation
        logger.warning("Artifact coordinator unavailable: %s", error)
        return None
    # Compatibility alias only; lifecycle and scope are owned by the factory.
    st.session_state.artifact_coordinator = coordinator
    st.session_state.artifact_generator = coordinator.generator
    return coordinator


class _UnavailableArtifactPlanner:
    """Small local stand-in so deterministic artifact paths still work offline."""

    def chat(self, *_args, **_kwargs):
        raise RuntimeError("模型服务暂时不可用")


def get_workflow_store():
    import streamlit as st
    return st.session_state.get("workflow_store")


def get_profile_store():
    import streamlit as st
    return st.session_state.get("profile_store")


def get_knowledge_store():
    import streamlit as st
    return st.session_state.get("knowledge_store")


def get_wiki_store():
    import streamlit as st
    return st.session_state.get("wiki_store")


def get_permission_store():
    import streamlit as st
    return st.session_state.get("permission_store")


def get_current_profile():
    store = get_profile_store()
    if store is None:
        return None
    tenant_context = _trusted_ui_tenant_context()
    if tenant_context is None:
        return None
    try:
        profile = store.get_effective_profile(scope=tenant_context.to_scope())
        import streamlit as st

        st.session_state.profile_id = profile.profile_id
        return profile
    except Exception:
        return None


# ── 运行时初始化错误记录 ──
_RUNTIME_COMPONENT_LABELS = {
    "conversation_store": "会话记录",
    "permission_store": "审批记录",
    "chat_attachment_store": "附件存储",
    "artifact_generator": "文件生成",
    "workflow_store": "工作流",
    "profile_store": "配置档案",
    "knowledge_store": "知识库",
    "wiki_store": "Wiki 知识库",
    "agent": "对话服务",
    "database": "业务数据库",
}


def record_runtime_init_failure(component: str, error: Exception) -> None:
    import streamlit as st
    failures = dict(st.session_state.get("runtime_init_errors", {}))
    failures[component] = _RUNTIME_COMPONENT_LABELS.get(component, component)
    st.session_state.runtime_init_errors = failures
    logger.exception("Runtime component initialization failed: %s: %s", component, error)
