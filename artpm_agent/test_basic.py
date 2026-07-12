"""
Simple test to verify the basic framework
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from agent import ArtPMAgent


def check_agent_initialization():
    """Test agent initialization"""
    print("Testing agent initialization...")

    try:
        # This will fail if API keys are not set, but that's expected
        agent = ArtPMAgent()
        print("✓ Agent initialized successfully")
        return True
    except ValueError as e:
        if "API_KEY" in str(e):
            print("✓ Agent structure correct (API key not configured, which is expected)")
            return True
        else:
            print(f"✗ Unexpected error: {e}")
            return False
    except Exception as e:
        print(f"✗ Failed to initialize agent: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_config_system():
    """Test configuration system"""
    print("\nTesting configuration system...")

    try:
        from config import Config
        config = Config()

        # Test getting values
        llm_provider = config.get("llm.provider")
        db_path = config.get("database.db_path")

        print(f"  LLM Provider: {llm_provider}")
        print(f"  DB Path: {db_path}")
        print("✓ Configuration system working")
        return True
    except Exception as e:
        print(f"✗ Configuration test failed: {e}")
        return False


def check_database():
    """Test database initialization"""
    print("\nTesting database...")

    try:
        from memory.sqlite_manager import SQLiteManager
        import tempfile
        import os

        # Use temporary database
        temp_db = os.path.join(tempfile.gettempdir(), "test_artpm.db")
        db = SQLiteManager(temp_db)

        # Test basic operations
        from utils import generate_uuid
        test_id = generate_uuid()

        # Insert test record
        db.insert("projects", {
            "id": test_id,
            "name": "Test Project",
            "client_name": "Test Client",
            "status": "pending"
        })

        # Query back
        result = db.get_by_id("projects", test_id)

        if result and result["name"] == "Test Project":
            print("[OK] Database operations working")

            # Cleanup
            os.remove(temp_db)
            return True
        else:
            print("[FAIL] Database query failed")
            return False

    except Exception as e:
        print(f"[FAIL] Database test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_skill_system():
    """Test skill system"""
    print("\nTesting skill system...")

    try:
        from skills import BaseSkill

        # Create a test skill
        class TestSkill(BaseSkill):
            skill_name = "test_skill"
            description = "A test skill"

            def execute(self, inputs):
                return {"result": "success", "input_received": inputs.get("test_input")}

        # Test the skill
        skill = TestSkill()
        result = skill.run({"test_input": "hello"})

        if result["success"] and result["result"] == "success":
            print("✓ Skill system working")
            return True
        else:
            print("✗ Skill execution failed")
            return False

    except Exception as e:
        print(f"✗ Skill test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_skill_router():
    """Test that SkillRouter loads inline skills correctly"""
    print("\nTesting skill router...")

    try:
        from skills import SkillRouter

        router = SkillRouter({})
        loaded = router.list_skills()

        if not loaded:
            print("✗ Skill router loaded 0 skills (should be 5)")
            return False

        expected = {"document_classifier_parser", "quote_calculator",
                     "task_allocator", "progress_tracker", "reminder_bot"}
        loaded_names = {s["skill_name"] for s in loaded}

        if expected.issubset(loaded_names):
            print(f"✓ Skill router loaded all {len(loaded)} skills correctly")
            return True
        else:
            missing = expected - loaded_names
            if missing:
                print(f"✗ Missing skills: {missing}")
            return False

    except Exception as e:
        print(f"✗ Skill router test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_agent_initialization():
    assert check_agent_initialization()


def test_config_system():
    assert check_config_system()


def test_database():
    assert check_database()


def test_skill_system():
    assert check_skill_system()


def test_skill_router():
    assert check_skill_router()


def main():
    """Run all tests"""
    print("=" * 60)
    print("ArtPM Copilot - Basic Framework Tests")
    print("=" * 60)

    tests = [
        check_config_system,
        check_database,
        check_skill_system,
        check_skill_router,
        check_agent_initialization,
    ]

    results = []
    for test in tests:
        results.append(test())

    print("\n" + "=" * 60)
    print(f"Test Results: {sum(results)}/{len(results)} passed")
    print("=" * 60)

    if all(results):
        print("\n✓ All basic framework tests passed!")
        print("\nNext steps:")
        print("1. Configure .env file with your API keys")
        print("2. Run: python main.py")
        print("3. Start implementing Phase 2 skills (document processing)")
    else:
        print("\n✗ Some tests failed. Please check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
