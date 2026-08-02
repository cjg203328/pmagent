"""Configuration validator to catch common misconfigurations.

Run this before deploying or when troubleshooting model issues:
    python -m artpm_agent.tools.check_config
"""

import os
import sys
import json
from pathlib import Path
from typing import List


def check_api_keys() -> List[str]:
    """Check for placeholder API keys that won't work."""
    issues = []

    placeholder_patterns = [
        "sk-your-",
        "your-api-key",
        "your-key-here",
        "example",
        "placeholder",
    ]

    api_keys = {
        "OPENAI_API_KEY": "OpenAI",
        "ANTHROPIC_API_KEY": "Anthropic",
        "ZHIPU_API_KEY": "Zhipu",
        "DEEPSEEK_API_KEY": "DeepSeek",
    }

    for env_var, provider_name in api_keys.items():
        value = os.getenv(env_var, "")
        if value:
            for pattern in placeholder_patterns:
                if pattern in value.lower():
                    issues.append(
                        f"⚠️  {env_var} 看起来像占位符，{provider_name} API 调用会失败"
                    )
                    break

    return issues


def check_provider_model_alignment() -> List[str]:
    """Check if available_models match the primary provider."""
    issues = []

    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    primary_model = os.getenv("LLM_MODEL", "").strip()
    available_models_str = os.getenv("LLM_AVAILABLE_MODELS", "")

    if not provider or not primary_model:
        return []

    try:
        available_models = json.loads(available_models_str) if available_models_str else []
    except json.JSONDecodeError:
        issues.append("⚠️  LLM_AVAILABLE_MODELS 不是有效的 JSON 列表")
        return issues

    if not isinstance(available_models, list):
        return []

    # OpenAI-compatible aggregators intentionally expose mixed model families
    # behind one API key and base URL, so family alignment is not meaningful.
    if provider == "custom":
        return []

    # Infer provider from model name
    def infer_provider(model_id: str) -> str:
        model_lower = model_id.lower()
        if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            return "openai"
        elif "claude" in model_lower:
            return "anthropic"
        elif "deepseek" in model_lower:
            return "deepseek"
        elif "glm" in model_lower:
            return "zhipu"
        elif "qwen" in model_lower:
            return "qwen"
        elif "gemini" in model_lower or "gemma" in model_lower:
            return "google"
        elif "kimi" in model_lower:
            return "moonshot"
        else:
            return "custom"

    primary_provider = infer_provider(primary_model)

    cross_provider_models = [
        model for model in available_models
        if infer_provider(model) != primary_provider and infer_provider(model) != "custom"
    ]

    if cross_provider_models:
        issues.append(
            "⚠️  LLM_AVAILABLE_MODELS 包含跨 provider 的模型："
        )
        for model in cross_provider_models:
            model_provider = infer_provider(model)
            issues.append(
                f"    - {model} (provider: {model_provider}，主 provider: {primary_provider})"
            )
        issues.append(
            "    建议：只保留与主模型相同 provider 的候选，避免 failover 时的认证失败"
        )

    return issues


def check_missing_required_keys() -> List[str]:
    """Check if the configured provider has a valid API key."""
    issues = []

    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        return []

    if provider == "custom":
        provider = "openai"  # custom provider uses OPENAI_API_KEY

    key_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "zhipu": "ZHIPU_API_KEY",
    }

    required_key = key_map.get(provider)
    if not required_key:
        return []

    key_value = os.getenv(required_key, "").strip()
    if not key_value:
        issues.append(
            f"❌ LLM_PROVIDER={provider}，但缺少 {required_key}（对话将无法进行）"
        )
    elif key_value.startswith("sk-your-") or "your-key-here" in key_value:
        issues.append(
            f"❌ {required_key} 是占位符，需要替换为真实的 API key"
        )

    return issues


def check_data_paths() -> List[str]:
    """Check if data directories are accessible."""
    issues = []

    paths_to_check = [
        ("DB_PATH", os.getenv("DB_PATH", "./data/artpm.db")),
        ("VECTOR_DB_PATH", os.getenv("VECTOR_DB_PATH", "./data/vector_store")),
        ("MEMORY_DB_PATH", os.getenv("MEMORY_DB_PATH", "./data/memory.db")),
    ]

    for name, path_str in paths_to_check:
        path = Path(path_str)
        parent = path.parent

        if not parent.exists():
            try:
                parent.mkdir(parents=True, exist_ok=True)
                issues.append(f"✅ 已创建数据目录: {parent}")
            except OSError as e:
                issues.append(
                    f"❌ {name} 路径 {parent} 无法创建: {e}"
                )

    return issues


def main():
    """Run all configuration checks and report issues."""
    # Windows console encoding fix
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    print("=" * 60)
    print("ArtPM Agent 配置检查")
    print("=" * 60)
    print()

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        print(f"⚠️  未找到 .env 文件: {env_path}")
        print("   请复制 .env.example 为 .env 并配置 API keys")
        sys.exit(1)

    print(f"✅ .env 文件路径: {env_path}")
    print()

    # Load .env
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=env_path, override=True)

    all_issues = []

    # Run checks
    checks = [
        ("API Keys 检查", check_api_keys),
        ("Provider/Model 对齐检查", check_provider_model_alignment),
        ("必需 Keys 检查", check_missing_required_keys),
        ("数据路径检查", check_data_paths),
    ]

    for check_name, check_func in checks:
        print(f"[{check_name}]")
        issues = check_func()
        if issues:
            for issue in issues:
                print(f"  {issue}")
            all_issues.extend(issues)
        else:
            print("  ✅ 无问题")
        print()

    # Summary
    print("=" * 60)
    if all_issues:
        error_count = sum(1 for issue in all_issues if issue.startswith("❌"))
        warning_count = sum(1 for issue in all_issues if issue.startswith("⚠️"))

        print(f"发现 {error_count} 个错误，{warning_count} 个警告")

        if error_count > 0:
            print()
            print("❌ 配置存在严重问题，应用可能无法正常运行")
            print("   请修复上述错误后再启动应用")
            sys.exit(1)
        else:
            print()
            print("⚠️  配置存在一些潜在问题，建议修复以避免运行时错误")
            sys.exit(0)
    else:
        print("✅ 配置检查通过，未发现问题")
        sys.exit(0)


if __name__ == "__main__":
    main()
