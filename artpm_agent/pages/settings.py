"""设置页面（从 app.py 拆分）。"""
from ui_helpers import *  # noqa: F401,F403

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
