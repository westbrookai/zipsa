"""Per-model token pricing.

Updated manually from https://docs.anthropic.com/en/docs/about-claude/pricing.
Pricing is in USD per 1,000,000 tokens.

The estimate_cost() function multiplies token counts from a Claude
Code `usage` block by these per-million rates. The launcher uses this
to enforce `max_cost_usd` mid-execution (the SDK only reports cost on
the final `result` event, which is too late).

Unknown models fall back to the most expensive model (Opus) so a
mis-named manifest trips the budget EARLIER, not later.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """USD per 1,000,000 tokens. All four kinds are billed separately."""
    input: float
    output: float
    cache_read: float
    cache_creation: float


# Source: https://docs.anthropic.com/en/docs/about-claude/pricing — 2026-05-19.
PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-7":           ModelPricing(input=15.00, output=75.00, cache_read=1.50, cache_creation=18.75),
    "claude-sonnet-4-6":         ModelPricing(input=3.00,  output=15.00, cache_read=0.30, cache_creation=3.75),
    "claude-haiku-4-5-20251001": ModelPricing(input=0.80,  output=4.00,  cache_read=0.08, cache_creation=1.00),
}

_FALLBACK_MODEL = "claude-opus-4-7"

_USAGE_KEYS = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_read_input_tokens", "cache_read"),
    ("cache_creation_input_tokens", "cache_creation"),
)


def estimate_cost(model: str, usage: dict) -> float:
    """Sum the four billable token classes against the model's rates.

    Missing usage keys default to 0. Unknown model => Opus pricing.
    """
    p = PRICING.get(model) or PRICING[_FALLBACK_MODEL]
    total = 0.0
    for usage_key, attr in _USAGE_KEYS:
        n = usage.get(usage_key, 0) or 0
        rate = getattr(p, attr)
        total += n / 1_000_000 * rate
    return total
