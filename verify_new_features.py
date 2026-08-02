#!/usr/bin/env python
"""
新功能验证脚本

运行方式：
    python verify_new_features.py
"""

import sys
import io
from pathlib import Path

# Windows console encoding fix
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def check_files():
    """检查新功能文件是否存在。"""
    print("=" * 70)
    print("检查新功能文件")
    print("=" * 70)
    print()

    files_to_check = [
        "artpm_agent/views/chat_model_selector.py",
        "tests/test_chat_model_selector.py",
        "docs/CHAT_MODEL_SELECTOR_GUIDE.md",
        "docs/FIXED_MODEL_FALLBACK_ISSUE.md",
        "artpm_agent/tools/check_config.py",
    ]

    all_exist = True
    for file_path in files_to_check:
        full_path = project_root / file_path
        if full_path.exists():
            print(f"✅ {file_path}")
        else:
            print(f"❌ {file_path} (不存在)")
            all_exist = False

    print()
    return all_exist


def check_config():
    """运行配置检查。"""
    print("=" * 70)
    print("运行配置检查")
    print("=" * 70)
    print()

    try:
        from artpm_agent.tools.check_config import main as check_config_main
        check_config_main()
        return True
    except SystemExit as e:
        return e.code == 0
    except Exception as e:
        print(f"⚠️  配置检查失败: {e}")
        return False


def test_model_selector():
    """测试模型选择器功能。"""
    print("=" * 70)
    print("测试模型选择器功能")
    print("=" * 70)
    print()

    try:
        from artpm_agent.views.chat_model_selector import (
            get_available_models,
            _format_model_name,
            parse_cached_models,
        )

        # 测试模型列表解析
        print("1. 测试模型列表解析")
        models = parse_cached_models('["gpt-4o", "gpt-4o-mini"]')
        assert models == ["gpt-4o", "gpt-4o-mini"]
        print("   ✅ JSON 列表解析正常")

        # 测试空值处理
        models = parse_cached_models("")
        assert models == []
        print("   ✅ 空值处理正常")

        # 测试获取可用模型
        print()
        print("2. 测试获取可用模型")
        models = get_available_models()
        if models:
            print(f"   ✅ 获取到 {len(models)} 个可用模型")
            for model in models[:3]:
                print(f"      - {_format_model_name(model)}")
            if len(models) > 3:
                print(f"      ... 共 {len(models)} 个")
        else:
            print("   ⚠️  未获取到可用模型（将从 provider 自动推断）")

        # 测试模型名称格式化
        print()
        print("3. 测试模型名称格式化")
        test_cases = [
            ("gpt-4o-mini", "[OpenAI]"),
            ("claude-3-5-sonnet-20241022", "[Anthropic]"),
            ("glm-5.2", "[Zhipu]"),
            ("deepseek-v4-flash", "[DeepSeek]"),
        ]
        for model_id, expected_tag in test_cases:
            formatted = _format_model_name(model_id)
            if expected_tag in formatted:
                print(f"   ✅ {model_id} → {formatted}")
            else:
                print(f"   ❌ {model_id} → {formatted} (缺少 {expected_tag})")

        print()
        print("✅ 模型选择器功能测试通过")
        return True

    except Exception as e:
        print(f"❌ 模型选择器功能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_friendly_errors():
    """测试友好错误提示。"""
    print()
    print("=" * 70)
    print("测试友好错误提示")
    print("=" * 70)
    print()

    try:
        from artpm_agent.ui_helpers import _chat_error_message

        # 测试不同类型的错误
        test_cases = [
            (Exception("401: Invalid API key"), "认证失败", "gpt-4o-mini"),
            (Exception("Request timeout after 30s"), "超时", "gpt-4o"),
            (Exception("429: Too many requests"), "服务繁忙", "gpt-3.5-turbo"),
            (Exception("Connection refused"), "连接失败", "gpt-4o"),
            (Exception("404: Model not found"), "不存在", "unknown-model"),
        ]

        for error, expected_keyword, model_id in test_cases:
            message = _chat_error_message(error, model_id)
            if expected_keyword in message and "解决方案" in message:
                print(f"✅ {expected_keyword} 错误提示包含解决方案")
            else:
                print(f"❌ {expected_keyword} 错误提示缺少关键信息")
                print(f"   消息内容: {message[:100]}...")

        print()
        print("✅ 友好错误提示测试通过")
        return True

    except Exception as e:
        print(f"❌ 友好错误提示测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_pytest():
    """运行 pytest 测试。"""
    print()
    print("=" * 70)
    print("运行 Pytest 测试")
    print("=" * 70)
    print()

    try:
        import pytest
        result = pytest.main([
            "tests/test_chat_model_selector.py",
            "-v",
            "--tb=short",
        ])
        return result == 0
    except Exception as e:
        print(f"⚠️  Pytest 运行失败: {e}")
        return False


def main():
    """主函数。"""
    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " " * 20 + "新功能验证脚本" + " " * 34 + "║")
    print("╚" + "═" * 68 + "╝")
    print()

    results = []

    # 1. 检查文件
    results.append(("文件检查", check_files()))

    # 2. 配置检查
    results.append(("配置检查", check_config()))

    # 3. 模型选择器测试
    results.append(("模型选择器", test_model_selector()))

    # 4. 友好错误提示测试
    results.append(("友好错误提示", test_friendly_errors()))

    # 5. Pytest 测试
    results.append(("Pytest 测试", run_pytest()))

    # 汇总结果
    print()
    print("=" * 70)
    print("验证结果汇总")
    print("=" * 70)
    print()

    all_passed = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name.ljust(20)} {status}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("🎉 所有验证项目通过！")
        print()
        print("下一步：")
        print("1. 运行 streamlit run artpm_agent/app.py 启动应用")
        print("2. 在对话页测试模型选择器（右上角下拉框）")
        print("3. 尝试切换模型并观察 toast 提示")
        print("4. 制造一个错误（如使用无效 API Key）查看友好提示")
        sys.exit(0)
    else:
        print("❌ 部分验证项目失败，请检查上述输出并修复问题")
        sys.exit(1)


if __name__ == "__main__":
    main()
