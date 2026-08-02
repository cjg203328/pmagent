"""对话压缩器 — 长对话自动摘要与压缩。

对标 Kimi/ChatGPT 的「记忆」机制：
- 当对话超过阈值时，用 LLM 生成结构化摘要
- 保留关键决策点、用户偏好、重要结论和待办事项
- 原始消息归档为 episode，释放上下文窗口
- 摘要注入 WorkspaceKnowledgeStore 作为长期可检索记忆

触发条件（满足任一即触发）：
  - 消息数 > MAX_MESSAGES (默认 50)
  - 对话 Token 数 > MAX_TOKENS (默认 8000)
  - 距上次压缩 > MIN_COMPRESS_INTERVAL_MIN 分钟且新增 > MIN_NEW_MESSAGES 条
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from artpm_agent.harness.token_budget import estimate_tokens

logger = logging.getLogger(__name__)

# ---- 可调参数 ----------------------------------------------------------

DEFAULT_MAX_MESSAGES = 50          # 消息数触发阈值
DEFAULT_MAX_TOKENS = 8000           # Token 数触发阈值
DEFAULT_MIN_COMPRESS_INTERVAL_MIN = 30  # 最小压缩间隔（分钟）
DEFAULT_MIN_NEW_MESSAGES = 10      # 间隔内新增消息触发量
DEFAULT_KEEP_RECENT_MESSAGES = 8    # 压缩后保留的最近原始消息数
DEFAULT_SUMMARY_MAX_TOKENS = 1500   # 摘要目标 Token 数


@dataclass
class CompressionResult:
    """一次压缩运行的产出。"""

    triggered: bool = False                    # 是否触发了压缩
    was_compressed: bool = False               # 是否实际执行了压缩
    summary_text: str = ""                     # 生成的摘要文本
    messages_before: int = 0                   # 压缩前消息数
    messages_after: int = 0                    # 压缩后保留消息数
    tokens_estimated: int = 0                  # 压缩前估算 Token 数
    key_points: List[str] = field(default_factory=list)     # 提取的关键点
    user_preferences: List[str] = field(default_factory=list)  # 提取的用户偏好
    entities: List[Dict[str, str]] = field(default_factory=list)  # 提取的实体
    compressed_at: str = ""                    # ISO 时间戳
    error: Optional[str] = None               # 错误信息

    def to_dict(self) -> Dict[str, Any]:
        return {
            "triggered": self.triggered,
            "was_compressed": self.was_compressed,
            "summary_text": self.summary_text,
            "messages_before": self.messages_before,
            "messages_after": self.messages_after,
            "tokens_estimated": self.tokens_estimated,
            "key_points": self.key_points,
            "user_preferences": self.user_preferences,
            "entities": self.entities,
            "compressed_at": self.compressed_at,
            "error": self.error,
        }


class ConversationCompressor:
    """检测是否需要压缩，并执行 LLM 驱动的对话摘要。

    设计原则：
    - 无副作用检测（should_compress 只读不写）
    - 压缩幂等性（对同一状态多次 compress 结果一致）
    - 摘要持久化到 KnowledgeStore，跨会话可检索
    - 降级友好：LLM 失败时退化为截断策略
    """

    def __init__(
        self,
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        min_compress_interval_min: int = DEFAULT_MIN_COMPRESS_INTERVAL_MIN,
        min_new_messages: int = DEFAULT_MIN_NEW_MESSAGES,
        keep_recent: int = DEFAULT_KEEP_RECENT_MESSAGES,
        summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
    ):
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.min_compress_interval_min = min_compress_interval_min
        self.min_new_messages = min_new_messages
        self.keep_recent = keep_recent
        self.summary_max_tokens = summary_max_tokens

    # ---- 公共接口 ------------------------------------------------------

    def should_compress(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        last_compressed_at: Optional[str] = None,
        message_count_at_last_compress: int = 0,
    ) -> bool:
        """判断当前对话是否需要压缩（只读，无副作用）。"""
        msg_count = len(messages)

        # Once a window has been compressed, the original messages remain in
        # durable history.  Gate subsequent summaries by both cursor growth and
        # time before re-evaluating the absolute size thresholds.
        if last_compressed_at:
            try:
                last_ts = datetime.fromisoformat(last_compressed_at)
                elapsed = (
                    datetime.now(timezone.utc) - last_ts
                ).total_seconds() / 60.0
                new_msgs = msg_count - message_count_at_last_compress
                if (
                    new_msgs <= self.min_new_messages
                    or elapsed < self.min_compress_interval_min
                ):
                    return False
                logger.info(
                    "压缩触发: 距上次 %.0f min, 新增 %d 条消息",
                    elapsed,
                    new_msgs,
                )
                return True
            except (ValueError, TypeError):
                # Invalid legacy cursors fall through to the size checks.
                pass

        # 条件 1：消息数超限
        if msg_count > self.max_messages:
            logger.info(
                "压缩触发: 消息数 %d > %d", msg_count, self.max_messages
            )
            return True

        # 条件 2：Token 数超限
        full_text = "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')}"
            for m in messages
        )
        token_count = estimate_tokens(full_text)
        if token_count > self.max_tokens:
            logger.info(
                "压缩触发: 估算 Token %d > %d",
                token_count,
                self.max_tokens,
            )
            return True

        return False

    def compress(
        self,
        messages: Sequence[Dict[str, Any]],
        llm_callable: Any,  # Callable[[str], str] — 接收 prompt 返回摘要文本
        *,
        conversation_id: str = "",
        workspace_id: str = "local-default",
        knowledge_store: Any = None,  # WorkspaceKnowledgeStore or None
    ) -> CompressionResult:
        """执行压缩：生成摘要 → 提取关键点 → 可选写入知识库。

        Args:
            messages: 当前完整消息列表 (role/content 格式)
            llm_callable: LLM 调用入口，接收 system+user prompt，返回文本
            conversation_id: 所属会话 ID（用于知识库关联）
            workspace_id: 所属工作区 ID（用于知识隔离）
            knowledge_store: 若提供，将摘要写入知识库供跨会话检索

        Returns:
            CompressionResult 包含摘要、关键点、错误信息等
        """
        result = CompressionResult(
            triggered=True,
            messages_before=len(messages),
            tokens_estimated=estimate_tokens(
                "\n".join(f"{m.get('role','?')}: {m.get('content','')}" for m in messages)
            ),
        )

        if not messages:
            result.error = "消息列表为空，无需压缩"
            return result

        try:
            # 1. 构建压缩 prompt
            compress_prompt = self._build_compress_prompt(messages)

            # 2. 调用 LLM 生成结构化摘要
            raw_summary = llm_callable(compress_prompt)
            if not raw_summary or not raw_summary.strip():
                result.error = "LLM 返回空摘要"
                return result

            # 3. 解析结构化输出
            parsed = self._parse_summary(raw_summary)
            result.summary_text = parsed.get("summary", raw_summary)
            result.key_points = parsed.get("key_points", [])
            result.user_preferences = parsed.get("user_preferences", [])
            result.entities = parsed.get("entities", [])
            result.was_compressed = True
            result.compressed_at = datetime.now(timezone.utc).isoformat()
            result.messages_after = min(self.keep_recent, len(messages))

            # 4. 写入知识库（如果可用）
            if knowledge_store is not None and result.summary_text:
                self._persist_to_knowledge_store(
                    knowledge_store,
                    result,
                    conversation_id,
                    workspace_id,
                )

            logger.info(
                "对话压缩完成: %d → %d 条消息, 摘要 %d 字符, 关键点 %d 个",
                result.messages_before,
                result.messages_after,
                len(result.summary_text),
                len(result.key_points),
            )

        except Exception as exc:
            result.error = f"压缩异常: {exc}"
            logger.exception("对话压缩失败")

        return result

    def build_compressed_context(
        self,
        recent_messages: Sequence[Dict[str, Any]],
        compression_result: CompressionResult,
    ) -> str:
        """将「最近原始消息 + 历史摘要」组装为可注入 context 的文本。"""
        parts: List[str] = []

        if compression_result.was_compressed and compression_result.summary_text:
            parts.append("[历史对话摘要]")
            parts.append(compression_result.summary_text)
            if compression_result.key_points:
                parts.append("\n[关键决策]")
                for kp in compression_result.key_points:
                    parts.append(f"- {kp}")
            if compression_result.user_preferences:
                parts.append("\n[已识别的用户偏好]")
                for pref in compression_result.user_preferences:
                    parts.append(f"- {pref}")
            parts.append("\n--- 以上是之前的对话摘要，以下是最近的消息 ---\n")

        for msg in recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content and content.strip():
                label = "用户" if role == "user" else ("助手" if role == "assistant" else role)
                parts.append(f"{label}: {content}")

        return "\n".join(parts)

    # ---- 内部方法 ------------------------------------------------------

    @staticmethod
    def _build_compress_prompt(messages: Sequence[Dict[str, Any]]) -> str:
        """构建发送给 LLM 的压缩指令。"""
        dialog_text = ""
        for m in messages:
            role = m.get("role", "?")
            content = str(m.get("content", ""))[:500]  # 单条截断避免过长
            label = {"user": "用户", "assistant": "助手"}.get(role, role)
            dialog_text += f"{label}: {content}\n"

        return f"""请对以下对话进行结构化摘要压缩。输出必须严格遵循以下 JSON 格式（不要加 markdown 代码块标记）：

