# ruff: noqa: E402,F405 - Streamlit may execute this page as a standalone script
"""设置页面（从 app.py 拆分）。"""
import sys
import json
import os
import re
import stat
import asyncio
from pathlib import Path
from typing import Optional

# 确保项目根目录在 Python 路径中（Streamlit 以多页面方式加载本文件时也能找到包）
# 先 resolve(__file__) 为绝对路径再取 parent，避免 __file__ 为相对路径时多退一层目录。
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from artpm_agent.ui_helpers import *  # noqa: F401,F403
from artpm_agent.ui_feedback import build_error_info, render_error_callback
from artpm_agent.config import (
    Config,
    PROJECT_ENV_PATH,
    reset_config,
    resolve_data_root,
    save_data_root,
)
from artpm_agent.tenancy import TenantContext
from artpm_agent.views.workflow_designer import render_workflow_designer
from artpm_agent.views.wiki import render_wiki_workspace
from artpm_agent.workflows.designer import capability_allowlist_from_skill_metadata

MANUAL_MODEL_OPTION = "手动输入模型 ID"
_ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _toast(message: str) -> None:
    """Show one short completion notice without adding a persistent alert."""

    try:
        st.toast(str(message))
    except (AttributeError, TypeError):
        # Streamlit versions without toast still get a visible, non-fatal notice.
        st.success(str(message))


def _mcp_refresh_ready(*, enabled: bool, transport: str, api_key: str, url: str) -> bool:
    """Return whether the connection probe has enough user input to run."""

    if not enabled or not api_key.strip():
        return False
    return transport.strip().lower() != "http" or bool(url.strip())


def _mcp_status_message(*, enabled: bool, error_category: str | None = None) -> str:
    """Map connection state to concise, non-technical UI copy."""

    if not enabled:
        return "未启用"
    messages = {
        "auth": "连接失败，请检查 API Key。",
        "config": "连接配置不完整，请检查 API Key 和地址。",
        "url": "连接地址无效，请检查地址格式。",
        "parse": "连接地址无效，请填写服务端地址。",
        "network": "暂时无法连接，请检查网络后重试。",
        "http": "服务暂时不可用，请稍后重试。",
    }
    return messages.get(error_category or "", "连接检查失败，请检查配置后重试。")


def _trusted_workflow_scope():
    """Resolve workflow scope from a server-created tenant context only."""

    candidate = st.session_state.get("tenant_context")
    if candidate is None:
        candidate = TenantContext.local()
        st.session_state["tenant_context"] = candidate
    if not isinstance(candidate, TenantContext):
        raise RuntimeError("tenant_context must be created by the application host")
    profile_id = str(st.session_state.get("profile_id") or "local-default")
    return candidate, profile_id


def _workflow_capability_allowlist():
    agent = st.session_state.get("agent")
    router = getattr(agent, "router", None)
    list_skills = getattr(router, "list_skills", None)
    if not callable(list_skills):
        return capability_allowlist_from_skill_metadata(())
    try:
        return capability_allowlist_from_skill_metadata(list_skills())
    except Exception:
        logger.warning("Unable to build plugin workflow allowlist", exc_info=True)
        return capability_allowlist_from_skill_metadata(())


def _dotenv_value(value):
    text = str(value)
    if text == "" or any(ch.isspace() for ch in text) or any(
        ch in text for ch in "#'\"\\"
    ):
        return json.dumps(text, ensure_ascii=False)
    return text


def _persist_env_with_dotenv(env_path, values, keys_to_unset):
    """Persist through python-dotenv first so comments/order stay intact."""
    from dotenv import set_key, unset_key

    for key, value in values.items():
        set_key(str(env_path), key, str(value), quote_mode="auto")
    for key in keys_to_unset:
        unset_key(str(env_path), key)


