"""
Unit tests for harness handler modules (Phase 3 Stage 2).

Tests Profile and Knowledge handlers that were migrated from chat.py.
"""

from artpm_agent.harness import TurnContext


class TestProfileHandler:
    """Test Profile change proposal handler (Stage 2)."""

    def test_profile_not_triggered_without_change_request(self):
        """Should return None for regular chat."""
        from artpm_agent.harness.profile_handler import try_profile_proposal

        class MockAgent:
            pass

        ctx = TurnContext(
            turn_id="test-prof-001",
            conversation_id="conv-001",
            user_input="你好",
            agent=MockAgent(),
        )

        result = try_profile_proposal(ctx, None, "conv-001")
        assert result is None

    def test_profile_requires_profile_store(self):
        """Should return None if profile_store is None."""
        from artpm_agent.harness.profile_handler import try_profile_proposal

        class MockAgent:
            pass

        ctx = TurnContext(
            turn_id="test-prof-002",
            conversation_id="conv-002",
            user_input="调整税率为5%",
            agent=MockAgent(),
            agent_profile={"tax_rate": 0.06},
        )

        result = try_profile_proposal(ctx, None, "conv-002")
        assert result is None


class TestKnowledgeHandler:
    """Test Knowledge ingestion handler (Stage 2)."""

    def test_knowledge_detection(self):
        """Should detect knowledge ingestion requests."""
        from artpm_agent.harness.knowledge_handler import is_knowledge_ingestion_request

        assert is_knowledge_ingestion_request("把这份文件加入知识库")
        assert is_knowledge_ingestion_request("将附件保存到资料库")
        assert is_knowledge_ingestion_request("这些文件收录到知识库")
        assert not is_knowledge_ingestion_request("查询知识库")
        assert not is_knowledge_ingestion_request("这个文档内容是什么")

    def test_knowledge_not_triggered_without_pattern(self):
        """Should return None for regular chat."""
        from artpm_agent.harness.knowledge_handler import try_knowledge_ingestion

        class MockAgent:
            pass

        ctx = TurnContext(
            turn_id="test-know-001",
            conversation_id="conv-001",
            user_input="你好",
            agent=MockAgent(),
        )

        result = try_knowledge_ingestion(ctx, None, "conv-001", [], [])
        assert result is None

    def test_knowledge_requires_attachments(self):
        """Should return error message if no attachments."""
        from artpm_agent.harness.knowledge_handler import try_knowledge_ingestion

        class MockAgent:
            pass

        ctx = TurnContext(
            turn_id="test-know-002",
            conversation_id="conv-002",
            user_input="把这份文件加入知识库",
            agent=MockAgent(),
        )

        result = try_knowledge_ingestion(ctx, None, "conv-002", [], [])
        assert result is not None
        assert result.success is True
        assert "附上需要加入资料库的文件" in result.response
        assert result.handled_by == "knowledge_ingestion_no_attachments"

    def test_turn_context_attachment_field_drives_ingestion(self):
        from artpm_agent.harness import run_turn

        class Agent:
            @staticmethod
            def process_document(_path, _prompt):
                return {"success": True, "raw_text": "searchable body"}

        class Store:
            proposed = None

            def propose_ingestion(self, *args):
                self.proposed = args

        store = Store()
        ctx = TurnContext(
            turn_id="turn-attachment",
            conversation_id="conv-attachment",
            user_input="把这份文件加入知识库",
            attachments=[
                {
                    "name": "notes.txt",
                    "extension": "txt",
                    "stored_path": "notes.txt",
                }
            ],
            agent=Agent(),
        )

        result = run_turn(
            ctx,
            knowledge_store=store,
            request_conversation_id="conv-attachment",
        )

        assert result.handled_by == "knowledge_ingestion"
        assert result.awaiting_approval is True
        assert store.proposed is not None

    def test_partial_attachment_failure_blocks_the_entire_proposal(self):
        from artpm_agent.harness.knowledge_handler import try_knowledge_ingestion

        class Agent:
            @staticmethod
            def process_document(path, _prompt):
                if path == "bad.txt":
                    return {"success": False, "error": "parse failed"}
                return {"success": True, "raw_text": "valid text"}

        class Store:
            called = False

            def propose_ingestion(self, *_args):
                self.called = True

        store = Store()
        ctx = TurnContext(
            turn_id="turn-partial",
            conversation_id="conv-partial",
            user_input="把这些文件加入知识库",
            agent=Agent(),
        )

        result = try_knowledge_ingestion(
            ctx,
            store,
            "conv-partial",
            [
                {"name": "good.txt", "extension": "txt"},
                {"name": "bad.txt", "extension": "txt"},
            ],
            ["good.txt", "bad.txt"],
        )

        assert result.success is False
        assert "parse failed" in result.response
        assert store.called is False


class TestArtifactHandler:
    def test_artifact_metadata_keeps_preview_and_export_formats(self):
        from artpm_agent.harness.artifact_handler import try_artifact_generation

        class Outcome:
            matched = True
            message = "created"
            artifact = {
                "id": "artifact-1",
                "name": "report.xlsx",
                "stored_path": "report.xlsx",
                "format": "xlsx",
                "mime_type": "application/vnd.ms-excel",
                "size": 10,
                "sha256": "abc",
                "version": 1,
                "preview_markdown": "| A |\n| --- |",
                "export_formats": ["csv", "md"],
            }

        class Coordinator:
            def process(self, *args, **kwargs):
                return Outcome()

        ctx = TurnContext(
            turn_id="turn-artifact",
            conversation_id="conv-artifact",
            user_input="generate xlsx",
            agent=object(),
        )

        result = try_artifact_generation(ctx, Coordinator(), "conv-artifact")

        assert result is not None
        assert result.artifacts[0]["preview_markdown"].startswith("| A |")
        assert result.artifacts[0]["export_formats"] == ["csv", "md"]

    def test_artifact_handler_passes_multimodal_attachment_context(self):
        from artpm_agent.harness.artifact_handler import try_artifact_generation

        class Agent:
            def _parse_context_attachments(self, user_input, context):
                assert user_input == "generate xlsx from image"
                assert context["file_paths"] == ["capture.png"]
                return (
                    [{"success": True, "markdown": "ocr table"}],
                    "<attachment_markdown>ocr table</attachment_markdown>",
                )

        class Outcome:
            matched = True
            message = "created"
            artifact = None

        class Coordinator:
            kwargs = None

            def process(self, *args, **kwargs):
                self.kwargs = kwargs
                return Outcome()

        coordinator = Coordinator()
        ctx = TurnContext(
            turn_id="turn-artifact-ocr",
            conversation_id="conv-artifact",
            user_input="generate xlsx from image",
            agent=Agent(),
            extra={"file_paths": ["capture.png"]},
        )

        result = try_artifact_generation(ctx, coordinator, "conv-artifact")

        assert result is not None
        assert "ocr table" in coordinator.kwargs["attachment_context"]
