# ruff: noqa: E402 - bootstrap adjusts sys.path before package imports
"""
UI 共享状态层 — 导入、常量、lazy getter、可用性标志。

从 ui_helpers.py 提取，所有 UI 子模块均可安全导入此模块，
不会产生循环引用（此模块不导入任何 ui_helpers 子模块）。

可用性标志在导入期就地确定：每个可选运行时各自 try/except，失败时把
对应符号降级为 None 并留下日志，UI 侧据此进入离线降级路径。
"""
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
_project_root = Path(__file__).parent.parent.resolve()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from artpm_agent.utils.logger import get_logger

logger = get_logger(__name__)

# ── 导航常量 ──
_NAV_OPTIONS = ("对话", "设置", "可观测")
_NAV_WIDGET_KEY = "sidebar_nav_pills"
_NAV_EVENT_KEY = "_sidebar_nav_event"

# ── 可用性标志与可选运行时导入 ──
# 每个可选运行时单独 try/except：任一子系统缺失只降级它自己，其余功能照常。
AVAILABLE = False
try:
    from artpm_agent.agent import ArtPMAgent
    from artpm_agent.config import Config
    from artpm_agent.database.models import DatabaseManager

    AVAILABLE = True
    logger.info("核心模块导入成功")
except Exception as error:
    ArtPMAgent = None
    Config = None
    DatabaseManager = None
    logger.error("核心模块导入失败: %s", error)

CONVERSATION_STORE_AVAILABLE = False
try:
    from artpm_agent.memory import (
        SessionStore,
        WorkspaceKnowledgeStore,
        WorkspaceWikiStore,
        create_embedding_provider,
    )
    from artpm_agent.memory.conversation_store import ConversationStore

    CONVERSATION_STORE_AVAILABLE = True
except Exception as error:
    ConversationStore = None
    SessionStore = None
    WorkspaceKnowledgeStore = None
    WorkspaceWikiStore = None
    create_embedding_provider = None
    logger.error("会话存储模块导入失败: %s", error)

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
    logger.error("工作流运行时导入失败: %s", error)

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
    logger.error("Agent Profile 模块导入失败: %s", error)

ARTIFACT_RUNTIME_AVAILABLE = False
try:
    from artpm_agent.artifacts import ArtifactCoordinator, WorkspaceArtifactGenerator

    ARTIFACT_RUNTIME_AVAILABLE = True
except Exception as error:
    ArtifactCoordinator = None
    WorkspaceArtifactGenerator = None
    logger.error("文件生成模块导入失败: %s", error)

PERMISSION_RUNTIME_AVAILABLE = False
try:
    from artpm_agent.security import (
        PermissionConflictError,
        PermissionStore,
        redact_sensitive,
    )

    PERMISSION_RUNTIME_AVAILABLE = True
except Exception as error:
    PermissionConflictError = RuntimeError
    PermissionStore = None
    redact_sensitive = None
    logger.error("审批运行时导入失败: %s", error)

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


def get_session_store():
    """Return the append-only runtime log bound to the active conversation DB."""

    import streamlit as st

    conversation_store = get_conversation_store()
    if conversation_store is None or SessionStore is None:
        return None
    cached = st.session_state.get("session_store")
    if cached is None or getattr(cached, "conversations", None) is not conversation_store:
        cached = SessionStore(conversation_store)
        st.session_state.session_store = cached
    return cached


def get_chat_attachment_store():
    import streamlit as st
    return st.session_state.get("chat_attachment_store")


def get_artifact_generator():
    import streamlit as st
    return st.session_state.get("artifact_generator")


def get_artifact_coordinator():
    import streamlit as st
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
    try:
        return store.get_effective_profile()
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
