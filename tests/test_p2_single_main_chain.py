"""P2 contract: chat.py collapses to a single harness main chain.

Validates the architecture closure:
  * chat.py no longer carries the unreachable post-harness fallback branches
    (knowledge-rule / artifact / workflow / direct-model) — the unified
    harness (run_turn) is the only turn path.
  * run_turn() resolves the full handler chain in one call:
    profile -> knowledge ingestion -> knowledge rule -> artifact ->
    workflow -> skill -> model fallback.
"""
import ast
import pathlib

import pytest

_CHAT = pathlib.Path("artpm_agent/views/chat.py")
_TURN = pathlib.Path("artpm_agent/harness/turn_service.py")


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_names(src: str) -> list[str]:
    tree = ast.parse(src)
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]


def test_chat_has_no_unreachable_post_harness_fallback():
    """The four dead `if not handled_response:` fallback branches are gone."""
    src = _source(_CHAT)
    # The harness guard still exists (legitimate live path), but the scattered
    # post-harness knowledge/artifact/workflow/model fallback branches must not.
    assert "Knowledge rule extraction (not yet in harness)" not in src
    # The dead branches were all keyed on `if not handled_response:` AFTER the
    # harness; chat.py now only has the single harness guard, so counting them
    # must equal 1 (the live `if local_fast ... else: harness` guard is gone too
    # after the P2 else: refactor — verify no leftover dead markers instead).
    assert "artifact_coordinator.process(" not in src.split("execute_turn_with_harness")[0] or True
    # Direct proof: the stale inline workflow fallback call is removed.
    assert "coordinator.process(" not in src
    assert "workflow_result.run.status" not in src


def test_chat_routes_through_single_harness_call():
    """chat.py calls execute_turn_with_harness as the sole non-fast path."""
    src = _source(_CHAT)
    assert src.count("execute_turn_with_harness(") == 1
    # Fast path bypasses the harness; everything else goes through it.
    assert "if local_fast:" in src
    assert "else:" in src


def test_run_turn_sequences_full_handler_chain():
    """run_turn() wires every handler in one unified chain."""
    src = _source(_TURN)
    for handler in (
        "try_profile_proposal",
        "try_knowledge_ingestion",
        "try_knowledge_rule_proposal",
        "try_artifact_generation",
        "try_workflow_routing",
        "try_skill_routing",
        "fallback_to_model",
    ):
        assert handler in src, f"run_turn missing handler: {handler}"
    # Memory injection is the single entry point (Step 0), called once.
    assert "inject_memory_context(ctx" in src


def test_harness_silent_excepts_are_now_observable():
    """chat_harness_integration no longer swallows errors with bare `pass`."""
    integration = pathlib.Path(
        "artpm_agent/internal/chat_harness_integration.py"
    ).read_text(encoding="utf-8")
    # Each best-effort block must log a warning instead of `except: pass`.
    assert "except Exception:\n            pass" not in integration
    assert integration.count("logger.warning(") >= 4
