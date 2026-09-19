import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from artpm_agent.memory import SessionStore
from artpm_agent.profiles import AgentProfilePatch, QuotePolicyPatch
from artpm_agent.ui_style import STYLE_CSS
from artpm_agent.utils.chat_attachments import (
    DEFAULT_ALLOWED_EXTENSIONS,
    DEFAULT_MAX_FILE_SIZE,
)
from artpm_agent.views.chat import _compact_legacy_assistant_copy

# Resolve the app entrypoint from this file's location so AppTest.from_file
# works regardless of the current working directory.
APP_FILE = str(Path(__file__).resolve().parent.parent / "artpm_agent" / "app.py")
LEGACY_APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")


class StubAgent:
    def chat(self, message, context=None):
        assert "报价10万" in message
        return "利润分析结果：报价 10 万元，成本 6 万元，毛利 4 万元。"


class EmptyStubAgent:
    def chat(self, message, context=None):
        return None


class TimeoutStubAgent:
    def __init__(self):
        self.config = SimpleNamespace(
            get=lambda key, default=None: (
                "deepseek-v4-flash" if key == "llm.model" else default
            )
        )

    def chat(self, message, context=None):
        try:
            raise TimeoutError("provider read timed out")
        except TimeoutError as error:
            raise RuntimeError("模型请求失败") from error


class BusyStubAgent(TimeoutStubAgent):
    def chat(self, message, context=None):
        try:
            raise RuntimeError("ResourceExhausted: Worker local total request limit")
        except RuntimeError as error:
            raise RuntimeError("模型请求失败") from error


class RetryAgent:
    def __init__(self):
        self.calls = 0
        self.config = SimpleNamespace(
            get=lambda key, default=None: (
                "deepseek-v4-flash" if key == "llm.model" else default
            )
        )

    def chat(self, message, context=None):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("provider timed out")
        return "重试成功"


class RecordingAgent:
    def __init__(self):
        self.calls = []

    def chat(self, message, context=None):
        self.calls.append({"message": message, "context": context or {}})
        return f"回答：{message}"


class StreamingAgent(RecordingAgent):
    def stream_chat(self, message, context=None):
        self.calls.append({"message": message, "context": context or {}})
        yield "流式"
        yield "回答"


class FallbackRecordingAgent(RecordingAgent):
    def __init__(self):
        super().__init__()
        self.last_response_model = "deepseek-v4-pro"
        self.config = SimpleNamespace(
            get=lambda key, default=None: (
                "deepseek-v4-flash" if key == "llm.model" else default
            )
        )


class ArtifactPlanLLM:
    def chat(self, message, system_prompt=None, history=None):
        assert "生成" in message
        assert "xlsx" in system_prompt
        return (
            '{"format":"xlsx","filename":"项目清单.xlsx",'
            '"table":{"sheet_name":"项目",'
            '"columns":["任务","负责人"],'
            '"rows":[["角色建模","小李"]]}}'
        )


class ArtifactAgent:
    def __init__(self):
        self.llm_client = ArtifactPlanLLM()

    def chat(self, message, context=None):
        raise AssertionError("artifact request should not fall through to chat")


class LocalArtifactAgent:
    def chat(self, message, context=None):
        raise AssertionError("explicit artifact request should stay local")


def message_contents(app):
    return [message["content"] for message in app.session_state["messages"]]


def test_legacy_root_entrypoint_renders_the_application():
    app = AppTest.from_file(LEGACY_APP_FILE).run(timeout=30)

    assert not app.exception
    assert app.chat_input(key="chat_input") is not None


def test_chat_composer_upload_contract_matches_backend_limits():
    app = AppTest.from_file(APP_FILE).run(timeout=30)

    composer = app.chat_input(key="chat_input")

    assert composer.proto.accept_file
    assert not composer.proto.accept_audio
    assert composer.proto.audio_sample_rate == 16000
    assert composer.proto.max_upload_size_mb == DEFAULT_MAX_FILE_SIZE // (1024 * 1024)
    assert set(composer.proto.file_type) == {
        f".{extension}" for extension in DEFAULT_ALLOWED_EXTENSIONS
    }


