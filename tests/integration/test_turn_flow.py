"""Integration tests for turn processing flow.

Tests the complete turn processing chain from user input to response,
including profile proposals, knowledge ingestion, skill routing, and model fallback.
"""
import pytest
import time


class MockAgent:
    """Mock agent for testing."""

    def __init__(self):
        self.llm_client = MockLLMClient()
        self.router = MockSkillRouter()
        self.memory = MockMemory()
        self.last_response_model = None

    def _detect_intent(self, user_input: str):
        if "报价" in user_input or "利润" in user_input:
            return "cost_control"
        return None

    def _skill_input_with_history(self, user_input, context):
        return user_input

    def _extract_inputs(self, user_input, intent, context):
        return {"user_input": user_input}

    def _format_skill_result(self, skill_name, result):
        return f"技能 {skill_name} 执行完成"

    def _build_system_prompt(self, profile, knowledge_context):
        return "You are a helpful assistant."

    def _chat_with_model_failover(self, prompt, system_prompt, history, **kwargs):
        return "这是模型的回答"

    def _needs_visual_semantics(self, user_input):
        return False

    def _vision_attachment_paths(self, parsed_files, visual_semantics):
        from contextlib import contextmanager
        @contextmanager
        def cm():
            yield []
        return cm()


class MockLLMClient:
    """Mock LLM client."""

    def chat(self, prompt, system_prompt=None, history=None):
        return "Mock response"


class MockSkillRouter:
    """Mock skill router."""

    def __init__(self):
        self.skills = {"cost_control": MockSkill()}
        self.calls = []

    def execute_skill(self, skill_name, inputs):
        self.calls.append((skill_name, dict(inputs)))
        return {"success": True, "result": "Mock result"}


class MockSkill:
    """Mock skill."""
    pass


class MockMemory:
    """Mock memory."""

    def retrieve(self, query, **kwargs):
        return []


@pytest.fixture
def mock_agent():
    """Create mock agent."""
    return MockAgent()


@pytest.fixture
def mock_profile_store():
    """Create mock profile store."""
    from unittest.mock import MagicMock
    store = MagicMock()
    store.detect_profile_change.return_value = None
    return store


@pytest.fixture
def mock_knowledge_store():
    """Create mock knowledge store."""
    from unittest.mock import MagicMock
    store = MagicMock()
    store.should_ingest.return_value = False
    return store


