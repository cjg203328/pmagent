"""Validated workflow authoring for UI and API clients.

The editor never persists arbitrary executable code. A draft can only select
server-allowlisted Skill/capability pairs, and risk/approval fields are derived
from the host policy so clients cannot downgrade a side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from collections.abc import Iterable, Mapping, Sequence, Set
from typing import Any

from .models import (
    DEFAULT_PROFILE_ID,
    DEFAULT_WORKSPACE_ID,
    MAX_WORKFLOW_STEPS,
    WorkflowDefinition,
    WorkflowStepDefinition,
    WorkflowTrigger,
)
from .risk_policy import (
    DEFAULT_RISK_POLICY,
    DEFAULT_SKILL_CAPABILITIES,
    CapabilityRiskPolicy,
)
from .selector import CapabilityAllowlist


class WorkflowDraftConflictError(RuntimeError):
    """Raised when an editor saves against a stale workflow version."""


@dataclass(frozen=True, slots=True)
class WorkflowCapabilityOption:
    skill_id: str
    capability: str
    risk: str
    approval: str
    side_effect: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "capability": self.capability,
            "risk": self.risk,
            "approval": self.approval,
            "side_effect": self.side_effect,
        }


def list_capability_options(
    capability_allowlist: CapabilityAllowlist = DEFAULT_SKILL_CAPABILITIES,
    *,
    risk_policy: CapabilityRiskPolicy = DEFAULT_RISK_POLICY,
) -> tuple[WorkflowCapabilityOption, ...]:
    """Return deterministic, policy-derived options for an editor."""

    options: list[WorkflowCapabilityOption] = []
    for skill_id in sorted(capability_allowlist):
        for capability in sorted(capability_allowlist[skill_id]):
            rule = risk_policy.resolve(capability)
            if not rule.allowed:
                continue
            options.append(
                WorkflowCapabilityOption(
                    skill_id=skill_id,
                    capability=capability,
                    risk=rule.risk,
                    approval=rule.minimum_approval,
                    side_effect=rule.side_effect,
                )
            )
    return tuple(options)


def capability_allowlist_from_skill_metadata(
    items: Iterable[Mapping[str, Any]],
    *,
    base_allowlist: CapabilityAllowlist = DEFAULT_SKILL_CAPABILITIES,
    risk_policy: CapabilityRiskPolicy = DEFAULT_RISK_POLICY,
) -> dict[str, frozenset[str]]:
    """Combine built-ins with policy-approved plugin capability declarations.

    A plugin manifest is an operator trust declaration, but it is not the risk
    authority. Unknown or blocked capabilities stay unavailable and
    side-effecting capabilities must still declare an approval requirement.
    """

    combined: dict[str, set[str]] = {
        str(skill_id): {str(capability) for capability in capabilities}
        for skill_id, capabilities in base_allowlist.items()
    }
    for item in items:
        if not isinstance(item, Mapping) or not item.get("is_plugin_skill"):
            continue
        skill_id = str(item.get("name") or "").strip()
        capabilities = item.get("capabilities")
        if not skill_id or isinstance(capabilities, (str, bytes)) or not isinstance(
            capabilities, (list, tuple, set, frozenset)
        ):
            continue
        accepted: set[str] = set()
        for raw_capability in capabilities:
            capability = str(raw_capability or "").strip()
            if not capability:
                continue
            rule = risk_policy.resolve(capability)
            if not rule.allowed:
                continue
            if rule.side_effect and not bool(item.get("requires_approval")):
                continue
            accepted.add(capability)
        if accepted:
            combined.setdefault(skill_id, set()).update(accepted)
    return {
        skill_id: frozenset(capabilities)
        for skill_id, capabilities in combined.items()
        if capabilities
    }


def parse_input_map(value: Any) -> dict[str, Any]:
    """Parse one bounded editor cell as a JSON object."""

    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        parsed = dict(value)
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > 16 * 1024:
            raise ValueError("step input_map exceeds 16 KiB")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"step input_map is not valid JSON: {error.msg}") from error
    else:
        raise TypeError("step input_map must be a JSON object or JSON text")
    if not isinstance(parsed, dict):
        raise ValueError("step input_map must be a JSON object")
    if any(not isinstance(key, str) for key in parsed):
        raise ValueError("step input_map keys must be strings")
    return parsed


def _keywords(value: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = value.replace("\n", ",").split(",")
    else:
        parts = [str(item) for item in value]
    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        keyword = " ".join(part.split()).strip()
        folded = keyword.casefold()
        if keyword and folded not in seen:
            normalized.append(keyword)
            seen.add(folded)
    return tuple(normalized)


def _allowed_pairs(
    capability_allowlist: CapabilityAllowlist,
) -> dict[str, frozenset[str]]:
    return {
        str(skill_id): frozenset(str(item) for item in capabilities)
        for skill_id, capabilities in capability_allowlist.items()
    }


def _step_from_row(
    row: Mapping[str, Any],
    *,
    index: int,
    allowed: Mapping[str, Set[str]],
    risk_policy: CapabilityRiskPolicy,
) -> WorkflowStepDefinition:
    skill_id = str(row.get("skill_id") or "").strip()
    if skill_id not in allowed:
        raise ValueError(f"step {index + 1} uses an unavailable skill: {skill_id}")
    requested_capability = str(row.get("capability") or "").strip()
    if requested_capability:
        capability = requested_capability
    elif len(allowed[skill_id]) == 1:
        capability = next(iter(allowed[skill_id]))
    else:
        raise ValueError(f"step {index + 1} must choose a capability")
    if capability not in allowed[skill_id]:
        raise ValueError(
            f"step {index + 1} capability is not allowlisted for {skill_id}"
        )
    rule = risk_policy.resolve(capability)
    if not rule.allowed:
        raise ValueError(f"step {index + 1} capability is blocked by policy")
    step_id = str(row.get("id") or f"step_{index + 1}").strip()
    return WorkflowStepDefinition(
        id=step_id,
        skill_id=skill_id,
        capability=capability,
        input_map=parse_input_map(row.get("input_map")),
        output_key=(str(row.get("output_key") or "").strip() or None),
        side_effect=rule.side_effect,
        approval=rule.minimum_approval,
    )


def _same_draft(first: WorkflowDefinition, second: WorkflowDefinition) -> bool:
    first_payload = first.model_dump(mode="json", exclude={"version"})
    second_payload = second.model_dump(mode="json", exclude={"version"})
    return first_payload == second_payload


def save_workflow_draft(
    store: Any,
    *,
    workflow_id: str,
    name: str,
    description: str,
    rows: Sequence[Mapping[str, Any]],
    trigger_keywords: str | Sequence[str] = (),
    trigger_always: bool = False,
    enabled: bool = True,
    priority: int = 0,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    profile_id: str = DEFAULT_PROFILE_ID,
    expected_base_version: int | None = None,
    capability_allowlist: CapabilityAllowlist = DEFAULT_SKILL_CAPABILITIES,
    risk_policy: CapabilityRiskPolicy = DEFAULT_RISK_POLICY,
) -> WorkflowDefinition:
    """Validate and persist a custom workflow as a new immutable version."""

    if not callable(getattr(store, "get_definition", None)) or not callable(
        getattr(store, "put_definition", None)
    ):
        raise TypeError("store must expose get_definition and put_definition")
    if not isinstance(trigger_always, bool) or not isinstance(enabled, bool):
        raise TypeError("trigger_always and enabled must be booleans")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise TypeError("priority must be an integer")
    if not 1 <= len(rows) <= MAX_WORKFLOW_STEPS:
        raise ValueError(f"workflow must contain 1 to {MAX_WORKFLOW_STEPS} steps")

    keywords = _keywords(trigger_keywords)
    if not keywords and not trigger_always:
        raise ValueError("workflow needs trigger keywords or always-on mode")
    allowed = _allowed_pairs(capability_allowlist)
    steps = tuple(
        _step_from_row(
            row,
            index=index,
            allowed=allowed,
            risk_policy=risk_policy,
        )
        for index, row in enumerate(rows)
    )
    latest = store.get_definition(
        workflow_id,
        workspace_id=workspace_id,
        profile_id=profile_id,
    )
    if latest is not None and latest.source == "builtin":
        raise ValueError("built-in workflows cannot be edited; use a new workflow ID")
    if expected_base_version is not None and (
        latest is None or latest.version != expected_base_version
    ):
        raise WorkflowDraftConflictError("workflow changed after the editor loaded")
    version = (latest.version + 1) if latest is not None else 1
    definition = WorkflowDefinition(
        id=workflow_id,
        version=version,
        name=name,
        description=description,
        workspace_id=workspace_id,
        profile_id=profile_id,
        source="custom",
        read_only=all(not step.side_effect for step in steps),
        enabled=enabled,
        priority=priority,
        trigger=WorkflowTrigger(
            keywords=keywords,
            always=trigger_always,
            min_keyword_matches=1 if keywords else 0,
        ),
        steps=steps,
    )
    if latest is not None and _same_draft(latest, definition):
        return latest
    return store.put_definition(definition)


__all__ = [
    "WorkflowCapabilityOption",
    "WorkflowDraftConflictError",
    "capability_allowlist_from_skill_metadata",
    "list_capability_options",
    "parse_input_map",
    "save_workflow_draft",
]
