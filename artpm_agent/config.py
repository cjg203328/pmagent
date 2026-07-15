"""
Configuration Management Module
"""
import os
import json
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

from artpm_agent.config_data import load_default_config

# Project-local configuration is authoritative for this application.
PROJECT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=PROJECT_ENV_PATH, override=True)


class Config:
    """Configuration Manager"""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration

        Args:
            config_path: Path to custom config file (optional)
        """
        self.base_dir = Path(__file__).parent

        # Load the packaged resource so source and wheel installations behave alike.
        self.config = load_default_config()

        # Override with custom config if provided
        if config_path:
            custom_path = Path(config_path)
            if not custom_path.is_file():
                raise FileNotFoundError(f"Config file not found: {config_path}")
            with open(custom_path, 'r', encoding='utf-8') as f:
                custom_config = json.load(f)
                self._deep_update(self.config, custom_config)

        # Override with environment variables
        self._load_from_env()

        # Ensure data directories exist
        self._ensure_directories()

    def _deep_update(self, base: Dict, update: Dict) -> Dict:
        """Deep update dictionary"""
        for key, value in update.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value
        return base

    def _load_from_env(self):
        """Load configuration from environment variables"""
        # LLM configuration
        if os.getenv("LLM_PROVIDER"):
            self.config["llm"]["provider"] = os.getenv("LLM_PROVIDER")
        if os.getenv("LLM_MODEL"):
            self.config["llm"]["model"] = os.getenv("LLM_MODEL")
        if os.getenv("LLM_VISION_MODEL"):
            self.config["llm"]["vision_model"] = os.getenv("LLM_VISION_MODEL")
        if os.getenv("LLM_REQUEST_TIMEOUT_SECONDS"):
            try:
                self.config["llm"]["request_timeout_seconds"] = float(
                    os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "")
                )
            except ValueError:
                pass
        if os.getenv("LLM_MAX_TOKENS"):
            try:
                self.config["llm"]["max_tokens"] = int(
                    os.getenv("LLM_MAX_TOKENS", "")
                )
            except ValueError:
                pass
        if os.getenv("LLM_HISTORY_MAX_MESSAGES"):
            try:
                self.config["llm"]["history_max_messages"] = int(
                    os.getenv("LLM_HISTORY_MAX_MESSAGES", "")
                )
            except ValueError:
                pass
        if os.getenv("LLM_HISTORY_MAX_CHARS"):
            try:
                self.config["llm"]["history_max_chars"] = int(
                    os.getenv("LLM_HISTORY_MAX_CHARS", "")
                )
            except ValueError:
                pass
        if os.getenv("LLM_AVAILABLE_MODELS"):
            try:
                models = json.loads(os.getenv("LLM_AVAILABLE_MODELS", "[]"))
                if isinstance(models, list):
                    self.config["llm"]["available_models"] = models
            except ValueError:
                pass
        if os.getenv("LLM_MODELS_SYNCED_AT"):
            self.config["llm"]["models_synced_at"] = os.getenv("LLM_MODELS_SYNCED_AT")

        # API Keys
        if os.getenv("OPENAI_API_KEY"):
            self.config["llm"]["openai_api_key"] = os.getenv("OPENAI_API_KEY")
        if os.getenv("ANTHROPIC_API_KEY"):
            self.config["llm"]["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY")
        if os.getenv("ZHIPU_API_KEY"):
            self.config["llm"]["zhipu_api_key"] = os.getenv("ZHIPU_API_KEY")

        # API Base URLs
        if os.getenv("OPENAI_API_BASE"):
            self.config["llm"]["openai_api_base"] = os.getenv("OPENAI_API_BASE")
        if os.getenv("ANTHROPIC_API_BASE"):
            self.config["llm"]["anthropic_api_base"] = os.getenv("ANTHROPIC_API_BASE")
        if os.getenv("ZHIPU_API_BASE"):
            self.config["llm"]["zhipu_api_base"] = os.getenv("ZHIPU_API_BASE")

        # The structured tool loop is opt-in until a host approval boundary is
        # supplied. Sensitive data-access and write tools fail closed without it.
        model_tool_calls = os.getenv("AGENT_MODEL_TOOL_CALLS_ENABLED")
        if model_tool_calls is not None:
            self.config.setdefault("agent_runtime", {})[
                "model_tool_calls_enabled"
            ] = model_tool_calls.strip().lower() in {"1", "true", "yes", "on"}

        # Hidden OCR skill configuration. The deployment bundle owns the runtime,
        # model weights, and environment; the end-user settings page never does.
        unlimited_ocr = self.config.setdefault("unlimited_ocr", {})
        env_map = {
            "UNLIMITED_OCR_ENABLED": (
                "enabled",
                lambda value: value.strip().lower() in {"1", "true", "yes", "on"},
            ),
            "UNLIMITED_OCR_BASE_URL": ("base_url", str),
            "UNLIMITED_OCR_MODEL": ("model", str),
            "UNLIMITED_OCR_TIMEOUT": ("timeout", float),
            "UNLIMITED_OCR_MAX_OUTPUT": ("max_output", int),
            "UNLIMITED_OCR_ALLOW_REMOTE": (
                "allow_remote",
                lambda value: value.strip().lower() in {"1", "true", "yes", "on"},
            ),
            "UNLIMITED_OCR_API_KEY": ("api_key", str),
            "UNLIMITED_OCR_NGRAM_SIZE": ("no_repeat_ngram_size", int),
            "UNLIMITED_OCR_NGRAM_WINDOW_SINGLE": ("ngram_window_single", int),
            "UNLIMITED_OCR_NGRAM_WINDOW_MULTI": ("ngram_window_multi", int),
            "UNLIMITED_OCR_CUSTOM_LOGIT_PROCESSOR": (
                "custom_logit_processor",
                str,
            ),
        }
        for env_name, (config_key, converter) in env_map.items():
            value = os.getenv(env_name)
            if not value:
                continue
            try:
                unlimited_ocr[config_key] = converter(value)
            except (TypeError, ValueError):
                # The adapter applies final bounds and reports invalid URLs safely.
                continue

        # Database paths
        if os.getenv("DB_PATH"):
            self.config["database"]["db_path"] = os.getenv("DB_PATH")
        if os.getenv("MEMORY_DB_PATH"):
            self.config["database"]["memory_db_path"] = os.getenv("MEMORY_DB_PATH")
        if os.getenv("CONVERSATION_DB_PATH"):
            self.config["database"]["conversation_db_path"] = os.getenv("CONVERSATION_DB_PATH")
        if os.getenv("VECTOR_DB_PATH"):
            self.config["database"]["vector_db_path"] = os.getenv("VECTOR_DB_PATH")

        # Operator-managed embedding settings. These are intentionally not
        # exposed in the end-user settings page because index compatibility is
        # a deployment concern, not a per-conversation preference.
        memory_config = self.config.setdefault("memory", {})
        if os.getenv("EMBEDDING_PROVIDER"):
            memory_config["embedding_provider"] = os.getenv("EMBEDDING_PROVIDER")
        if os.getenv("EMBEDDING_DIMENSION"):
            try:
                memory_config["embedding_dimension"] = int(
                    os.getenv("EMBEDDING_DIMENSION", "")
                )
            except ValueError:
                pass

        # Cost configuration
        if os.getenv("OVERHEAD_RATE"):
            self.config["cost_config"]["overhead_rate"] = float(os.getenv("OVERHEAD_RATE"))
        if os.getenv("TAX_RATE"):
            self.config["cost_config"]["tax_rate"] = float(os.getenv("TAX_RATE"))

        # WeChat configuration
        if os.getenv("WECOM_ENABLED"):
            self.config["wecom"]["enabled"] = os.getenv("WECOM_ENABLED").lower() == "true"
        if os.getenv("WECOM_CORP_ID"):
            self.config["wecom"]["corp_id"] = os.getenv("WECOM_CORP_ID")
        if os.getenv("WECOM_AGENT_ID"):
            self.config["wecom"]["agent_id"] = os.getenv("WECOM_AGENT_ID")
        if os.getenv("WECOM_SECRET"):
            self.config["wecom"]["secret"] = os.getenv("WECOM_SECRET")

    def _ensure_directories(self):
        """Ensure required directories exist"""
        project_root = self.base_dir.parent
        for key in (
            "db_path",
            "memory_db_path",
            "conversation_db_path",
            "vector_db_path",
        ):
            configured = Path(self.config["database"][key]).expanduser()
            if not configured.is_absolute():
                configured = project_root / configured
            configured = configured.resolve()
            self.config["database"][key] = str(configured)

        Path(self.config["database"]["db_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.config["database"]["memory_db_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.config["database"]["conversation_db_path"]).parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        Path(self.config["database"]["vector_db_path"]).mkdir(parents=True, exist_ok=True)

        # Log directory
        log_dir = self.base_dir / "logs"
        log_dir.mkdir(exist_ok=True)

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-separated key path

        Args:
            key_path: Dot-separated key path (e.g., "llm.model")
            default: Default value if key not found

        Returns:
            Configuration value
        """
        keys = key_path.split(".")
        value = self.config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def set(self, key_path: str, value: Any):
        """
        Set configuration value by dot-separated key path

        Args:
            key_path: Dot-separated key path
            value: Value to set
        """
        keys = key_path.split(".")
        config = self.config

        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]

        config[keys[-1]] = value

    def get_all(self) -> Dict[str, Any]:
        """Get all configuration"""
        return deepcopy(self.config)

    def save(self, output_path: str):
        """
        Save current configuration to file

        Args:
            output_path: Output file path
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)


# Global config instance
_config_instance: Optional[Config] = None


def get_config(config_path: Optional[str] = None) -> Config:
    """
    Get global configuration instance

    Args:
        config_path: Path to custom config file (optional)

    Returns:
        Config instance
    """
    global _config_instance

    if config_path is not None:
        return Config(config_path)

    if _config_instance is None:
        _config_instance = Config(config_path)

    return _config_instance