def test_voice_callback_renders_for_the_active_conversation_only():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    conversation_id = app.session_state["active_conversation_id"]
    app.session_state["voice_callback"] = {
        "state": "playing",
        "conversation_id": conversation_id,
        "turn_id": "turn-voice",
        "provider": "minimax",
        "detail": "",
        "updated_at": time.time(),
    }

    app.run(timeout=30)

    callbacks = [
        item.value for item in app.markdown if 'class="pm-voice-callback ' in item.value
    ]
    assert len(callbacks) == 1
    assert "正在播放语音回复" in callbacks[0]
    assert "MiniMax" in callbacks[0]
    assert 'aria-live="polite"' in callbacks[0]


def test_legacy_model_copy_is_compacted_for_display():
    old_runtime_copy = (
        "当前配置的生成模型 ID 是 **`deepseek-v4-flash`**，通过"
        " **第三方 OpenAI 兼容服务** 接入；连接状态：**已配置**。\n\n"
        "需要生成式回答时，ArtPM 会实际调用该模型，并携带当前会话的上下文。"
    )
    old_fallback_copy = (
        "默认模型 `deepseek-v4-flash` 暂时不可用，本次临时使用 "
        "`deepseek-v4-pro` 生成回答；默认设置未修改。\n\n"
        "色彩空间是用于定义和表示色彩的系统。"
    )

    assert _compact_legacy_assistant_copy(old_runtime_copy) == (
        "当前模型：`deepseek-v4-flash`。状态：已配置。"
    )
    assert _compact_legacy_assistant_copy(old_fallback_copy) == (
        "已切换备用模型：`deepseek-v4-pro`。\n\n色彩空间是用于定义和表示色彩的系统。"
    )


def test_sidebar_restore_control_is_not_hidden_with_header():
    hidden_block = STYLE_CSS.split('[data-testid="stSidebarNav"]', 1)[0]

    assert "footer, header" not in hidden_block
    assert '[data-testid="stHeader"]' in STYLE_CSS
    assert "background: transparent" in STYLE_CSS


def test_sidebar_pills_expose_exactly_the_three_views():
    app = AppTest.from_file(APP_FILE).run(timeout=30)

    nav = app.pills(key="sidebar_nav_pills")
    assert set(nav.options) == {"对话", "设置", "可观测"}

    # legacy multi-section button nav must be gone
    legacy_keys = {button.key for button in app.button if button.key.startswith("nav_")}
    assert legacy_keys == set()


def test_empty_chat_renders_all_welcome_actions():
    app = AppTest.from_file(APP_FILE).run(timeout=30)

    welcome_labels = {
        "分析利润率",
        "创建报价",
        "项目概览",
        "评估需求",
        "生成周报",
        "优化流程",
        "智能编辑文件",
    }

    assert welcome_labels.issubset({button.label for button in app.button})
    welcome_markup = [
        item.value for item in app.markdown if 'class="pm-welcome"' in item.value
    ]
    assert len(welcome_markup) == 1
    assert "今天先推进哪件事？" in welcome_markup[0]
    assert "查项目、算成本、起草报价" in welcome_markup[0]


def test_sidebar_pills_switch_between_all_views():
    app = AppTest.from_file(APP_FILE).run(timeout=30)

    app.pills(key="sidebar_nav_pills").set_value("设置").run(timeout=30)
    assert not app.exception
    assert app.session_state["view"] == "设置"

    app.pills(key="sidebar_nav_pills").set_value("可观测").run(timeout=30)
    assert not app.exception
    assert app.session_state["view"] == "可观测"

    app.pills(key="sidebar_nav_pills").set_value("对话").run(timeout=30)
    assert not app.exception
    assert app.session_state["view"] == "对话"
    assert app.chat_input(key="chat_input") is not None