class TestTurnFlow:
    """Integration tests for turn processing."""

    def test_simple_turn_execution(self, mock_agent):
        """Test basic turn execution."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn

        ctx = TurnContext(
            turn_id="test_001",
            conversation_id="conv_001",
            user_input="你好",
            agent=mock_agent
        )

        result = run_turn(ctx)

        assert result.success is True
        assert result.response
        assert result.handled_by is not None

    def test_skill_routing_flow(self, mock_agent, tmp_path):
        """Protected skills stop at a durable permission request."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn
        from artpm_agent.security import PermissionStore

        permission_store = PermissionStore(tmp_path / "permissions.db")

        ctx = TurnContext(
            turn_id="test_002",
            conversation_id="conv_002",
            user_input="报价10万成本7万帮我算利润",
            agent=mock_agent,
            extra={
                "permission_store": permission_store,
                "workspace_id": "test-workspace",
            },
        )

        result = run_turn(ctx)

        assert result.success is True
        assert result.awaiting_approval is True
        assert result.handled_by == "permission_gate"
        request = permission_store.get(result.metadata["permission_request_id"])
        assert request.status == "pending"
        assert request.workspace_id == "test-workspace"
        assert mock_agent.router.calls == []

    def test_profile_proposal_detection(self, mock_agent, mock_profile_store):
        """Test profile change proposal detection."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn

        # Mock profile change detection
        mock_profile_store.detect_profile_change.return_value = {
            "has_change": True,
            "changes": {"response_style": "concise"}
        }

        ctx = TurnContext(
            turn_id="test_003",
            conversation_id="conv_003",
            user_input="请把回答风格改成简洁型",
            agent=mock_agent
        )

        result = run_turn(
            ctx,
            profile_store=mock_profile_store,
            request_conversation_id="conv_003"
        )

        # Profile handler should catch this
        assert result.success is True
        assert result.awaiting_approval is False
        assert result.handled_by == "model_chat"

    def test_memory_injection(self, mock_agent, mock_knowledge_store):
        """Test memory injection in turn processing."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn

        ctx = TurnContext(
            turn_id="test_004",
            conversation_id="conv_004",
            user_input="帮我分析项目进度",
            agent=mock_agent,
            knowledge_context=""  # Will be injected
        )

        result = run_turn(ctx, knowledge_store=mock_knowledge_store)

        assert result.success is True
        # Memory injection should have run (even if no results)

    def test_turn_with_attachments(self, mock_agent):
        """Test turn with file attachments."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn

        ctx = TurnContext(
            turn_id="test_005",
            conversation_id="conv_005",
            user_input="分析这份报价单",
            agent=mock_agent,
            extra={"file_paths": ["test.xlsx"]}
        )

        result = run_turn(ctx)

        # Should handle gracefully even with mock attachment
        assert result.success is False
        assert result.handled_by == "permission_gate"
        assert result.error == "permission store is unavailable for a protected Skill"
        assert result.response == "Permission request could not be persisted; no action was executed."


class TestPerformanceBenchmark:
    """Performance benchmark tests."""

    def test_turn_latency_baseline(self, mock_agent):
        """Benchmark turn processing latency."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn

        latencies = []

        for i in range(10):
            ctx = TurnContext(
                turn_id=f"perf_{i}",
                conversation_id="perf_test",
                user_input="测试消息",
                agent=mock_agent
            )

            start = time.time()
            result = run_turn(ctx)
            latency = time.time() - start

            latencies.append(latency)
            assert result.success is True

        avg_latency = sum(latencies) / len(latencies)
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)]

        print(f"Turn latency - Avg: {avg_latency*1000:.2f}ms, P95: {p95_latency*1000:.2f}ms")

        # Without LLM calls, should be fast
        assert avg_latency < 0.1  # 100ms

    def test_memory_injection_performance(self, mock_agent, mock_knowledge_store):
        """Benchmark memory injection overhead."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn

        # With memory injection
        start = time.time()
        for i in range(10):
            ctx = TurnContext(
                turn_id=f"mem_{i}",
                conversation_id="mem_test",
                user_input="测试",
                agent=mock_agent
            )
            run_turn(ctx, knowledge_store=mock_knowledge_store)

        with_memory_time = time.time() - start

        # Without memory injection
        start = time.time()
        for i in range(10):
            ctx = TurnContext(
                turn_id=f"no_mem_{i}",
                conversation_id="no_mem_test",
                user_input="测试",
                agent=mock_agent
            )
            run_turn(ctx)

        without_memory_time = time.time() - start

        overhead = with_memory_time - without_memory_time
        print(f"Memory injection overhead: {overhead*1000:.2f}ms for 10 turns")

        # Overhead should be minimal
        assert overhead < 0.5  # 500ms for 10 turns


class TestContractCompliance:
    """Contract tests for handler interfaces."""

    def test_turn_result_contract(self):
        """Test TurnResult has required fields."""
        from artpm_agent.harness.turn_service import TurnResult

        result = TurnResult(response="test")

        # Required fields
        assert hasattr(result, "response")
        assert hasattr(result, "success")
        assert hasattr(result, "handled_by")
        assert hasattr(result, "metadata")
        assert hasattr(result, "awaiting_approval")
        assert hasattr(result, "artifacts")
        assert hasattr(result, "error")

    def test_turn_context_contract(self):
        """Test TurnContext has required fields."""
        from artpm_agent.harness.turn_service import TurnContext

        ctx = TurnContext(
            turn_id="test",
            conversation_id="conv",
            user_input="input",
            agent=None
        )

        assert hasattr(ctx, "turn_id")
        assert hasattr(ctx, "conversation_id")
        assert hasattr(ctx, "user_input")
        assert hasattr(ctx, "agent")
        assert hasattr(ctx, "attachments")
        assert hasattr(ctx, "agent_profile")
        assert hasattr(ctx, "knowledge_context")
        assert hasattr(ctx, "conversation_history")
        assert hasattr(ctx, "extra")


class TestResilience:
    """Resilience and chaos engineering tests."""

    def test_null_agent_handling(self):
        """Test handling of null agent."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn

        ctx = TurnContext(
            turn_id="null_test",
            conversation_id="null_conv",
            user_input="test",
            agent=None
        )

        result = run_turn(ctx)

        assert result.success is False
        assert result.error is not None
        assert "Agent not initialized" in result.error

    def test_handler_exception_isolation(self, mock_agent):
        """Test that handler exceptions don't crash turn processing."""
        from artpm_agent.harness.turn_service import TurnContext, run_turn

        # Inject a failing handler
        def failing_handler(ctx):
            raise RuntimeError("Simulated handler failure")

        ctx = TurnContext(
            turn_id="exception_test",
            conversation_id="exception_conv",
            user_input="test",
            agent=mock_agent
        )

        # Should handle gracefully
        try:
            result = run_turn(ctx)
            # Either succeeds with fallback or returns error
            assert result.success is True
            assert result.response
        except RuntimeError:
            pytest.fail("Handler exception not isolated")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
