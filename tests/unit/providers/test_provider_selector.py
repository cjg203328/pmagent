"""Unit tests for ProviderSelector."""

from artpm_agent.providers.model_selector import ProviderSelector


class TestProviderSelector:
    """Test provider and model selection logic."""

    def test_basic_selection(self):
        """Test basic provider and model selection from config."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
        }
        selector = ProviderSelector(config)
        assert selector.provider == "openai"
        assert selector.model == "gpt-4"

    def test_empty_strings_treated_as_none(self):
        """Test that empty strings are normalized to None."""
        config = {
            "provider": "",
            "model": "  ",
        }
        selector = ProviderSelector(config)
        assert selector.provider is None
        assert selector.model is None

    def test_whitespace_trimmed(self):
        """Test that provider and model names are trimmed."""
        config = {
            "provider": "  openai  ",
            "model": "  gpt-4  ",
        }
        selector = ProviderSelector(config)
        assert selector.provider == "openai"
        assert selector.model == "gpt-4"

    def test_provider_normalized_to_lowercase(self):
        """Test that provider is normalized to lowercase."""
        config = {
            "provider": "OpenAI",
            "model": "gpt-4",
        }
        selector = ProviderSelector(config)
        assert selector.provider == "openai"

    def test_model_case_preserved(self):
        """Test that model name case is preserved."""
        config = {
            "provider": "openai",
            "model": "GPT-4",
        }
        selector = ProviderSelector(config)
        assert selector.model == "GPT-4"

    def test_missing_keys_default_to_none(self):
        """Test that missing provider/model keys default to None."""
        config = {}
        selector = ProviderSelector(config)
        assert selector.provider is None
        assert selector.model is None

    def test_fallback_selection(self):
        """Test fallback provider selection when primary is unavailable."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "fallback_providers": ["anthropic", "zhipu"],
        }
        selector = ProviderSelector(config)
        # Primary is always returned first
        assert selector.provider == "openai"
        # TODO: Add fallback iteration logic when implemented

    def test_vision_provider_selection(self):
        """Test vision provider selection."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "vision_provider": "zhipu",
            "vision_model": "glm-4v-flash",
        }
        selector = ProviderSelector(config)
        assert selector.provider == "openai"
        assert selector.model == "gpt-4"
        # Vision selection is handled by VisionModelRouter

    def test_deepseek_provider(self):
        """Test DeepSeek provider selection."""
        config = {
            "provider": "deepseek",
            "model": "deepseek-chat",
        }
        selector = ProviderSelector(config)
        assert selector.provider == "deepseek"
        assert selector.model == "deepseek-chat"

    def test_custom_provider(self):
        """Test custom OpenAI-compatible provider."""
        config = {
            "provider": "custom",
            "model": "custom-model",
        }
        selector = ProviderSelector(config)
        assert selector.provider == "custom"
        assert selector.model == "custom-model"