@pytest.mark.parametrize("stale_view", ["设置", "可观测"])
def test_stale_sidebar_state_cannot_hijack_chat_submission(stale_view):
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = RecordingAgent()
    app.session_state["agent"] = agent
    composer = app.chat_input(key="chat_input")

    # A browser tab can retain an older keyed widget value across reconnects.
    app.session_state["view"] = "对话"
    app.session_state["_last_nav_selection"] = "对话"
    app.session_state["sidebar_nav_pills"] = stale_view
    composer.set_value("导航状态不应抢走这条消息").run(timeout=30)

    assert not app.exception
    assert app.session_state["view"] == "对话"
    assert app.session_state["sidebar_nav_pills"] == "对话"
    assert app.chat_input(key="chat_input") is not None
    assert agent.calls[-1]["message"] == "导航状态不应抢走这条消息"


def test_conversation_switch_from_settings_keeps_next_submission_on_chat():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = RecordingAgent()
    app.session_state["agent"] = agent
    conversation_id = app.session_state["active_conversation_id"]

    app.pills(key="sidebar_nav_pills").set_value("设置").run(timeout=30)
    app.button(key=f"conversation_{conversation_id}").click().run(timeout=30)

    assert app.session_state["view"] == "对话"
    assert app.session_state["sidebar_nav_pills"] == "对话"
    app.chat_input(key="chat_input").set_value("从设置返回后的消息").run(timeout=30)
    assert app.session_state["view"] == "对话"
    assert agent.calls[-1]["message"] == "从设置返回后的消息"


def test_no_floating_global_nav_overlays_content():
    """Redesign removed the fixed top-right mini nav so it no longer blocks
    main content. Confirm those floating buttons no longer exist."""
    app = AppTest.from_file(APP_FILE).run(timeout=30)

    global_nav_keys = {
        button.key for button in app.button if button.key.startswith("global_nav_")
    }
    assert global_nav_keys == set()


@pytest.mark.parametrize("legacy_view", ["概览", "项目", "上传", "未知页面"])
def test_legacy_or_unknown_view_falls_back_to_chat(legacy_view):
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.session_state["view"] = legacy_view

    app.run(timeout=30)

    assert not app.exception
    assert app.session_state["view"] == "对话"
    assert app.chat_input(key="chat_input") is not None


def test_settings_round_trip_preserves_active_conversation_and_messages():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.session_state["agent"] = RecordingAgent()
    app.chat_input(key="chat_input").set_value("设置前的问题").run(timeout=30)
    conversation_id = app.session_state["active_conversation_id"]
    messages = message_contents(app)

    app.pills(key="sidebar_nav_pills").set_value("设置").run(timeout=30)

    assert not app.exception
    assert app.session_state["view"] == "设置"
    assert app.session_state["active_conversation_id"] == conversation_id
    assert message_contents(app) == messages

    app.pills(key="sidebar_nav_pills").set_value("对话").run(timeout=30)

    assert not app.exception
    assert app.session_state["view"] == "对话"
    assert app.session_state["active_conversation_id"] == conversation_id
    assert message_contents(app) == messages


def test_settings_does_not_expose_volatile_quote_policy_controls():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.pills(key="sidebar_nav_pills").set_value("设置").run(timeout=30)

    number_labels = {widget.label for widget in app.number_input}
    select_labels = {widget.label for widget in app.selectbox}
    tab_labels = {tab.label for tab in app.tabs}
    assert number_labels.isdisjoint(
        {"管理费率 (%)", "税率 (%)", "高风险线 (%)", "目标利润率 (%)"}
    )
    assert "币种" not in select_labels
    assert "OCR" not in tab_labels


def test_environment_managed_data_root_has_no_fake_save_actions():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.pills(key="sidebar_nav_pills").set_value("设置").run(timeout=30)

    assert app.text_input(key="storage_data_root_input").disabled is True
    assert app.button(key="storage_save").disabled is True
    assert app.button(key="storage_reset").disabled is True
    assert any("DATA_ROOT 管理" in caption.value for caption in app.caption)


