"""跨会话记忆管理器 — 核心记忆的持久化、检索与生命周期管理。

对标 Kimi 的「长期记忆」能力：
- 从对话中自动提取并沉淀用户偏好、项目上下文、决策历史、实体关系
- 新会话启动时根据当前输入自动检索相关记忆注入 context
- 记忆有置信度/衰减机制（复用 ConsolidationService）
- 支持用户显式「记住 XXX」指令和隐式提取两种模式

记忆类型分类：
  - user_preference: 用户反复表达的习惯/偏好/规则
  - project_context: 项目相关的关键信息（技术栈、架构决策等）
  - decision_history: 重要决策及其原因和结果
  - entity_relationship: 人/组织/文件之间的关系
  - fact: 通用事实性知识
"""

from __future__ import annotations

import json
import logging
from math import isfinite
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_RETRIEVE_TOP_K = 5          # 检索返回的最大条数
DEFAULT_MAX_INJECTION_CHARS = 4000   # 注入 context 的最大字符数
DEFAULT_CONFIDENCE_FLOOR = 0.1       # 置信度下限（低于此不返回）
DEFAULT_PREFERENCE_MIN_OCCURRENCES = 2  # 隐式偏好最小出现次数


@dataclass
class MemoryItem:
    """单条跨会话记忆。"""

    id: str = ""
    memory_type: str = ""            # preference / project_context / decision / entity / fact
    content: str = ""                # 记忆正文
    source_conversation: str = ""    # 来源会话 ID
    source_type: str = ""            # explicit(用户说"记住") / auto_extract(自动提取)
    confidence: float = 1.0          # 置信度 [0, 1]
    hit_count: int = 0               # 被命中次数
    created_at: str = ""
    last_hit_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "memory_type": self.memory_type,
            "content": self.content,
            "source_conversation": self.source_conversation,
            "source_type": self.source_type,
            "confidence": self.confidence,
            "hit_count": self.hit_count,
            "created_at": self.created_at,
            "last_hit_at": self.last_hit_at,
            "metadata": self.metadata,
        }


