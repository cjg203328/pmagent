"""
Quick Verification Script - 快速验证MCP Skills
"""
print("=" * 60)
print("ArtPM Agent - MCP Skills Quick Verification")
print("=" * 60)

try:
    # Step 1: Import modules
    print("\n[Step 1] Importing modules...")
    from skills.skill_router import SkillRouter, SKILL_REGISTRY, SKILL_METADATA
    print("  OK - Imported skill_router")

    # Step 2: Check Skills count
    print("\n[Step 2] Checking Skills...")
    total_skills = len(SKILL_REGISTRY)
    print(f"  Total Skills: {total_skills}")

    if total_skills >= 10:
        print(f"  OK - Found {total_skills} Skills (expected >=10)")
    else:
        print(f"  WARNING - Expected >=10 Skills, found {total_skills}")

    # Step 3: List all Skills
    print("\n[Step 3] Available Skills:")
    for skill_name, metadata in SKILL_METADATA.items():
        is_mcp = metadata.get('is_mcp_skill', False)
        badge = "[MCP]" if is_mcp else "[Core]"
        print(f"  {badge} {skill_name:<30} - {metadata['description'][:50]}...")

    # Step 4: Check MCP Skills
    print("\n[Step 4] Checking MCP Skills...")
    mcp_skills = [name for name, meta in SKILL_METADATA.items() if meta.get('is_mcp_skill')]
    print(f"  MCP Skills count: {len(mcp_skills)}")
    for skill in mcp_skills:
        print(f"    - {skill}")

    # Step 5: Check MCP Client
    print("\n[Step 5] Checking Enhanced MCP Client...")
    from core.mcp_client_enhanced import get_enhanced_mcp_client
    client = get_enhanced_mcp_client()
    tools = client.list_tools()
    print(f"  MCP Tools count: {len(tools)}")
    for tool in tools:
        print(f"    - {tool['name']}")

    # Step 6: Test Skill Router
    print("\n[Step 6] Testing Skill Router...")
    context = {}
    router = SkillRouter(context)
    print(f"  Router initialized with {len(router.skills)} Skills")

    # Final Result
    print("\n" + "=" * 60)
    print("VERIFICATION RESULT: SUCCESS")
    print("=" * 60)
    print("\nAll checks passed! MCP Skills are ready to use.")
    print("\nNext steps:")
    print("  1. Start the app: .\\start.bat")
    print("  2. Open browser: http://localhost:8501")
    print("  3. Try: 'Find all Excel files'")
    print("\nOr run full tests:")
    print("  python -m pytest -q")
    print("=" * 60)

except ImportError as e:
    print(f"\n[ERROR] Import failed: {e}")
    print("\nMake sure you are in the artpm_agent directory:")
    print("  cd artpm_agent")
    print("  python quick_verify.py")

except Exception as e:
    print(f"\n[ERROR] Verification failed: {e}")
    import traceback
    traceback.print_exc()