def test_settings_can_disable_and_restore_a_workflow():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.pills(key="sidebar_nav_pills").set_value("设置").run(timeout=30)

    enabled_key = "workflow_enabled_quote_assessment_1"
    app.toggle(key=enabled_key).set_value(False).run(timeout=30)
    app.button(key="save_workflow_settings").click().run(timeout=30)

    assert not app.exception
    store = app.session_state["workflow_store"]
    assert store.get_definition("quote_assessment").enabled is False

    app.button(key="reset_workflow_settings").click().run(timeout=30)

    assert not app.exception
    assert store.get_definition("quote_assessment").enabled is True


def test_streamlit_offline_quote_workflow():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.session_state["agent"] = StubAgent()
    app.chat_input(key="chat_input").set_value("报价10万成本6万帮我算利润").run(
        timeout=30
    )
    assert not app.exception
    assert len(app.session_state["messages"]) == 2
    assert any("利润分析结果" in item.value for item in app.markdown)


def test_streamlit_real_quote_request_persists_workflow_metadata():
    app = AppTest.from_file(APP_FILE).run(timeout=30)

    app.chat_input(key="chat_input").set_value("报价10万成本6万，帮我评估利润").run(
        timeout=30
    )

    assert not app.exception
    messages = app.session_state["messages"]
    assert len(messages) == 2
    assert messages[-1]["metadata"]["workflow_id"] == "quote_assessment"
    workflow_store = app.session_state["workflow_store"]
    runs = workflow_store.list_runs(app.session_state["active_conversation_id"])
    assert len(runs) == 1
    assert runs[0].status == "succeeded"


def test_streamlit_reminder_waits_for_persisted_approval_and_can_be_cancelled():
    app = AppTest.from_file(APP_FILE).run(timeout=30)

    app.chat_input(key="chat_input").set_value("请催办任务 T1").run(timeout=30)

    assert not app.exception
    workflow_store = app.session_state["workflow_store"]
    conversation_id = app.session_state["active_conversation_id"]
    waiting = workflow_store.list_runs(
        conversation_id,
        statuses=("awaiting_approval",),
    )
    assert len(waiting) == 1
    run = waiting[0]
    assert len(app.session_state["messages"]) == 1
    assert app.button(key=f"approve_workflow_{run.id}_{run.current_step}") is not None

    app.button(key=f"reject_workflow_{run.id}_{run.current_step}").click().run(
        timeout=30
    )

    assert not app.exception
    assert workflow_store.get_run(run.id).status == "failed"
    assert "未执行后续外部操作" in app.session_state["messages"][-1]["content"]


def test_streamlit_identity_question_returns_an_answer():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.chat_input(key="chat_input").set_value("你是什么").run(timeout=30)

    assert not app.exception
    assert len(app.session_state["messages"]) == 2
    assert app.session_state["messages"][1]["role"] == "assistant"
    assert "ArtPM Agent" in app.session_state["messages"][1]["content"]
    assert "pending_prompt" not in app.session_state


def test_streamlit_capability_question_skips_knowledge_lookup_and_returns_fast_answer(
    monkeypatch,
):
    import artpm_agent.app as streamlit_app

    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = RecordingAgent()
    app.session_state["agent"] = agent
    monkeypatch.setattr(
        streamlit_app,
        "build_knowledge_context",
        lambda prompt: (_ for _ in ()).throw(AssertionError("unexpected lookup")),
    )

    app.chat_input(key="chat_input").set_value("你可以帮我做什么？").run(timeout=30)

    assert not app.exception
    assert agent.calls[-1]["context"]["knowledge_context"] == ""
    assert app.session_state["messages"][-1]["content"] == "回答：你可以帮我做什么？"
    assert app.session_state["messages"][-1]["metadata"]["turn_mode"] == "fast"


