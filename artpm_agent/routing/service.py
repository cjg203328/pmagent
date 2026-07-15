"""Provider-neutral intent routing.

This module owns the three-tier intent detection (keyword -> embedding ->
LLM) that was previously embedded in ``ArtPMAgent``. Keeping it separate lets
the agent class stay a thin orchestration facade while the routing tables and
similarity math live behind a single, testable boundary.

The router is constructed with callables so it never depends on the concrete
agent, LLM client, or memory backend.
"""
from typing import Any, Callable, Dict, List, Mapping, Optional
from collections import OrderedDict
from threading import RLock

from artpm_agent.utils.logger import get_logger

logger = get_logger(__name__)


class IntentRouter:
    """Three-tier intent detection: keywords -> embedding -> LLM.

    Tier 0 (keywords): Deterministic and zero-cost. Strict gate (explicit
        action + business object) keeps precision high.
    Tier 1 (embedding): Offline semantic fallback. A strong similarity score
        relaxes the gate so natural phrasings route correctly.
    Tier 2 (LLM): Flexible fallback, needs an API key. The classifier already
        judged semantics, so a single signal (action OR object) is enough.
    """

    # ── Keyword routing table ──
    INTENT_KEYWORDS = {
        "quote_calculator": [
            "利润", "利润率", "报价", "成本", "毛利", "净利", "计算",
            "赚钱", "收益", "盈利", "赚多少", "成本分析", "算一下",
            "赚头", "回本", "盈亏",
        ],
        "task_allocator": [
            "分配", "任务分配", "派活", "安排", "指派", "谁来做",
            "分工", "分配给", "分配任务", "人员安排",
            "活怎么分", "怎么分", "分活", "人手", "活儿",
        ],
        "progress_tracker": [
            "进度", "延期", "预警", "截止", "跟踪", "状态",
            "项目进度", "催进度", "快到期", "还有几天",
            "卡点", "卡住", "卡在哪", "到哪了", "进度到哪", "堵点",
        ],
        "reminder_bot": [
            "催办", "提醒", "通知", "催一下", "催催", "发消息",
            "催促", "告知", "推送",
        ],
        "document_classifier_parser": [
            "文档", "报价单", "解析", "识别", "上传", "合同",
            "排期表", "文档解析", "导入",
        ],
        "file_search": [
            "查找文件", "搜索文件", "找文件", "有哪些文件", "Excel文件", "查找", "搜索",
            "find file", "search file", "find", "search", "excel files",
        ],
        "file_reader": [
            "读取文件", "打开文件", "查看文件内容", "read file", "open file",
        ],
        "data_analyzer": [
            "分析数据", "数据分析", "统计数据", "描述统计", "analyze data",
        ],
        "trend_analyzer": [
            "趋势分析", "走势", "增长趋势", "下降趋势", "trend", "forecast",
        ],
        "project_evaluator": [
            "项目评估", "可行性评估", "风险收益", "项目风险", "feasibility",
            "能接", "接不接", "能接吗", "要不要接", "敢接", "能接不",
        ],
        "quality_control": [
            "质检", "质量", "评审", "验收", "驳回", "返工", "质量分",
            "质检报告", "把控", "质量把关", "验收单",
        ],
        "requirements_assessment": [
            "需求评估", "需求确认", "范围确认", "复杂度评估", "需求复杂度",
            "报价落库", "导入需求", "建项目", "需求清单",
        ],
        "cost_control": [
            "成本管控", "成本估算", "人天成本", "预算跟踪", "超支告警",
            "成本分析", "预算还剩", "工时成本",
            "预算", "超支", "成本够", "花了多少",
        ],
        "quote_scheduling": [
            "报价排期", "排期", "人天估算", "工期估算", "时间线",
            "里程碑计划", "交付时间", "排期表",
            "工期", "多久做完", "时间计划",
        ],
        "progress_management": [
            "里程碑进度", "进度视图", "阻塞卡点", "风险卡点", "每日站会",
            "站会摘要", "进度巡检",
        ],
        "delivery": [
            "产品交付", "交付清单", "验收单", "记录交付", "版本记录",
            "交付记录", "验收清单",
        ],
        "retrospective": [
            "复盘总结", "结项复盘", "经验沉淀", "项目复盘", "复盘报告",
            "经验教训", "复盘", "复盘一下", "结项", "经验",
        ],
    }

    # Tools should only run for an explicit business action. Broad words such as
    # "状态", "搜索", or "文档" alone are ordinary language and belong to the LLM.
    SKILL_ROUTE_SIGNALS = {
        "quote_calculator": {
            "actions": ("计算", "算一下", "帮我算", "测算", "核算", "评估", "分析", "赚"),
            "entities": ("报价", "成本", "利润", "利润率", "毛利", "净利", "收益", "盈利", "赚头", "回本", "盈亏"),
        },
        "task_allocator": {
            "actions": ("分配", "指派", "派活", "分工", "怎么分", "安排任务", "谁来做", "分给"),
            "entities": ("任务", "工作", "团队", "人员", "成员", "角色", "建模", "原画", "活", "活儿", "人手"),
        },
        "progress_tracker": {
            "actions": ("检查", "查看", "查询", "跟踪", "追踪", "列出", "怎么样", "到哪", "有没有", "卡"),
            "entities": ("项目", "任务", "进度", "延期", "截止", "到期", "交付", "里程碑", "卡点", "堵点"),
        },
        "reminder_bot": {
            "actions": ("催", "催办", "发送", "发消息", "提醒一下", "通知团队", "推送"),
            "entities": ("项目", "任务", "进度", "负责人", "团队", "成员", "截止", "交付"),
        },
        "document_classifier_parser": {
            "actions": ("解析", "识别", "读取", "导入", "上传", "打开", "查看"),
            "entities": ("文件", "文档", "报价单", "合同", "排期表", "excel", "pdf", "图片"),
        },
        "file_search": {
            "actions": ("查找", "搜索", "找文件", "find", "search"),
            "entities": ("文件", "目录", "excel", "pdf", "markdown", "docx", "file", "files"),
        },
        "file_reader": {
            "actions": ("读取", "打开", "查看"),
            "entities": ("文件", "内容", "excel", "pdf", "markdown", "docx", ".md", ".txt"),
        },
        "data_analyzer": {
            "actions": ("分析", "统计", "汇总", "计算"),
            "entities": ("数据", "表格", "数据集", "excel", "csv"),
        },
        "trend_analyzer": {
            "actions": ("分析", "预测", "查看", "判断"),
            "entities": ("趋势", "走势", "增长", "下降", "forecast", "trend"),
        },
        "project_evaluator": {
            "actions": ("评估", "判断", "分析", "能接吗", "可行吗", "能接", "接不接", "要不要接", "敢接"),
            "entities": ("项目", "可行性", "风险", "收益", "预算", "周期", "单子", "接单"),
        },
        "quality_control": {
            "actions": ("提交评审", "评审", "质检", "验收", "驳回", "返工", "把控", "评分"),
            "entities": ("任务", "质量", "验收", "美术", "资产", "质量分", "效果图"),
        },
        "requirements_assessment": {
            "actions": ("评估", "判定", "确认", "高不高", "落库", "导入", "生成", "建项目"),
            "entities": ("需求", "复杂度", "范围", "资产", "报价", "项目"),
        },
        "cost_control": {
            "actions": ("估算", "测算", "跟踪", "检查", "告警", "算", "分析"),
            "entities": ("成本", "预算", "人天", "工时", "超支", "报价"),
        },
        "quote_scheduling": {
            "actions": ("排期", "估算", "排", "计划", "多久", "生成"),
            "entities": ("人天", "工期", "时间线", "里程碑", "交付", "项目"),
        },
        "progress_management": {
            "actions": ("查看", "检查", "巡检", "排查", "列", "摘要", "生成"),
            "entities": ("进度", "里程碑", "阻塞", "卡点", "风险", "项目", "站会"),
        },
        "delivery": {
            "actions": ("生成", "记录", "列出", "交付", "出", "验收"),
            "entities": ("交付", "清单", "验收单", "版本", "资产", "项目"),
        },
        "retrospective": {
            "actions": ("复盘", "总结", "沉淀", "生成", "写"),
            "entities": ("复盘", "经验", "项目", "结项", "教训"),
        },
    }

    # ── Embedding-based intent examples ("wiki") ──
    # Each skill has 15-20 realistic phrasings a PM would use.
    # These are embedded at init and matched via cosine similarity.
    INTENT_EXAMPLES: Dict[str, List[str]] = {
        "quote_calculator": [
            "帮我算一下利润", "报价30万成本20万能赚多少", "这个项目利润率高吗",
            "计算一下成本和利润", "帮我看看这个报价合不合理", "毛利有多少",
            "净利润怎么算", "这个项目能接吗", "报价评估", "帮我算算赚头",
            "利润空间怎么样", "成本分析", "做这个项目划算吗", "盈利测算",
            "帮我看看赚多少", "算一下能挣多少钱", "利润大概几个点",
            "这个单子有钱赚吗", "帮我分析下成本和收益",
        ],
        "task_allocator": [
            "帮我分配一下任务", "这些活怎么分", "谁来做角色建模",
            "安排一下人员", "任务给谁做", "分工安排", "帮我派活",
            "分配工作任务", "谁有空做这个", "帮我安排人手",
            "人员怎么分配", "任务派给谁比较合适", "组里谁能接这个活",
            "分配角色给美术", "这个需求找谁做", "分任务", "分活",
            "帮我分一下任务", "任务分派", "把人安排一下", "活分给谁",
        ],
        "progress_tracker": [
            "项目进度怎么样了", "帮我看看进度", "有没有延期的项目",
            "项目到哪了", "检查一下任务状态", "哪些项目快到期了",
            "进度跟踪", "看一眼项目情况", "最近项目有什么问题",
            "有没有需要跟进的", "项目是不是延期了", "状态查询",
            "还有几天截止", "项目进展如何", "看一下整体进度",
        ],
        "reminder_bot": [
            "帮我催一下进度", "发个提醒", "催办一下", "通知团队",
            "帮我催催", "发送催办消息", "提醒一下负责人",
            "帮我催一下他们", "发消息催一下", "推送个通知",
            "告知一下进度", "催促一下任务",
        ],
        "document_classifier_parser": [
            "帮我解析这个报价单", "上传了一个文件帮我看看", "识别一下这个文档",
            "读取报价单", "分析这个Excel", "文档处理", "导入报价文件",
            "帮我看看这个表格", "解析一下合同", "这个文件里有什么信息",
            "识别文档内容", "报价单分析", "帮我读一下这个文件",
        ],
        "quality_control": [
            "帮我评审任务3", "这个资产的质量分打几分", "任务5验收通过",
            "把任务2驳回", "任务4需要返工", "生成质检报告", "看看项目质量情况",
            "提交任务7评审", "这个美术稿验收一下", "质量把控严一点",
            "帮我出个验收单", "哪些任务要返工", "质量不达标打回",
        ],
        "requirements_assessment": [
            "帮我评估这个角色模型的复杂度", "生成需求范围确认清单",
            "把这份报价单落库建项目", "这个资产复杂度高吗帮我判定一下",
            "导入需求到系统建项目", "评估一下特效资产的复杂度",
        ],
        "cost_control": [
            "估算一下中级原画8小时成本", "跟踪项目预算还剩多少",
            "检查成本是否超支", "测算这个人天成本多少",
            "分析一下报价预算", "成本超支告警检查一下",
        ],
        "quote_scheduling": [
            "排一下这个项目的时间线", "估算角色模型的工期人天",
            "生成里程碑计划", "这个项目多久能交付排期",
            "排期生成各资产时间线", "估算人天生成排期",
        ],
        "progress_management": [
            "查看项目进度里程碑", "排查一下阻塞卡点",
            "生成每日站会摘要", "检查项目风险点",
            "巡检项目进度", "列出里程碑进度视图",
        ],
        "delivery": [
            "生成项目交付清单", "出一张验收单",
            "记录这次资产交付", "记录资产版本v2",
            "列出产品交付清单", "生成验收单逐项签收",
        ],
        "retrospective": [
            "生成项目复盘报告", "结项复盘总结一下",
            "把经验教训沉淀到知识库", "复盘这个项目生成报告",
            "写项目复盘经验", "沉淀经验教训",
        ],
        "project_evaluator": [
            "评估这个项目能不能接", "这个项目能接吗",
            "测算一下项目可行性", "判断项目风险收益",
            "评估一下项目预算周期", "这个单子敢不敢接",
        ],
        "file_search": [
            "帮我找一下报价单文件", "搜索项目里的 excel",
            "查找所有 pdf 文档", "项目目录下有哪些文件",
            "find the contract file", "search markdown 纪要",
        ],
        "file_reader": [
            "读取这份合同内容", "打开报价单文件看看",
            "查看 excel 里的数据", "read the spec file",
        ],
        "data_analyzer": [
            "分析一下这份表格数据", "统计 excel 里的指标",
            "对数据集做描述统计", "analyze the csv data",
        ],
        "trend_analyzer": [
            "分析一下收入走势", "预测下个季度增长趋势",
            "看看成本下降还是上升", "forecast the trend",
        ],
    }

    def __init__(
        self,
        embed_fn: Callable[[str], List[float]],
        llm_client_getter: Callable[[], Any],
        config: Mapping[str, Any],
    ) -> None:
        if not callable(embed_fn):
            raise TypeError("embed_fn must be callable")
        if not callable(llm_client_getter):
            raise TypeError("llm_client_getter must be callable")
        self._embed_fn = embed_fn
        self._llm_client_getter = llm_client_getter
        self._config = config
        self._intent_embeddings: Optional[Dict[str, List[List[float]]]] = None
        self._embedding_lock = RLock()
        # Routing decision cache: identical/near-identical prompts skip the
        # (potentially embedding- or LLM-backed) detection pipeline entirely.
        # Deterministic, so safe to always enable.
        self._route_cache: "OrderedDict[str, Optional[str]]" = OrderedDict()
        self._route_cache_lock = RLock()
        self._route_cache_max = 256

    # ── Public detection API ──

    def detect(self, user_input: str) -> Optional[str]:
        """Run the three tiers and return a skill name or None."""
        cache_key = user_input.strip().lower()
        with self._route_cache_lock:
            if cache_key in self._route_cache:
                return self._route_cache[cache_key]

        skill, kw_score = self.detect_via_keywords(user_input)
        if skill and self.is_high_confidence(
            skill_name=skill, text=user_input, tier="keyword", kw_score=kw_score
        ):
            result = skill
        else:
            if self._intent_embeddings is None:
                self.build_embeddings()
            skill, sim = self.detect_via_embedding(user_input)
            if skill and self.is_high_confidence(
                skill_name=skill, text=user_input, tier="embedding", sim=sim
            ):
                result = skill
            elif (
                len(user_input.strip()) >= 8
                and self._llm_client is not None
                and self._intent_classification_enabled
            ):
                intent = self.detect_via_llm(user_input)
                result = (
                    intent
                    if intent
                    and self.is_high_confidence(
                        skill_name=intent, text=user_input, tier="llm"
                    )
                    else None
                )
            else:
                result = None

        with self._route_cache_lock:
            self._route_cache[cache_key] = result
            self._route_cache.move_to_end(cache_key)
            while len(self._route_cache) > self._route_cache_max:
                self._route_cache.popitem(last=False)
        return result

    def is_high_confidence(
        self,
        *,
        skill_name: str,
        text: str,
        tier: str = "keyword",
        sim: float = 0.0,
        kw_score: float = 0.0,
    ) -> bool:
        """Decide whether a routed skill should actually execute."""
        signals = self.SKILL_ROUTE_SIGNALS.get(skill_name)
        if not signals:
            return False

        t = text.lower()
        if any(
            marker in t
            for marker in ("是什么意思", "什么是", "概念", "定义", "怎么理解", "如何理解")
        ):
            return False
        has_action = any(s in t for s in signals["actions"])
        has_entity = any(s in t for s in signals["entities"])

        if tier == "keyword":
            return has_action and has_entity

        if tier == "embedding":
            if sim >= 0.40:
                return has_action or has_entity
            if has_action and has_entity:
                return True
            return has_entity and sim >= 0.30

        if tier == "llm":
            return has_action or has_entity

        return has_action and has_entity

    def detect_via_embedding(self, user_input: str):
        """Match against pre-computed intent example embeddings (dot product)."""
        MIN_SIM = 0.25
        MARGIN = 0.05

        if len(user_input.strip()) < 6:
            return None, 0.0

        if self._intent_embeddings is None:
            return None, 0.0
        query_vec = self._embed_fn(user_input)

        skill_scores: Dict[str, float] = {}
        for skill_name, embeddings in self._intent_embeddings.items():
            best = max(
                sum(a * b for a, b in zip(query_vec, ev))
                for ev in embeddings
            )
            skill_scores[skill_name] = best

        ranked = sorted(skill_scores.items(), key=lambda x: x[1], reverse=True)
        if not ranked:
            return None, 0.0

        best_skill, best_sim = ranked[0]
        second_sim = ranked[1][1] if len(ranked) > 1 else 0.0

        if best_sim >= MIN_SIM and (best_sim - second_sim) >= MARGIN:
            return best_skill, best_sim

        return None, 0.0

    def detect_via_llm(self, user_input: str) -> Optional[str]:
        """LLM classifier into one of the known skills or None."""
        skill_descriptions = {
            "quote_calculator": "利润计算、成本分析、报价评估、盈利测算",
            "task_allocator": "任务分配、人员安排、分工派活",
            "progress_tracker": "进度检查、延期预警、状态查询、项目跟踪",
            "reminder_bot": "催办提醒、发送通知、催促消息",
            "document_classifier_parser": "文档解析、报价单识别、文件上传处理",
            "file_search": "按名称或扩展名搜索本地项目文件",
            "file_reader": "读取指定本地文件内容",
            "data_analyzer": "分析 Excel、CSV、JSON 或结构化数据",
            "trend_analyzer": "计算时间序列的上升、下降和预测趋势",
            "project_evaluator": "评估项目利润、工期、风险和可行性",
            "quality_control": "提交评审、质量把控、验收/驳回/返工、质量评分",
            "requirements_assessment": "需求评估、复杂度判定、范围确认、建项目",
            "cost_control": "成本估算、预算跟踪、人天工时成本、超支告警",
            "quote_scheduling": "排期、人天估算、工期计划、里程碑与时间线",
            "progress_management": "里程碑进度视图、阻塞卡点、每日站会摘要",
            "delivery": "产品交付清单、验收单、版本记录、交付台账",
            "retrospective": "项目复盘总结、结项报告、经验教训沉淀",
        }

        skills_text = "\n".join(
            f"- {name}: {desc}" for name, desc in skill_descriptions.items()
        )

        prompt = f"""你是一个意图分类器。分析用户消息，判断它最匹配哪个功能模块。

可用模块:
{skills_text}
- chat: 普通闲聊、问候、不涉及以上功能的问题

用户消息: "{user_input}"

规则:
1. 只返回上方列出的模块名称或 chat
2. 如果用户的问题隐含了某个功能需求，选最匹配的模块。例如:
   - "这个项目能接吗" → project_evaluator（评估可行性）
   - "帮我看看赚头" → quote_calculator
   - "活怎么分" → task_allocator
   - "项目到哪了" → progress_tracker
   - "帮我催催" → reminder_bot
   - "这份报价单解析一下" → document_classifier_parser
   - "帮我看看质量" → quality_control
   - "需求复杂度高不高" → requirements_assessment
   - "预算还够吗" → cost_control
   - "排一下工期" → quote_scheduling
   - "有没有卡住的任务" → progress_management
   - "出个交付清单" → delivery
   - "做个复盘" → retrospective
3. 只返回模块名，不要任何解释。"""

        try:
            llm = self._llm_client
            if llm is None:
                return None
            response = llm.chat(prompt)
            if not response:
                return None
            parts = response.strip().strip('"\'')
            parts = parts.split() if parts else []
            result = parts[0].lower() if parts else None
            valid = set(skill_descriptions.keys())
            if result in valid:
                return result
            if result == "chat":
                return None
            return None
        except Exception as e:
            logger.warning("[IntentRouter] LLM intent detection failed: %s", e)
            return None

    def detect_via_keywords(self, user_input: str):
        """Weighted keyword matching — offline fallback."""
        text = user_input.lower()
        scores: Dict[str, float] = {}
        for skill_name, keywords in self.INTENT_KEYWORDS.items():
            total = 0.0
            for kw in keywords:
                if kw.lower() in text:
                    total += len(kw)
            if total > 0:
                scores[skill_name] = total

        if not scores:
            return None, 0.0

        best = max(scores, key=scores.get)
        return best, scores[best]

    def build_embeddings(self) -> None:
        """Pre-compute embeddings for all intent examples (offline, no API)."""
        if self._intent_embeddings is not None:
            return
        with self._embedding_lock:
            if self._intent_embeddings is not None:
                return
            built: Dict[str, List[List[float]]] = {}
            try:
                for skill_name, examples in self.INTENT_EXAMPLES.items():
                    built[skill_name] = [self._embed_fn(ex) for ex in examples]
            except Exception as error:
                logger.warning("[IntentRouter] embedding cache build failed: %s", error)
                return
            self._intent_embeddings = built
        logger.info(
            "[IntentRouter] embeddings built: %d examples",
            sum(len(v) for v in self._intent_embeddings.values()),
        )

    # ── Internal helpers ──

    @property
    def _llm_client(self) -> Any:
        return self._llm_client_getter()

    @property
    def _intent_classification_enabled(self) -> bool:
        try:
            return bool(self._config.get("llm.intent_classification_enabled", False))
        except Exception:
            return False