{{
  "summary": "用 3-5 句话概括整个对话的核心内容、结论和进展",
  "key_points": ["关键决策1及其原因", "重要发现2", "待办事项3"],
  "user_preferences": ["用户表达的习惯/偏好1", "对XX的要求"],
  "entities": [{{"name": "名称", "type": "人物|项目|文件|组织", "role": "角色描述"}}]
}}

要求：
- summary 要具体，包含数字、名称等关键信息
- key_points 侧重决策和结论，不是流水账
- user_preferences 仅提取用户明确表达的重复性偏好或规则
- entities 提取对话中出现的关键实体及其关系
- 如果某类信息不存在，返回空数组

待压缩对话：

{dialog_text}"""

    @staticmethod
    def _parse_summary(raw: str) -> Dict[str, Any]:
        """从 LLM 输出中解析结构化 JSON。"""
        text = raw.strip()

        # 尝试去掉可能的 markdown 代码块包裹
        for prefix in ("```json", "```"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # JSON 解析失败时尝试提取 summary 字段
        import re
        m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            return {"summary": m.group(1)}

        # 全部失败时把原始文本作为 summary
        return {"summary": text}

    def _persist_to_knowledge_store(
        self,
        store: Any,  # WorkspaceKnowledgeStore
        result: CompressionResult,
        conversation_id: str,
        workspace_id: str,
    ) -> None:
        """将摘要和相关记忆写入知识库。"""
        try:
            # 主摘要条目
            metadata = {
                "compression": {
                    "messages_before": result.messages_before,
                    "messages_after": result.messages_after,
                    "tokens_estimated": result.tokens_estimized if hasattr(result, 'tokens_estimized') else result.tokens_estimated,
                    "key_point_count": len(result.key_points),
                    "preference_count": len(result.user_preferences),
                    "compressed_at": result.compressed_at,
                }
            }

            searchable_parts = [result.summary_text]
            searchable_parts.extend(result.key_points)
            searchable_parts.extend(result.user_preferences)
            searchable_text = "\n".join(searchable_parts)

            store.ingest_resource(
                title=f"对话摘要 — {result.compressed_at[:16]}",
                searchable_text=searchable_text,
                resource_type="conversation_summary",
                source_type="auto_compress",
                workspace_id=workspace_id,
                source_id=conversation_id,
                structured_data={
                    "type": "conversation_summary",
                    "conversation_id": conversation_id,
                    "summary": result.summary_text,
                    "key_points": result.key_points,
                    "user_preferences": result.user_preferences,
                    "entities": result.entities,
                    "compressed_at": result.compressed_at,
                },
                metadata=metadata,
            )

            # 用户偏好单独入库（便于后续精准检索）
            for pref in result.user_preferences:
                try:
                    store.ingest_resource(
                        title=f"用户偏好: {pref[:40]}",
                        searchable_text=pref,
                        resource_type="user_preference",
                        source_type="auto_extract",
                        workspace_id=workspace_id,
                        source_id=conversation_id,
                        structured_data={
                            "type": "user_preference",
                            "preference_text": pref,
                            "source_conversation": conversation_id,
                            "extracted_at": result.compressed_at,
                        },
                    )
                except Exception:
                    pass  # 偏好写入失败不影响主流程

        except Exception as exc:
            logger.warning("摘要写入知识库失败(不影响压缩结果): %s", exc)
