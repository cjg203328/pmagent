"""Provider-neutral runtime contracts for the application harness.

The harness used to reach into :class:`ArtPMAgent` through private methods.
That made it impossible to run the turn pipeline from an API worker, a plugin,
or a test process without constructing the whole Streamlit agent.  This module
defines the small public contract the pipeline needs and keeps the legacy
translation in one adapter.

``LegacyAgentRuntimeAdapter`` is intentionally the only place that knows the
old underscore-prefixed method names.  New hosts should implement
``BaseHarnessRuntime`` (or the ``HarnessRuntime`` protocol) directly.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import (
    Any,
    ContextManager,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    cast,
    runtime_checkable,
)

from artpm_agent.routing.service import IntentDecision


class RuntimeCapabilityError(RuntimeError):
    """Raised when a host asks the runtime to perform an unsupported action."""


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Feature flags exposed by a harness runtime.

    ``turn_processing`` means the runtime supports the complete skill/model
    path.  It is separate from individual capabilities so a deliberately
    minimal runtime can still provide a safe direct response fallback.
    """

    turn_processing: bool = False
    skill_routing: bool = False
    model_chat: bool = False
    direct_response: bool = False
    document_parsing: bool = False
    attachment_parsing: bool = False


@runtime_checkable
class HarnessRuntime(Protocol):
    """Public runtime contract consumed by harness handlers.

    Implementations may be local, remote, or plugin-provided.  No concrete
    agent class is part of this interface.
    """

    @property
    def capabilities(self) -> RuntimeCapabilities: ...

    @property
    def memory_manager(self) -> Any: ...

    @property
    def model_available(self) -> bool: ...

    def detect_intent(self, user_input: str) -> Optional[str]: ...

    def detect_intent_decision(self, user_input: str) -> IntentDecision: ...

    def intent_execution_gate(
        self,
        decision: IntentDecision,
        *,
        explicit: bool = False,
    ) -> tuple[bool, str]: ...

    def skill_names(self) -> set[str]: ...

    def skill_metadata(self, skill_name: str) -> Mapping[str, Any]: ...

    def for_tenant(self, tenant_context: Any) -> "HarnessRuntime": ...

    def build_skill_input(self, user_input: str, context: Mapping[str, Any]) -> str: ...

    def extract_skill_inputs(
        self,
        user_input: str,
        intent: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def execute_skill(self, intent: str, inputs: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def format_skill_result(self, skill_name: str, result: Mapping[str, Any]) -> str: ...

    def process_document(self, file_path: str, user_hint: str = "") -> Mapping[str, Any]: ...

    def parse_attachments(
        self,
        user_input: str,
        context: Mapping[str, Any],
    ) -> tuple[list[Mapping[str, Any]], str]: ...

    def build_system_prompt(self, profile: Any, knowledge_context: str) -> str: ...

    def needs_visual_semantics(self, user_input: str) -> bool: ...

    def vision_attachment_paths(
        self,
        parsed_files: Sequence[Mapping[str, Any]],
        visual_semantics: bool,
    ) -> ContextManager[list[str]]: ...

    def chat_with_failover(
        self,
        prompt: str,
        system_prompt: str,
        history: Sequence[Mapping[str, Any]],
        *,
        image_paths: list[str],
    ) -> str: ...

    def make_llm_callable(self) -> Any: ...

    def chat(self, user_input: str, *, context: Optional[Mapping[str, Any]] = None) -> Any: ...


class BaseHarnessRuntime:
    """Convenience base with safe, explicit defaults for external hosts.

    Unsupported capabilities raise ``RuntimeCapabilityError`` instead of
    silently invoking a model or pretending an action completed.
    """

    capabilities = RuntimeCapabilities()
    memory_manager: Any = None
    tencentdb_memory: Any = None
    supports_response_cache_scope = False

    @property
    def model_available(self) -> bool:
        return False

    def detect_intent(self, _user_input: str) -> Optional[str]:
        return None

    def detect_intent_decision(self, user_input: str) -> IntentDecision:
        result = self.detect_intent(user_input)
        return _coerce_intent_decision(result, tier="legacy")

    def intent_execution_gate(
        self,
        decision: IntentDecision,
        *,
        explicit: bool = False,
    ) -> tuple[bool, str]:
        from artpm_agent.routing.service import execution_gate_for_decision

        return execution_gate_for_decision(decision, explicit=explicit)

    def skill_names(self) -> set[str]:
        return set()

    def skill_metadata(self, _skill_name: str) -> Mapping[str, Any]:
        return {}

    def for_tenant(self, _tenant_context: Any) -> "HarnessRuntime":
        """Return a request-scoped runtime; stateless runtimes can reuse self."""
        return self

    def build_skill_input(self, _user_input: str, _context: Mapping[str, Any]) -> str:
        raise RuntimeCapabilityError("skill input construction is unavailable")

    def extract_skill_inputs(
        self,
        _user_input: str,
        _intent: str,
        _context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise RuntimeCapabilityError("skill input extraction is unavailable")

    def execute_skill(self, _intent: str, _inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeCapabilityError("skill execution is unavailable")

    def format_skill_result(self, _skill_name: str, result: Mapping[str, Any]) -> str:
        return str(result)

    def process_document(self, _file_path: str, _user_hint: str = "") -> Mapping[str, Any]:
        raise RuntimeCapabilityError("document parsing is unavailable")

    def parse_attachments(
        self,
        _user_input: str,
        _context: Mapping[str, Any],
    ) -> tuple[list[Mapping[str, Any]], str]:
        raise RuntimeCapabilityError("attachment parsing is unavailable")

    def build_system_prompt(self, _profile: Any, _knowledge_context: str) -> str:
        raise RuntimeCapabilityError("model prompt construction is unavailable")

    def needs_visual_semantics(self, _user_input: str) -> bool:
        return False

    def vision_attachment_paths(
        self,
        _parsed_files: Sequence[Mapping[str, Any]],
        _visual_semantics: bool,
    ) -> ContextManager[list[str]]:
        return nullcontext([])

    def chat_with_failover(
        self,
        _prompt: str,
        _system_prompt: str,
        _history: Sequence[Mapping[str, Any]],
        *,
        image_paths: list[str],
    ) -> str:
        del image_paths
        raise RuntimeCapabilityError("model chat is unavailable")

    def make_llm_callable(self) -> Any:
        return None

    def chat(self, _user_input: str, *, context: Optional[Mapping[str, Any]] = None) -> Any:
        del context
        raise RuntimeCapabilityError("direct response is unavailable")


class LegacyAgentRuntimeAdapter(BaseHarnessRuntime):
    """Translate the historical ``ArtPMAgent`` surface to ``HarnessRuntime``.

    The adapter is deliberately duck-typed.  It does not import or subclass
    ``ArtPMAgent``, which keeps the harness importable in API workers and plugin
    environments that do not install the Streamlit application.
    """

    _SKILL_API = (
        "_detect_intent",
        "router",
        "_skill_input_with_history",
        "_extract_inputs",
        "_format_skill_result",
    )
    supports_response_cache_scope = True

    def __init__(self, target: Any, *, router: Any = None):
        if target is None:
            raise ValueError("target agent is required")
        self.target = target
        self._router = router if router is not None else getattr(target, "router", None)
        router = self._router
        skill_ready = bool(
            all(hasattr(target, name) for name in self._SKILL_API)
            and router is not None
            and callable(getattr(router, "execute_skill", None))
            and isinstance(getattr(router, "skills", None), Mapping)
        )
        model_ready = bool(
            hasattr(target, "_build_system_prompt")
            and hasattr(target, "_needs_visual_semantics")
            and hasattr(target, "_vision_attachment_paths")
            and (
                callable(getattr(target, "_chat_with_model_failover", None))
                or callable(getattr(getattr(target, "model_gateway", None), "chat_with_failover", None))
            )
        )
        self.capabilities = RuntimeCapabilities(
            turn_processing=skill_ready and model_ready,
            skill_routing=skill_ready,
            model_chat=model_ready,
            direct_response=callable(getattr(target, "chat", None)),
            document_parsing=callable(getattr(target, "process_document", None)),
            attachment_parsing=callable(getattr(target, "_parse_context_attachments", None)),
        )

    @property
    def memory_manager(self) -> Any:
        return getattr(self.target, "memory", None)

    @property
    def tencentdb_memory(self) -> Any:
        return getattr(self.target, "tencentdb_memory", None)

    @property
    def model_available(self) -> bool:
        # Preserve the legacy offline contract: a gateway object is created at
        # startup even without credentials, so only a live client means model
        # generation is available.
        return getattr(self.target, "llm_client", None) is not None

    @property
    def last_response_model(self) -> Any:
        return getattr(self.target, "last_response_model", None)

    @property
    def last_model_fallback_from(self) -> Any:
        return getattr(self.target, "last_model_fallback_from", None)

    def detect_intent(self, user_input: str) -> Optional[str]:
        method = getattr(self.target, "_detect_intent", None)
        if not callable(method):
            return None
        result = method(user_input)
        return result if isinstance(result, str) else None

    def detect_intent_decision(self, user_input: str) -> IntentDecision:
        method = getattr(self.target, "_detect_intent_decision", None)
        if callable(method):
            return _coerce_intent_decision(method(user_input), tier="legacy")
        return _coerce_intent_decision(self.detect_intent(user_input), tier="legacy")

    def intent_execution_gate(
        self,
        decision: IntentDecision,
        *,
        explicit: bool = False,
    ) -> tuple[bool, str]:
        method = getattr(self.target, "_intent_router", None)
        if callable(method):
            try:
                router = method()
                gate = getattr(router, "execution_gate", None)
                if callable(gate):
                    return gate(decision, explicit=explicit)
            except Exception:
                pass
        return super().intent_execution_gate(decision, explicit=explicit)

    def skill_names(self) -> set[str]:
        skills = getattr(self._router, "skills", {})
        return set(skills) if isinstance(skills, Mapping) else set()

    def skill_metadata(self, skill_name: str) -> Mapping[str, Any]:
        try:
            from artpm_agent.skills.skill_router import SKILL_METADATA

            metadata = SKILL_METADATA.get(skill_name)
            if isinstance(metadata, Mapping):
                return dict(metadata)
        except Exception:  # pragma: no cover - optional registry
            pass
        router = self._router
        if router is None:
            return {}
        try:
            for item in router.list_skills():
                if isinstance(item, Mapping) and item.get("name") == skill_name:
                    return dict(item)
        except Exception:
            return {}
        return {}

    def for_tenant(self, tenant_context: Any) -> HarnessRuntime:
        """Derive a request-scoped router view without mutating the base agent."""
        binder = getattr(self._router, "for_tenant", None)
        if not callable(binder):
            return self
        scoped_router = binder(tenant_context)
        if scoped_router is None:
            raise RuntimeCapabilityError("tenant-scoped router was not created")
        return LegacyAgentRuntimeAdapter(self.target, router=scoped_router)

    def build_skill_input(self, user_input: str, context: Mapping[str, Any]) -> str:
        method = getattr(self.target, "_skill_input_with_history", None)
        if not callable(method):
            raise RuntimeCapabilityError("legacy agent has no skill input builder")
        return str(method(user_input, dict(context)))

    def extract_skill_inputs(
        self,
        user_input: str,
        intent: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        method = getattr(self.target, "_extract_inputs", None)
        if not callable(method):
            raise RuntimeCapabilityError("legacy agent has no skill input extractor")
        result = method(user_input, intent, dict(context))
        return result if isinstance(result, Mapping) else {}

    def execute_skill(self, intent: str, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        method = getattr(self._router, "execute_skill", None)
        if not callable(method):
            raise RuntimeCapabilityError("legacy agent has no skill router")
        result = method(intent, dict(inputs))
        return result if isinstance(result, Mapping) else {"success": False, "error": "invalid skill result"}

    def format_skill_result(self, skill_name: str, result: Mapping[str, Any]) -> str:
        method = getattr(self.target, "_format_skill_result", None)
        if not callable(method):
            method = getattr(self.target, "format_skill_result", None)
        if callable(method):
            return str(method(skill_name, dict(result)) or "")
        return str(result)

    def process_document(self, file_path: str, user_hint: str = "") -> Mapping[str, Any]:
        method = getattr(self.target, "process_document", None)
        if not callable(method):
            raise RuntimeCapabilityError("legacy agent has no document parser")
        result = method(file_path, user_hint)
        return result if isinstance(result, Mapping) else {"success": False, "error": "invalid parser result"}

    def parse_attachments(
        self,
        user_input: str,
        context: Mapping[str, Any],
    ) -> tuple[list[Mapping[str, Any]], str]:
        method = getattr(self.target, "_parse_context_attachments", None)
        if not callable(method):
            raise RuntimeCapabilityError("legacy agent has no attachment parser")
        parsed, formatted = method(user_input, dict(context))
        return list(parsed or []), str(formatted or "")

    def build_system_prompt(self, profile: Any, knowledge_context: str) -> str:
        method = getattr(self.target, "_build_system_prompt", None)
        if not callable(method):
            raise RuntimeCapabilityError("legacy agent has no system prompt builder")
        return str(method(profile, knowledge_context))

    def needs_visual_semantics(self, user_input: str) -> bool:
        method = getattr(self.target, "_needs_visual_semantics", None)
        return bool(method(user_input)) if callable(method) else False

    def vision_attachment_paths(
        self,
        parsed_files: Sequence[Mapping[str, Any]],
        visual_semantics: bool,
    ) -> ContextManager[list[str]]:
        method = getattr(self.target, "_vision_attachment_paths", None)
        if not callable(method):
            return nullcontext([])
        return cast(ContextManager[list[str]], method(list(parsed_files), visual_semantics))

    def chat_with_failover(
        self,
        prompt: str,
        system_prompt: str,
        history: Sequence[Mapping[str, Any]],
        *,
        image_paths: list[str],
        cache_scope: str = "local:default",
    ) -> str:
        gateway = getattr(self.target, "model_gateway", None)
        gateway_method = getattr(gateway, "chat_with_failover", None)
        if callable(gateway_method):
            return str(
                gateway_method(
                    prompt,
                    system_prompt,
                    list(history),
                    image_paths=image_paths,
                    task_type=None,
                    cache_scope=cache_scope,
                )
            )
        method = getattr(self.target, "_chat_with_model_failover", None)
        if not callable(method):
            raise RuntimeCapabilityError("legacy agent has no model failover")
        return str(
            method(
                prompt,
                system_prompt,
                list(history),
                image_paths=image_paths,
                cache_scope=cache_scope,
            )
        )

    def make_llm_callable(self) -> Any:
        client = getattr(self.target, "llm_client", None)
        if client is not None and callable(getattr(client, "chat", None)):
            def _call(prompt: str) -> str:
                try:
                    result = client.chat(prompt)
                    if isinstance(result, Mapping):
                        return str(result.get("content", result.get("text", result)))
                    return str(result)
                except Exception:  # noqa: BLE001 - memory compression is best effort
                    return ""

            return _call
        if self.capabilities.direct_response:
            def _fallback(prompt: str) -> str:
                try:
                    result = self.chat(prompt)
                    if isinstance(result, Mapping):
                        return str(result.get("content", result.get("text", result)))
                    return str(result)
                except Exception:  # noqa: BLE001 - memory compression is best effort
                    return ""

            return _fallback
        return None

    def chat(self, user_input: str, *, context: Optional[Mapping[str, Any]] = None) -> Any:
        method = getattr(self.target, "chat", None)
        if not callable(method):
            raise RuntimeCapabilityError("legacy agent has no direct chat")
        try:
            return method(user_input, context=dict(context or {}))
        except TypeError as error:
            # A few legacy facades exposed ``chat(prompt)`` only. Retry that
            # narrow signature for compatibility, while preserving the
            # original error if the one-argument call also rejects the input.
            try:
                return method(user_input)
            except TypeError:
                raise error


def adapt_runtime(runtime: Optional[Any] = None, agent: Optional[Any] = None) -> Optional[HarnessRuntime]:
    """Resolve a public runtime, retaining ``agent=`` compatibility.

    Explicit runtimes always win.  The fallback adapter is lazy and only
    created at the boundary, so core handlers never inspect a concrete agent.
    """

    if runtime is not None:
        return cast(HarnessRuntime, runtime)
    if agent is None:
        return None
    if isinstance(agent, HarnessRuntime):
        # Permit a gradual migration where callers still use the historical
        # ``agent=`` keyword but already provide a public runtime object.
        return agent
    if isinstance(agent, LegacyAgentRuntimeAdapter):
        return agent
    return LegacyAgentRuntimeAdapter(agent)


def _coerce_intent_decision(value: Any, *, tier: str = "legacy") -> IntentDecision:
    """Normalize modern and legacy intent results at the runtime boundary."""

    if isinstance(value, IntentDecision):
        return value
    if isinstance(value, Mapping):
        try:
            candidates = value.get("candidates", ())
            if isinstance(candidates, str):
                candidates = (candidates,)
            return IntentDecision(
                intent=value.get("intent"),
                confidence=value.get("confidence", 0.0),
                tier=value.get("tier", tier),
                candidates=tuple(candidates or ()),
                margin=value.get("margin", 0.0),
                clarification_required=value.get("clarification_required", False),
                explicit=value.get("explicit", False),
            )
        except (TypeError, ValueError):
            return IntentDecision(tier="invalid", clarification_required=True)
    if isinstance(value, str) and value.strip():
        return IntentDecision(
            intent=value,
            confidence=1.0,
            tier=tier,
            candidates=(value,),
            margin=1.0,
        )
    return IntentDecision(tier="none")
