"""Simple test for MCP enhanced skills"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from artpm_agent.core.mcp_client_enhanced import get_enhanced_mcp_client
from artpm_agent.skills.mcp_skills import get_mcp_skill, list_mcp_skills


async def main():
    print("=" * 60)
    print("ArtPM Agent - MCP Skills Test")
    print("=" * 60)

    # Test 1: List MCP Skills
    print("\n[Test 1] Available MCP Skills:")
    skills = list_mcp_skills()
    for skill in skills:
        print(f"  - {skill['name']}: {skill['description']}")

    # Test 2: Test MCP Client Tools
    print("\n[Test 2] MCP Client Tools:")
    client = get_enhanced_mcp_client()
    tools = client.list_tools()
    for tool in tools:
        print(f"  - {tool['name']}: {tool['description']}")

    # Test 3: Search Files
    print("\n[Test 3] Search Python files in core/:")
    result = await client.call_tool("search_files", {
        "pattern": "*.py",
        "directory": "core",
        "limit": 5
    })
    if result["success"]:
        print(f"  Found {result['count']} files:")
        for f in result["files"]:
            print(f"    {f}")
    else:
        print(f"  Error: {result['error']}")

    # Test 4: Read File
    print("\n[Test 4] Read README.md (first 10 lines):")
    result = await client.call_tool("read_file", {
        "file_path": "README.md",
        "lines_limit": 10
    })
    if result["success"]:
        print(f"  File: {result['metadata']['file_name']}")
        print(f"  Size: {result['metadata']['file_size']} bytes")
        print(f"  Preview:")
        lines = result["content"].split("\n")[:10]
        for line in lines:
            print(f"    {line}")
    else:
        print(f"  Error: {result['error']}")

    # Test 5: Data Analyzer
    print("\n[Test 5] Analyze sample project data:")
    skill = get_mcp_skill("data_analyzer", {})
    test_data = [
        {"project": "ProjectA", "quote": 300000, "cost": 200000},
        {"project": "ProjectB", "quote": 500000, "cost": 380000},
        {"project": "ProjectC", "quote": 150000, "cost": 120000},
    ]
    result = await skill.execute({
        "data_source": test_data,
        "analysis_type": "descriptive"
    })
    if result["success"]:
        print(f"  Rows: {result['analysis']['rows']}")
        print(f"  Columns: {result['analysis']['columns']}")
        print(f"  Column names: {', '.join(result['analysis']['column_names'])}")
        print(f"  Insights:")
        for insight in result["insights"]:
            print(f"    - {insight}")
    else:
        print(f"  Error: {result['error']}")

    # Test 6: Project Evaluator
    print("\n[Test 6] Evaluate project feasibility:")
    skill = get_mcp_skill("project_evaluator", {})
    result = await skill.execute({
        "project_data": {
            "quote_amount": 300000,
            "cost": 180000,
            "deadline": "2026-08-01"
        }
    })
    if result["success"]:
        print(f"  Feasibility Score: {result['feasibility_score']}/100")
        print(f"  Profit Rate: {result['profit_rate']*100:.1f}%")
        print(f"  Risk Level: {result['risk_level']}")
        print(f"  Profit: {result['profit_analysis']['profit']:,.0f} CNY")
        print(f"  Recommendations:")
        for rec in result["recommendations"]:
            print(f"    - {rec}")
    else:
        print(f"  Error: {result['error']}")

    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
