"""
Skill routing handler for Phase 3 Stage 3.

Routes to deterministic skills based on intent detection.
Migrated from agent.py chat() method skill routing logic (lines 1199-1218).
"""

from __future__ import annotations

import logging
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Optional

from artpm_agent.security.access_mode import access_decision
from artpm_agent.routing.service import IntentDecision, execution_gate_for_decision

if TYPE_CHECKING:
    from .turn_service import TurnContext, TurnResult

logger = logging.getLogger(__name__)


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Keep the server-owned permission payload inside the JSON contract.

    Skill input extraction normally returns JSON values, but date/path objects
    can be supplied by integrations.  Converting those values to bounded text
    keeps the approval request durable without allowing an arbitrary Python
    object to cross the persistence boundary.
    """
    if depth > 16:
        return "[depth limited]"
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)[:4000]
    return value


def _skill_metadata(runtime: Any, skill_name: str) -> dict[str, Any]:
    """Resolve capability metadata through the public runtime contract."""
    metadata_method = getattr(runtime, "skill_metadata", None)
    if callable(metadata_method):
        try:
            metadata = metadata_method(skill_name)
            if isinstance(metadata, Mapping):
                return dict(metadata)
        except Exception:
            logger.debug("Runtime skill metadata lookup failed", exc_info=True)
    try:
        from artpm_agent.skills.skill_router import SKILL_METADATA

        metadata = SKILL_METADATA.get(skill_name)
        if isinstance(metadata, Mapping):
            return dict(metadata)
    except Exception:  # pragma: no cover - optional registry import
        pass
    try:
        list_skills = getattr(runtime, "list_skills", None)
        if callable(list_skills):
            for item in list_skills():
                if isinstance(item, Mapping) and item.get("name") == skill_name:
                    return dict(item)
    except Exception:
        pass
    return {}


def _requires_permission(metadata: Mapping[str, Any]) -> bool:
    """Every non-read-only capability is approval protected, even if metadata lies."""
    return bool(metadata.get("requires_approval") or metadata.get("read_only") is False)


_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3, "untrusted": 4}
_HIGH_RISK_ACTIONS = frozenset(
    {
        "bulk_change",
        "bulk_update",
        "delete",
        "dispatch",
        "export",
        "import",
        "overwrite",
        "publish",
        "purge",
        "remove",
        "replace",
        "send",
    }
)


def _effective_permission_metadata(
    metadata: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply server-owned floors so missing or weak metadata cannot self-authorize."""
    if not metadata:
        return {
            "read_only": False,
            "requires_approval": True,
            "risk": "untrusted",
            "required_role": "admin",
            "metadata_status": "missing",
        }

    effective = dict(metadata)
    declared_read_only = effective.get("read_only") is True
    declared_approval = bool(effective.get("requires_approval"))
    if declared_read_only and not declared_approval:
        return effective

    # Anything not explicitly read-only is treated as a write. A plugin may
    # harden this declaration but cannot label a persistent change as low risk.
    effective["read_only"] = False
    effective["requires_approval"] = True
    declared_risk = str(effective.get("risk") or "medium").casefold()
    if declared_risk not in _RISK_RANK:
        declared_risk = "untrusted"
    action = str(inputs.get("action") or "").strip().casefold()
    floor = "high" if action in _HIGH_RISK_ACTIONS else "medium"
    effective_risk = max((declared_risk, floor), key=_RISK_RANK.__getitem__)
    effective["risk"] = effective_risk
    declared_role = str(effective.get("required_role") or "user").casefold()
    effective["required_role"] = (
        "admin"
        if effective_risk in {"critical", "untrusted"} or declared_role == "admin"
        else "user"
    )
    return effective


def _bind_tenant_inputs(
    ctx: "TurnContext",
    inputs: Mapping[str, Any],
) -> tuple[dict[str, Any], Any]:
    """Bind server-authenticated tenant scope before persistence or execution."""
    from artpm_agent.tenancy import TenantContextError, tenant_context_from_host

    if not isinstance(inputs, Mapping):
        raise TenantContextError("skill inputs must be a mapping")
    host_context = ctx.extra if isinstance(ctx.extra, Mapping) else {}
    tenant_context = tenant_context_from_host(host_context)
    if tenant_context is None:
        return dict(inputs), None
    return tenant_context.bind_inputs(inputs), tenant_context


