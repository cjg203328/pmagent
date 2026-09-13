"""Public ArtPM agent facade.

The implementation is split across component construction and request
orchestration. This module remains the stable import and dependency-injection
surface used by the UI, API, scripts, and downstream integrations.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from artpm_agent.components import ComponentFactory, ComponentRegistry
from artpm_agent.request_orchestrator import RequestOrchestrator
from artpm_agent.runtime.request_services import RequestServiceBundle
from artpm_agent.utils import create_llm_client
from artpm_agent.plugins import build_plugin_manager_from_environment
from artpm_agent.harness.attachment_pipeline import (
    parse_context_attachments,
    vision_attachment_paths,
)
from artpm_agent.presentation import format_skill_result


class ArtPMAgent(RequestOrchestrator):
    """Thin compatibility facade over the request orchestrator."""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        # Preserve the historical module-level injection points used by tests
        # and deployment wrappers while implementation lives in its own module.
        import artpm_agent.request_orchestrator as implementation

        implementation.create_llm_client = create_llm_client
        implementation.build_plugin_manager_from_environment = (
            build_plugin_manager_from_environment
        )
        registry = ComponentFactory.create(
            config,
            orchestrator_class=RequestOrchestrator,
        )
        self.__dict__.update(registry.as_dict())
        # ComponentFactory builds the implementation once and the public
        # facade copies its state for compatibility. Rebind callbacks that
        # must observe facade-level test/host injection after construction.
        if all(
            hasattr(self, name)
            for name in ("intent_router", "router", "model_gateway", "process_document")
        ):
            self.request_services = RequestServiceBundle(
                intent_router=self.intent_router,
                skill_router=self.router,
                model_gateway=self.model_gateway,
                parse_attachments=lambda user_input, context: parse_context_attachments(
                    user_input,
                    dict(context),
                    self.process_document,
                ),
                prepare_vision_attachments=vision_attachment_paths,
                format_skill_result=format_skill_result,
            )
        self.components = registry

    def chat(self, user_input: str, context: Optional[dict[str, Any]] = None) -> str:
        from artpm_agent.observability import traced

        attributes = {
            "artpm.request.type": "chat",
            "artpm.has_context": context is not None,
        }
        with traced("artpm.agent.chat", attributes=attributes):
            return super().chat(user_input, context)

    async def execute_async(
        self,
        user_input: str,
        context: Optional[dict[str, Any]] = None,
    ) -> str:
        """Canonical async request entry point for API and integrations."""
        return await asyncio.to_thread(self.chat, user_input, context)


__all__ = [
    "ArtPMAgent",
    "ComponentFactory",
    "ComponentRegistry",
    "RequestOrchestrator",
]
