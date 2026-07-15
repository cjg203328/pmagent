"""
路由召回优化回归测试。

验证此前“听不懂”的口语化 PM 表达现在能正确路由到技能，
同时普通闲聊仍不触发技能（无假阳性）。

三层门禁协同：
- 关键词层（INTENT_KEYWORDS 扩充口语同义词）
- embedding 层（语义相似度高时放宽到 action OR entity）
- LLM 层技能清单补齐到全部 17 个技能
"""
from types import SimpleNamespace

from artpm_agent.agent import ArtPMAgent
from artpm_agent.memory.embeddings import DeterministicEmbeddingProvider


def _make_agent():
    """绕过 __init__，用真实离线 embedding 构造可用于路由的 agent。"""
    agent = object.__new__(ArtPMAgent)
    agent._intent_embeddings = None
    agent.memory = SimpleNamespace(
        _get_embedding=DeterministicEmbeddingProvider().embed
    )
    agent.llm_client = None
    agent.config = {}
    agent._build_intent_embeddings()
    return agent


# 之前会回退到通用 LLM 的口语化表达 -> 现在应路由到正确技能
RECALL_CASES = {
    "这个项目能接吗": "project_evaluator",
    "敢接这个单子吗": "project_evaluator",
    "活怎么分": "task_allocator",
    "谁来做原画": "task_allocator",
    "帮我看看赚头": "quote_calculator",
    "这个报价有赚头吗": "quote_calculator",
    "项目卡在哪了": "progress_tracker",
    "进度到哪了": "progress_tracker",
    "做个复盘": "retrospective",
    "出个交付清单": "delivery",
    "排一下工期": "quote_scheduling",
    "预算还够吗": "cost_control",
    "需求复杂度高不高": "requirements_assessment",
    "帮我催催张三": "reminder_bot",
}

# 普通闲聊 -> 不应触发任何技能（召回但不误伤）
CHAT_CASES = [
    "你好",
    "今天天气怎么样",
    "谢谢你",
    "哈哈哈哈",
    "项目进度是什么意思",
    "请解释成本分析的概念",
    "什么是需求评估",
]


def test_recall_routes_conversational_phrasings():
    agent = _make_agent()
    for text, expected in RECALL_CASES.items():
        got = agent._detect_intent(text)
        assert got == expected, f"「{text}」应路由到 {expected}，实际 {got}"


def test_chat_is_not_misrouted():
    agent = _make_agent()
    for text in CHAT_CASES:
        got = agent._detect_intent(text)
        assert got is None, f"闲聊「{text}」不应触发技能，实际 {got}"


def test_routing_tables_are_consistent():
    # 关键词表 / 信号表 / 示例表必须覆盖同一组 17 个技能
    skills = set(ArtPMAgent.INTENT_KEYWORDS)
    assert skills == set(ArtPMAgent.SKILL_ROUTE_SIGNALS)
    assert skills == set(ArtPMAgent.INTENT_EXAMPLES)
    assert len(skills) == 17


def test_llm_classifier_can_route_every_skill():
    # LLM 分类器的有效集合来自其描述表；LLM 回显任一技能名都应被接受，
    # 从而保证 7 大工作流技能不会被 LLM 层遗漏。
    agent = _make_agent()

    class _Echo:
        def __init__(self, name):
            self._name = name

        def chat(self, prompt):
            return self._name

    for name in ArtPMAgent.INTENT_KEYWORDS:
        agent.llm_client = _Echo(name)
        got = agent._detect_intent_via_llm(f"请对 {name} 执行操作")
        assert got == name, f"LLM 层应能路由 {name}，实际 {got}"