def _permission_request(ctx: "TurnContext", intent: str, inputs: Mapping[str, Any], metadata: Mapping[str, Any]):
    """Create one idempotent, server-owned request for a sensitive Skill."""
    service_bundle = getattr(ctx, "services", None)
    store = (
        getattr(service_bundle, "permission_store", None)
        if service_bundle is not None
        else None
    )
    if store is None and isinstance(ctx.extra, dict):
        store = ctx.extra.get("permission_store")
    if store is None or not callable(getattr(store, "create_request", None)):
        raise RuntimeError("permission store is unavailable for a protected Skill")
    from .turn_service import get_turn_scope

    scope = get_turn_scope(ctx)
    tenant_id = scope.tenant_id
    workspace_id = scope.workspace_id
    risk = str(metadata.get("risk") or "medium").casefold()
    if risk not in {"low", "medium", "high", "critical", "untrusted"}:
        risk = "untrusted"
    required_role = str(metadata.get("required_role") or "user").casefold()
    if required_role not in {"user", "admin"}:
        required_role = "user"
    safe_inputs = _json_safe(dict(inputs))
    payload = {"skill_name": intent, "inputs": safe_inputs}
    resource = {
        "skill": intent,
        "conversation_id": ctx.conversation_id,
        "target": safe_inputs,
    }
    return store.create_request(
        workspace_id=str(workspace_id),
        tenant_id=str(tenant_id),
        conversation_id=ctx.conversation_id or "unknown-conversation",
        turn_id=ctx.turn_id or "unknown-turn",
        agent_id=str(ctx.extra.get("agent_id") or "artpm-agent"),
        source="skill",
        action=f"skill.{intent}",
        resource=resource,
        risk=risk,
        required_role=required_role,
        payload=payload,
        idempotency_key=(
            f"skill:{ctx.conversation_id}:{ctx.turn_id}:{intent}"
        ),
    )