class CrossSessionMemory:
    """跨会话记忆的统一管理层。

    不直接操作数据库，通过 WorkspaceKnowledgeStore 做所有读写，
    保持单一数据源。记忆在 KnowledgeStore 中以 resource_type 标识。
    """

    TYPE_MAP = {
        "user_preference": "user_preference",
        "project_context": "project_context",
        "decision_history": "decision_history",
        "entity_relationship": "entity_relationship",
        "fact": "fact",
        "conversation_summary": "conversation_summary",
    }

    def __init__(
        self,
        knowledge_store: Any,  # WorkspaceKnowledgeStore 实例
        *,
        retrieve_top_k: int = DEFAULT_RETRIEVE_TOP_K,
        max_injection_chars: int = DEFAULT_MAX_INJECTION_CHARS,
        confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    ):
        if (
            isinstance(retrieve_top_k, bool)
            or not isinstance(retrieve_top_k, int)
            or not 1 <= retrieve_top_k <= 100
        ):
            raise ValueError("retrieve_top_k must be between 1 and 100")
        if (
            isinstance(max_injection_chars, bool)
            or not isinstance(max_injection_chars, int)
            or not 1 <= max_injection_chars <= 100_000
        ):
            raise ValueError(
                "max_injection_chars must be between 1 and 100000"
            )
        if isinstance(confidence_floor, bool) or not isinstance(
            confidence_floor, (int, float)
        ):
            raise ValueError("confidence_floor must be between 0 and 1")
        try:
            confidence_floor = float(confidence_floor)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("confidence_floor must be between 0 and 1") from error
        if not isfinite(confidence_floor) or not 0.0 <= confidence_floor <= 1.0:
            raise ValueError("confidence_floor must be between 0 and 1")
        self.store = knowledge_store
        self.retrieve_top_k = retrieve_top_k
        self.max_injection_chars = max_injection_chars
        self.confidence_floor = confidence_floor

    # ---- 写入接口 ------------------------------------------------------

    def save_memory(
        self,
        content: str,
        memory_type: str,
        *,
        tenant_id: str = "local",
        workspace_id: str = "local-default",
        source_conversation: str = "",
        source_type: str = "auto_extract",
        metadata: Optional[Dict[str, Any]] = None,
        confidence: float = 1.0,
    ) -> Optional[str]:
        """保存一条新记忆到知识库。

        Args:
            content: 记忆正文
            memory_type: 记忆类型 (preference/project_context/decision/entity/fact)
            workspace_id: 记忆所属工作区
            source_conversation: 来源会话 ID
            source_type: explicit 或 auto_extract
            metadata: 附加元信息
            confidence: 初始置信度

        Returns:
            资源 ID，失败时返回 None
        """
        if memory_type not in self.TYPE_MAP:
            logger.warning("未知记忆类型 '%s', 降级为 fact", memory_type)
            memory_type = "fact"

        try:
            normalized_content = " ".join(str(content).split()).casefold()
            content_key = sha256(normalized_content.encode("utf-8")).hexdigest()[:20]
            stable_source_id = (
                f"{source_conversation or 'global'}:{memory_type}:{content_key}"
            )
            result = self.store.ingest_resource(
                title=self._build_title(content, memory_type),
                searchable_text=content,
                resource_type=memory_type,
                source_type="memory_" + source_type,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                # Source identity must be content-level, not only conversation
                # level.  Otherwise two memories from one conversation become
                # versions of one resource and different memory types collide.
                source_id=stable_source_id,
                structured_data={
                    "type": "cross_session_memory",
                    "memory_subtype": memory_type,
                    "content": content,
                    "source_conversation": source_conversation,
                    "source_type": source_type,
                    **(metadata or {}),
                },
                metadata={"initial_confidence": confidence},
            )
            rid = result.get("id", "")
            logger.debug("保存记忆 [%s]: %s… (%s)", memory_type, content[:40], rid)
            return rid
        except Exception as exc:
            logger.error("保存记忆失败: %s", exc)
            return None

    def save_batch(
        self,
        items: Iterable[Dict[str, Any]],
        *,
        tenant_id: str = "local",
        workspace_id: str = "local-default",
        source_conversation: str = "",
        source_type: str = "auto_extract",
    ) -> int:
        """批量保存记忆，返回成功数量。"""
        count = 0
        for item in items:
            content = item.get("content", "")
            mtype = item.get("memory_type", "fact")
            meta = item.get("metadata")
            conf = float(item.get("confidence", 1.0))
            if self.save_memory(
                content, mtype,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source_conversation=source_conversation,
                source_type=source_type,
                metadata=meta,
                confidence=conf,
            ):
                count += 1
        return count

    def extract_and_save_from_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        conversation_id: str = "",
        tenant_id: str = "local",
        workspace_id: str = "local-default",
        llm_callable: Any = None,  # 可选 LLM 用于增强提取
    ) -> List[MemoryItem]:
        """从消息列表中提取记忆并持久化。

        分两阶段：
        1. 正则规则匹配（免费、确定性强）— 用户显式说"记住"/"以后"
        2. LLM 辅助提取（可选）— 识别隐性偏好和实体关系

        Returns:
            提取出的 MemoryItem 列表
        """
        extracted: List[MemoryItem] = []

        # Phase 1: 规则匹配
        rule_items = self._extract_by_rules(messages, conversation_id)
        extracted.extend(rule_items)

        # Phase 2: LLM 增强（如果可用）
        if llm_callable is not None:
            try:
                llm_items = self._extract_by_llm(messages, conversation_id, llm_callable)
                extracted.extend(llm_items)
            except Exception as exc:
                logger.warning("LLM 记忆提取失败，仅使用规则提取: %s", exc)

        # 持久化
        for item in extracted:
            self.save_memory(
                item.content,
                item.memory_type,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source_conversation=item.source_conversation,
                source_type=item.source_type,
                metadata=item.metadata,
                confidence=item.confidence,
            )

        logger.info(
            "从对话 %s 提取 %d 条记忆 (规则: %d, LLM: %d)",
            conversation_id, len(extracted), len(rule_items),
            len(extracted) - len(rule_items),
        )
        return extracted

    # ---- 检索接口 ------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        memory_types: Optional[List[str]] = None,
        top_k: int = 0,
        max_chars: int = 0,
        tenant_id: str = "local",
        workspace_id: str = "local-default",
        exclude_conversation: str = "",  # 排除当前会话的记忆
    ) -> List[MemoryItem]:
        """根据查询文本检索相关记忆。

        Args:
            query: 用户当前输入或上下文
            memory_types: 限制返回类型，None 表示全部
            top_k: 返回条数上限（默认用实例配置）
            max_chars: 单条最大字符数（默认用实例配置）
            workspace_id: 只检索此工作区的记忆
            exclude_conversation: 排除指定来源（避免自我引用）

        Returns:
            相关 MemoryItem 列表（按相关性排序）
        """
        if not isinstance(query, str) or not query.strip():
            return []
        query = query.strip()
        if len(query) > 4_000:
            return []
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            return []
        if top_k == 0:
            top_k = self.retrieve_top_k
        elif not 1 <= top_k <= 100:
            return []
        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            return []
        if max_chars == 0:
            max_chars = self.max_injection_chars
        elif not 1 <= max_chars <= 100_000:
            return []

        resource_types = (
            [self.TYPE_MAP.get(t, t) for t in memory_types]
            if memory_types else None
        )
        candidate_limit = top_k
        if exclude_conversation:
            try:
                max_search_limit = int(
                    getattr(self.store, "MAX_SEARCH_LIMIT", 100)
                )
            except (TypeError, ValueError, OverflowError):
                max_search_limit = 100
            max_search_limit = max(1, min(max_search_limit, 100))
            candidate_limit = min(max_search_limit, max(top_k * 4, top_k + 4))

        try:
            results = self.store.search(
                query,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                limit=candidate_limit,
                resource_types=resource_types,
                include_rules=False,
                # WorkspaceKnowledgeStore requires at least 100 characters,
                # while this compatibility layer allows smaller injection
                # budgets. Fetch the minimum accepted excerpt and trim below.
                max_text_chars=max(100, max_chars),
                use_confidence=True,
                confidence_floor=self.confidence_floor,
            )

            items: List[MemoryItem] = []
            seen_ids: set = set()
            total_chars = 0

            for r in results or []:
                if not isinstance(r, Mapping):
                    continue
                rid = str(r.get("id") or "").strip()
                if not rid:
                    continue
                if rid in seen_ids:
                    continue

                src_conv = ""
                version = r.get("version")
                if not isinstance(version, Mapping):
                    version = {}
                struct = r.get("structured_data") or version.get(
                    "structured_data"
                ) or {}
                if isinstance(struct, str):
                    try:
                        struct = json.loads(struct)
                    except (json.JSONDecodeError, TypeError):
                        struct = {}
                if isinstance(struct, dict):
                    src_conv = struct.get("source_conversation", "")

                # 排除当前会话
                if exclude_conversation and src_conv == exclude_conversation:
                    continue

                text = str(r.get("text") or r.get("searchable_text") or "")
                text = text[:max_chars]
                if not text:
                    continue
                mtype = str(r.get("resource_type") or "fact")

                raw_confidence = r.get("confidence", 1.0)
                if isinstance(raw_confidence, bool):
                    continue
                try:
                    confidence = float(raw_confidence)
                except (TypeError, ValueError, OverflowError):
                    continue
                if not isfinite(confidence):
                    continue
                metadata = r.get("metadata")
                if not isinstance(metadata, Mapping):
                    metadata = {}

                # 字符预算控制
                if total_chars + len(text) > max_chars and items:
                    break

                item = MemoryItem(
                    id=rid,
                    memory_type=mtype,
                    content=text,
                    source_conversation=src_conv,
                    source_type="memory",
                    confidence=confidence,
                    metadata=dict(metadata),
                )
                items.append(item)
                seen_ids.add(rid)
                total_chars += len(text)

                # 记录命中（用于衰减/强化计算）
                try:
                    self.store.record_hit(
                        rid,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                    )
                except Exception:
                    pass

                if len(items) >= top_k:
                    break

            return items

        except Exception as exc:
            logger.error("记忆检索失败: %s", exc)
            return []

    def get_all_preferences(
        self,
        *,
        limit: int = 20,
        tenant_id: str = "local",
        workspace_id: str = "local-default",
    ) -> List[MemoryItem]:
        """获取所有用户偏好记忆（用于 system prompt 注入）。"""
        try:
            results = self.store.search(
                "用户偏好 设置 规则 习惯 要求",
                workspace_id=workspace_id,
                limit=limit,
                tenant_id=tenant_id,
                resource_types=["user_preference"],
                include_rules=False,
                max_text_chars=500,
                use_confidence=True,
                confidence_floor=self.confidence_floor,
            )
            return [
                MemoryItem(
                    id=r.get("id", ""),
                    memory_type="user_preference",
                    content=(r.get("text") or r.get("searchable_text") or ""),
                    confidence=float(r.get("confidence", 1.0)),
                )
                for r in results
            ]
        except Exception as exc:
            logger.error("获取用户偏好失败: %s", exc)
            return []

    # ---- 注入格式化 ----------------------------------------------------

    def format_for_context(
        self,
        items: List[MemoryItem],
        *,
        include_type_header: bool = True,
    ) -> str:
        """将记忆列表格式化为可注入 LLM context 的文本。"""
        if not items:
            return ""

        parts: List[str] = []
        current_type = None

        for item in items:
            if include_type_header and item.memory_type != current_type:
                current_type = item.memory_type
                type_label = {
                    "user_preference": "用户偏好",
                    "project_context": "项目上下文",
                    "decision_history": "历史决策",
                    "entity_relationship": "实体关系",
                    "fact": "事实知识",
                    "conversation_summary": "历史摘要",
                }.get(current_type, current_type)
                parts.append(f"\n[{type_label}]")

            parts.append(f"- {item.content}")

        return "\n".join(parts)

    def build_memory_context(
        self,
        user_input: str,
        *,
        conversation_id: str = "",
        tenant_id: str = "local",
        workspace_id: str = "local-default",
        include_preferences: bool = True,
    ) -> str:
        """为一次 LLM 调用构建完整的记忆上下文。

        组合：
        1. 全局用户偏好（始终注入）
        2. 与当前输入相关的记忆（语义检索）
        """
        parts: List[str] = []

        # 用户偏好
        if include_preferences:
            prefs = self.get_all_preferences(
                limit=10,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if prefs:
                parts.append("[已知用户偏好]")
                for p in prefs:
                    parts.append(f"- {p.content}")

        # 相关记忆检索
        relevant = self.retrieve(
            user_input,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            exclude_conversation=conversation_id,
        )
        if relevant:
            formatted = self.format_for_context(relevant)
            if formatted:
                parts.append("\n[相关记忆]")
                parts.append(formatted)

        return "\n".join(parts)

    # ---- 内部方法 ------------------------------------------------------

    @staticmethod
    def _build_title(content: str, memory_type: str) -> str:
        maxlen = 60
        short = content.replace("\n", " ").strip()[:maxlen]
        labels = {
            "user_preference": "偏好",
            "project_context": "上下文",
            "decision_history": "决策",
            "entity_relationship": "实体",
            "fact": "事实",
        }
        label = labels.get(memory_type, "记忆")
        return f"[{label}] {short}"

    @staticmethod
    def _extract_by_rules(
        messages: Sequence[Dict[str, Any]],
        conversation_id: str,
    ) -> List[MemoryItem]:
        """基于正则规则从对话中提取显式记忆表达。"""
        items: List[MemoryItem] = []

        # 显式记忆指令模式
        explicit_patterns = [
            (r"记住\s*[：:]\s*(.{2,500})", "fact"),
            (r"记住\s+(.{2,500})", "fact"),
            (r"(?:以后|今后|下次|默认|总是)\s*(?:要|应该|必须)?(.{2,300})", "user_preference"),
            (r"不要(?:再?)\s*(.{2,300})", "user_preference"),
            (r"(?:把|将)(.{2,200})(?:设为|作为)(?:默认|标准|规则)", "user_preference"),
            (r"(?:采用|使用)(.{2,200})(?:方案|方式|策略|技术栈)", "project_context"),
        ]

        # Only explicit user statements can become durable facts.  Including
        # assistant text here would persist model guesses as if the user had
        # confirmed them.
        full_text = "\n".join(
            str(m.get("content", ""))
            for m in messages
            if isinstance(m, dict) and m.get("role") == "user"
        )

        for pattern, mtype in explicit_patterns:
            for match in re.finditer(pattern, full_text, re.IGNORECASE):
                content = match.group(1).strip()
                if len(content) < 4:
                    continue
                items.append(MemoryItem(
                    memory_type=mtype,
                    content=content,
                    source_conversation=conversation_id,
                    source_type="explicit",
                    confidence=1.0,
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))

        return items

    @staticmethod
    def _extract_by_llm(
        messages: Sequence[Dict[str, Any]],
        conversation_id: str,
        llm_callable: Any,  # Callable[[str], str]
    ) -> List[MemoryItem]:
        """调用 LLM 从对话中提取隐性记忆。"""
        # 只取最近的 30 条消息，避免 prompt 过长
        recent = messages[-30:] if len(messages) > 30 else messages

        dialog_text = ""
        for m in recent:
            role = {"user": "用户", "assistant": "助手"}.get(m.get("role", ""), m.get("role", ""))
            content = str(m.get("content", ""))[:300]
            dialog_text += f"{role}: {content}\n"

        prompt = f"""从以下对话中提取值得长期记住的信息。只输出 JSON 数组（不要 markdown 包裹），每项包含:

{{
  "content": "具体内容（完整句子）",
  "type": "preference | project_context | decision | entity | fact",
  "confidence": 0.5~1.0
}}

要求：
- 只提取有长期价值的信息（偏好、关键决策、重要上下文、实体关系）
- 不要提取临时性的闲聊、单次指令
- 如果没有值得记住的内容，返回空数组 []
- confidence 反映确定性（用户明确说的=1.0，推断的=0.6）

对话：

{dialog_text}"""

        raw = llm_callable(prompt)
        if not raw or not raw.strip():
            return []

        text = raw.strip()
        for prefix in ("```json", "```"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []

        items: List[MemoryItem] = []
        now = datetime.now(timezone.utc).isoformat()
        for item in parsed:
            content = item.get("content", "").strip()
            mtype = item.get("type", "fact")
            conf = float(item.get("confidence", 0.7))
            type_aliases = {
                "preference": "user_preference",
                "decision": "decision_history",
                "entity": "entity_relationship",
            }
            mtype = type_aliases.get(mtype, mtype)
            if len(content) >= 4 and mtype in CrossSessionMemory.TYPE_MAP:
                items.append(MemoryItem(
                    memory_type=mtype,
                    content=content,
                    source_conversation=conversation_id,
                    source_type="auto_extract",
                    confidence=max(0.1, min(1.0, conf)),
                    created_at=now,
                ))

        return items
