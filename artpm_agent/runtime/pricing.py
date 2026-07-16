"""Indicative LLM pricing for observability cost estimation.

Costs are expressed in **USD per 1,000,000 tokens** (input / output) and are
*indicative* — vendors change prices and regional/volume discounts vary. The
numbers below are reasonable defaults; override ``PRICING`` or call
``register_price`` to pin your contract rates.

The estimator never crashes a turn: unknown models fall back to a tier-based
rate derived from the model id markers (mini/nano → cheap, o1/o3/r1 → strong).
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# (input_usd_per_1m, output_usd_per_1m) — indicative, sorted most-specific first.
PRICING: Dict[str, Tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4": (5.00, 15.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o3": (10.00, 40.00),
    "o1": (15.00, 60.00),
    # DeepSeek
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-chat": (0.27, 1.10),
    "deepseek": (0.27, 1.10),
    # Zhipu GLM
    "glm-4-plus": (1.00, 4.00),
    "glm-4": (1.00, 4.00),
    "glm-3": (0.30, 0.30),
    # Qwen
    "qwen-max": (1.60, 4.80),
    "qwen-plus": (0.80, 2.40),
    "qwen-turbo": (0.30, 0.90),
    "qwen": (0.80, 2.40),
    # Anthropic Claude
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-opus": (15.00, 75.00),
    "claude-3-haiku": (0.25, 1.25),
    "claude-3": (3.00, 15.00),
    "claude": (3.00, 15.00),
    # Google Gemini
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini": (1.25, 5.00),
    # Moonshot (Kimi) / MiniMax — approximate
    "moonshot": (1.20, 4.80),
    "minimax": (1.20, 4.80),
}

# Fallback rates per 1M tokens by capability tier.
_TIER_FALLBACK: Dict[str, Tuple[float, float]] = {
    "cheap": (0.30, 1.20),
    "general": (2.50, 10.00),
    "strong": (10.00, 40.00),
}


def register_price(model_id: str, input_usd_per_1m: float, output_usd_per_1m: float) -> None:
    """Pin a contract rate for a model id (case-insensitive)."""
    PRICING[str(model_id or "").strip().casefold()] = (
        float(input_usd_per_1m),
        float(output_usd_per_1m),
    )


def _match_pricing(model_id: str) -> Optional[Tuple[float, float]]:
    key = str(model_id or "").strip().casefold()
    if not key:
        return None
    # 1) exact
    if key in PRICING:
        return PRICING[key]
    # 2) longest prefix (e.g. "gpt-4o" matches "gpt-4o-2024-08-06")
    prefix_match = None
    for k, v in PRICING.items():
        if key.startswith(k) and (prefix_match is None or len(k) > len(prefix_match[0])):
            prefix_match = (k, v)
    if prefix_match is not None:
        return prefix_match[1]
    # 3) substring (e.g. "abc-gpt-4o-xyz")
    for k, v in PRICING.items():
        if k in key:
            return v
    return None


def _tier_for(model_id: str) -> str:
    k = str(model_id or "").casefold()
    if any(m in k for m in ("mini", "nano", "lite", "turbo", "haiku", "flash", "8b", "small")):
        return "cheap"
    if any(m in k for m in ("o1", "o3", "o4", "r1", "reasoning", "think", "deepseek-r")):
        return "strong"
    return "general"


def estimate_cost(
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    provider: str = "",
) -> float:
    """Estimate USD cost for a request.

    Uses the pricing table when the model is recognised, otherwise falls back
    to a tier-based indicative rate. Always returns a non-negative float and
    never raises.
    """
    try:
        prompt_tokens = max(0, int(prompt_tokens or 0))
        completion_tokens = max(0, int(completion_tokens or 0))
    except (TypeError, ValueError):
        return 0.0

    rate = _match_pricing(model_id) or _TIER_FALLBACK[_tier_for(model_id)]
    in_rate, out_rate = rate
    cost = (prompt_tokens / 1_000_000.0) * in_rate + (
        completion_tokens / 1_000_000.0
    ) * out_rate
    return round(cost, 6)
