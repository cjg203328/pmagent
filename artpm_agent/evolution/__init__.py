"""Evolution subsystem — closes the learn-from-experience loop.

Public surface:
    * ``StrategyStore`` — approved strategies/rules that shape future turns.
    * ``ReflectionJob`` (reflection.py) — mines Episodes + feedback into
      improvement proposals and applies the low-risk ones.
    * ``ReflectionScheduler`` (scheduler.py) — throttles reflection runs.
    * ``MetaMemory`` / ``MetaMemoryStore`` (meta_memory.py) — Phase 4 元记忆：
      识别「已知/未知/缺口」并给出「联网检索 / 向用户澄清」建议。

All self-modifications are routed through ``workflows.risk_policy`` so that
only low-risk changes apply automatically; medium/high-risk proposals are
recorded as ``proposed`` and wait for human approval.
"""

from artpm_agent.evolution.strategy_store import Strategy, StrategyStore
from artpm_agent.evolution.reflection import (
    ImprovementProposal,
    ReflectionJob,
    ReflectionReport,
    run_reflection,
)
from artpm_agent.evolution.scheduler import ReflectionScheduler
from artpm_agent.evolution.meta_memory import (
    KnowledgeGap,
    MetaMemory,
    MetaMemoryReport,
    MetaMemoryStore,
    format_meta_memory_context,
    get_default_meta_memory,
    get_default_meta_memory_store,
)

__all__ = [
    "Strategy",
    "StrategyStore",
    "ImprovementProposal",
    "ReflectionJob",
    "ReflectionReport",
    "run_reflection",
    "ReflectionScheduler",
    "KnowledgeGap",
    "MetaMemory",
    "MetaMemoryReport",
    "MetaMemoryStore",
    "format_meta_memory_context",
    "get_default_meta_memory",
    "get_default_meta_memory_store",
]
