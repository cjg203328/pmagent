"""
测试增强型MCP客户端和Skills
"""

import asyncio
import subprocess
import sys
from pathlib import Path

# 添加仓库根路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from artpm_agent.core.mcp_client_enhanced import (
    EnhancedMCPClient,
    get_enhanced_mcp_client,
)
from artpm_agent.skills.mcp_skills import get_mcp_skill


async def test_sensitive_files_are_never_exposed_and_output_is_bounded(tmp_path):
    (tmp_path / ".env").write_text("API_KEY=secret", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x" * 40000, encoding="utf-8")
    client = EnhancedMCPClient(str(tmp_path))

    blocked = await client.call_tool("read_file", {"file_path": ".env"})
    bounded = await client.call_tool("read_file", {"file_path": "notes.txt"})
    listed = await client.call_tool("search_files", {"pattern": "*"})

    assert blocked["success"] is False
    assert "Sensitive" in blocked["error"]
    assert bounded["success"] is True
    assert len(bounded["content"]) == 32 * 1024
    assert bounded["metadata"]["truncated"] is True
    assert ".env" not in listed["files"]


async def test_search_files_ignores_generated_and_dependency_trees(tmp_path):
    (tmp_path / "README.md").write_text("project", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("guide", encoding="utf-8")
    for directory in (
        ".ai-memory",
        ".cache",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "venv",
    ):
        nested = tmp_path / directory / "generated"
        nested.mkdir(parents=True)
        (nested / "noise.md").write_text("generated", encoding="utf-8")

    client = EnhancedMCPClient(str(tmp_path))

    result = await client.call_tool("search_files", {"pattern": "*.md"})

    assert result["success"] is True
    assert result["files"] == ["README.md", str(Path("docs") / "guide.md")]
    assert result["count"] == 2

    limited = await client.call_tool("search_files", {"pattern": "*.md", "limit": 1})

    assert limited["files"] == ["README.md"]
    assert limited["count"] == 1


async def test_blocking_local_tools_are_offloaded_from_event_loop(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "notes.txt"
    source.write_text("alpha\nbeta\n", encoding="utf-8")
    client = EnhancedMCPClient(str(tmp_path), allow_commands=True)
    client.command_allowlist = frozenset({"python"})
    offloaded = []

    async def run_in_worker(function, *args, **kwargs):
        offloaded.append(function)
        return function(*args, **kwargs)

    completed = subprocess.CompletedProcess(
        args=["python", "--version"],
        returncode=0,
        stdout="Python test",
        stderr="",
    )
    monkeypatch.setattr(asyncio, "to_thread", run_in_worker)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)

    read_result = await client.call_tool("read_file", {"file_path": "notes.txt"})
    search_result = await client.call_tool(
        "search_content",
        {"query": "alpha", "file_pattern": "*.txt"},
    )
    analysis_result = await client.call_tool(
        "analyze_data",
        {"data_source": [{"value": 1}, {"value": 2}]},
    )
    command_result = await client.call_tool(
        "execute_command",
        {"command": ["python", "--version"]},
    )

    assert read_result["success"] is True
    assert search_result["count"] == 1
    assert analysis_result["analysis"]["rows"] == 2
    assert command_result["success"] is True
    assert len(offloaded) >= 4


async def test_mcp_client():
    """测试MCP客户端基础功能"""
    print("=" * 60)
    print("测试 1: MCP客户端基础功能")
    print("=" * 60)

    client = get_enhanced_mcp_client()

    # 测试1: 列出可用工具
    print("\n可用工具:")
    tools = client.list_tools()
    for tool in tools:
        print(f"  • {tool['name']}: {tool['description']}")

    # 测试2: 搜索项目文件
    print("\n\n测试文件搜索:")
    result = await client.call_tool("search_files", {"pattern": "*.md", "limit": 5})
    if result["success"]:
        print(f"  找到 {result['count']} 个文件:")
        for file in result["files"][:5]:
            print(f"    - {file}")
    else:
        print(f"  错误: {result['error']}")

    # 测试3: 读取文件
    print("\n\n测试文件读取:")
    if result.get("files"):
        test_file = result["files"][0]
        read_result = await client.call_tool(
            "read_file", {"file_path": test_file, "lines_limit": 10}
        )
        if read_result["success"]:
            print(f"  文件: {read_result['metadata']['file_name']}")
            print(f"  大小: {read_result['metadata']['file_size']} bytes")
            print(f"  前10行:")
            print("  " + "-" * 50)
            print("  " + "\n  ".join(read_result["content"].split("\n")[:10]))
        else:
            print(f"  错误: {read_result['error']}")


async def test_file_skills():
    """测试文件操作Skills"""
    print("\n\n" + "=" * 60)
    print("测试 2: 文件操作Skills")
    print("=" * 60)

    context = {}

    # 测试 FileSearchSkill
    print("\n测试 FileSearchSkill:")
    skill = get_mcp_skill("file_search", context)
    if skill:
        result = await skill.execute(
            {"pattern": "*.py", "directory": "artpm_agent/core", "limit": 5}
        )
        if result["success"]:
            print(f"  找到 {result['count']} 个Python文件:")
            for file in result["files"]:
                print(f"    - {file}")
        else:
            print(f"  错误: {result['error']}")
    else:
        print("  FileSearchSkill 未找到")

    # 测试 FileReaderSkill
    print("\n测试 FileReaderSkill:")
    skill = get_mcp_skill("file_reader", context)
    if skill:
        result = await skill.execute({"file_path": "README.md", "lines_limit": 15})
        if result["success"]:
            print(f"  文件: {result['metadata']['file_name']}")
            print(f"  大小: {result['metadata']['file_size']} bytes")
            print(f"  行数: {result['metadata']['lines']}")
            print(f"  前15行:")
            print("  " + "-" * 50)
            content_lines = result["content"].split("\n")[:15]
            for line in content_lines:
                print(f"  {line}")
        else:
            print(f"  错误: {result['error']}")
    else:
        print("  FileReaderSkill 未找到")


async def test_data_analyzer():
    """测试数据分析Skills"""
    print("\n\n" + "=" * 60)
    print("测试 3: 数据分析Skills")
    print("=" * 60)

    context = {}

    # 测试 DataAnalyzerSkill
    print("\n测试 DataAnalyzerSkill (模拟数据):")
    skill = get_mcp_skill("data_analyzer", context)
    if skill:
        # 创建测试数据
        test_data = [
            {"项目": "项目A", "报价": 300000, "成本": 200000, "利润率": 0.20},
            {"项目": "项目B", "报价": 500000, "成本": 380000, "利润率": 0.18},
            {"项目": "项目C", "报价": 150000, "成本": 120000, "利润率": 0.15},
        ]

        result = await skill.execute(
            {"data_source": test_data, "analysis_type": "descriptive"}
        )

        if result["success"]:
            print(f"  数据行数: {result['analysis']['rows']}")
            print(f"  数据列数: {result['analysis']['columns']}")
            print(f"  列名: {', '.join(result['analysis']['column_names'])}")
            print(f"\n  洞察:")
            for insight in result["insights"]:
                print(f"    • {insight}")

            if result.get("preview"):
                print(f"\n  数据预览:")
                for row in result["preview"]:
                    print(f"    {row}")
        else:
            print(f"  错误: {result['error']}")
    else:
        print("  DataAnalyzerSkill 未找到")


async def test_project_evaluator():
    """测试项目评估Skill"""
    print("\n\n" + "=" * 60)
    print("测试 4: 项目评估Skill")
    print("=" * 60)

    context = {}

    skill = get_mcp_skill("project_evaluator", context)
    if skill:
        # 测试案例1: 高利润率项目
        print("\n案例1: 高利润率项目")
        result = await skill.execute(
            {
                "project_data": {
                    "quote_amount": 300000,
                    "cost": 180000,
                    "deadline": "2026-08-01",
                }
            }
        )

        if result["success"]:
            print(f"  可行性评分: {result['feasibility_score']}/100")
            print(f"  利润率: {result['profit_rate'] * 100:.1f}%")
            print(f"  风险等级: {result['risk_level']}")
            print(f"  利润分析:")
            for key, value in result["profit_analysis"].items():
                if isinstance(value, (int, float)):
                    print(f"    {key}: ¥{value:,.0f}")
                else:
                    print(f"    {key}: {value}")
            print(f"  建议:")
            for rec in result["recommendations"]:
                print(f"    • {rec}")

        # 测试案例2: 低利润率项目
        print("\n案例2: 低利润率项目")
        result = await skill.execute(
            {
                "project_data": {
                    "quote_amount": 200000,
                    "cost": 180000,
                    "deadline": "2026-08-15",
                }
            }
        )

        if result["success"]:
            print(f"  可行性评分: {result['feasibility_score']}/100")
            print(f"  利润率: {result['profit_rate'] * 100:.1f}%")
            print(f"  风险等级: {result['risk_level']}")
            print(f"  建议:")
            for rec in result["recommendations"]:
                print(f"    • {rec}")
    else:
        print("  ProjectEvaluatorSkill 未找到")


async def test_integrated_workflow():
    """测试集成工作流 - 完整的项目评估流程"""
    print("\n\n" + "=" * 60)
    print("测试 5: 集成工作流 - 完整项目评估")
    print("=" * 60)

    context = {}

    # 步骤1: 搜索项目文件
    print("\n步骤1: 搜索项目中的Excel文件")
    search_skill = get_mcp_skill("file_search", context)
    search_result = await search_skill.execute({"pattern": "*.xlsx", "limit": 3})

    if search_result["success"] and search_result["files"]:
        print(f"  找到 {search_result['count']} 个Excel文件")

        # 步骤2: 读取第一个文件
        print("\n步骤2: 读取报价单文件")
        reader_skill = get_mcp_skill("file_reader", context)
        # 注意: 实际项目中需要解析Excel,这里简化处理
        print(f"  模拟读取: {search_result['files'][0]}")

        # 步骤3: 项目评估
        print("\n步骤3: 评估项目可行性")
        evaluator_skill = get_mcp_skill("project_evaluator", context)
        eval_result = await evaluator_skill.execute(
            {
                "project_data": {
                    "quote_amount": 350000,
                    "cost": 240000,
                    "deadline": "2026-09-01",
                }
            }
        )

        if eval_result["success"]:
            print(f"\n  📊 评估结果:")
            print(f"  • 可行性评分: {eval_result['feasibility_score']}/100")
            print(f"  • 利润率: {eval_result['profit_rate'] * 100:.1f}%")
            print(f"  • 风险等级: {eval_result['risk_level']}")
            print(f"  • 预计利润: ¥{eval_result['profit_analysis']['profit']:,.0f}")

            print(f"\n  💡 建议:")
            for rec in eval_result["recommendations"]:
                print(f"    {rec}")
    else:
        print("  未找到Excel文件,使用模拟数据")


async def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print(" ArtPM Agent - 增强型MCP Skills 测试")
    print("=" * 60)

    try:
        await test_mcp_client()
        await test_file_skills()
        await test_data_analyzer()
        await test_project_evaluator()
        await test_integrated_workflow()

        print("\n\n" + "=" * 60)
        print("✅ 所有测试完成")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
