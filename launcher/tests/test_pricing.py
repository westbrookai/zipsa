"""Pricing module tests."""

import pytest

from zipsa.core.pricing import PRICING, ModelPricing, estimate_cost


class TestPricingTable:
    def test_haiku_present(self):
        p = PRICING["claude-haiku-4-5-20251001"]
        assert isinstance(p, ModelPricing)
        # Cheapest model — should have small per-token rates
        assert p.input < 5.0
        assert p.output < 20.0

    def test_opus_present(self):
        p = PRICING["claude-opus-4-7"]
        assert isinstance(p, ModelPricing)
        # Most expensive — should be more than Sonnet
        assert p.input > PRICING["claude-sonnet-4-6"].input

    def test_pricing_is_frozen(self):
        p = PRICING["claude-opus-4-7"]
        with pytest.raises(Exception):  # FrozenInstanceError
            p.input = 999  # type: ignore


class TestEstimateCost:
    def test_zero_usage_zero_cost(self):
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        assert estimate_cost("claude-haiku-4-5-20251001", usage) == 0.0

    def test_per_token_math_matches_table(self):
        # Haiku: $0.80 / 1M input. 1,000,000 input tokens => $0.80
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        cost = estimate_cost("claude-haiku-4-5-20251001", usage)
        assert cost == pytest.approx(PRICING["claude-haiku-4-5-20251001"].input)

    def test_all_four_token_kinds_summed(self):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
            "cache_creation_input_tokens": 1_000_000,
        }
        p = PRICING["claude-haiku-4-5-20251001"]
        expected = p.input + p.output + p.cache_read + p.cache_creation
        assert estimate_cost("claude-haiku-4-5-20251001", usage) == pytest.approx(expected)

    def test_missing_fields_treated_as_zero(self):
        # Real usage blocks sometimes omit fields with value 0
        usage = {"input_tokens": 100}
        cost = estimate_cost("claude-haiku-4-5-20251001", usage)
        # Only input counted; output/cache fields default to 0
        expected = 100 / 1_000_000 * PRICING["claude-haiku-4-5-20251001"].input
        assert cost == pytest.approx(expected)

    def test_unknown_model_falls_back_to_opus(self):
        """Unknown model => use Opus pricing (safety upper bound).
        Triggers limits EARLIER, not LATER."""
        usage = {"input_tokens": 1_000_000}
        cost_unknown = estimate_cost("does-not-exist-v9", usage)
        cost_opus = estimate_cost("claude-opus-4-7", usage)
        assert cost_unknown == cost_opus
