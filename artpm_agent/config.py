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

# Load project defaults without overwriting deployment-level environment values.
# This keeps local development convenient while allowing cloud/container secrets
# and feature flags to remain authoritative.
PROJECT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=PROJECT_ENV_PATH, override=False)

# User-chosen data root override (project-local, no secrets). The settings page
# writes this file so the knowledge base and all caches can be relocated without
# touching .env. DATA_ROOT env wins over this; both win over the ./data default.
DATA_ROOT_OVERRIDE_PATH = Path(__file__).resolve().parent.parent / "data_root.json"


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

        # dsh-style layered config: an active profile patch (+ home patch +
        # overlays) is applied over the merged defaults/custom config *before*
        # environment overrides, so deployment-level env values stay
        # authoritative (the existing "env wins" contract).
        profile = os.getenv("ARTPM_PROFILE") or None
        if profile:
            from .config_layers import load_layered_config

            layered = load_layered_config(self.config, profile=profile)
            self.config = layered.merged()

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
        if os.getenv("LLM_FRAMEWORK"):
            self.config["llm"]["framework"] = os.getenv("LLM_FRAMEWORK")
        if os.getenv("LLM_VISION_MODEL"):
            self.config["llm"]["vision_model"] = os.getenv("LLM_VISION_MODEL")
        if os.getenv("LLM_VISION_PROVIDER"):
            self.config["llm"]["vision_provider"] = os.getenv("LLM_VISION_PROVIDER")
        if os.getenv("LLM_VISION_API_KEY"):
            self.config["llm"]["vision_api_key"] = os.getenv("LLM_VISION_API_KEY")
        if os.getenv("LLM_VISION_API_BASE"):
            self.config["llm"]["vision_api_base"] = os.getenv("LLM_VISION_API_BASE")
        if os.getenv("LLM_REQUEST_TIMEOUT_SECONDS"):
            try:
                self.config["llm"]["request_timeout_seconds"] = float(
                    os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "")
                )
            except ValueError:
                pass
        if os.getenv("LLM_FAILOVER_MAX_ATTEMPTS"):
            try:
                self.config["llm"]["failover_max_attempts"] = int(
                    os.getenv("LLM_FAILOVER_MAX_ATTEMPTS", "")
                )
            except ValueError:
                pass
        if os.getenv("LLM_FAILOVER_REQUEST_TIMEOUT_SECONDS"):
            try:
                self.config["llm"]["failover_request_timeout_seconds"] = float(
                    os.getenv("LLM_FAILOVER_REQUEST_TIMEOUT_SECONDS", "")
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
        if os.getenv("ARTPM_RESPONSE_CACHE"):
            self.config["llm"]["response_cache_enabled"] = os.getenv(
                "ARTPM_RESPONSE_CACHE", ""
            ).strip().lower() in {"1", "true", "yes", "on"}
        if os.getenv("ARTPM_RESPONSE_CACHE_TTL"):
            try:
                self.config["llm"]["response_cache_ttl"] = int(
                    os.getenv("ARTPM_RESPONSE_CACHE_TTL", "")
                )
            except ValueError:
                pass
        if os.getenv("ARTPM_RESPONSE_CACHE_MAX_SIZE"):
            try:
                self.config["llm"]["response_cache_max_size"] = int(
                    os.getenv("ARTPM_RESPONSE_CACHE_MAX_SIZE", "")
                )
            except ValueError:
                pass
        if os.getenv("ARTPM_RESPONSE_CACHE_REDIS"):
            self.config["llm"]["response_cache_redis_enabled"] = os.getenv(
                "ARTPM_RESPONSE_CACHE_REDIS", ""
            ).strip().lower() in {"1", "true", "yes", "on"}
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
        if os.getenv("DEEPSEEK_API_KEY"):
            self.config["llm"]["deepseek_api_key"] = os.getenv("DEEPSEEK_API_KEY")

        # API Base URLs
        if os.getenv("OPENAI_API_BASE"):
            self.config["llm"]["openai_api_base"] = os.getenv("OPENAI_API_BASE")
        if os.getenv("ANTHROPIC_API_BASE"):
            self.config["llm"]["anthropic_api_base"] = os.getenv("ANTHROPIC_API_BASE")
        if os.getenv("ZHIPU_API_BASE"):
            self.config["llm"]["zhipu_api_base"] = os.getenv("ZHIPU_API_BASE")
        if os.getenv("DEEPSEEK_API_BASE"):
            self.config["llm"]["deepseek_api_base"] = os.getenv("DEEPSEEK_API_BASE")
        if os.getenv("DEEPSEEK_REASONING_EFFORT"):
            self.config["llm"]["reasoning_effort"] = os.getenv(
                "DEEPSEEK_REASONING_EFFORT"
            )

        # The structured tool loop is opt-in until a host approval boundary is
        # supplied. Sensitive data-access and write tools fail closed without it.
        model_tool_calls = os.getenv("AGENT_MODEL_TOOL_CALLS_ENABLED")
        if model_tool_calls is not None:
            self.config.setdefault("agent_runtime", {})[
                "model_tool_calls_enabled"
            ] = model_tool_calls.strip().lower() in {"1", "true", "yes", "on"}

        # Task collaboration is opt-in at the call site, but the runtime
        # defaults to the LangGraph orchestration boundary for new flows.
        agent_runtime = self.config.setdefault("agent_runtime", {})
        orchestration_framework = os.getenv("AGENT_ORCHESTRATION_FRAMEWORK")
        if orchestration_framework:
            normalized = orchestration_framework.strip().lower()
            if normalized in {"langgraph", "linear", "workflow"}:
                agent_runtime["orchestration_framework"] = normalized
        langgraph_enabled = os.getenv("LANGGRAPH_ENABLED")
        if langgraph_enabled is not None:
            agent_runtime["langgraph_enabled"] = (
                langgraph_enabled.strip().lower() in {"1", "true", "yes", "on"}
            )
        for env_name, config_key in (
            ("LANGGRAPH_MAX_PARALLELISM", "langgraph_max_parallelism"),
            ("LANGGRAPH_MAX_RETRIES", "langgraph_max_retries"),
            ("MEMORY_CONTEXT_MAX_TOKENS", "memory_context_max_tokens"),
        ):
            value = os.getenv(env_name)
            if not value:
                continue
            try:
                parsed = int(value)
            except ValueError:
                continue
            if parsed >= 0:
                agent_runtime[config_key] = parsed

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

        # MinerU is an optional, deployment-owned document conversion backend.
        # Keep it out of the core dependency graph; the adapter can use a local
        # CLI or an isolated mineru-api sidecar when configured.
        mineru = self.config.setdefault("mineru", {})
        bool_env = {
            "MINERU_ENABLED": "enabled",
            "MINERU_FORMULA_ENABLE": "formula_enable",
            "MINERU_TABLE_ENABLE": "table_enable",
            "MINERU_IMAGE_ANALYSIS": "image_analysis",
            "MINERU_CACHE_ENABLED": "cache_enabled",
            "MINERU_ALLOW_INSECURE_HTTP": "allow_insecure_http",
        }
        for env_name, config_key in bool_env.items():
            value = os.getenv(env_name)
            if value is not None:
                mineru[config_key] = value.strip().lower() in {"1", "true", "yes", "on"}
        text_env = {
            "MINERU_MODE": "mode",
            "MINERU_COMMAND": "command",
            "MINERU_API_URL": "api_url",
            "MINERU_API_KEY": "api_key",
            "MINERU_API_KEY_HEADER": "api_key_header",
            "MINERU_API_KEY_PREFIX": "api_key_prefix",
            "MINERU_API_PATH": "api_path",
            "MINERU_HEALTH_PATH": "health_path",
            "MINERU_BACKEND": "backend",
            "MINERU_PARSE_METHOD": "parse_method",
            "MINERU_EFFORT": "effort",
            "MINERU_LANGUAGE": "language",
            "MINERU_OUTPUT_DIR": "output_dir",
        }
        for env_name, config_key in text_env.items():
            value = os.getenv(env_name)
            if value:
                mineru[config_key] = value
        numeric_env = {
            "MINERU_TIMEOUT_SECONDS": ("timeout_seconds", float),
            "MINERU_CONNECT_TIMEOUT_SECONDS": ("connect_timeout_seconds", float),
            "MINERU_MAX_OUTPUT_BYTES": ("max_output_bytes", int),
            "MINERU_MAX_TEXT_CHARS": ("max_text_chars", int),
            "MINERU_MAX_STRUCTURED_BYTES": ("max_structured_bytes", int),
            "MINERU_MAX_BLOCKS": ("max_blocks", int),
            "MINERU_CACHE_MAX_ENTRIES": ("cache_max_entries", int),
        }
        for env_name, (config_key, converter) in numeric_env.items():
            value = os.getenv(env_name)
            if not value:
                continue
            try:
                mineru[config_key] = converter(value)
            except (TypeError, ValueError):
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
        if os.getenv("DATA_ROOT"):
            self.config["database"]["data_root"] = os.getenv("DATA_ROOT")

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

        tencentdb_memory = memory_config.setdefault("tencentdb_agent_memory", {})
        if os.getenv("TENCENTDB_AGENT_MEMORY_ENABLED"):
            tencentdb_memory["enabled"] = (
                os.getenv("TENCENTDB_AGENT_MEMORY_ENABLED", "").strip().lower()
                in {"1", "true", "yes", "on"}
            )
        for env_name, config_key in (
            ("TENCENTDB_AGENT_MEMORY_BASE_URL", "base_url"),
            ("TENCENTDB_AGENT_MEMORY_API_KEY", "api_key"),
            ("TENCENTDB_AGENT_MEMORY_SCOPE_SECRET", "scope_secret"),
            ("TENCENTDB_AGENT_MEMORY_AGENT_ID", "agent_id"),
        ):
            value = os.getenv(env_name)
            if value:
                tencentdb_memory[config_key] = value
        if os.getenv("TENCENTDB_AGENT_MEMORY_ALLOW_INSECURE_HTTP"):
            tencentdb_memory["allow_insecure_http"] = (
                os.getenv("TENCENTDB_AGENT_MEMORY_ALLOW_INSECURE_HTTP", "")
                .strip()
                .lower()
                in {"1", "true", "yes", "on"}
            )
        for env_name, config_key in (
            ("TENCENTDB_AGENT_MEMORY_TIMEOUT_SECONDS", "timeout_seconds"),
            ("TENCENTDB_AGENT_MEMORY_MAX_CONTEXT_CHARS", "max_context_chars"),
            ("TENCENTDB_AGENT_MEMORY_MAX_CAPTURE_CHARS", "max_capture_chars"),
        ):
            value = os.getenv(env_name)
            if not value:
                continue
            try:
                tencentdb_memory[config_key] = (
                    float(value)
                    if config_key == "timeout_seconds"
                    else int(value)
                )
            except ValueError:
                continue

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
        """Ensure required directories exist.

        A single ``data_root`` governs every local database and cache so the
        user can relocate the knowledge base and all derived files together.
        Priority: ``DATA_ROOT`` env > persisted project override
        (``data_root.json``) > project-local ``./data`` default.
        """
        project_root = self.base_dir.parent
        data_root = os.getenv("DATA_ROOT") or _load_data_root_override()
        if data_root:
            resolved_root = Path(data_root).expanduser()
            if not resolved_root.is_absolute():
                resolved_root = project_root / resolved_root
            resolved_root = resolved_root.resolve()
        else:
            resolved_root = (project_root / "data").resolve()
        self.config["database"]["data_root"] = str(resolved_root)

        for key in (
            "db_path",
            "memory_db_path",
            "conversation_db_path",
            "vector_db_path",
        ):
            configured = Path(self.config["database"][key]).expanduser()
            if configured.is_absolute():
                configured = configured.resolve()
            else:
                # Rebase the default relative layout (e.g. "data/conversations.db")
                # onto the chosen data root so everything lives in one directory.
                leaf = configured
                if leaf.parts and leaf.parts[0] in ("data", ".", ".."):
                    leaf = Path(*leaf.parts[1:])
                configured = (resolved_root / leaf).resolve()
            self.config["database"][key] = str(configured)

        Path(self.config["database"]["db_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.config["database"]["memory_db_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(self.config["database"]["conversation_db_path"]).parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        Path(self.config["database"]["vector_db_path"]).mkdir(parents=True, exist_ok=True)

        mineru_config = self.config.setdefault("mineru", {})
        mineru_output = Path(str(mineru_config.get("output_dir") or "./data/mineru")).expanduser()
        if not mineru_output.is_absolute():
            leaf = mineru_output
            if leaf.parts and leaf.parts[0] in ("data", ".", ".."):
                leaf = Path(*leaf.parts[1:])
            mineru_output = resolved_root / leaf
        mineru_output = mineru_output.resolve()
        mineru_output.mkdir(parents=True, exist_ok=True)
        mineru_config["output_dir"] = str(mineru_output)

        # Log directory stays with the install (operational logs, not user data).
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


def _load_data_root_override() -> Optional[str]:
    """Read the persisted data-root override, if any."""
    path = DATA_ROOT_OVERRIDE_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    root = data.get("data_root") if isinstance(data, dict) else None
    return str(root) if root else None


def resolve_data_root() -> Path:
    """Resolve the active data root without constructing a full Config.

    Mirrors the priority used by ``Config._ensure_directories`` so callers that
    run before or outside a Config instance (e.g. token/episode caches) agree
    on where user data lives.
    """
    project_root = Path(__file__).resolve().parent.parent
    data_root = os.getenv("DATA_ROOT") or _load_data_root_override()
    if data_root:
        root = Path(data_root).expanduser()
        if not root.is_absolute():
            root = project_root / root
        return root.resolve()
    return (project_root / "data").resolve()


def resolve_state_path(filename: str, env_var: Optional[str] = None) -> Path:
    """Resolve one persistent state file under the active data root.

    A dedicated environment variable may override the location for advanced
    deployments and tests. Otherwise every state database follows DATA_ROOT.
    """
    if env_var:
        configured = os.getenv(env_var)
        if configured:
            return Path(configured).expanduser().resolve()
    return (resolve_data_root() / filename).resolve()


def save_data_root(root: Optional[str]) -> None:
    """Persist (or clear) the user-chosen data root override."""
    path = DATA_ROOT_OVERRIDE_PATH
    if not root:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        return
    resolved = Path(root).expanduser()
    if not resolved.is_absolute():
        resolved = (Path(__file__).resolve().parent.parent / resolved).resolve()
    else:
        resolved = resolved.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"data_root": str(resolved)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def reset_config() -> None:
    """Drop the cached Config singleton so the next get_config() rebuilds it.

    Used by the settings page after the data root changes, so subsequent
    ``Config()`` calls pick up the new location.
    """
    global _config_instance
    _config_instance = None