def test_streamlit_wiki_publish_projects_into_workspace_rag():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.pills(key="sidebar_nav_pills").set_value("设置").run(timeout=30)
    token = f"wiki-rag-{uuid4().hex}"

    app.text_input(key="wiki_title_new").set_value("项目 Wiki 规范").run(
        timeout=30
    )
    app.text_area(key="wiki_markdown_new").set_value(
        f"发布后必须能够检索到 {token}。"
    ).run(timeout=30)
    app.button(key="wiki_publish_new").click().run(timeout=30)

    assert not app.exception
    wiki_store = app.session_state["wiki_store"]
    knowledge_store = app.session_state["knowledge_store"]
    page = next(
        page
        for page in wiki_store.list_pages(workspace_id="local-default")
        if page["title"] == "项目 Wiki 规范"
        and page["sync"]["status"] == "synced"
    )
    assert knowledge_store.search(token, workspace_id="local-default")

    wiki_store.archive_page(
        page["id"],
        workspace_id="local-default",
        expected_version=page["version"],
    )


def test_streamlit_ordinary_text_uses_streaming_agent_response():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = StreamingAgent()
    app.session_state["agent"] = agent

    app.chat_input(key="chat_input").set_value("解释一下色彩空间").run(timeout=30)

    assert not app.exception
    assert app.session_state["messages"][-1]["content"] == "流式回答"
    assert [call["message"] for call in agent.calls] == ["解释一下色彩空间"]


def test_streamlit_passes_harness_injected_feedback_to_response_agent():
    """The response adapter must consume run_turn's enriched context."""
    from artpm_agent.memory.feedback_store import get_default_feedback_store

    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = RecordingAgent()
    app.session_state["agent"] = agent
    feedback_store = get_default_feedback_store()
    assert feedback_store is not None
    marker = "后续回答先给结论，再给核验步骤"
    feedback_id = feedback_store.add("correction", marker, scope="chat")

    try:
        app.chat_input(key="chat_input").set_value("分析这个需求的风险").run(timeout=30)

        assert not app.exception
        injected = agent.calls[-1]["context"]["knowledge_context"]
        assert "【用户偏好与纠正】" in injected
        assert marker in injected
    finally:
        feedback_store.deactivate(feedback_id)


def test_streamlit_persists_the_model_that_actually_answered():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = FallbackRecordingAgent()
    app.session_state["agent"] = agent

    app.chat_input(key="chat_input").set_value("解释一下色彩空间").run(timeout=30)

    assert not app.exception
    stored = app.session_state["conversation_store"].list_messages(
        app.session_state["active_conversation_id"]
    )
    assert stored[-1]["model_id"] == "deepseek-v4-pro"


def test_streamlit_profile_change_requires_conversation_confirmation():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.button(key="new_conversation").click().run(timeout=30)
    profile_store = app.session_state["profile_store"]
    conversation_id = app.session_state["active_conversation_id"]
    before = profile_store.get_effective_profile()
    target_percent = 23 if before.quote_policy.overhead_rate != 0.23 else 24

    app.chat_input(key="chat_input").set_value(f"把管理费改成{target_percent}%").run(
        timeout=30
    )

    assert not app.exception
    pending = profile_store.list_pending(conversation_id=conversation_id)
    assert len(pending) == 1
    proposal = pending[0]
    assert profile_store.get_effective_profile().revision == before.revision
    assert profile_store.get_effective_profile().quote_policy.overhead_rate == (
        before.quote_policy.overhead_rate
    )

    app.button(key=f"approve_profile_{proposal.id}").click().run(timeout=30)

    assert not app.exception
    after = profile_store.get_effective_profile()
    assert after.revision == before.revision + 1
    assert after.quote_policy.overhead_rate == target_percent / 100
    assert "后续回答将使用新配置" in app.session_state["messages"][-1]["content"]

    app.chat_input(key="chat_input").set_value(
        "报价10万成本6万，按当前规则评估利润"
    ).run(timeout=30)
    quote_answer = app.session_state["messages"][-1]["content"]
    assert f"管理费({target_percent}%)" in quote_answer
    assert f"¥{target_percent * 1000:,.2f}" in quote_answer

    restore_id = f"restore-{proposal.id}"
    restore = profile_store.propose_change(
        conversation_id,
        AgentProfilePatch(
            quote_policy=QuotePolicyPatch(
                overhead_rate=before.quote_policy.overhead_rate
            )
        ),
        turn_id=restore_id,
        summary="恢复测试前配置",
        idempotency_key=restore_id,
    )
    profile_store.confirm_change(restore.id, actor="测试清理")


