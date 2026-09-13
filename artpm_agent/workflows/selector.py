"""Deterministic workflow selection from server-supplied definitions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from typing import Any

from .models import (
    WorkflowDecision,
    WorkflowDefinition,
    WorkflowSelectionContext,
)


CapabilityAllowlist = Mapping[str, Set[str]]


def definition_is_allowed(
    definition: WorkflowDefinition,
    capability_allowlist: CapabilityAllowlist,
) -> bool:
    """Require every step's Skill/capability pair to be server-approved."""
    return all(
        step.skill_id in capability_allowlist
        and step.capability in capability_allowlist[step.skill_id]
        for step in definition.steps
    )


def _lookup_context(data: Mapping[str, Any], dotted_path: str) -> tuple[bool, Any]:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


class WorkflowSelector:
    """Select only from validated, enabled definitions supplied by the server."""

    def __init__(
        self,
        capability_allowlist: CapabilityAllowlist,
        *,
        minimum_score: float = 1.0,
    ) -> None:
        self.capability_allowlist = {
            skill_id: frozenset(capabilities)
            for skill_id, capabilities in capability_allowlist.items()
        }
        if minimum_score < 0:
            raise ValueError("minimum_score must be non-negative")
        self.minimum_score = float(minimum_score)

    def select(
        self,
        context: WorkflowSelectionContext,
        definitions: Iterable[WorkflowDefinition],
    ) -> WorkflowDecision:
        candidates = [
            definition
            for definition in definitions
            if definition.enabled
            and definition.tenant_id == context.tenant_id
            and definition.workspace_id == context.workspace_id
            and definition.profile_id == context.profile_id
            and definition_is_allowed(definition, self.capability_allowlist)
        ]

        if context.explicit_workflow_id:
            explicit = [
                definition
                for definition in candidates
                if definition.id == context.explicit_workflow_id
                and (
                    context.explicit_workflow_version is None
                    or definition.version == context.explicit_workflow_version
                )
            ]
            if not explicit:
                return WorkflowDecision(
                    score=0.0,
                    explicit=True,
                    reasons=("explicit workflow is unavailable or not allowed",),
                )
            selected = max(explicit, key=lambda item: item.version)
            return WorkflowDecision(
                definition=selected,
                score=10_000.0,
                explicit=True,
                reasons=("explicit workflow_id",),
            )

        ranked: list[tuple[float, WorkflowDefinition, tuple[str, ...]]] = []
        prompt = context.prompt.casefold()
        attachment_extensions = {
            attachment.extension.casefold() for attachment in context.attachments
        }
        for definition in candidates:
            trigger = definition.trigger
            reasons: list[str] = []

            if trigger.requires_attachment and not context.attachments:
                continue
            if trigger.attachment_extensions and context.attachments:
                required_extensions = {
                    extension.casefold() for extension in trigger.attachment_extensions
                }
                if attachment_extensions.isdisjoint(required_extensions):
                    continue
                reasons.append("attachment extension matched")
            if trigger.project_statuses:
                if context.project_status not in trigger.project_statuses:
                    continue
                reasons.append("project status matched")

            context_matches = 0
            context_missing = False
            for key in trigger.required_context_keys:
                exists, value = _lookup_context(context.context_data, key)
                if not exists or value in (None, "", [], {}):
                    context_missing = True
                    break
                context_matches += 1
            if context_missing:
                continue
            if context_matches:
                reasons.append("required context available")

            matched_keywords = [
                keyword for keyword in trigger.keywords if keyword.casefold() in prompt
            ]
            if trigger.keywords and len(matched_keywords) < trigger.min_keyword_matches:
                if not trigger.always and not reasons:
                    continue
            if matched_keywords:
                reasons.append(f"matched {len(matched_keywords)} keyword(s)")

            evidence = bool(reasons) or trigger.always
            if not evidence:
                continue
            score = max(0.0, definition.priority / 100.0)
            score += len(matched_keywords) * 2.0
            score += context_matches * 1.5
            score += 1.5 if "attachment extension matched" in reasons else 0.0
            score += 1.5 if "project status matched" in reasons else 0.0
            score += 1.0 if trigger.always else 0.0
            ranked.append((score, definition, tuple(reasons)))

        if not ranked:
            return WorkflowDecision(score=0.0, reasons=("no hard rules matched",))
        ranked.sort(key=lambda item: (-item[0], -item[1].version, item[1].id))
        score, definition, reasons = ranked[0]
        if score < self.minimum_score:
            return WorkflowDecision(score=score, reasons=("score below threshold",))
        return WorkflowDecision(
            definition=definition,
            score=score,
            reasons=reasons,
        )
