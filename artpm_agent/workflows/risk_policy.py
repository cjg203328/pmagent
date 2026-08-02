"""Server-owned capability risk policy for workflow execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


ApprovalRequirement = Literal["none", "user", "admin"]
RiskLevel = Literal["low", "medium", "high", "critical", "untrusted"]
OperationKind = Literal[
    "read",
    "analyze",
    "generate",
    "create",
    "update",
    "overwrite",
    "delete",
    "external_send",
    "bulk_change",
    "execute",
    "unknown",
]

_APPROVAL_RANK: dict[ApprovalRequirement, int] = {
    "none": 0,
    "user": 1,
    "admin": 2,
}


@dataclass(frozen=True)
class CapabilityRiskRule:
    """Immutable minimum policy for one capability."""

    capability: str
    operation: OperationKind
    risk: RiskLevel
    minimum_approval: ApprovalRequirement
    allowed: bool = True
    confirmation_scope: Literal["none", "conversation"] = "none"
    requires_idempotency: bool = False

    @property
    def read_only(self) -> bool:
        return self.operation in {"read", "analyze", "generate"}

    @property
    def side_effect(self) -> bool:
        return not self.read_only

    @property
    def requires_confirmation(self) -> bool:
        return self.minimum_approval != "none"


@dataclass(frozen=True)
class RegistryFinding:
    """One mismatch between Skill metadata and the server risk policy."""

    code: str
    skill_id: str
    capability: str | None
    message: str


def _strongest_approval(
    *requirements: ApprovalRequirement,
) -> ApprovalRequirement:
    for requirement in requirements:
        if requirement not in _APPROVAL_RANK:
            raise ValueError(f"unsupported approval requirement: {requirement}")
    return max(requirements, key=_APPROVAL_RANK.__getitem__)


def _rule(
    capability: str,
    operation: OperationKind,
    risk: RiskLevel,
    approval: ApprovalRequirement,
    *,
    allowed: bool = True,
    idempotent: bool = False,
) -> CapabilityRiskRule:
    confirmation_scope: Literal["none", "conversation"] = (
        "conversation" if approval != "none" else "none"
    )
    return CapabilityRiskRule(
        capability=capability,
        operation=operation,
        risk=risk,
        minimum_approval=approval,
        allowed=allowed,
        confirmation_scope=confirmation_scope,
        requires_idempotency=idempotent,
    )


CAPABILITY_RISK_REGISTRY: Mapping[str, CapabilityRiskRule] = MappingProxyType({
    # Built-in read, analysis, and preview capabilities.
    "documents.read": _rule("documents.read", "read", "low", "none"),
    "quote.calculate": _rule("quote.calculate", "analyze", "low", "none"),
    "projects.read": _rule("projects.read", "read", "low", "none"),
    "tasks.plan": _rule("tasks.plan", "generate", "low", "none"),
    "reminders.preview": _rule("reminders.preview", "generate", "low", "none"),
    "artifacts.generate": _rule("artifacts.generate", "generate", "low", "none"),
    "files.read": _rule("files.read", "read", "low", "none"),
    "files.search": _rule("files.search", "read", "low", "none"),
    "data.analyze": _rule("data.analyze", "analyze", "low", "none"),
    "trends.analyze": _rule("trends.analyze", "analyze", "low", "none"),
    "projects.evaluate": _rule("projects.evaluate", "analyze", "low", "none"),
    # Persistent or externally visible changes require a second confirmation in
    # the conversation that owns the workflow run.
    "files.create": _rule("files.create", "create", "medium", "user"),
    "files.update": _rule("files.update", "update", "high", "user"),
    "files.overwrite": _rule("files.overwrite", "overwrite", "high", "user"),
    "files.delete": _rule("files.delete", "delete", "high", "user"),
    "data.create": _rule("data.create", "create", "medium", "user"),
    "data.update": _rule("data.update", "update", "high", "user"),
    "data.delete": _rule("data.delete", "delete", "high", "user"),
    "data.bulk_change": _rule(
        "data.bulk_change", "bulk_change", "high", "user", idempotent=True
    ),
    "reminders.dispatch": _rule(
        "reminders.dispatch", "external_send", "high", "user", idempotent=True
    ),
    "commands.execute": _rule(
        "commands.execute", "execute", "critical", "admin", allowed=False
    ),
})


DEFAULT_SKILL_CAPABILITIES: Mapping[str, frozenset[str]] = MappingProxyType({
    "document_classifier_parser": frozenset({"documents.read"}),
    "quote_calculator": frozenset({"quote.calculate"}),
    "progress_tracker": frozenset({"projects.read"}),
    "task_allocator": frozenset({"tasks.plan"}),
    "reminder_bot": frozenset({"reminders.preview"}),
    "reminder_dispatch": frozenset({"reminders.dispatch"}),
    "file_reader": frozenset({"files.read"}),
    "file_search": frozenset({"files.search"}),
    "data_analyzer": frozenset({"data.analyze"}),
    "trend_analyzer": frozenset({"trends.analyze"}),
    "project_evaluator": frozenset({"projects.evaluate"}),
})


_OPERATION_SUFFIXES: tuple[tuple[tuple[str, ...], OperationKind], ...] = (
    (("bulk_change", "bulk_update", "batch_write", "batch_delete", "import"), "bulk_change"),
    (("overwrite", "replace"), "overwrite"),
    (("delete", "remove", "purge"), "delete"),
    (("dispatch", "send", "publish", "notify", "export"), "external_send"),
    (("execute", "command", "shell", "run"), "execute"),
    (("create", "insert", "append"), "create"),
    (("update", "write", "save", "modify"), "update"),
    (("read", "search", "list", "get", "inspect", "parse"), "read"),
    (("analyze", "calculate", "evaluate", "summarize"), "analyze"),
    (("generate", "preview", "plan", "draft"), "generate"),
)


def _operation_from_name(capability: str) -> OperationKind:
    suffix = capability.rsplit(".", 1)[-1].casefold()
    for candidates, operation in _OPERATION_SUFFIXES:
        if suffix in candidates:
            return operation
    return "unknown"


def _fallback_rule(capability: str) -> CapabilityRiskRule:
    operation = _operation_from_name(capability)
    if operation in {"read", "analyze", "generate"}:
        return _rule(capability, operation, "low", "none")
    if operation == "create":
        return _rule(capability, operation, "medium", "user")
    if operation in {
        "update",
        "overwrite",
        "delete",
        "external_send",
        "bulk_change",
    }:
        return _rule(
            capability,
            operation,
            "high",
            "user",
            idempotent=operation in {"external_send", "bulk_change"},
        )
    if operation == "execute":
        return _rule(capability, operation, "critical", "admin", allowed=False)
    return _rule(capability, "unknown", "untrusted", "admin", allowed=False)


class CapabilityRiskPolicy:
    """Resolve capabilities and enforce non-downgradable approval floors."""

    def __init__(
        self,
        registry: Mapping[str, CapabilityRiskRule] | None = None,
    ) -> None:
        source = registry if registry is not None else CAPABILITY_RISK_REGISTRY
        self._registry = dict(source)

    def resolve(self, capability: str) -> CapabilityRiskRule:
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError("capability must be a non-empty string")
        capability = capability.strip()
        return self._registry.get(capability, _fallback_rule(capability))

    def is_allowed(self, capability: str) -> bool:
        return self.resolve(capability).allowed

    def is_side_effect(
        self,
        capability: str,
        *,
        declared_side_effect: bool = False,
    ) -> bool:
        return declared_side_effect or self.resolve(capability).side_effect

    def effective_approval(
        self,
        capability: str,
        *,
        declared_side_effect: bool = False,
        declared_approval: ApprovalRequirement = "none",
        approval_floor: ApprovalRequirement = "none",
    ) -> ApprovalRequirement:
        rule = self.resolve(capability)
        side_effect_floor: ApprovalRequirement = (
            "user" if declared_side_effect else "none"
        )
        return _strongest_approval(
            rule.minimum_approval,
            side_effect_floor,
            declared_approval,
            approval_floor,
        )


DEFAULT_RISK_POLICY = CapabilityRiskPolicy()


# Actions are deliberately mapped only where the server already has a static
# capability decision.  Unknown application Skills can still request a human
# confirmation, while a capability explicitly denied by this registry can
# never be upgraded into an allowed action by an approval button.
_ACTION_CAPABILITY_ALIASES: Mapping[str, str] = MappingProxyType({
    "bash": "commands.execute",
    "cmd": "commands.execute",
    "execute": "commands.execute",
    "execute_command": "commands.execute",
    "exec": "commands.execute",
    "powershell": "commands.execute",
    "pwsh": "commands.execute",
    "run_command": "commands.execute",
    "shell": "commands.execute",
    "terminal": "commands.execute",
})


def capability_for_action(action: str) -> str | None:
    """Resolve an action to a known server capability, if one exists."""

    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a non-empty string")
    normalized = action.strip().casefold()
    if normalized in CAPABILITY_RISK_REGISTRY:
        return normalized
    for prefix in ("tool.", "skill.", "capability."):
        if not normalized.startswith(prefix):
            continue
        candidate = normalized[len(prefix):]
        if candidate in CAPABILITY_RISK_REGISTRY:
            return candidate
        return _ACTION_CAPABILITY_ALIASES.get(candidate)
    return _ACTION_CAPABILITY_ALIASES.get(normalized)


def action_is_allowed(
    action: str,
    *,
    policy: CapabilityRiskPolicy = DEFAULT_RISK_POLICY,
) -> bool:
    """Return whether a known static capability may execute at all.

    ``True`` for an unknown application action means it still needs its normal
    approval/allowlist checks; it does not grant execution by itself.  Only an
    explicit registry deny is handled here.
    """

    capability = capability_for_action(action)
    return True if capability is None else policy.is_allowed(capability)


def audit_skill_capability_registry(
    skill_metadata: Mapping[str, Mapping[str, object]],
    skill_capabilities: Mapping[str, frozenset[str]] = DEFAULT_SKILL_CAPABILITIES,
    *,
    policy: CapabilityRiskPolicy = DEFAULT_RISK_POLICY,
) -> tuple[RegistryFinding, ...]:
    """Report missing or weaker Skill metadata without mutating either registry."""
    findings: list[RegistryFinding] = []
    for skill_id, capabilities in skill_capabilities.items():
        metadata = skill_metadata.get(skill_id)
        if metadata is None:
            findings.append(
                RegistryFinding(
                    "missing_skill_metadata",
                    skill_id,
                    None,
                    "Skill is allowlisted but has no metadata",
                )
            )
            continue
        for capability in capabilities:
            rule = policy.resolve(capability)
            if not rule.allowed:
                findings.append(
                    RegistryFinding(
                        "blocked_capability",
                        skill_id,
                        capability,
                        "Capability is blocked by the server risk policy",
                    )
                )
            if bool(metadata.get("read_only", False)) != rule.read_only:
                findings.append(
                    RegistryFinding(
                        "read_only_mismatch",
                        skill_id,
                        capability,
                        "Skill read_only metadata disagrees with capability policy",
                    )
                )
            if rule.requires_confirmation and not bool(
                metadata.get("requires_approval", False)
            ):
                findings.append(
                    RegistryFinding(
                        "approval_downgrade",
                        skill_id,
                        capability,
                        "Skill metadata omits a required conversation confirmation",
                    )
                )
    return tuple(findings)


__all__ = [
    "CAPABILITY_RISK_REGISTRY",
    "DEFAULT_RISK_POLICY",
    "DEFAULT_SKILL_CAPABILITIES",
    "CapabilityRiskPolicy",
    "CapabilityRiskRule",
    "RegistryFinding",
    "action_is_allowed",
    "audit_skill_capability_registry",
    "capability_for_action",
]