def _persist_env_in_place(env_path, values, keys_to_unset):
    """Fallback writer for Windows replace/permission failures."""
    env_path = Path(env_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(exist_ok=True)
    try:
        env_path.chmod(env_path.stat().st_mode | stat.S_IWRITE)
    except OSError:
        pass

    existing_text = env_path.read_text(encoding="utf-8", errors="replace")
    lines = existing_text.splitlines()
    trailing_newline = existing_text.endswith(("\n", "\r"))
    remaining = {str(key): str(value) for key, value in values.items()}
    unset = {str(key) for key in keys_to_unset}
    updated_lines = []

    for line in lines:
        match = _ENV_ASSIGNMENT_RE.match(line)
        key = match.group(1) if match else None
        if key in unset:
            continue
        if key in remaining:
            prefix = "export " if line.lstrip().startswith("export ") else ""
            updated_lines.append(f"{prefix}{key}={_dotenv_value(remaining.pop(key))}")
        else:
            updated_lines.append(line)

    for key, value in remaining.items():
        if key not in unset:
            updated_lines.append(f"{key}={_dotenv_value(value)}")

    output = "\n".join(updated_lines)
    if updated_lines or trailing_newline:
        output += "\n"
    env_path.write_text(output, encoding="utf-8")

def _replace_session_agent(refreshed_agent):
    """Install a ready Agent, then release the replaced instance."""
    previous_agent = st.session_state.get("agent")
    active_chat_model = st.session_state.get("chat_active_model")
    if active_chat_model:
        # The selector is session-scoped. Rebuilding the Agent after saving
        # settings or moving DATA_ROOT must not make the visible model differ
        # from the gateway that will actually serve the next turn.
        from artpm_agent.views.chat_model_selector import apply_model_override_to_agent

        apply_model_override_to_agent(refreshed_agent, active_chat_model)
    st.session_state["agent"] = refreshed_agent
    st.session_state["db"] = refreshed_agent.database
    if previous_agent is None or previous_agent is refreshed_agent:
        return
    close_previous = getattr(previous_agent, "close", None)
    if callable(close_previous):
        try:
            close_previous()
        except Exception:
            logger.warning(
                "Failed to close the replaced Agent instance",
                exc_info=True,
            )


def _pick_directory() -> Optional[str]:
    """Open the OS native folder picker when available (local Streamlit)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        st.info("当前环境不支持系统文件选择器，请手动输入路径。")
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title="选择数据存储目录")
        root.destroy()
        return chosen or None
    except Exception as error:
        logger.warning(f"打开系统目录选择器失败: {error}")
        st.info("无法打开系统文件选择器，请手动输入路径。")
        return None


def _is_cloud_deployment() -> bool:
    return os.getenv("ARTPM_DEPLOYMENT_MODE", "local").strip().lower() in {
        "cloud",
        "container",
        "remote",
        "server",
    }


def _render_storage_settings():
    """让用户选择知识库与缓存的统一数据目录，并支持热重载与迁移。"""
    render_section_heading("数据目录")
    cfg = Config() if Config is not None else None
    current_root = resolve_data_root()
    db = (cfg.get("database", {}) if cfg is not None else {}) or {}
    managed_data_root = bool(os.getenv("DATA_ROOT", "").strip())

    with st.expander("目录明细", expanded=False):
        st.code(
            f"数据根目录: {current_root}\n"
            f"  会话库:    {db.get('conversation_db_path')}\n"
            f"  业务库:    {db.get('db_path')}\n"
            f"  记忆库:    {db.get('memory_db_path')}\n"
            f"  向量索引:  {db.get('vector_db_path')}\n"
            f"  Token统计: {current_root / 'token_usage.db'}\n"
            f"  轨迹库:    {current_root / 'episodes.db'}"
        )

    new_root = st.text_input(
        "数据根目录",
        value=str(current_root),
        key="storage_data_root_input",
        help="留空或恢复默认时使用项目内的 data 目录。",
        disabled=managed_data_root,
    ).strip()

    if managed_data_root:
        st.caption("当前目录由部署环境变量 DATA_ROOT 管理。")
    elif _is_cloud_deployment():
        st.caption("云端部署请填写服务端挂载路径；系统文件选择器仅在本地桌面模式提供。")
    else:
        browse_col, _ = st.columns([1, 3])
        with browse_col:
            if st.button(
                "选择目录",
                key="storage_browse",
                icon=":material/folder_open:",
                width="stretch",
            ):
                chosen = _pick_directory()
                if chosen:
                    st.session_state.storage_data_root_input = chosen
                    st.rerun()

    migrate = st.checkbox(
        "同时把现有数据迁移到新目录",
        value=True,
        key="storage_migrate",
        help="复制现有数据，旧目录保留。",
        disabled=managed_data_root,
    )

    save_col, reset_col = st.columns(2)
    with save_col:
        if st.button(
            "保存并重载",
            key="storage_save",
            type="primary",
            width="stretch",
            disabled=managed_data_root,
        ):
            _apply_data_root(new_root if new_root else None, migrate=migrate, reset=False)
    with reset_col:
        if st.button(
            "恢复默认目录",
            key="storage_reset",
            width="stretch",
            disabled=managed_data_root,
        ):
            _apply_data_root(None, migrate=False, reset=True)


def _apply_mcp_env_preview(
    mcp_enabled: bool,
    mcp_transport: str | None = None,
) -> None:
    """将 MCP 设置页当前填写的 URL/Key/开关即时写入 os.environ，
    使「刷新连接状态」按钮无需先保存即可测试新配置。
    注意：这只影响当前进程内存，不写 .env 文件。"""
    os.environ["MCP_ENABLED"] = "true" if mcp_enabled else "false"
    transport = str(
        mcp_transport
        or st.session_state.get("mcp_transport", "")
        or os.getenv("MCP_TRANSPORT", "stdio")
    ).strip().lower()
    if transport not in {"stdio", "http"}:
        transport = "stdio"
    os.environ["MCP_TRANSPORT"] = transport
    # 从 session_state 的 widget 值读取（st.text_input 在 rerun 前已写入）
    key = st.session_state.get("Skills Forge API Key", "")
    url = st.session_state.get("Skills Forge URL", "").strip()
    if key:
        os.environ["SKILLS_FORGE_KEY"] = key
    else:
        os.environ.pop("SKILLS_FORGE_KEY", None)
    if url:
        os.environ["SKILLS_FORGE_URL"] = url
    else:
        os.environ.pop("SKILLS_FORGE_URL", None)
    # 重置 unified mcp_client 单例，让下次 get 重建时读新 env
    from artpm_agent.core.mcp_client import reset_mcp_client
    reset_mcp_client()
    # 同时清除 agent 上缓存的 remote client，强制惰性重载
    if AVAILABLE and st.session_state.get("agent"):
        agent = st.session_state.agent
        unified = getattr(agent, "mcp_client", None)
        if unified:
            unified._remote = None
            unified._remote_failed = False


def _apply_data_root(new_root, *, migrate, reset):
    """Persist the chosen data root, optionally migrate data, then hot-reload."""
    import shutil

    old_root = resolve_data_root()
    previous_agent = st.session_state.get("agent")
    previous_factory = st.session_state.get("runtime_factory")
    factory_agent = getattr(previous_factory, "_agent", None)
    target = None
    if new_root:
        target = Path(new_root).expanduser()
        if not target.is_absolute():
            target = (
                Path(__file__).resolve().parent.parent.parent / new_root
            ).resolve()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            render_error_callback(
                build_error_info(error, context={"operation": "data_root_create"}),
                key="data_root_create_error",
                retry=False,
            )
            return
        if not os.access(str(target), os.W_OK):
            st.error(f"目录不可写：{target}")
            return

    if migrate and target and old_root != target and old_root.exists():
        try:
            shutil.copytree(str(old_root), str(target), dirs_exist_ok=True)
            st.toast(f"已迁移现有数据到 {target}")
        except Exception as error:
            logger.exception("迁移数据失败")
            render_error_callback(
                build_error_info(error, context={"operation": "data_root_migrate"}),
                key="data_root_migrate_error",
                retry=False,
            )
            return

    try:
        save_data_root(str(target) if target else None)
        reset_config()
    except OSError as error:
        logger.exception("保存数据目录失败")
        render_error_callback(
            build_error_info(error, context={"operation": "data_root_save"}),
            key="data_root_save_error",
            retry=False,
        )
        return

    # The MCP transport is process-global. Reset it before creating the first
    # replacement Agent; otherwise init_session would reuse the old client and
    # closing the previous Agent would also close the replacement's session.
    from artpm_agent.core.mcp_client import reset_mcp_client
    from artpm_agent.runtime.factory import reset_runtime_factory

    reset_mcp_client()
    reset_runtime_factory()
    if previous_agent is not None and previous_agent is not factory_agent:
        close_previous = getattr(previous_agent, "close", None)
        if callable(close_previous):
            try:
                close_previous()
            except Exception:
                logger.warning("关闭旧 Agent 失败", exc_info=True)

    # 清空会话内已缓存的存储实例，迫使按新目录重建。
    for key in (
        "conversation_store",
        "session_store",
        "chat_attachment_store",
        "artifact_generator",
        "workflow_store",
        "profile_store",
        "knowledge_store",
        "wiki_store",
        "agent",
        "runtime_factory",
        "event_bus",
        "episode_store",
        "consolidation_scheduler",
        "workflow_coordinator",
        "db",
        "messages_loaded_for",
        "_conv_cache",
        "mcp_skills_cache",
        "mcp_error_cache",
    ):
        st.session_state.pop(key, None)

    try:
        init_session()
    except Exception:
        logger.exception("重载存储失败")
        st.error("目录已切换，但重建存储失败，请重启服务。")
        return

    try:
        _load_knowledge_snapshot.clear()
    except Exception:
        pass

    _toast("数据目录已切换")
    st.rerun()


def persist_settings(config, env_path=None):
    """只更新受管理的配置项，保留 .env 中的注释和其它设置。"""
    env_path = (
        Path(env_path)
        if env_path
        else PROJECT_ENV_PATH
    )
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(exist_ok=True)
    keys_to_unset = set()
    values = {
        "LLM_PROVIDER": config["provider"],
        "LLM_MODEL": config["model"],
        "MCP_ENABLED": str(config["mcp_enabled"]).lower(),
        "MCP_TRANSPORT": (
            config.get("mcp_transport", "stdio")
            if config.get("mcp_transport", "stdio") in {"stdio", "http"}
            else "stdio"
        ),
        "SKILLS_FORGE_KEY": config["mcp_key"],
        "SKILLS_FORGE_URL": config["mcp_url"],
    }
    framework = str(config.get("framework", "") or "").strip().lower()
    if framework in {"langchain", "langchain-v1", "lc", "native"}:
        values["LLM_FRAMEWORK"] = "native" if framework == "native" else "langchain"
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
        "deepseek": "DEEPSEEK_API_KEY",
        "custom": "OPENAI_API_KEY",
    }[config["provider"]]
    provider_base = {
        "openai": "OPENAI_API_BASE",
        "anthropic": "ANTHROPIC_API_BASE",
        "zhipu": "ZHIPU_API_BASE",
        "deepseek": "DEEPSEEK_API_BASE",
        "custom": "OPENAI_API_BASE",
    }[config["provider"]]

    if config["api_key"]:
        values[provider_key] = config["api_key"]
    else:
        keys_to_unset.add(provider_key)
    if config["api_base_url"]:
        values[provider_base] = config["api_base_url"]
    else:
        keys_to_unset.add(provider_base)

    # Vision pairing ("eyes model"): optional dedicated vision model that
    # handles image requests independently of the text primary model.
    vision_provider = str(config.get("vision_provider", "") or "").strip().lower()
    vision_model = str(config.get("vision_model", "") or "").strip()
    vision_api_key = str(config.get("vision_api_key", "") or "").strip()
    vision_api_base = str(config.get("vision_api_base", "") or "").strip()
    vision_env = {
        "LLM_VISION_PROVIDER": vision_provider,
        "LLM_VISION_MODEL": vision_model,
        "LLM_VISION_API_KEY": vision_api_key,
        "LLM_VISION_API_BASE": vision_api_base,
    }
    for env_key, value in vision_env.items():
        if value:
            values[env_key] = value
        else:
            keys_to_unset.add(env_key)

    try:
        _persist_env_with_dotenv(env_path, values, keys_to_unset)
    except OSError:
        logger.warning("dotenv 写入失败，改用原地更新 .env", exc_info=True)
        _persist_env_in_place(env_path, values, keys_to_unset)

    for key, value in values.items():
        os.environ[key] = str(value)
    for key in keys_to_unset:
        os.environ.pop(key, None)
@st.cache_data(ttl=60, show_spinner=False)
def _load_knowledge_snapshot(
    tenant_id: str = "local",
    workspace_id: str = "local-default",
    principal_id: str = "",
):
    """缓存工作区知识库只读快照，避免设置页每次 rerun 都打向量库。"""
    knowledge_store = get_knowledge_store()
    if knowledge_store is None:
        return None

    # The knowledge tab is also the operator's proof that the learning loop is
    # alive.  Keep these counters sourced from the same durable stores used by
    # the turn pipeline; never infer learning from a model response string.
    learning = {
        "feedback": 0,
        "episodes": 0,
        "strategies": 0,
        "knowledge_gaps": 0,
        "last_reflection": None,
    }
    try:
        from artpm_agent.memory.feedback_store import get_default_feedback_store

        feedback_store = get_default_feedback_store()
        if feedback_store is not None:
            learning["feedback"] = len(
                feedback_store.active(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    principal_id=principal_id,
                )
            )
    except Exception:
        logger.exception("读取反馈统计失败")
    try:
        from artpm_agent.harness.outcome_recorder import default_episode_db_path
        from artpm_agent.memory.episode_store import EpisodeStore

        learning["episodes"] = EpisodeStore(default_episode_db_path()).count(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except Exception:
        logger.exception("读取回合经验统计失败")
    try:
        from artpm_agent.evolution.strategy_store import get_default_strategy_store

        strategy_store = get_default_strategy_store()
        if strategy_store is not None:
            learning["strategies"] = len(
                strategy_store.active(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
            )
    except Exception:
        logger.exception("读取进化策略统计失败")
    try:
        from artpm_agent.evolution.meta_memory import get_default_meta_memory_store

        learning["knowledge_gaps"] = len(
            get_default_meta_memory_store().top_gaps(
                limit=100,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                principal_id=principal_id,
            )
        )
    except Exception:
        logger.exception("读取知识缺口统计失败")
    try:
        from artpm_agent.evolution.scheduler import get_default_scheduler

        scheduler = get_default_scheduler()
        if scheduler is not None:
            learning["last_reflection"] = scheduler.last_run(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )[0]
    except Exception:
        logger.exception("读取复盘状态失败")

    return {
        "resources": knowledge_store.list_resources(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            limit=100,
        ),
        "active_rules": knowledge_store.get_active_rules(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            limit=100,
        ),
        "pending_ingestions": knowledge_store.list_ingestion_proposals(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            status="pending",
            limit=100,
        ),
        "pending_rules": knowledge_store.list_rules(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            status="proposed",
            limit=100,
        ),
        "learning": learning,
    }


def settings_page():
    """设置页面"""
    st.markdown('<div class="settings-page-marker"></div>', unsafe_allow_html=True)
    render_page_header("系统 / 设置", "设置")
    st.markdown(
        '<div class="settings-intro">把常用配置放在前面，其他选项按需展开。改动保存后会立即应用到当前会话。</div>',
        unsafe_allow_html=True,
    )

    # 远端状态只在用户主动刷新时检测，避免打开设置页就产生网络等待。
    mcp_skills = st.session_state.get("mcp_skills_cache", [])
    mcp_error = st.session_state.get("mcp_error_cache", None)
    mcp_error_category = st.session_state.get("mcp_error_category_cache", None)
    mcp_market = st.session_state.get("mcp_market_cache", [])
    configured_mcp_enabled = os.getenv("MCP_ENABLED", "false").lower() == "true"
    refresh_mcp = bool(st.session_state.get("mcp_skills_refresh"))
    if not configured_mcp_enabled and not refresh_mcp:
        mcp_skills, mcp_error, mcp_error_category, mcp_market = [], None, None, []
        st.session_state.mcp_skills_cache = []
        st.session_state.mcp_error_cache = None
        st.session_state.mcp_error_category_cache = None
        st.session_state.mcp_market_cache = []
    elif refresh_mcp:
        mcp_skills, mcp_error, mcp_error_category, mcp_market = [], None, None, []
        if AVAILABLE and st.session_state.get("agent"):
            try:
                mcp_client = getattr(st.session_state.agent, "mcp_client", None)
                if mcp_client:
                    # 用 ping() 做轻量连通性测试（不拉全量技能列表）
                    if hasattr(mcp_client, "ping"):
                        ok, msg = mcp_client.ping()
                        if ok:
                            mcp_skills = mcp_client.list_skills()
                            # 拉市场技能清单（list_market_skills 带 TTL 缓存，
                            # 仅首次/强制刷新才真正打云端拉 88 个技能）
                            if hasattr(mcp_client, "list_market_skills"):
                                try:
                                    mcp_market = asyncio.run(
                                        mcp_client.list_market_skills()
                                    )
                                except Exception as e:
                                    logger.warning("拉取市场技能失败: %s", e, exc_info=True)
                                    mcp_market = []
                        else:
                            mcp_error = msg
                            mcp_error_category = getattr(
                                mcp_client, "last_error_category", None
                            )
                    elif mcp_client.enabled:
                        mcp_skills = mcp_client.list_skills()
                    else:
                        # 未启用但有 remote client 实例 → 取诊断信息
                        diag = getattr(mcp_client, "last_error", None)
                        mcp_error = diag or "Skills Forge 未启用"
                        mcp_error_category = getattr(
                            mcp_client, "last_error_category", None
                        )
            except Exception as error:
                logger.warning("MCP 状态检查失败: %s", error, exc_info=True)
                mcp_error = "connection_check_failed"
                mcp_error_category = "unknown"
        elif not AVAILABLE:
            mcp_error = "Agent 运行时未就绪"
            mcp_error_category = "runtime"
        st.session_state.mcp_skills_cache = mcp_skills
        st.session_state.mcp_error_cache = mcp_error
        st.session_state.mcp_error_category_cache = mcp_error_category
        st.session_state.mcp_market_cache = mcp_market
        st.session_state.mcp_skills_refresh = False
    elif "mcp_skills_cache" not in st.session_state:
        st.session_state.mcp_skills_cache = []
        st.session_state.mcp_error_cache = None
        st.session_state.mcp_error_category_cache = None
        st.session_state.mcp_market_cache = []

    provider_options = ["openai", "anthropic", "zhipu", "deepseek", "custom"]
    configured_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    provider_index = (
        provider_options.index(configured_provider)
        if configured_provider in provider_options
        else 0
    )

    configured_model_summary = os.getenv("LLM_MODEL", "尚未选择") or "尚未选择"
    data_root_summary = os.getenv("DATA_ROOT", "./data") or "./data"
    mcp_summary = "已开启" if configured_mcp_enabled else "未开启"
    st.markdown(
        (
            '<div class="settings-summary" role="status">'
            '<div class="settings-summary-item">'
            '<span class="settings-summary-label">当前模型</span>'
            f'<strong>{escape(configured_model_summary)}</strong>'
            f'<small>{escape(configured_provider.title())}</small>'
            '</div>'
            '<div class="settings-summary-item">'
            '<span class="settings-summary-label">远程工具</span>'
            f'<strong>{escape(mcp_summary)}</strong>'
            '<small>Skills Forge</small>'
            '</div>'
            '<div class="settings-summary-item">'
            '<span class="settings-summary-label">数据目录</span>'
            f'<strong>{escape(data_root_summary)}</strong>'
            '<small>本地资料与运行记录</small>'
            '</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )

    # Keep the six capability areas separate, but use short task-oriented labels
    # so the tab row reads as a simple checklist instead of a feature catalog.
    model_tab, mcp_tab, workflow_tab, knowledge_tab, storage_tab, identity_tab = st.tabs(
        ["模型", "连接", "自动化", "知识", "数据", "助手"]
    )
    with model_tab:
        st.markdown(
            '<div class="settings-tab-lead"><strong>模型</strong><span>选择回答引擎和默认模型。通常只需要填写提供商、Key 和模型。</span></div>',
            unsafe_allow_html=True,
        )
        framework_options = ["langchain", "native"]
        configured_framework = os.getenv("LLM_FRAMEWORK", "langchain").lower()
        if configured_framework not in framework_options:
            configured_framework = "langchain"
        with st.expander("高级：模型框架", expanded=False, icon=":material/tune:"):
            framework = st.selectbox(
                "模型框架",
                framework_options,
                index=framework_options.index(configured_framework),
                format_func=lambda value: (
                    "LangChain v1" if value == "langchain" else "原生 SDK"
                ),
                key="llm_framework",
            )
        llm_provider = st.selectbox(
            "LLM 提供商",
            provider_options,
            index=provider_index,
            key="llm_provider",
        )
        provider_key_names = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "zhipu": "ZHIPU_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "custom": "OPENAI_API_KEY",
        }
        provider_base_names = {
            "openai": "OPENAI_API_BASE",
            "anthropic": "ANTHROPIC_API_BASE",
            "zhipu": "ZHIPU_API_BASE",
            "deepseek": "DEEPSEEK_API_BASE",
            "custom": "OPENAI_API_BASE",
        }
        defaults = {
            "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
            "anthropic": ("https://api.anthropic.com", "claude-3-5-sonnet-20241022"),
            "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4"),
            "deepseek": ("https://api.deepseek.com", "deepseek-chat"),
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
            and llm_provider in {"openai", "custom", "zhipu", "deepseek"}
        )
        can_sync_models = supports_model_sync and bool(api_key.strip())
        sync_feedback = None
        sync_col, sync_meta_col = st.columns([1.2, 3.8], vertical_alignment="bottom")
        with sync_col:
            sync_requested = st.button(
                "同步模型",
                key=f"sync_models_{llm_provider}",
                icon=":material/sync:",
                disabled=not can_sync_models,
                width="stretch",
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
                st.caption("模型同步不可用")
            elif not supports_model_sync:
                st.caption("当前提供商不支持模型同步")
            elif not api_key.strip():
                st.caption("填写 API Key 后可同步")
            else:
                st.caption("尚未同步模型列表")
        if sync_feedback:
            feedback_type, feedback_message = sync_feedback
            if feedback_type == "success":
                _toast(feedback_message)
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

        # ── 视觉模型搭配（"眼睛模型"）──
        # 主模型（如 DeepSeek）不支持图片时，图片请求自动路由到这个独立视觉
        # 模型（例如智谱免费 GLM-4V-Flash），文本与视觉可来自不同供应商。
        st.divider()
        st.markdown(
            '<div class="settings-tab-lead"><strong>视觉模型搭配（眼睛模型）</strong>'
            '<span>可选：为不支持图片的主模型补上图片识别能力。</span></div>',
            unsafe_allow_html=True,
        )
        vision_enabled = st.toggle(
            "启用视觉模型搭配",
            value=bool(os.getenv("LLM_VISION_MODEL", "").strip()),
            key="vision_enabled_toggle",
            help="关闭后图片请求回到主模型路径，并清空 LLM_VISION_* 配置",
        )
        if vision_enabled:
            vision_provider_options = ["zhipu", "openai", "deepseek", "custom"]
            configured_vision_provider = os.getenv("LLM_VISION_PROVIDER", "").lower()
            vision_provider_index = (
                vision_provider_options.index(configured_vision_provider)
                if configured_vision_provider in vision_provider_options
                else 0
            )
            vision_provider = st.selectbox(
                "视觉提供商",
                options=vision_provider_options,
                index=vision_provider_index,
                key="vision_provider",
            )
            vision_model = st.text_input(
                "视觉模型 ID",
                value=os.getenv("LLM_VISION_MODEL", ""),
                placeholder="glm-4v-flash",
                key="vision_model_input",
                help="例如智谱 GLM-4V-Flash（免费）：glm-4v-flash",
            ).strip()
            vision_api_key = st.text_input(
                "视觉 API Key",
                type="password",
                value=os.getenv("LLM_VISION_API_KEY", ""),
                placeholder="LLM_VISION_API_KEY；留空则用该供应商的默认 Key",
                key="vision_api_key_input",
            ).strip()
            vision_api_base = st.text_input(
                "视觉 API Base URL",
                value=os.getenv("LLM_VISION_API_BASE", ""),
                placeholder={
                    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
                    "openai": "https://api.openai.com/v1",
                    "deepseek": "https://api.deepseek.com",
                    "custom": "https://api.example.com/v1",
                }.get(vision_provider, ""),
                key="vision_api_base_input",
            ).strip()
        else:
            vision_provider = ""
            vision_model = ""
            vision_api_key = ""
            vision_api_base = ""

    with mcp_tab:
        st.markdown(
            '<div class="settings-tab-lead"><strong>连接</strong><span>需要远程工具时再打开；不开启也不影响本地报价、分析和项目管理。</span></div>',
            unsafe_allow_html=True,
        )
        mcp_enabled = st.toggle(
            "启用远程 Skills Forge",
            value=os.getenv("MCP_ENABLED", "false").lower() == "true",
        )
        transport_options = ["stdio", "http"]
        configured_transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
        if configured_transport not in transport_options:
            configured_transport = "stdio"
        mcp_transport = st.selectbox(
            "连接协议",
            options=transport_options,
            index=transport_options.index(configured_transport),
            key="mcp_transport",
            format_func=lambda value: (
                "MCP stdio" if value == "stdio" else "HTTP REST"
            ),
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
            disabled=mcp_transport == "stdio",
        ).strip()
        can_refresh_mcp = _mcp_refresh_ready(
            enabled=mcp_enabled,
            transport=mcp_transport,
            api_key=mcp_key,
            url=mcp_url,
        )
        mcp_config_changed = (
            mcp_enabled != configured_mcp_enabled
            or mcp_transport != os.getenv("MCP_TRANSPORT", "stdio").lower()
            or mcp_key != os.getenv("SKILLS_FORGE_KEY", "")
            or (
                mcp_transport == "http"
                and mcp_url != os.getenv("SKILLS_FORGE_URL", "").strip()
            )
        )
        if st.button(
            "刷新连接状态",
            key="refresh_mcp_status",
            icon=":material/refresh:",
            disabled=not can_refresh_mcp,
        ):
            _apply_mcp_env_preview(mcp_enabled, mcp_transport)
            st.session_state.mcp_skills_refresh = True
            st.rerun()

        if not mcp_enabled:
            st.caption("状态：未启用")
        elif not can_refresh_mcp:
            missing = "API Key" if not mcp_key.strip() else "服务地址"
            st.caption(f"填写 {missing} 后可检查连接")
        elif mcp_config_changed:
            st.caption("状态：当前配置尚未检查")
        elif mcp_error:
            st.warning(
                _mcp_status_message(
                    enabled=True,
                    error_category=mcp_error_category,
                )
            )
        elif mcp_skills:
            transport_label = "MCP stdio" if mcp_transport == "stdio" else "HTTP REST"
            market_count = len(mcp_market)
            count_text = f"{len(mcp_skills)} 个工具"
            if market_count:
                count_text += f"，{market_count} 个技能"
            st.success(f"已连接 · {transport_label} · {count_text}")
            skill_rows = [
                {
                    "技能": skill.get("name", "未命名"),
                    "说明": skill.get("description", "—"),
                }
                for skill in mcp_skills
            ]
            st.dataframe(
                pd.DataFrame(skill_rows),
                width="stretch",
                hide_index=True,
                height=min(280, 38 + len(skill_rows) * 36),
                key="mcp_skills_table",
            )
        else:
            st.caption("状态：尚未检查")

    with workflow_tab:
        st.markdown(
            '<div class="settings-tab-lead"><strong>自动化</strong><span>把重复工作串起来。没有明确需求时可以保持默认。</span></div>',
            unsafe_allow_html=True,
        )
        workflow_store = get_workflow_store()
        reset_widget_state = st.session_state.pop(
            "reset_workflow_widget_state", False
        )
        if reset_widget_state:
            for state_key in list(st.session_state):
                if state_key.startswith(("workflow_enabled_", "workflow_priority_")):
                    del st.session_state[state_key]
            _toast("工作流已恢复默认")
        if workflow_store is None:
            st.error("工作流运行时未就绪，请查看服务日志。")
        else:
            tenant_context, workflow_profile_id = _trusted_workflow_scope()
            workflow_workspace_id = tenant_context.workspace_id
            workflow_capability_allowlist = _workflow_capability_allowlist()
            with st.expander(
                "可视化编排",
                expanded=True,
                icon=":material/account_tree:",
            ):
                render_workflow_designer(
                    workflow_store,
                    workspace_id=workflow_workspace_id,
                    profile_id=workflow_profile_id,
                    capability_allowlist=workflow_capability_allowlist,
                )
            workflow_definitions = workflow_store.list_definitions(
                workspace_id=workflow_workspace_id,
                profile_id=workflow_profile_id,
            )
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
                    width="stretch",
                )
            with reset_flow_col:
                reset_workflows = st.button(
                    "恢复默认",
                    key="reset_workflow_settings",
                    width="stretch",
                )
            if save_workflows:
                try:
                    for definition, enabled, priority in workflow_values:
                        workflow_store.set_override(
                            WorkflowOverride(
                                workflow_id=definition.id,
                                workflow_version=definition.version,
                                workspace_id=workflow_workspace_id,
                                profile_id=workflow_profile_id,
                                enabled=enabled,
                                priority=priority,
                            )
                        )
                    _toast("工作流设置已保存")
                except Exception as error:
                    logger.exception("保存工作流设置失败")
                    render_error_callback(
                        build_error_info(
                            error,
                            context={"operation": "workflow_settings_save"},
                        ),
                        key="workflow_settings_save_error",
                        retry=False,
                    )
            if reset_workflows:
                try:
                    for definition in workflow_definitions:
                        workflow_store.clear_override(
                            definition.id,
                            version=definition.version,
                            workspace_id=workflow_workspace_id,
                            profile_id=workflow_profile_id,
                        )
                    st.session_state.reset_workflow_widget_state = True
                    st.rerun()
                except Exception as error:
                    logger.exception("恢复默认工作流失败")
                    render_error_callback(
                        build_error_info(
                            error,
                            context={"operation": "workflow_settings_reset"},
                        ),
                        key="workflow_settings_reset_error",
                        retry=False,
                    )

    with knowledge_tab:
        st.markdown(
            '<div class="settings-tab-lead"><strong>知识</strong><span>查看工作区资料和已确认的规则，内容会在对话中被优先参考。</span></div>',
            unsafe_allow_html=True,
        )
        render_wiki_workspace(on_changed=_load_knowledge_snapshot.clear)
        st.divider()
        tenant_context, _ = _trusted_workflow_scope()
        snapshot = _load_knowledge_snapshot(
            tenant_id=tenant_context.tenant_id,
            workspace_id=tenant_context.workspace_id,
            principal_id=tenant_context.principal_id,
        )
        if snapshot is None:
            st.error("工作区知识库未就绪，请查看服务日志。")
        else:
            resources = snapshot["resources"]
            active_rules = snapshot["active_rules"]
            pending_ingestions = snapshot["pending_ingestions"]
            pending_rules = snapshot["pending_rules"]
            learning = snapshot.get("learning", {})
            st.caption(
                f"资料 {len(resources)} · 已采纳规则 {len(active_rules)} · "
                f"待确认 {len(pending_ingestions) + len(pending_rules)}"
            )
            st.markdown("#### 知识与记录")
            metric_cols = st.columns(4)
            with metric_cols[0]:
                st.metric("资料", len(resources))
            with metric_cols[1]:
                st.metric("反馈", int(learning.get("feedback", 0)))
            with metric_cols[2]:
                st.metric("回合记录", int(learning.get("episodes", 0)))
            with metric_cols[3]:
                st.metric("策略", int(learning.get("strategies", 0)))
            last_reflection = learning.get("last_reflection")
            gap_count = int(learning.get("knowledge_gaps", 0))
            reflection_text = last_reflection or "无"
            st.caption(f"知识缺口 {gap_count} 条 · 最近复盘 {reflection_text}")
            if resources:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "资料": resource["title"],
                                "类型": resource["resource_type"],
                                "版本": resource["current_version"],
                                "来源": (
                                    (resource.get("source") or {}).get("type")
                                    or resource.get("source_type")
                                    or "未知"
                                ),
                            }
                            for resource in resources
                        ]
                    ),
                    width="stretch",
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
                    width="stretch",
                    hide_index=True,
                    key="knowledge_rules_table",
                )
            if not resources and not active_rules:
                st.info("当前工作区还没有已确认的资料或规则。")

    with storage_tab:
        st.markdown(
            '<div class="settings-tab-lead"><strong>数据</strong><span>管理本地资料、缓存和运行记录的保存位置。</span></div>',
            unsafe_allow_html=True,
        )
        _render_storage_settings()

    with identity_tab:
        st.markdown(
            '<div class="settings-tab-lead"><strong>助手</strong><span>设置助手的称呼、角色和回答方式，让它更像你的项目搭档。</span></div>',
            unsafe_allow_html=True,
        )
        current_profile = get_current_profile()
        if current_profile is not None:
            identity_defaults = current_profile.identity
            st.caption(f"当前配置版本：v{current_profile.revision}")
        else:
            identity_defaults = AgentIdentity() if AgentIdentity else None

        render_section_heading("身份配置")
        identity_name = st.text_input(
            "名称",
            value=getattr(identity_defaults, "display_name", "ArtPM 助手"),
        ).strip()
        identity_role = st.text_input(
            "角色",
            value=getattr(identity_defaults, "role", "游戏美术项目管理"),
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

    if st.button("保存模型、连接与身份", type="primary", key="save_settings"):
        if not model.strip():
            st.error("请从模型列表选择模型，或填写手动模型 ID。")
            return
        if not identity_name or not identity_role or not identity_domain:
            st.error("名称、角色和业务领域不能为空。")
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
            "framework": framework,
            "api_key": api_key.strip(),
            "api_base_url": api_base_url,
            "available_models": available_models,
            "models_synced_at": st.session_state.get(synced_at_key, ""),
            "mcp_enabled": mcp_enabled,
            "mcp_transport": mcp_transport,
            "mcp_key": mcp_key.strip(),
            "mcp_url": mcp_url,
            "vision_provider": vision_provider,
            "vision_model": vision_model,
            "vision_api_key": vision_api_key,
            "vision_api_base": vision_api_base,
        }
        try:
            persist_settings(settings)
        except Exception as error:
            logger.exception("保存配置失败")
            render_error_callback(
                build_error_info(error, context={"operation": "settings_save"}),
                key="settings_save_error",
                retry=False,
            )
            return

        # 保存后即时将 MCP 配置刷入进程环境，让后续 ping() 读到新值
        _apply_mcp_env_preview(mcp_enabled, mcp_transport)

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
                render_error_callback(
                    build_error_info(error, context={"operation": "profile_save"}),
                    key="profile_save_error",
                    retry=False,
                )
                return

        try:
            reset_config()
            runtime_factory = get_ui_runtime_factory()
            runtime_factory.reset_agent()
            for key in (
                "agent",
                "workflow_coordinator",
            ):
                st.session_state.pop(key, None)
            refreshed_agent = runtime_factory.agent()
            _replace_session_agent(refreshed_agent)
        except Exception as error:
            logger.exception("配置已保存，但当前运行实例刷新失败")
            render_error_callback(
                build_error_info(error, context={"operation": "agent_refresh"}),
                key="agent_refresh_error",
                retry=False,
            )
            return

        profile_version = (
            f"，身份配置 v{applied_profile.revision}"
            if applied_profile is not None
            else ""
        )
        st.success(
            f"配置已保存并应用，当前模型：{model.strip()}"
            f"{profile_version}"
        )
