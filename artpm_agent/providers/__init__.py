"""Provider-neutral model and tool orchestration boundaries.

Keeps provider selection, cooldown bookkeeping, and failover out of the agent
monolith so the agent stays a thin orchestration facade.
"""

from .gateway import ModelGateway
from .structured import (
    AnthropicStructuredAdapter,
    LangChainStructuredAdapter,
    OpenAIStructuredAdapter,
    ProviderAdapterError,
    ProviderResponseError,
    StructuredProviderAdapter,
    StructuredProviderGateway,
    create_structured_provider_adapter,
)

__all__ = [
    "AnthropicStructuredAdapter",
    "LangChainStructuredAdapter",
    "ModelGateway",
    "OpenAIStructuredAdapter",
    "ProviderAdapterError",
    "ProviderResponseError",
    "StructuredProviderAdapter",
    "StructuredProviderGateway",
    "create_structured_provider_adapter",
]
