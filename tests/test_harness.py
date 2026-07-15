"""
Unit tests for harness/turn_service.py (Phase 3 Stage 1).

Tests the basic framework and TurnContext/TurnResult data structures.
Stage-specific tests will be added as handlers are migrated.
"""

import pytest
from artpm_agent.harness import TurnContext, TurnResult, run_turn


class TestTurnContext:
    """Test TurnContext data structure."""

    def test_minimal_context(self):
        ctx = TurnContext(
            turn_id="test-001",
            conversation_id="conv-123",
            user_input="测试输入",
        )
        assert ctx.turn_id == "test-001"
        assert ctx.conversation_id == "conv-123"
        assert ctx.user_input == "测试输入"
        assert ctx.attachments == []
        assert ctx.agent_profile is None
        assert ctx.knowledge_context == ""
        assert ctx.conversation_history == []
        assert ctx.extra == {}

    def test_full_context(self):
        ctx = TurnContext(
            turn_id="test-002",
            conversation_id="conv-456",
            user_input="完整测试",
            attachments=["file1.txt", "file2.pdf"],
            agent_profile={"name": "测试助手"},
            knowledge_context="相关知识",
            conversation_history=[{"role": "user", "content": "历史消息"}],
            extra={"project_id": 789, "custom_flag": True},
        )
        assert len(ctx.attachments) == 2
        assert ctx.agent_profile["name"] == "测试助手"
        assert ctx.knowledge_context == "相关知识"
        assert len(ctx.conversation_history) == 1
        assert ctx.extra["project_id"] == 789


class TestTurnResult:
    """Test TurnResult data structure."""

    def test_minimal_result(self):
        result = TurnResult(response="测试响应")
        assert result.response == "测试响应"
        assert result.metadata == {}
        assert result.awaiting_approval is False
        assert result.artifacts == []
        assert result.handled_by is None
        assert result.error is None
        assert result.success is True

    def test_error_result(self):
        result = TurnResult(
            response="错误消息",
            success=False,
            error="具体错误",
            handled_by="test_handler",
        )
        assert result.success is False
        assert result.error == "具体错误"
        assert result.handled_by == "test_handler"

    def test_approval_result(self):
        result = TurnResult(
            response="需要批准",
            awaiting_approval=True,
            handled_by="profile_handler",
            metadata={"proposal_id": "prop-123"},
        )
        assert result.awaiting_approval is True
        assert result.metadata["proposal_id"] == "prop-123"

    def test_artifact_result(self):
        result = TurnResult(
            response="生成了产物",
            artifacts=[
                {"type": "markdown", "title": "报告", "content": "# 标题\n内容"},
            ],
            handled_by="artifact_handler",
        )
        assert len(result.artifacts) == 1
        assert result.artifacts[0]["type"] == "markdown"


class TestRunTurnFramework:
    """Test run_turn() framework (Stage 2: with Profile and Knowledge handlers)."""

    def test_missing_agent_reference(self):
        """Should return error if agent is None."""
        ctx = TurnContext(
            turn_id="test-003",
            conversation_id="conv-789",
            user_input="测试",
            agent=None,
        )
        result = run_turn(ctx)
        assert result.success is False
        assert "Agent not initialized" in result.response
        assert result.handled_by == "harness_error"

    def test_delegation_to_agent_chat(self):
        """Stage 3: should try all handlers then fall back to model."""

        class MockAgent:
            last_response_model = "test-model"
            llm_client = None  # Offline mode

            def _detect_intent(self, user_input):
                return None  # No intent matched

            def chat(self, user_input, context=None):
                """Fallback chat method for thin-agent compatibility."""
                return "Mock offline response"

        ctx = TurnContext(
            turn_id="test-004",
            conversation_id="conv-101",
            user_input="你好",
            agent=MockAgent(),
        )
        result = run_turn(ctx)
        assert result.success is True
        # Thin-agent fallback when full handler methods not present
        assert "Mock offline response" in result.response
        assert result.handled_by == "thin_agent_chat"

    def test_exception_handling(self):
        """Should catch exceptions from handlers gracefully."""

        class FailingAgent:
            llm_client = object()  # Has LLM

            def _detect_intent(self, user_input):
                return None

            def _build_system_prompt(self, profile, knowledge):
                return "system prompt"

            def _needs_visual_semantics(self, user_input):
                return False

            def _vision_attachment_paths(self, parsed_files, visual):
                class CM:
                    def __enter__(self):
                        return []

                    def __exit__(self, *args):
                        pass

                return CM()

            def _chat_with_model_failover(self, *args, **kwargs):
                raise RuntimeError("模拟错误")

            def chat(self, user_input, context=None):
                """Thin-agent fallback that also fails."""
                raise RuntimeError("模拟错误")

        ctx = TurnContext(
            turn_id="test-005",
            conversation_id="conv-202",
            user_input="触发错误",
            agent=FailingAgent(),
        )
        result = run_turn(ctx)
        assert result.success is False
        assert "模型请求失败" in result.response
        assert result.handled_by == "thin_agent_error"

    def test_context_assembly(self):
        """Should pass context through to handlers correctly."""

        class SpyAgent:
            llm_client = object()
            last_response_model = "spy-model"

            def _detect_intent(self, user_input):
                return None  # No skill match

            def _build_system_prompt(self, profile, knowledge):
                assert profile["id"] == "profile-1"
                assert knowledge == "知识片段"
                return "system prompt"

            def _needs_visual_semantics(self, user_input):
                return False

            def _vision_attachment_paths(self, parsed_files, visual):
                class CM:
                    def __enter__(self):
                        return []

                    def __exit__(self, *args):
                        pass

                return CM()

            def _chat_with_model_failover(self, prompt, system, history, **kwargs):
                assert "验证上下文" in prompt
                assert len(history) == 1
                return "context验证通过"

            def chat(self, user_input, context=None):
                """Thin-agent fallback."""
                return "context验证通过"

        ctx = TurnContext(
            turn_id="test-006",
            conversation_id="conv-303",
            user_input="验证上下文",
            agent=SpyAgent(),
            agent_profile={"id": "profile-1"},
            knowledge_context="知识片段",
            conversation_history=[{"role": "assistant", "content": "历史"}],
            extra={"project_id": 999},
        )
        result = run_turn(ctx)
        assert result.success is True
        assert "context验证通过" in result.response
        assert result.handled_by in ("model_chat", "thin_agent_chat")


class TestFutureHandlerStubs:
    """Tests for Stage 3+ handlers."""

    def test_artifact_handler_stub(self):
        """Stage 3: artifact generation."""
        # Will test artifact matching when migrated from chat.py
        pass

    def test_skill_handler_stub(self):
        """Stage 3: skill routing."""
        # Will test skill routing when migrated from agent.py
        pass


@pytest.mark.integration
class TestHarnessIntegration:
    """Integration tests with real ArtPMAgent (optional, requires config)."""

    def test_real_agent_offline_mode(self):
        """Test harness with real agent in offline mode."""
        # Will test full stack when Stage 2+ handlers are complete
        pass
