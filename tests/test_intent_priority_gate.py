"""PRD §4.5 意图优先级门的次序回归。

执行类动词（生成/导出/汇总…）必须让执行族先跑；没有执行动词时，
知识捕获 handler 仍保持先行，避免技能关键词劫走「请记住…」这类捕获意图。
"""

from __future__ import annotations

from artpm_agent.harness import run_turn
from artpm_agent.harness.turn_service import TurnContext


class _Agent:
    @staticmethod
    def process_document(_path, _prompt):
        return {"success": True, "raw_text": "searchable body"}

    @staticmethod
    def chat(*_args, **_kwargs):
        return {"success": True, "response": "fallback answer"}


class _IngestionStore:
    def __init__(self) -> None:
        self.proposed = None

    def propose_ingestion(self, *args):
        self.proposed = args
        return object()


def _context(prompt: str) -> TurnContext:
    return TurnContext(
        turn_id="turn-gate",
        conversation_id="conv-gate",
        user_input=prompt,
        attachments=[
            {
                "name": "notes.txt",
                "extension": "txt",
                "stored_path": "notes.txt",
            }
        ],
        agent=_Agent(),
    )


def test_capture_intent_without_execution_verb_still_runs_first():
    store = _IngestionStore()

    result = run_turn(
        _context("把这份文件加入知识库"),
        knowledge_store=store,
        request_conversation_id="conv-gate",
    )

    assert result.handled_by == "knowledge_ingestion"
    assert store.proposed is not None


def test_execution_verb_defers_capture_to_the_execution_family():
    """「导出」是执行意图：不得先被知识捕获吞掉，也不得留下入库提案。"""
    store = _IngestionStore()

    result = run_turn(
        _context("导出这份文件并加入知识库"),
        knowledge_store=store,
        request_conversation_id="conv-gate",
    )

    assert result.handled_by != "knowledge_ingestion"
    assert store.proposed is None


def test_weekly_report_prompt_is_an_execution_intent():
    """办公流请求属于执行族：捕获 handler 不得抢先。"""
    from artpm_agent.harness.turn_service import _EXECUTION_INTENT

    assert _EXECUTION_INTENT.search("生成这周周报")
    assert _EXECUTION_INTENT.search("把这张表汇总成 11 月")
    assert not _EXECUTION_INTENT.search("请记住：所有交付物默认附带版本号")
    assert not _EXECUTION_INTENT.search("上次的报价规则是什么")
