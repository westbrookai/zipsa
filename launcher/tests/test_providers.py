"""Provider registry tests."""

import pytest
from zipsa.auth.providers import PROVIDERS, Provider, get_provider, UnknownProvider


class TestProviderRegistry:
    def test_x_provider_present(self):
        p = get_provider("x")
        assert isinstance(p, Provider)
        assert p.name == "x"
        assert p.token_env_var == "ZIPSA_TOKEN_X"

    def test_x_provider_endpoints_are_https(self):
        p = get_provider("x")
        assert p.authorization_endpoint.startswith("https://")
        assert p.token_endpoint.startswith("https://")

    def test_x_provider_required_scopes(self):
        p = get_provider("x")
        # Posting requires tweet.write; refresh requires offline.access
        assert "tweet.write" in p.scopes
        assert "offline.access" in p.scopes

    def test_unknown_provider_raises(self):
        with pytest.raises(UnknownProvider) as exc:
            get_provider("not-a-real-thing")
        assert "not-a-real-thing" in str(exc.value)
        # Error should hint at what IS available
        assert "x" in str(exc.value)

    def test_provider_is_frozen(self):
        p = get_provider("x")
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            p.name = "mutated"  # type: ignore
