"""Unit tests for VisionModelRouter."""

from unittest.mock import Mock

from artpm_agent.providers.vision_router import VisionModelRouter


class TestVisionModelRouter:
    """Test vision model routing and configuration."""

    def test_no_vision_model_configured(self):
        """Test when no vision model is configured."""
        config = {"provider": "openai", "model": "gpt-4"}
        router = VisionModelRouter(config, Mock())

        assert not router.has_vision_pairing
        assert not router.has_vision_model()

    def test_vision_model_configured(self):
        """Test when vision model is configured."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "vision_provider": "zhipu",
            "vision_model": "glm-4v-flash",
        }
        router = VisionModelRouter(config, Mock())

        assert router.has_vision_pairing
        assert router.has_vision_model()

    def test_cache_key_format(self):
        """Test cache key format."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "vision_provider": "zhipu",
            "vision_model": "glm-4v-flash",
        }
        router = VisionModelRouter(config, Mock())

        key = router.cache_key()
        assert key == "__vision__:zhipu:glm-4v-flash"
        assert key == router.vision_cache_key()

    def test_cache_key_no_vision_provider(self):
        """Test cache key when no vision provider specified."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "vision_model": "glm-4v-flash",
        }
        router = VisionModelRouter(config, Mock())

        key = router.cache_key()
        assert key == "__vision__::glm-4v-flash"

    def test_vision_client_config_zhipu(self):
        """Test vision client config for Zhipu provider."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "vision_provider": "zhipu",
            "vision_model": "glm-4v-flash",
            "vision_api_key": "test-key",
            "max_tokens": 2000,
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["provider"] == "zhipu"
        assert vision_config["model"] == "glm-4v-flash"
        assert vision_config["zhipu_api_key"] == "test-key"
        assert vision_config["max_tokens"] == 1024  # Capped for vision
        assert vision_config["retry_max_attempts"] == 1

    def test_vision_client_config_openai(self):
        """Test vision client config for OpenAI provider."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "vision_provider": "openai",
            "vision_model": "gpt-4-vision-preview",
            "vision_api_key": "test-key",
            "vision_api_base": "https://api.openai.com/v1",
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["provider"] == "openai"
        assert vision_config["model"] == "gpt-4-vision-preview"
        assert vision_config["openai_api_key"] == "test-key"
        assert vision_config["openai_api_base"] == "https://api.openai.com/v1"

    def test_vision_client_config_deepseek(self):
        """Test vision client config for DeepSeek provider."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "vision_provider": "deepseek",
            "vision_model": "deepseek-vl",
            "vision_api_key": "test-key",
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["provider"] == "deepseek"
        assert vision_config["model"] == "deepseek-vl"
        assert vision_config["deepseek_api_key"] == "test-key"

    def test_vision_client_config_fallback_to_main_provider(self):
        """Test that vision config falls back to main provider if not specified."""
        config = {
            "provider": "zhipu",
            "model": "glm-4",
            "vision_model": "glm-4v-flash",
            "zhipu_api_key": "main-key",
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["provider"] == "zhipu"
        assert vision_config["model"] == "glm-4v-flash"
        assert vision_config["zhipu_api_key"] == "main-key"

    def test_max_tokens_capped_at_1024(self):
        """Test that max_tokens is capped at 1024 for vision."""
        config = {
            "vision_model": "glm-4v-flash",
            "max_tokens": 4096,
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["max_tokens"] == 1024

    def test_max_tokens_preserved_if_lower(self):
        """Test that max_tokens is preserved if already below 1024."""
        config = {
            "vision_model": "glm-4v-flash",
            "max_tokens": 512,
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["max_tokens"] == 512

    def test_max_tokens_default_1200_capped(self):
        """Test that default max_tokens 1200 is capped to 1024."""
        config = {
            "vision_model": "glm-4v-flash",
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["max_tokens"] == 1024

    def test_retry_max_attempts_set_to_1(self):
        """Test that retry_max_attempts is always set to 1 for vision."""
        config = {
            "vision_model": "glm-4v-flash",
            "retry_max_attempts": 5,
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["retry_max_attempts"] == 1

    def test_build_vision_client_success(self):
        """Test successful vision client build."""
        mock_client = Mock(name="vision_client")
        factory = Mock(return_value=mock_client)

        config = {"vision_model": "glm-4v-flash"}
        router = VisionModelRouter(config, factory)

        client = router.build_vision_client()
        assert client is mock_client
        factory.assert_called_once()

    def test_build_vision_client_failure(self):
        """Test that build_vision_client returns None on failure."""
        factory = Mock(side_effect=Exception("build failed"))

        config = {"vision_model": "glm-4v-flash"}
        router = VisionModelRouter(config, factory)

        client = router.build_vision_client()
        assert client is None

    def test_empty_vision_model_string_treated_as_none(self):
        """Test that empty vision_model string is treated as None."""
        config = {
            "provider": "openai",
            "model": "gpt-4",
            "vision_model": "  ",
        }
        router = VisionModelRouter(config, Mock())

        assert not router.has_vision_model()

    def test_vision_provider_normalized_to_lowercase(self):
        """Test that vision_provider is normalized to lowercase."""
        config = {
            "vision_provider": "ZHIPU",
            "vision_model": "glm-4v-flash",
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["provider"] == "zhipu"

    def test_api_key_priority(self):
        """Test that vision_api_key takes priority over provider-specific key."""
        config = {
            "vision_provider": "zhipu",
            "vision_model": "glm-4v-flash",
            "vision_api_key": "vision-key",
            "zhipu_api_key": "zhipu-key",
        }
        router = VisionModelRouter(config, Mock())

        vision_config = router.vision_client_config()
        assert vision_config["zhipu_api_key"] == "vision-key"
