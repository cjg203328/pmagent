"""
系统健康检查 - 验证所有组件是否正常工作
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from typing import Dict, Any
import json


def _configure_console() -> None:
    """Avoid UnicodeEncodeError on legacy Windows terminals."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


def check_imports() -> Dict[str, Any]:
    """检查所有模块导入"""
    results = {}

    try:
        import streamlit
        results['streamlit'] = {'status': 'ok', 'version': streamlit.__version__}
    except ImportError as e:
        results['streamlit'] = {'status': 'error', 'message': str(e)}

    try:
        import pandas
        results['pandas'] = {'status': 'ok', 'version': pandas.__version__}
    except ImportError as e:
        results['pandas'] = {'status': 'error', 'message': str(e)}

    try:
        import openpyxl
        results['openpyxl'] = {'status': 'ok', 'version': openpyxl.__version__}
    except ImportError as e:
        results['openpyxl'] = {'status': 'error', 'message': str(e)}

    try:
        import sqlalchemy
        results['sqlalchemy'] = {'status': 'ok', 'version': sqlalchemy.__version__}
    except ImportError as e:
        results['sqlalchemy'] = {'status': 'error', 'message': str(e)}

    return results


def check_llm_providers() -> Dict[str, Any]:
    """检查LLM提供商可用性"""
    results = {}

    try:
        import openai
        results['openai'] = {'status': 'available', 'version': openai.__version__}
    except ImportError:
        results['openai'] = {'status': 'not_installed'}

    try:
        import anthropic
        results['anthropic'] = {'status': 'available', 'version': anthropic.__version__}
    except ImportError:
        results['anthropic'] = {'status': 'not_installed'}

    return results


def check_database() -> Dict[str, Any]:
    """检查数据库连接"""
    try:
        from database.models import DatabaseManager
        from config import get_config

        db_path = Path(get_config().get("database.db_path"))
        db = DatabaseManager(f"sqlite:///{db_path.as_posix()}")
        stats = db.get_project_stats()

        return {
            'status': 'ok',
            'connection': 'success',
            'projects': stats.get('total_projects', 0)
        }
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e)
        }


def check_config() -> Dict[str, Any]:
    """检查配置文件"""
    try:
        from config import get_config
        from utils.llm_client import is_valid_api_key

        config = get_config()
        cfg_data = config.get_all()

        # 检查API密钥
        api_keys = {}
        llm_cfg = cfg_data.get('llm', {})

        api_keys['openai'] = 'configured' if is_valid_api_key(llm_cfg.get('openai_api_key')) else 'missing'
        api_keys['anthropic'] = 'configured' if is_valid_api_key(llm_cfg.get('anthropic_api_key')) else 'missing'
        api_keys['zhipu'] = 'configured' if is_valid_api_key(llm_cfg.get('zhipu_api_key')) else 'missing'

        return {
            'status': 'ok',
            'provider': llm_cfg.get('provider', 'not_set'),
            'model': llm_cfg.get('model', 'not_set'),
            'api_keys': api_keys
        }
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e)
        }


def check_agent() -> Dict[str, Any]:
    """检查Agent是否可以初始化"""
    try:
        from agent import ArtPMAgent

        agent = ArtPMAgent()
        skills = agent.list_skills()

        return {
            'status': 'ok',
            'skills_count': len(skills),
            'llm_available': agent.llm_client is not None,
            'mcp_enabled': agent.mcp_client.enabled if hasattr(agent, 'mcp_client') else False
        }
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e)
        }


def run_health_check() -> Dict[str, Any]:
    """运行完整健康检查"""
    _configure_console()
    print("=" * 60)
    print("ArtPM Agent - 系统健康检查")
    print("=" * 60)

    results = {
        'imports': check_imports(),
        'llm_providers': check_llm_providers(),
        'database': check_database(),
        'config': check_config(),
        'agent': check_agent()
    }

    # 打印结果
    print("\n📦 模块导入:")
    for name, result in results['imports'].items():
        status = "✓" if result['status'] == 'ok' else "✗"
        version = f"v{result.get('version', 'N/A')}" if result['status'] == 'ok' else result.get('message', '')
        print(f"  {status} {name:20s} {version}")

    print("\n🤖 LLM提供商:")
    for name, result in results['llm_providers'].items():
        status = "✓" if result['status'] == 'available' else "✗"
        version = f"v{result.get('version', 'N/A')}" if result['status'] == 'available' else 'Not installed'
        print(f"  {status} {name:20s} {version}")

    print("\n💾 数据库:")
    db_result = results['database']
    if db_result['status'] == 'ok':
        print(f"  ✓ 连接状态: {db_result['connection']}")
        print(f"  ✓ 项目数量: {db_result['projects']}")
    else:
        print(f"  ✗ 错误: {db_result['message']}")

    print("\n⚙️ 配置:")
    cfg_result = results['config']
    if cfg_result['status'] == 'ok':
        print(f"  LLM Provider: {cfg_result['provider']}")
        print(f"  LLM Model: {cfg_result['model']}")
        print("  API Keys:")
        for provider, status in cfg_result['api_keys'].items():
            icon = "✓" if status == 'configured' else "✗"
            print(f"    {icon} {provider}: {status}")
    else:
        print(f"  ✗ 错误: {cfg_result['message']}")

    print("\n🤖 Agent:")
    agent_result = results['agent']
    if agent_result['status'] == 'ok':
        print("  ✓ 初始化成功")
        print(f"  ✓ Skills数量: {agent_result['skills_count']}")
        print(f"  {'✓' if agent_result['llm_available'] else '✗'} LLM可用: {agent_result['llm_available']}")
        print(f"  {'✓' if agent_result['mcp_enabled'] else '✗'} MCP启用: {agent_result['mcp_enabled']}")
    else:
        print(f"  ✗ 错误: {agent_result['message']}")

    # 总体状态
    print("\n" + "=" * 60)
    imports_ok = all(item.get('status') == 'ok' for item in results['imports'].values())
    all_ok = imports_ok and all(
        r.get('status') == 'ok' for r in [
            results['database'],
            results['config'],
            results['agent']
        ]
    )

    if all_ok and agent_result.get('llm_available'):
        results['overall_status'] = 'healthy'
        print("✓ 系统健康 - 所有组件正常工作")
    elif all_ok:
        results['overall_status'] = 'degraded'
        print("⚠️ 系统可用 - 当前为离线模式，配置有效 API Key 后可启用智能对话")
    else:
        results['overall_status'] = 'unhealthy'
        print("⚠️ 系统存在问题 - 请检查上述错误")

    print("=" * 60)

    return results


if __name__ == "__main__":
    results = run_health_check()

    # 保存结果到JSON文件
    output_path = Path(__file__).parent / "logs" / "health_check.json"
    output_path.parent.mkdir(exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n📄 详细结果已保存到: {output_path}")
    if results.get('overall_status') == 'unhealthy':
        sys.exit(1)
