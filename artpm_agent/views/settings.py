# ruff: noqa: E402,F405 - Streamlit may execute this page as a standalone script
"""设置页面（从 app.py 拆分）。"""
import sys
import json
import os
import re
import stat
from pathlib import Path
from typing import Optional

# 确保项目根目录在 Python 路径中（Streamlit 以多页面方式加载本文件时也能找到包）
# 先 resolve(__file__) 为绝对路径再取 parent，避免 __file__ 为相对路径时多退一层目录。
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from artpm_agent.ui_helpers import *  # noqa: F401,F403
from artpm_agent.config import PROJECT_ENV_PATH, resolve_data_root, save_data_root, reset_config

# 显式导入 Agent / Config，避免降级态（核心模块导入失败时）下
# 依赖通配导入拿不到名字而触发 NameError。
try:
    from artpm_agent.agent import ArtPMAgent
    from artpm_agent.config import Config
except Exception:
    ArtPMAgent = None
    Config = None

MANUAL_MODEL_OPTION = "手动输入模型 ID"
_ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


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


def _render_storage_settings():
    """让用户选择知识库与缓存的统一数据目录，并支持热重载与迁移。"""
    render_section_heading("数据存储目录", "知识库与缓存统一存放处")
    cfg = Config() if Config is not None else None
    current_root = resolve_data_root()
    db = (cfg.get("database", {}) if cfg is not None else {}) or {}

    st.caption(
        "所有本地知识库、向量索引、会话/业务数据库与运行缓存都会写入这个目录。"
        "修改后点击「保存并重载」，应用会在不重启进程的情况下切换到新目录。"
    )

    with st.expander("查看当前目录结构", expanded=False):
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
        help="知识库与所有缓存将存放于此。留空或「恢复默认目录」会使用项目内的 data/ 目录。",
    ).strip()

    browse_col, _ = st.columns([1, 3])
    with browse_col:
        if st.button("📂 浏览…", key="storage_browse", use_container_width=True):
            chosen = _pick_directory()
            if chosen:
                st.session_state.storage_data_root_input = chosen
                st.rerun()

    migrate = st.checkbox(
        "同时把现有数据迁移到新目录",
        value=True,
        key="storage_migrate",
        help="勾选后，当前目录下的数据库与缓存会被复制到新目录（旧目录保留、不删除）。",
    )

    save_col, reset_col = st.columns(2)
    with save_col:
        if st.button("保存并重载", key="storage_save", type="primary", use_container_width=True):
            _apply_data_root(new_root if new_root else None, migrate=migrate, reset=False)
    with reset_col:
        if st.button("恢复默认目录", key="storage_reset", use_container_width=True):
            _apply_data_root(None, migrate=False, reset=True)