def try_skill_routing(
    ctx: "TurnContext",
    has_attachments: bool = False,
) -> Optional["TurnResult"]:
    """
    Route to a deterministic skill if intent matches.

    Args:
        ctx: Turn context with user input and agent reference
        has_attachments: Whether request has attachments (skips intent detection)

    Returns:
        TurnResult if skill was routed and executed, None otherwise

    Logic:
        1. Skip intent detection if attachments present
           (attachment context takes priority)
        2. Call runtime.detect_intent() for three-tier routing
           (keyword → embedding → LLM)
        3. If intent matches a registered skill:
           a. Build skill input with conversation history
           b. Extract structured inputs for the skill
           c. Execute skill via router
           d. Format result
        4. If skill execution succeeds, return formatted result
        5. If skill fails or no intent matched, return None

    This preserves the existing three-tier intent routing and skill execution
    logic while moving it into a dedicated handler module.
    """

    runtime = ctx.runtime
    if runtime is None or not runtime.capabilities.skill_routing:
        return None

    # The attachment gate is itself a routing decision. Keep it on the turn
    # context so streaming/compatibility adapters do not classify the same
    # prompt a second time later in the request.
    if has_attachments:
        ctx.intent = None
        ctx.intent_checked = True
        return None

    # Try intent detection (three-tier: keyword -> embedding -> LLM) once per
    # turn. The cached value is also available to telemetry and UI adapters.
    if ctx.intent_checked:
        intent = ctx.intent
        decision = ctx.intent_decision or IntentDecision(intent=intent, confidence=1.0)
    else:
        try:
            detector = getattr(runtime, "detect_intent_decision", None)
            decision = detector(ctx.user_input) if callable(detector) else None
            if not isinstance(decision, IntentDecision):
                intent = runtime.detect_intent(ctx.user_input)
                decision = IntentDecision(
                    intent=intent,
                    confidence=1.0 if intent else 0.0,
                    tier="legacy",
                    candidates=(intent,) if intent else (),
                )
            else:
                intent = decision.intent
        except Exception as error:
            logger.warning(f"Intent detection failed: {error}")
            intent = None
            decision = IntentDecision(tier="error", clarification_required=True)
        ctx.intent = intent
        ctx.intent_decision = decision
        ctx.intent_checked = True

    decision_metadata = {"intent_decision": decision.as_dict()}

    # Explicitness must come from the trusted runtime decision, never from
    # client-supplied context fields that could be used to lower the gate.
    explicit_intent = decision.explicit
    gate = getattr(runtime, "intent_execution_gate", None)
    try:
        allowed, gate_reason = (
            gate(decision, explicit=explicit_intent)
            if callable(gate)
            else execution_gate_for_decision(decision, explicit=explicit_intent)
        )
    except Exception as error:  # noqa: BLE001 - invalid gate fails closed
        logger.warning("Intent execution gate failed: %s", error)
        allowed, gate_reason = False, "gate_error"
    decision_metadata["intent_execution"] = {
        "allowed": bool(allowed),
        "reason": gate_reason,
        "explicit": explicit_intent,
    }

    if not allowed and gate_reason in {
        "clarification_required",
        "low_confidence",
        "low_margin",
    }:
        candidates = "、".join(decision.candidates[:2]) or "相关功能"
        from .turn_service import TurnResult

        return TurnResult(
            response=(
                f"我识别到可能涉及：{candidates}，但把握不足。"
                "请明确你希望执行的具体操作。"
            ),
            success=True,
            handled_by="intent_clarification",
            metadata={
                "turn_id": ctx.turn_id,
                "conversation_id": ctx.conversation_id,
                **decision_metadata,
            },
        )

    if decision.clarification_required and not allowed:
        candidates = "、".join(decision.candidates[:2]) or "相关功能"
        from .turn_service import TurnResult

        return TurnResult(
            response=f"我识别到可能涉及：{candidates}。请明确你希望执行的具体操作。",
            success=True,
            handled_by="intent_clarification",
            metadata={
                "turn_id": ctx.turn_id,
                "conversation_id": ctx.conversation_id,
                **decision_metadata,
            },
        )

    if not intent:
        return None

    if intent not in runtime.skill_names():
        return None

    # Intent matched, try executing the skill
    tool_started = False
    tool_finished = False
    try:
        # Build skill input with conversation history
        skill_input = runtime.build_skill_input(
            ctx.user_input,
            {
                "agent_profile": ctx.agent_profile,
                "knowledge_context": ctx.knowledge_context,
                "conversation_history": ctx.conversation_history,
                **ctx.extra,
            },
        )

        # Extract structured inputs
        inputs = runtime.extract_skill_inputs(
            skill_input,
            intent,
            {
                "agent_profile": ctx.agent_profile,
                **ctx.extra,
            },
        )

        # Tenant scope is host-authenticated and must be bound before the
        # arguments cross either the approval persistence boundary or the
        # execution boundary. A forged mapping or conflicting scope is
        # terminal: falling back to a model could route the same action again.
        try:
            inputs, tenant_context = _bind_tenant_inputs(ctx, inputs)
            if tenant_context is not None:
                # Derive a request-scoped runtime when the host supports it.
                # This is what prevents a shared router from reusing plugin
                # instances carrying another request's tenant context.
                scope_factory = getattr(runtime, "for_tenant", None)
                if callable(scope_factory):
                    scoped_runtime = scope_factory(tenant_context)
                    if scoped_runtime is None:
                        raise RuntimeError("tenant-scoped runtime was not created")
                    runtime = scoped_runtime
        except Exception as error:  # noqa: BLE001 - tenant gate is fail closed
            logger.warning("Tenant scope binding rejected skill %s: %s", intent, error)
            from .turn_service import TurnResult

            return TurnResult(
                response=(
                    "租户或工作区范围校验失败，未执行任何操作。"
                ),
                success=False,
                awaiting_approval=False,
                handled_by="tenant_scope_gate",
                error=str(error) or error.__class__.__name__,
                metadata={
                    "turn_id": ctx.turn_id,
                    "conversation_id": ctx.conversation_id,
                    "skill_name": intent,
                    **decision_metadata,
                    "error_code": "workspace_access_denied",
                },
            )

        metadata = _effective_permission_metadata(
            _skill_metadata(runtime, intent),
            inputs,
        )
        permission_required = _requires_permission(metadata)
        access_context = dict(ctx.extra)
        service_bundle = getattr(ctx, "services", None)
        if service_bundle is not None:
            permission_store = service_bundle.get("permission_store")
            if permission_store is not None:
                access_context["permission_store"] = permission_store
        mode_decision = access_decision(
            access_context,
            read_only=metadata.get("read_only") is True,
            requires_approval=permission_required,
            risk=str(metadata.get("risk") or "untrusted"),
            required_role=str(metadata.get("required_role") or "user"),
            auto_approval_allowed=not bool(
                metadata.get("is_plugin_skill")
                or metadata.get("is_mcp_skill")
                or metadata.get("metadata_status") == "missing"
            ),
        )
        if permission_required and mode_decision == "confirm":
            try:
                request = _permission_request(ctx, intent, inputs, metadata)
            except Exception as error:
                # A protected action must fail closed. Falling through to the
                # model after a persistence outage can produce a false success
                # message or reach a second execution path.
                logger.exception(
                    "Unable to create permission request for skill %s",
                    intent,
                )
                from .turn_service import TurnResult

                return TurnResult(
                    response="Permission request could not be persisted; no action was executed.",
                    success=False,
                    awaiting_approval=False,
                    handled_by="permission_gate",
                    error=str(error) or error.__class__.__name__,
                    metadata={
                        "turn_id": ctx.turn_id,
                        "conversation_id": ctx.conversation_id,
                        "skill_name": intent,
                        **decision_metadata,
                        "permission_status": "persistence_failed",
                    },
                )
            if request is not None:
                from .turn_service import TurnResult

                if request.status == "pending":
                    return TurnResult(
                        response="该操作需要你的确认。确认前不会执行，也不会修改数据。",
                        success=True,
                        awaiting_approval=True,
                        handled_by="permission_gate",
                        metadata={
                            "turn_id": ctx.turn_id,
                            "conversation_id": ctx.conversation_id,
                            "permission_request_id": request.id,
                            "permission_status": request.status,
                            "skill_name": intent,
                            **decision_metadata,
                        },
                    )
                if request.status in {"approved", "executing"}:
                    return TurnResult(
                        response="该操作已获确认，正在恢复执行。",
                        success=True,
                        awaiting_approval=True,
                        handled_by="permission_gate",
                        metadata={
                            "turn_id": ctx.turn_id,
                            "conversation_id": ctx.conversation_id,
                            "permission_request_id": request.id,
                            "permission_status": request.status,
                            "skill_name": intent,
                            **decision_metadata,
                        },
                    )
                return TurnResult(
                    response=(
                        "该操作已执行完成。"
                        if request.status == "completed"
                        else "该操作未执行。"
                    ),
                    success=request.status == "completed",
                    awaiting_approval=False,
                    handled_by="permission_gate",
                    metadata={
                        "turn_id": ctx.turn_id,
                        "conversation_id": ctx.conversation_id,
                        "permission_request_id": request.id,
                        "permission_status": request.status,
                        "skill_name": intent,
                        **decision_metadata,
                    },
                )

        if permission_required and mode_decision == "allow":
            inputs = dict(inputs)
            inputs["approved"] = True
            inputs["confirmation_token"] = (
                f"full-access:{ctx.conversation_id}:{ctx.turn_id}:{intent}"
            )
            inputs.setdefault(
                "idempotency_key",
                f"full-access:{ctx.conversation_id}:{ctx.turn_id}:{intent}",
            )

        # Execute skill through the same lifecycle recorder used by the turn
        # service. Approval events are recorded separately; this pair means a
        # tool actually crossed the execution boundary.
        from artpm_agent.runtime.events import AgentEventType
        from artpm_agent.runtime.turn_events import recorder_for_turn

        recorder = recorder_for_turn(ctx)
        audit_inputs = {
            "keys": sorted(str(key) for key in inputs),
            "skill_name": intent,
        }
        recorder.emit(
            ctx,
            AgentEventType.TOOL_EXECUTION_START,
            tool_name=intent,
            tool_arguments=audit_inputs,
            metadata={"kind": "skill"},
        )
        tool_started = True
        result = runtime.execute_skill(intent, inputs)
        recorder.emit(
            ctx,
            AgentEventType.TOOL_EXECUTION_END,
            tool_name=intent,
            tool_arguments=audit_inputs,
            tool_result={
                "success": bool(result.get("success")),
                "error": str(result.get("error") or "")[:400],
            },
            metadata={"kind": "skill"},
        )
        tool_finished = True

        if not result.get("success"):
            # Skill execution failed, let it fall back to LLM
            logger.warning(
                f"Skill {intent} execution failed, will fallback to model: "
                f"{result.get('error', 'unknown error')}"
            )
            return None

        # Format result
        formatted = runtime.format_skill_result(intent, result)

        if not formatted:
            # Formatting failed, let it fall back to LLM
            logger.warning(f"Skill {intent} result formatting returned empty")
            return None

        # Success!
        from .turn_service import TurnResult

        return TurnResult(
            response=formatted,
            success=True,
            handled_by=f"skill:{intent}",
            metadata={
                "turn_id": ctx.turn_id,
                "conversation_id": ctx.conversation_id,
                "skill_name": intent,
                "skill_success": True,
                **decision_metadata,
            },
        )

    except Exception as error:
        if tool_started and not tool_finished:
            try:
                from artpm_agent.runtime.events import AgentEventType
                from artpm_agent.runtime.turn_events import recorder_for_turn

                recorder_for_turn(ctx).emit(
                    ctx,
                    AgentEventType.TOOL_EXECUTION_END,
                    tool_name=intent,
                    error=str(error) or error.__class__.__name__,
                    metadata={"kind": "skill"},
                )
            except Exception:  # noqa: BLE001 - audit must not mask routing failure
                logger.warning("Skill audit event failed", exc_info=True)
        logger.warning(
            f"Skill {intent} routing failed, will fallback to model: {error}"
        )
        # Don't return error result, just return None to fall back to LLM
        return None
