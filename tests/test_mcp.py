"""
测试 MCP 连接
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.mcp_client import get_mcp_client
import asyncio


def test_mcp_connection():
    """测试 MCP 连接"""
    print("=" * 60)
    print("MCP Connection Test")
    print("=" * 60)

    # 获取 MCP 客户端
    client = get_mcp_client()

    print(f"\nMCP Enabled: {client.enabled}")

    if client.enabled:
        # 列出技能
        skills = client.list_skills()
        print(f"\nAvailable Skills ({len(skills)}):")
        for skill in skills:
            print(f"  - {skill['name']}: {skill['description']}")

        # 测试调用技能
        print("\n" + "=" * 60)
        print("Testing Skill Call")
        print("=" * 60)

        async def test_call():
            result = await client.call_skill("web_search", {"query": "test"})
            print(f"\nResult:")
            print(f"  Success: {result['success']}")
            if result['success']:
                print(f"  Message: {result['result']}")
                print(f"  Data: {result['data']}")
            else:
                print(f"  Error: {result.get('error')}")

        asyncio.run(test_call())

    else:
        print("\nWARNING: MCP is disabled. Check your .env configuration:")
        print("  - SKILLS_FORGE_KEY=sk_live_...")
        print("  - MCP_ENABLED=true")

    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    test_mcp_connection()
