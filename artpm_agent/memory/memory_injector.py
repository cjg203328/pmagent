"""记忆注入器 — 协调对话压缩与跨会话记忆的统一入口。

这是记忆系统对外的唯一调用接口。在 Agent 每次处理用户输入前：
1. 检测当前会话是否需要压缩 → 执行压缩
2. 从历史中检索相关跨会话记忆
3. 将「压缩摘要 + 相关记忆」组装为 context 注入 LLM prompt

使用方式：
    injector = MemoryInjector(knowledge_store, llm_client)
    memory_context = injector.prepare_context(
        messages=current_messages,
        user_input=user_text,
        conversation_id=conv_id,
    )
    # 将 memory_context 传入 system prompt 或 messages 前部
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from artpm_agent.memory.conversation_compressor import (
    ConversationCompressor,
    CompressionResult,
)
from artpm_agent.memory.cross_session_memory import CrossSessionMemory

logger = logging.getLogger(__name__)


class MemoryInjector:
    """统一的记忆管理入口。

    职责编排：
    - ConversationCompressor: 对话过长时自动压缩
    - CrossSessionMemory: 跨会话记忆的提取/检索/注入

    设计原则：
    - 所有操作都是 best-effort：任何环节失败不阻塞主流程
    - 注入内容有 token 上限保护（复用 token_budget）
    - 支持降级模式（无 LLM 时退化为规则提取 + 截断）
    """

    def __init__(
        self,
        knowledge_store: Any,  # WorkspaceKnowledgeStore
        *,
        compressor: Optional[ConversationCompressor] = None,
        cross_memory: Optional[CrossSessionMemory] = None,
        auto_compress: bool = True,
        auto_extract: bool = True,
        max_context_chars: int = 5000,  # 注入 context 的总字符上限
    ):
        self.store = knowledge_store
        self.compressor = compressor or ConversationCompressor()
        self.cross_memory = cross_memory or CrossSessionMemory(knowledge_store)
        self.auto_compress = auto_compress
        self.auto_extract = auto_extract
        self.max_context_chars = max_context_chars

        # 状态追踪（按会话隔离）
        self._compress_state: Dict[str, Dict[str, Any]] = {}

    # ---- 主入口 ---------------------------------------------------------

    def prepare_context(
        self,
        *,
        messages: Sequence[Dict[str, Any]],
        user_input: str = "",
        conversation_id: str = "",
        llm_callable: Any = None,
    ) -> str:
        """为一次 LLM 调用准备完整的记忆上下文。

        这是唯一需要调用的方法。内部自动完成：
        1. 必要时执行对话压缩
        2. 提取新记忆（增量式）
        3. 检索相关跨会话记忆
        4. 组装并返回可注入文本

        Args:
            messages: 当前会话完整消息列表
            user_input: 用户最新输入（用于相关性检索）
            conversation_id: 当前会话 ID
            llm_callable: 可选 LLM 调用（用于压缩和增强提取）

        Returns:
            格式化后的上下文文本（空字符串表示无需注入）
        """
        if not messages and not user_input:
            return ""

        parts: List[str] = []
        total_chars = 0

        # Step 1: 检查并执行压缩
        compression_result: Optional[CompressionResult] = None
        if self.auto_compress and len(messages) > 10:
            compression_result = self._check_and_compress(
                messages, conversation_id, llm_callable
            )
            if compression_result and compression_result.was_compressed:
                summary_ctx = self.compressor.build_compressed_context(
                    messages[-self.compressor.keep_recent:],
                    compression_result,
                )
                if summary_ctx and len(summary_ctx) < self.max_context_chars:
                    parts.append(summary_ctx)
                    total_chars += len(summary_ctx)
                    logger.info(
                        "已注入压缩摘要: %d 字符 (%d→%d 条消息)",
                        len(summary_ctx),
                        compression_result.messages_before,
                        compression_result.messages_after,
                    )

        # Step 2: 增量提取新记忆（仅在有 LLM 时做增强提取）
        if self.auto_extract and llm_callable is not None:
            try:
                state = self._compress_state.get(conversation_id, {})
                last_extract_count = state.get("last_extract_count", 0)
                current_count = len(messages)

                # 只在新增消息超过阈值时重新提取（避免每轮都调 LLM）
                if current_count >= last_extract_count + 6:
                    extracted = self.cross_memory.extract_and_save_from_messages(
                        messages,
                        conversation_id=conversation_id,
                        llm_callable=llm_callable,
                    )
                    if conversation_id in self._compress_state:
                        self._compress_state[conversation_id]["last_extract_count"] = current_count
                    if extracted:
                        logger.info("增量提取 %d 条新记忆", len(extracted))
            except Exception as exc:
                logger.warning("增量记忆提取跳过: %s", exc)

        # Step 3: 检索相关跨会话记忆
        if user_input:
            try:
                memory_ctx = self.cross_memory.build_memory_context(
                    user_input,
                    conversation_id=conversation_id,
                    include_preferences=True,
                )
                if memory_ctx:
                    remaining = self.max_context_chars - total_chars
                    if remaining > 200 and len(memory_ctx) > 0:
                        # 截断以适应预算
                        if len(memory_ctx) > remaining:
                            memory_ctx = memory_ctx[:remaining-3] + "…"
                        parts.append(memory_ctx)
                        total_chars += len(memory_ctx)
            except Exception as exc:
                logger.warning("跨会话记忆检索跳过: %s", exc)

        result = "\n".join(parts).strip()
        if result:
            return f"<!-- 以下为系统记忆上下文 -->\n{result}\n<!-- 记忆上下文结束 -->"
        return ""

    # ---- 显式操作接口 ---------------------------------------------------

    def force_compress(
        self,
        messages: Sequence[Dict[str, Any]],
        conversation_id: str,
        llm_callable: Any,
    ) -> CompressionResult:
        """强制执行一次压缩（忽略触发条件）。"""
        result = self.compressor.compress(
            messages,
            llm_callable,
            conversation_id=conversation_id,
            knowledge_store=self.store,
        )

        state = self._compress_state.setdefault(conversation_id, {})
        state["last_compressed_at"] = result.compressed_at
        state["last_message_count"] = result.messages_after

        return result

    def remember_explicitly(
        self,
        content: str,
        memory_type: str = "fact",
        *,
        conversation_id: str = "",
    ) -> bool:
        """记录一条显式记忆（用户说"记住 XXX"时调用）。"""
        rid = self.cross_memory.save_memory(
            content,
            memory_type,
            source_conversation=conversation_id,
            source_type="explicit",
            confidence=1.0,
        )
        return rid is not None

    def get_conversation_summary(
        self, conversation_id: str
    ) -> List[Dict[str, Any]]:
        """获取指定会话的所有摘要。"""
        try:
            results = self.store.search(
                conversation_id,
                resource_types=["conversation_summary"],
                include_rules=False,
                limit=10,
                max_text_chars=4000,
            )
            return results
        except Exception as exc:
            logger.error("获取会话摘要失败: %s", exc)
            return []

    # ---- 内部方法 -------------------------------------------------------

    def _check_and_compress(
        self,
        messages: Sequence[Dict[str, Any]],
        conversation_id: str,
        llm_callable: Any,
    ) -> Optional[CompressionResult]:
        """检查是否需要压缩并在条件满足时执行。"""
        state = self._compress_state.get(conversation_id, {})

        should = self.compressor.should_compress(
            messages,
            last_compressed_at=state.get("last_compressed_at"),
            message_count_at_last_compress=state.get("last_message_count", 0),
        )

        if not should:
            return None

        if llm_callable is None:
            logger.info("压缩触发但无可用的 LLM，跳过")
            return None

        result = self.compressor.compress(
            messages,
            llm_callable,
            conversation_id=conversation_id,
            knowledge_store=self.store,
        )

        # 更新状态
        self._compress_state[conversation_id] = {
            "last_compressed_at": result.compressed_at,
            "last_message_count": result.messages_after,
            "last_extract_count": len(messages),
        }

        return result
