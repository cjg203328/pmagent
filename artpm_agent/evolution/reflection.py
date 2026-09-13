"""Reflection job — the evolution loop.

Mines per-turn ``Episode`` outcomes (success / failure / error-kind / feedback)
plus retrospective knowledge into concrete ``ImprovementProposal``s, then applies
the low-risk ones. Medium/high-risk proposals are recorded as ``proposed`` and
wait for human approval, so the agent can never silently self-modify risky
behaviour.

This is the "G2–G7 闭环" piece from the design doc: event stream + retrospective
experience -> proposals -> approved strategies/preferences -> injected next turn
by the MemoryRetrievalHook.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from artpm_agent.memory.episode_store import Episode, EpisodeStore
from artpm_agent.memory.feedback_store import FeedbackStore
from artpm_agent.evolution.strategy_store import Strategy, StrategyStore
from artpm_agent.tenancy import TenantContextManager, WorkspaceAccessDenied
from artpm_agent.workflows.risk_policy import DEFAULT_RISK_POLICY

logger = logging.getLogger(__name__)

# Thresholds that gate proposal generation.
FAILURE_RATE_THRESHOLD = 0.40
HIGH_FAILURE_RATE = 0.60
MIN_SAMPLES = 5

# Risk → required approval. Mirrors workflows/risk_policy intent: low-risk,
# local, reversible changes apply automatically; anything stronger waits.
_RISK_TO_APPROVAL = {
    "low": "none",
    "medium": "user",
    "high": "admin",
    "critical": "admin",
    "untrusted": "admin",
}

# Capability names used so the existing governance layer can reason about
# evolution self-modifications if wired up later.
_CAP_FOR_KIND = {
    "strategy": "evolution.apply_strategy",
    "preference": "evolution.update_preference",
    "routing": "evolution.adjust_routing",
}


@dataclass
class EpisodeStats:
    """Aggregated learning signals over a window of episodes."""

    episodes: List[Episode] = field(default_factory=list)
    per_handler: Dict[str, List[Episode]] = field(default_factory=dict)
    feedbacks: List[Episode] = field(default_factory=list)
    tenant_id: str = ""
    workspace_id: str = ""
    principal_id: str = ""
    scope_consistent: bool = True


@dataclass
class ImprovementProposal:
    """One concrete suggestion for improving future behaviour."""

    kind: str                       # strategy | preference | routing
    target: str = ""               # handler / skill / "global"
    rationale: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    risk: str = "low"              # low | medium | high | critical
    source: str = "episode"        # episode | feedback | manual
    status: str = "proposed"       # proposed | applied | rejected
    tenant_id: str = ""
    workspace_id: str = ""
    principal_id: str = ""


@dataclass
class ReflectionReport:
    """Outcome of one reflection run, for logging / observability."""

    episodes_analyzed: int = 0
    proposals_total: int = 0
    applied: List[ImprovementProposal] = field(default_factory=list)
    pending: List[ImprovementProposal] = field(default_factory=list)
    failure_by_handler: Dict[str, float] = field(default_factory=dict)

    @property
    def applied_count(self) -> int:
        return len(self.applied)

    @property
    def pending_count(self) -> int:
        return len(self.pending)

    def __str__(self) -> str:  # pragma: no cover - debug helper
        lines = [
            f"[Reflection] analyzed={self.episodes_analyzed} "
            f"proposals={self.proposals_total} "
            f"applied={self.applied_count} pending={self.pending_count}"
        ]
        for p in self.applied:
            lines.append(f"  ✓ applied[{p.risk}] {p.kind}:{p.target}")
        for p in self.pending:
            lines.append(f"  … pending[{p.risk}] {p.kind}:{p.target}")
        return "\n".join(lines)


def mine_episodes(
    episode_store: EpisodeStore,
    *,
    since: Optional[str] = None,
    limit: int = 500,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    principal_id: Optional[str] = None,
    all_principals: bool = False,
) -> EpisodeStats:
    """Aggregate recent episodes into per-handler failure stats + feedback."""
    episodes = episode_store.recent(
        limit=limit,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=principal_id,
        all_principals=all_principals,
    )
    if since:
        episodes = [e for e in episodes if (e.created_at or "") >= since]

    per_handler: Dict[str, List[Episode]] = {}
    feedbacks: List[Episode] = []
    for e in episodes:
        per_handler.setdefault(e.handler, []).append(e)
        if e.feedback:
            feedbacks.append(e)

    scope_pairs = {
        (
            str(e.tenant_id or "local"),
            str(e.workspace_id or "local-default"),
        )
        for e in episodes
    }
    scope_consistent = len(scope_pairs) <= 1
    if scope_pairs:
        resolved_tenant, resolved_workspace = next(iter(scope_pairs))
    else:
        resolved_tenant = str(tenant_id or "")
        resolved_workspace = str(workspace_id or "")
    principals = {str(e.principal_id or "") for e in episodes}
    resolved_principal = str(principal_id or "") if len(principals) != 1 else next(iter(principals))
    return EpisodeStats(
        episodes=episodes,
        per_handler=per_handler,
        feedbacks=feedbacks,
        tenant_id=resolved_tenant,
        workspace_id=resolved_workspace,
        principal_id=resolved_principal,
        scope_consistent=scope_consistent,
    )


def generate_proposals(
    stats: EpisodeStats,
    feedback_store: Optional[FeedbackStore] = None,
    *,
    min_samples: int = MIN_SAMPLES,
) -> List[ImprovementProposal]:
    """Turn aggregated signals into improvement proposals."""
    proposals: List[ImprovementProposal] = []

    for handler, eps in stats.per_handler.items():
        total = len(eps)
        if total < min_samples:
            continue
        failed = [e for e in eps if not e.success]
        if not failed:
            continue
        rate = len(failed) / total
        if rate < FAILURE_RATE_THRESHOLD:
            continue

        kinds = [e.error_kind for e in failed if e.error_kind]
        dominant = max(set(kinds), key=kinds.count) if kinds else None
        risk = "medium" if rate >= HIGH_FAILURE_RATE else "low"

        rule = (
            f"处理「{handler}」类请求时近期失败率 {rate:.0%}"
            + (f"，主要错误类型：{dominant}" if dominant else "")
            + "。建议增加前置校验或提前回退到模型兜底，避免重复失败。"
        )
        proposals.append(
            ImprovementProposal(
                kind="strategy",
                target=handler,
                rationale=f"failure_rate={rate:.2f}, n={total}",
                payload={"rule_text": rule},
                risk=risk,
                source="episode",
                tenant_id=stats.tenant_id,
                workspace_id=stats.workspace_id,
            )
        )
        if rate >= HIGH_FAILURE_RATE:
            proposals.append(
                ImprovementProposal(
                    kind="routing",
                    target=handler,
                    rationale=f"high failure rate {rate:.2f}",
                    payload={"action": "downweight_or_guard"},
                    risk="medium",
                    source="episode",
                    tenant_id=stats.tenant_id,
                    workspace_id=stats.workspace_id,
                )
            )

    for e in stats.feedbacks:
        fb = (e.feedback or "").strip()
        if not fb:
            continue
        if "👎" in fb or fb.lower().startswith("bad") or "差" in fb:
            proposals.append(
                ImprovementProposal(
                    kind="preference",
                    target=e.handler,
                    rationale=f"negative feedback on turn {e.turn_id}",
                    payload={
                        "kind": "avoid",
                        "content": (
                            f"用户反馈（回合 {e.turn_id}）：{fb}。"
                            f"后续此类请求应更谨慎或更换策略。"
                        ),
                    },
                    risk="low",
                    source="feedback",
                    tenant_id=e.tenant_id,
                    workspace_id=e.workspace_id,
                    principal_id=e.principal_id,
                )
            )
        elif "👍" in fb or fb.lower().startswith("good") or "好" in fb:
            proposals.append(
                ImprovementProposal(
                    kind="preference",
                    target=e.handler,
                    rationale=f"positive feedback on turn {e.turn_id}",
                    payload={
                        "kind": "preference",
                        "content": f"用户认可此类回答（回合 {e.turn_id}）。",
                    },
                    risk="low",
                    source="feedback",
                    tenant_id=e.tenant_id,
                    workspace_id=e.workspace_id,
                    principal_id=e.principal_id,
                )
            )
    return proposals


def assess_approval(
    proposal: ImprovementProposal,
    risk_policy: Any = DEFAULT_RISK_POLICY,
) -> str:
    """Map a proposal to a required approval level.

    The proposal's own ``risk`` is authoritative for the baseline approval.
    When the existing ``CapabilityRiskPolicy`` *explicitly* knows the evolution
    capability (i.e. it is a registered capability, not the ``untrusted``
    fallback), its minimum approval acts as a floor that can only tighten the
    requirement — never silently escalate a low-risk proposal to ``admin`` via
    the unknown-capability fallback.
    """
    required = _RISK_TO_APPROVAL.get(proposal.risk, "user")
    capability = _CAP_FOR_KIND.get(proposal.kind, "evolution.apply_strategy")
    rank = {"none": 0, "user": 1, "admin": 2}
    if risk_policy is not None and hasattr(risk_policy, "resolve"):
        try:
            resolved = risk_policy.resolve(capability)
            # Only registered (non-fallback) capabilities may raise the bar.
            if resolved.risk in ("low", "medium", "high", "critical"):
                required = max(
                    required, resolved.minimum_approval, key=lambda a: rank.get(a, 1)
                )
        except Exception:  # noqa: BLE001 - fall back to risk mapping
            pass
    return required


def apply_proposal(
    proposal: ImprovementProposal,
    strategy_store: StrategyStore,
    feedback_store: FeedbackStore,
    *,
    risk_policy: Any = DEFAULT_RISK_POLICY,
    auto_approve: bool = False,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    principal_id: Optional[str] = None,
) -> bool:
    """Apply an approved (low-risk) proposal; record others as pending.

    Returns True if the proposal was applied to a store.
    """
    def scoped_value(explicit: Optional[str], proposed: str, name: str) -> Optional[str]:
        explicit_value = str(explicit or "").strip()
        proposed_value = str(proposed or "").strip()
        if explicit_value and proposed_value and explicit_value != proposed_value:
            raise WorkspaceAccessDenied(f"{name} does not match the proposal scope")
        return explicit_value or proposed_value or None

    approval = assess_approval(proposal, risk_policy)
    if approval != "none" and not auto_approve:
        proposal.status = "proposed"
        return False

    resolved_tenant = scoped_value(tenant_id, proposal.tenant_id, "tenant_id")
    resolved_workspace = scoped_value(
        workspace_id, proposal.workspace_id, "workspace_id"
    )
    resolved_principal = scoped_value(
        principal_id, proposal.principal_id, "principal_id"
    )

    if proposal.kind == "strategy":
        strategy_store.add(
            Strategy(
                capability=proposal.target,
                rule_text=proposal.payload.get("rule_text", ""),
                rationale=proposal.rationale,
                risk=proposal.risk,
                source="reflection",
                tenant_id=resolved_tenant or "",
                workspace_id=resolved_workspace or "",
            ),
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
        )
    elif proposal.kind == "routing":
        strategy_store.add(
            Strategy(
                capability=proposal.target,
                rule_text=f"路由建议：{proposal.rationale}",
                rationale=proposal.rationale,
                risk=proposal.risk,
                source="reflection",
                tenant_id=resolved_tenant or "",
                workspace_id=resolved_workspace or "",
            ),
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
        )
    elif proposal.kind == "preference":
        fb = proposal.payload
        feedback_store.add(
            kind=fb.get("kind", "preference"),
            content=fb.get("content", ""),
            scope=proposal.target,
            weight=1.0,
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
            principal_id=resolved_principal,
        )
    proposal.status = "applied"
    return True


def run_reflection(
    episode_store: EpisodeStore,
    feedback_store: FeedbackStore,
    strategy_store: StrategyStore,
    *,
    knowledge_store: Optional[Any] = None,
    risk_policy: Any = DEFAULT_RISK_POLICY,
    auto_approve: bool = False,
    since: Optional[str] = None,
    min_samples: int = MIN_SAMPLES,
    limit: int = 500,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    principal_id: Optional[str] = None,
    all_principals: bool = False,
) -> ReflectionReport:
    """Mine episodes + feedback, generate proposals, and apply the safe ones."""
    stats = mine_episodes(
        episode_store,
        since=since,
        limit=limit,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=principal_id,
        all_principals=all_principals,
    )
    if not stats.scope_consistent:
        logger.warning(
            "Reflection skipped because the unscoped episode window spans multiple tenant/workspace scopes"
        )
        return ReflectionReport(
            episodes_analyzed=len(stats.episodes),
            failure_by_handler={
                h: round(len([e for e in eps if not e.success]) / len(eps), 3)
                for h, eps in stats.per_handler.items()
                if eps
            },
        )
    proposals = generate_proposals(stats, feedback_store, min_samples=min_samples)

    applied: List[ImprovementProposal] = []
    pending: List[ImprovementProposal] = []
    current_context = TenantContextManager.get_current()
    for p in proposals:
        if (
            current_context is not None
            and p.kind == "preference"
            and p.principal_id
            and p.principal_id != current_context.principal_id
        ):
            logger.warning(
                "Reflection skipped preference proposal for another principal: %s",
                p.principal_id,
            )
            continue
        if apply_proposal(
            p,
            strategy_store,
            feedback_store,
            risk_policy=risk_policy,
            auto_approve=auto_approve,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=principal_id,
        ):
            applied.append(p)
        else:
            pending.append(p)

    failure_by_handler = {
        h: round(len([e for e in eps if not e.success]) / len(eps), 3)
        for h, eps in stats.per_handler.items()
        if eps
    }
    return ReflectionReport(
        episodes_analyzed=len(stats.episodes),
        proposals_total=len(proposals),
        applied=applied,
        pending=pending,
        failure_by_handler=failure_by_handler,
    )


class ReflectionJob:
    """Stateful wrapper around ``run_reflection`` for scheduled execution."""

    def __init__(
        self,
        episode_store: EpisodeStore,
        feedback_store: FeedbackStore,
        strategy_store: StrategyStore,
        *,
        knowledge_store: Optional[Any] = None,
        risk_policy: Any = DEFAULT_RISK_POLICY,
        auto_approve: bool = False,
        min_samples: int = MIN_SAMPLES,
    ):
        self.episode_store = episode_store
        self.feedback_store = feedback_store
        self.strategy_store = strategy_store
        self.knowledge_store = knowledge_store
        self.risk_policy = risk_policy
        self.auto_approve = auto_approve
        self.min_samples = min_samples

    def run(
        self,
        *,
        since: Optional[str] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        principal_id: Optional[str] = None,
        all_principals: bool = False,
    ) -> ReflectionReport:
        return run_reflection(
            self.episode_store,
            self.feedback_store,
            self.strategy_store,
            knowledge_store=self.knowledge_store,
            risk_policy=self.risk_policy,
            auto_approve=self.auto_approve,
            since=since,
            min_samples=self.min_samples,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=principal_id,
            all_principals=all_principals,
        )