def test_streamlit_knowledge_rule_is_inert_until_accepted():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.button(key="new_conversation").click().run(timeout=30)
    knowledge_store = app.session_state["knowledge_store"]
    conversation_id = app.session_state["active_conversation_id"]
    statement = "所有交付物默认附带版本号"

    app.chat_input(key="chat_input").set_value(f"请记住：{statement}").run(timeout=30)

    proposed = [
        rule
        for rule in knowledge_store.list_rules(status="proposed")
        if rule.get("source_conversation_id") == conversation_id
        and rule["statement"] == statement
    ]
    assert len(proposed) == 1
    rule = proposed[0]
    assert statement not in {
        item["statement"] for item in knowledge_store.get_active_rules()
    }

    app.button(key=f"approve_knowledge_{rule['id']}").click().run(timeout=30)

    assert not app.exception
    assert statement in {
        item["statement"] for item in knowledge_store.get_active_rules()
    }

    recording_agent = RecordingAgent()
    app.session_state["agent"] = recording_agent
    app.chat_input(key="chat_input").set_value("交付物有什么规则").run(timeout=30)
    assert statement in recording_agent.calls[-1]["context"]["knowledge_context"]
    knowledge_store.revoke_rule(
        rule["id"],
        revoked_by="测试清理",
        confirmation_token=f"cleanup:{rule['id']}",
    )


def test_streamlit_clear_chat_requires_second_confirmation():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.button(key="new_conversation").click().run(timeout=30)
    app.session_state["agent"] = RecordingAgent()
    app.chat_input(key="chat_input").set_value("需要保留的消息").run(timeout=30)
    conversation_id = app.session_state["active_conversation_id"]
    session_store = SessionStore(app.session_state["conversation_store"])
    session_store.append(
        conversation_id,
        "tool_result",
        run_id="clear-chat-run",
        turn_id="clear-chat-turn",
        tool_result={"sensitive": "persisted tool output"},
    )
    before = list(app.session_state["messages"])

    app.button(key="clear_chat").click().run(timeout=30)

    assert not app.exception
    assert list(app.session_state["messages"]) == before
    confirm_key = f"confirm_clear_chat_action_{conversation_id}"
    assert app.button(key=confirm_key) is not None

    app.button(key=confirm_key).click().run(timeout=30)

    assert not app.exception
    assert app.session_state["messages"] == []
    assert session_store.replay(conversation_id) == []


def test_streamlit_generates_new_xlsx_and_exposes_download_metadata():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.button(key="new_conversation").click().run(timeout=30)
    agent = ArtifactAgent()
    runtime_factory = app.session_state["runtime_factory"]
    runtime_factory.clear_scoped_runtime_cache()
    runtime_factory._agent = agent
    app.session_state["agent"] = agent

    app.chat_input(key="chat_input").set_value("生成一个 Excel 项目清单").run(
        timeout=30
    )

    assert not app.exception
    response = app.session_state["messages"][-1]
    artifact = response["metadata"]["artifacts"][0]
    assert artifact["format"] == "xlsx"
    assert artifact["name"].startswith("项目清单")
    path = app.session_state["artifact_generator"].root / artifact["stored_path"]
    assert Path(path).is_file()
    Path(path).unlink()


def test_streamlit_generates_explicit_xlsx_without_llm_client():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.button(key="new_conversation").click().run(timeout=30)
    app.session_state["agent"] = LocalArtifactAgent()

    app.chat_input(key="chat_input").set_value(
        "生成一个 Excel 项目任务清单，列为任务、负责人、状态，"
        "包含角色建模、小李、进行中"
    ).run(timeout=30)

    assert not app.exception
    response = app.session_state["messages"][-1]
    artifact = response["metadata"]["artifacts"][0]
    assert artifact["format"] == "xlsx"
    assert artifact["rows"] == 1
    assert artifact["columns"] == 3
    assert "明确字段" in response["content"]
    path = app.session_state["artifact_generator"].root / artifact["stored_path"]
    assert Path(path).is_file()
    Path(path).unlink()


