"""
测试 Agent 与 MCP 集成
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from artpm_agent.agent import ArtPMAgent


def test_agent_with_mcp():
    """测试 Agent 与 MCP 的集成"""
    print("=" * 60)
    print("Agent + MCP Integration Test")
    print("=" * 60)

    # 初始化 Agent
    try:
        agent = ArtPMAgent()
        print("\nAgent initialized successfully")
    except Exception as e:
        print(f"\nAgent initialization failed: {e}")
        return

    # 测试对话
    test_queries = [
        "你好，请介绍一下你的功能",
        "计算一个项目的利润：报价10万，成本6万",
        "你有哪些 MCP 技能可用？",
    ]

    for query in test_queries:
        print("\n" + "-" * 60)
        print(f"User: {query}")
        print("-" * 60)

        try:
            response = agent.chat(query)
            print(f"Agent: {response}")
        except Exception as e:
            print(f"Error: {e}")

    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    test_agent_with_mcp()
