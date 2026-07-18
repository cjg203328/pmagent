#!/usr/bin/env python
"""
Safe startup wrapper with pre-flight configuration checks.

Usage:
    python start_with_checks.py
"""

import sys
import subprocess
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def run_config_check() -> bool:
    """Run configuration check and return True if passed."""
    print("=" * 70)
    print("启动前配置检查")
    print("=" * 70)
    print()

    try:
        from artpm_agent.tools.check_config import main as check_config_main
        check_config_main()
        return True
    except SystemExit as e:
        # check_config exits with code 0 (warnings) or 1 (errors)
        return e.code == 0
    except Exception as e:
        print(f"⚠️  配置检查失败: {e}")
        print("   继续启动，但可能遇到运行时错误")
        print()
        return True


def start_streamlit():
    """Start the Streamlit application."""
    print("=" * 70)
    print("启动 ArtPM Agent")
    print("=" * 70)
    print()

    try:
        subprocess.run(
            ["streamlit", "run", "artpm_agent/app.py"],
            cwd=project_root,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"❌ 应用启动失败: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n✅ 应用已停止")
        sys.exit(0)


def main():
    """Main entry point."""
    if not run_config_check():
        print()
        print("❌ 配置检查未通过，建议修复错误后再启动")
        response = input("是否仍要继续启动? (y/N): ")
        if response.lower() not in ("y", "yes"):
            print("已取消启动")
            sys.exit(1)

    start_streamlit()


if __name__ == "__main__":
    main()