def test_streamlit_natural_language_word_request_stays_in_artifact_pipeline():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.button(key="new_conversation").click().run(timeout=30)
    app.session_state["agent"] = LocalArtifactAgent()

    app.chat_input(key="chat_input").set_value(
        "帮我做一个word文档 里面就写一句话 你好，文档名称随意"
    ).run(timeout=30)

    assert not app.exception
    response = app.session_state["messages"][-1]
    artifact = response["metadata"]["artifacts"][0]
    assert artifact["format"] == "docx"
    assert artifact["verification"]["status"] == "passed"
    path = app.session_state["artifact_generator"].root / artifact["stored_path"]
    assert Path(path).is_file()
    Path(path).unlink()


def test_streamlit_empty_agent_response_is_a_visible_retryable_error():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.session_state["agent"] = EmptyStubAgent()
    app.chat_input(key="chat_input").set_value("普通问题").run(timeout=30)

    assert not app.exception
    assert len(app.session_state["messages"]) == 2
    response = app.session_state["messages"][1]
    assert response["status"] == "error"
    assert response["retry_prompt"] == "普通问题"
    assert response["metadata"]["retry_prompt"] == "普通问题"
    assert "未收到有效回答" in response["content"]


def test_streamlit_model_timeout_names_selected_model_and_is_retryable():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.session_state["agent"] = TimeoutStubAgent()
    app.chat_input(key="chat_input").set_value("解释一下色彩空间").run(timeout=30)

    assert not app.exception
    response = app.session_state["messages"][1]
    assert response["status"] == "error"
    assert response["retry_prompt"] == "解释一下色彩空间"
    assert "deepseek-v4-flash 响应超时" in response["content"]
    assert "pending_prompt" not in app.session_state


def test_streamlit_busy_model_names_capacity_issue_and_is_retryable():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.session_state["agent"] = BusyStubAgent()
    app.chat_input(key="chat_input").set_value("解释一下色彩空间").run(timeout=30)

    assert not app.exception
    response = app.session_state["messages"][1]
    assert response["status"] == "error"
    assert response["retry_prompt"] == "解释一下色彩空间"
    assert "deepseek-v4-flash 当前服务繁忙" in response["content"]


def test_streamlit_error_callback_retry_replays_the_original_prompt():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = RetryAgent()
    app.session_state["agent"] = agent
    app.chat_input(key="chat_input").set_value("需要重试的问题").run(timeout=30)

    error = app.session_state["messages"][-1]
    callback_key = (
        f"error_callback_{app.session_state['active_conversation_id']}_"
        f"{error['id']}_retry"
    )
    assert app.button(key=callback_key) is not None

    app.button(key=callback_key).click().run(timeout=30)

    assert not app.exception
    assert agent.calls == 2
    assert app.session_state["messages"][-1]["content"] == "重试成功"
    assert not any(
        item.get("status") == "error" for item in app.session_state["messages"]
    )


def test_streamlit_conversation_history_excludes_current_user_message():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = RecordingAgent()
    app.session_state["agent"] = agent

    app.chat_input(key="chat_input").set_value("第一问").run(timeout=30)
    app.chat_input(key="chat_input").set_value("第二问").run(timeout=30)

    assert not app.exception
    assert [call["message"] for call in agent.calls] == ["第一问", "第二问"]
    assert agent.calls[0]["context"]["conversation_history"] == []

    second_context = agent.calls[1]["context"]
    assert (
        second_context["conversation_id"] == app.session_state["active_conversation_id"]
    )
    assert [
        (message["role"], message["content"])
        for message in second_context["conversation_history"]
    ] == [
        ("user", "第一问"),
        ("assistant", "回答：第一问"),
    ]
    assert "第二问" not in [
        message["content"] for message in second_context["conversation_history"]
    ]