def _apply_mcp_env_preview(mcp_enabled: bool) -> None:
    """将 MCP 设置页当前填写的 URL/Key/开关即时写入 os.environ，
    使「刷新连接状态」按钮无需先保存即可测试新配置。
    注意：这只影响当前进程内存，不写 .env 文件。"""
    os.environ["MCP_ENABLED"] = "true" if mcp_enabled else "false"
    # 从 session_state 的 widget 值读取（st.text_input 在 rerun 前已写入）
    key = st.session_state.get("Skills Forge API Key", "")
    url = st.session_state.get("Skills Forge URL", "").strip()
    if key:
        os.environ["SKILLS_FORGE_KEY"] = key
    if url:
        os.environ["SKILLS_FORGE_URL"] = url
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
            st.error(f"无法创建目录 {target}：{error}")
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
            st.error(f"迁移现有数据失败：{error}；未切换目录。")
            return

    save_data_root(str(target) if target else None)
    reset_config()

    # 清空会话内已缓存的存储实例，迫使按新目录重建。
    for key in (
        "conversation_store",
        "chat_attachment_store",
        "artifact_generator",
        "workflow_store",
        "profile_store",
        "knowledge_store",
        "agent",
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

    if ArtPMAgent is not None and Config is not None:
        try:
            refreshed = ArtPMAgent(Config())
            _replace_session_agent(refreshed)
        except Exception:
            logger.exception("重建 Agent 失败")

    try:
        _load_knowledge_snapshot.clear()
    except Exception:
        pass

    st.success("数据存储目录已切换并重新加载。")
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
def _load_knowledge_snapshot():
    """缓存工作区知识库只读快照，避免设置页每次 rerun 都打向量库。"""
    knowledge_store = get_knowledge_store()
    if knowledge_store is None:
        return None
    return {
        "resources": knowledge_store.list_resources(limit=100),
        "active_rules": knowledge_store.get_active_rules(limit=100),
        "pending_ingestions": knowledge_store.list_ingestion_proposals(
            status="pending", limit=100
        ),
        "pending_rules": knowledge_store.list_rules(status="proposed", limit=100),
    }


def settings_page():
    """设置页面"""
    render_page_header("系统 / 设置", "设置", "模型、工具与业务参数")

    # MCP 连接状态：仅首次进入或显式刷新时检测，避免每次 rerun 打远端。
    mcp_skills = st.session_state.get("mcp_skills_cache", [])
    mcp_error = st.session_state.get("mcp_error_cache", None)
    if st.session_state.get("mcp_skills_refresh") or "mcp_skills_cache" not in st.session_state:
        mcp_skills, mcp_error = [], None
        if AVAILABLE and st.session_state.get("agent"):
            try:
                mcp_client = getattr(st.session_state.agent, "mcp_client", None)
                if mcp_client:
                    # 用 ping() 做轻量连通性测试（不拉全量技能列表）
                    if hasattr(mcp_client, "ping"):
                        ok, msg = mcp_client.ping()
                        if ok:
                            mcp_skills = mcp_client.list_skills()
                        else:
                            mcp_error = msg
                    elif mcp_client.enabled:
                        mcp_skills = mcp_client.list_skills()
                    else:
                        # 未启用但有 remote client 实例 → 取诊断信息
                        diag = getattr(mcp_client, "last_error", None)
                        mcp_error = diag or "Skills Forge 未启用"
            except Exception as error:
                logger.warning("MCP 状态检查失败: %s", error, exc_info=True)
                mcp_error = f"Skills Forge 状态检查异常: {error}"
        elif not AVAILABLE:
            mcp_error = "Agent 运行时未就绪"
        st.session_state.mcp_skills_cache = mcp_skills
        st.session_state.mcp_error_cache = mcp_error
        st.session_state.mcp_skills_refresh = False

    provider_options = ["openai", "anthropic", "zhipu", "custom"]
    configured_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    provider_index = (
        provider_options.index(configured_provider)
        if configured_provider in provider_options
        else 0
    )

    model_tab, mcp_tab, workflow_tab, knowledge_tab, storage_tab, business_tab = st.tabs(
        ["模型", "工具与连接", "工作流", "知识", "存储", "Agent"]
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
            st.error(mcp_error)
            # 如果错误类别是 url/parse，额外给一个可操作的提示
            if AVAILABLE and st.session_state.get("agent"):
                mc = getattr(st.session_state.agent, "mcp_client", None)
                cat = getattr(mc, "last_error_category", None) if mc else None
                if cat in ("url", "parse"):
                    st.caption(
                        "💡 **常见原因**：URL 填的是网站前台地址（如 `skillsforge.xyz`），"
                        "而不是 API 服务端地址。请尝试改为 `https://api.skillsforge.xyz` "
                        "或服务方提供的 API 地址。"
                    )
        elif mcp_skills:
            transport = os.getenv("MCP_TRANSPORT", "http").lower()
            transport_label = "stdio (npx)" if transport == "stdio" else "HTTP REST"
            st.success(f"Skills Forge 已连接（{transport_label}），{len(mcp_skills)} 个技能可用")
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
            st.info("Skills Forge 当前未连接。保存有效配置并点击「刷新连接状态」测试。")
        mcp_enabled = st.toggle(
            "启用远程 Skills Forge",
            value=os.getenv("MCP_ENABLED", "false").lower() == "true",
        )
        if st.button(
            "刷新连接状态",
            key="refresh_mcp_status",
            icon=":material/refresh:",
            help="重新检测 Skills Forge 连接（用当前填写的 URL 和 Key）",
        ):
            # 先把界面上的值写进 os.environ，让 ping() 能读到最新的配置
            _apply_mcp_env_preview(mcp_enabled)
            st.session_state.mcp_skills_refresh = True
            st.rerun()
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
            help=(
                "HTTP REST 模式使用此地址。当前若为 stdio 模式，"
                "连接由 npx 包 @skills-forge/mcp-server 内部负责，此字段不参与连接。"
            ),
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
        snapshot = _load_knowledge_snapshot()
        if snapshot is None:
            st.error("工作区知识库未就绪，请查看服务日志。")
        else:
            resources = snapshot["resources"]
            active_rules = snapshot["active_rules"]
            pending_ingestions = snapshot["pending_ingestions"]
            pending_rules = snapshot["pending_rules"]
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

    with storage_tab:
        _render_storage_settings()

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
            value=getattr(identity_defaults, "display_name", "ArtPM 助手"),
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

        # 保存后即时将 MCP 配置刷入进程环境，让后续 ping() 读到新值
        _apply_mcp_env_preview(mcp_enabled)

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
            if ArtPMAgent is None or Config is None:
                st.warning("配置已保存；当前 Agent 实例未能刷新（依赖未加载），重启服务后生效。")
            else:
                refreshed_agent = ArtPMAgent(Config())
                _replace_session_agent(refreshed_agent)
            profile_version = (
                f"，Agent 配置 v{applied_profile.revision}"
                if applied_profile is not None
                else ""
            )
            st.success(
                f"配置已保存并应用，当前模型：{model.strip()}"
                f"{profile_version}"
            )
        except Exception:
            logger.exception("配置已保存，但当前 Agent 刷新失败")
            st.warning("配置已保存；当前会话刷新失败，重启服务后生效。")
