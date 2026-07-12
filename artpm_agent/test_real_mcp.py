"""
测试真实MCP服务器连接
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.mcp_client_real import get_real_mcp_client


async def main():
    print("=" * 70)
    print("Testing Real MCP Server Connection")
    print("=" * 70)

    client = get_real_mcp_client()

    if not client.is_enabled():
        print("\n[ERROR] MCP SDK not available!")
        print("\nInstall MCP SDK:")
        print("  pip install mcp")
        print("\nOr install from git:")
        print("  pip install git+https://github.com/modelcontextprotocol/python-sdk.git")
        return

    print(f"\n[INFO] Configured servers: {', '.join(client.list_servers())}")

    # Test 1: Filesystem Server
    print("\n" + "=" * 70)
    print("[Test 1] Filesystem Server")
    print("=" * 70)

    print("\n[1.1] List available tools:")
    tools = await client.list_tools("filesystem")
    if tools:
        for tool in tools:
            print(f"  - {tool['name']}: {tool['description']}")
    else:
        print("  [WARN] No tools found or server not available")
        print("  Make sure Node.js is installed and npx is available")
        print("  Install filesystem server: npx -y @modelcontextprotocol/server-filesystem")

    if tools:
        print("\n[1.2] Read README.md:")
        result = await client.call_tool("filesystem", "read_file", {
            "path": "README.md"
        })

        if result["success"]:
            content = result["result"][:300]
            print(f"  Success! Content preview:\n  {content}...")
        else:
            print(f"  Failed: {result['error']}")

        print("\n[1.3] List files in artpm_agent/:")
        result = await client.call_tool("filesystem", "list_directory", {
            "path": "artpm_agent"
        })

        if result["success"]:
            print(f"  Success! Files:\n  {result['result'][:300]}...")
        else:
            print(f"  Failed: {result['error']}")

    # Test 2: SQLite Server
    print("\n" + "=" * 70)
    print("[Test 2] SQLite Server")
    print("=" * 70)

    print("\n[2.1] List available tools:")
    tools = await client.list_tools("sqlite")
    if tools:
        for tool in tools:
            print(f"  - {tool['name']}: {tool['description']}")

        print("\n[2.2] List tables:")
        result = await client.call_tool("sqlite", "list_tables", {})

        if result["success"]:
            print(f"  Success! Tables:\n  {result['result']}")
        else:
            print(f"  Failed: {result['error']}")
    else:
        print("  [WARN] SQLite server not available")
        print("  Install: npx -y @modelcontextprotocol/server-sqlite")

    # Cleanup
    await client.close()

    print("\n" + "=" * 70)
    print("Test completed!")
    print("=" * 70)
    print("\nNext steps:")
    print("  1. Install MCP servers if needed:")
    print("     npm install -g @modelcontextprotocol/server-filesystem")
    print("     npm install -g @modelcontextprotocol/server-sqlite")
    print("\n  2. Update Skills to use real MCP client")
    print("\n  3. Test in the application")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