def test_streamlit_multiple_conversations_are_isolated_and_restorable():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = RecordingAgent()
    app.session_state["agent"] = agent
    conversation_a = app.session_state["active_conversation_id"]

    app.chat_input(key="chat_input").set_value("A 会话问题").run(timeout=30)
    assert message_contents(app) == ["A 会话问题", "回答：A 会话问题"]

    app.button(key="new_conversation").click().run(timeout=30)
    conversation_b = app.session_state["active_conversation_id"]
    assert conversation_b != conversation_a
    assert message_contents(app) == []

    app.chat_input(key="chat_input").set_value("B 会话问题").run(timeout=30)
    assert agent.calls[-1]["context"]["conversation_id"] == conversation_b
    assert agent.calls[-1]["context"]["conversation_history"] == []
    assert message_contents(app) == ["B 会话问题", "回答：B 会话问题"]

    app.button(key=f"conversation_{conversation_a}").click().run(timeout=30)
    assert app.session_state["active_conversation_id"] == conversation_a
    assert message_contents(app) == ["A 会话问题", "回答：A 会话问题"]

    app.chat_input(key="chat_input").set_value("A 追问").run(timeout=30)
    final_call = agent.calls[-1]
    assert final_call["context"]["conversation_id"] == conversation_a
    assert [
        message["content"] for message in final_call["context"]["conversation_history"]
    ] == ["A 会话问题", "回答：A 会话问题"]
    assert all(
        "B 会话" not in message["content"]
        for message in final_call["context"]["conversation_history"]
    )


def test_streamlit_messages_survive_a_new_app_session():
    first_app = AppTest.from_file(APP_FILE).run(timeout=30)
    first_app.session_state["agent"] = RecordingAgent()
    first_app.chat_input(key="chat_input").set_value("持久化问题").run(timeout=30)
    conversation_id = first_app.session_state["active_conversation_id"]

    second_app = AppTest.from_file(APP_FILE).run(timeout=30)

    assert not second_app.exception
    assert second_app.session_state["active_conversation_id"] == conversation_id
    assert message_contents(second_app) == ["持久化问题", "回答：持久化问题"]


def test_sidebar_conversation_list_exposes_quick_delete():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    conversation_id = app.session_state["active_conversation_id"]
    button_keys = {button.key for button in app.button}

    assert not app.exception
    assert f"conversation_{conversation_id}" in button_keys
    assert f"quick_delete_conversation_{conversation_id}" in button_keys
    assert f"confirm_quick_delete_{conversation_id}" not in button_keys
    assert f"cancel_quick_delete_{conversation_id}" not in button_keys
    assert "pending_conversation_delete" not in app.session_state


def test_sidebar_quick_delete_requires_confirmation_and_switches_active_thread():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    conversation_id = app.session_state["active_conversation_id"]
    tenant = app.session_state["tenant_context"]
    app.session_state["conversation_permission_grants"] = {
        conversation_id: {
            "mode": "full_access",
            "tenant_id": tenant.tenant_id,
            "workspace_id": tenant.workspace_id,
            "principal_id": tenant.principal_id,
            "conversation_id": conversation_id,
            "issued_at": time.time(),
            "expires_at": time.time() + 3600,
        }
    }

    app.button(key=f"quick_delete_conversation_{conversation_id}").click().run(
        timeout=30
    )
    assert app.session_state["pending_conversation_delete"] == conversation_id
    assert f"confirm_quick_delete_{conversation_id}" in {
        button.key for button in app.button
    }

    app.button(key=f"cancel_quick_delete_{conversation_id}").click().run(timeout=30)
    assert "pending_conversation_delete" not in app.session_state

    app.button(key=f"quick_delete_conversation_{conversation_id}").click().run(
        timeout=30
    )
    app.button(key=f"confirm_quick_delete_{conversation_id}").click().run(timeout=30)

    assert app.session_state["active_conversation_id"] != conversation_id
    assert (
        app.session_state["conversation_store"].get_conversation(conversation_id)
        is None
    )
    assert conversation_id not in app.session_state["conversation_permission_grants"]
    assert "pending_conversation_delete" not in app.session_state
