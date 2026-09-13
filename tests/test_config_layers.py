"""Tests for the layered configuration system (dsh profile/patch style)."""

from __future__ import annotations

import json

import pytest

from artpm_agent.config_layers import (
    ConfigLayerError,
    LayeredConfig,
    apply_patch,
    home_patch_path,
    load_layered_config,
    main,
    profile_names,
)


BASE = {
    "llm": {"provider": "anthropic", "model": "claude-3-5-sonnet-20241022", "max_tokens": 1200},
    "agent_runtime": {"model_tool_calls_enabled": False},
    "features": ["a", "b"],
}


# ── apply_patch ──


def test_apply_patch_replaces_whole_value_not_deep_merge():
    patched = apply_patch(BASE, {"llm": {"model": "deepseek-chat"}})

    # The whole llm dict is replaced (dsh rule), not merged.
    assert patched["llm"] == {"model": "deepseek-chat"}
    assert BASE["llm"]["model"] == "claude-3-5-sonnet-20241022"  # base untouched


def test_apply_patch_dot_path_walks_nested_dict():
    patched = apply_patch(BASE, {"llm.model": "deepseek-chat", "llm.provider": "deepseek"})

    assert patched["llm"]["model"] == "deepseek-chat"
    assert patched["llm"]["provider"] == "deepseek"
    assert patched["llm"]["max_tokens"] == 1200  # sibling preserved with dot path


def test_apply_patch_inserts_new_key_and_copies_values():
    patched = apply_patch(BASE, {"new_section": {"x": [1, 2]}})

    assert patched["new_section"] == {"x": [1, 2]}
    patched["new_section"]["x"].append(3)
    assert BASE.get("new_section") is None  # no leak into base


def test_apply_patch_rejects_empty_dot_key():
    with pytest.raises(ConfigLayerError):
        apply_patch(BASE, {"llm.": 1})


# ── LayeredConfig ──


def test_layered_config_later_layers_win():
    layered = LayeredConfig(BASE)
    layered.add_patch("p1", {"llm": {"model": "a"}})
    layered.add_patch("p2", {"llm": {"model": "b"}})

    merged = layered.merged()
    assert merged["llm"] == {"model": "b"}


def test_dump_annotates_winning_layer(tmp_path, monkeypatch):
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    (profile_root / "dev").mkdir()
    (profile_root / "dev" / "config.patch.json").write_text(
        json.dumps({"llm": {"model": "deepseek-chat"}}), encoding="utf-8"
    )
    monkeypatch.setenv("ARTPM_PROFILE_DIR", str(profile_root))

    layered = load_layered_config(BASE, profile="dev", include_home_patch=False)

    annotated = layered.dump(annotate=True)
    assert annotated["llm"]["layer"] == "profile:dev"
    assert annotated["llm"]["value"] == {"model": "deepseek-chat"}
    assert annotated["agent_runtime"]["layer"] == "base"


def test_missing_profile_patch_fails_loudly(tmp_path):
    layered = LayeredConfig(BASE)
    with pytest.raises(ConfigLayerError, match="does not exist"):
        layered.add_json_file("profile:missing", tmp_path / "nope" / "config.patch.json")


def test_optional_home_patch_skipped_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ~/ may not exist; use HOME isolation
    monkeypatch.setenv("HOME", str(tmp_path))
    layered = LayeredConfig(BASE)
    layered.add_json_file("home", home_patch_path(), optional=True)
    assert layered.merged() == BASE


def test_load_layered_config_order_base_profile_home_overlay(tmp_path, monkeypatch):
    profile_root = tmp_path / "profiles"
    (profile_root / "prod").mkdir(parents=True)
    (profile_root / "prod" / "config.patch.json").write_text(
        json.dumps({"llm": {"model": "deepseek-chat"}}), encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()
    (home / ".artpm").mkdir()
    (home / ".artpm" / "config.patch.json").write_text(
        json.dumps({"llm.model": "deepseek-reasoner"}), encoding="utf-8"
    )
    overlay = tmp_path / "overlay.json"
    overlay.write_text(
        json.dumps({"llm.model": "custom-override"}), encoding="utf-8"
    )
    monkeypatch.setenv("ARTPM_PROFILE_DIR", str(profile_root))
    monkeypatch.setenv("HOME", str(home))

    layered = load_layered_config(BASE, profile="prod", overlays=[overlay])

    merged = layered.merged()
    # profile replaces whole llm -> then home dot-patch -> then overlay wins.
    assert merged["llm"] == {"model": "custom-override"}


def test_profile_names_lists_only_patched_dirs(tmp_path, monkeypatch):
    (tmp_path / "profiles" / "dev").mkdir(parents=True)
    (tmp_path / "profiles" / "dev" / "config.patch.json").write_text("{}", encoding="utf-8")
    (tmp_path / "profiles" / "empty").mkdir()
    monkeypatch.setenv("ARTPM_PROFILE_DIR", str(tmp_path / "profiles"))

    assert profile_names() == ("dev",)


# ── CLI ──


def test_cli_list_profiles(tmp_path, monkeypatch, capsys):
    (tmp_path / "profiles" / "dev").mkdir(parents=True)
    (tmp_path / "profiles" / "dev" / "config.patch.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ARTPM_PROFILE_DIR", str(tmp_path / "profiles"))

    assert main(["--list-profiles"]) == 0
    assert capsys.readouterr().out.strip() == "dev"


def test_cli_dump_config_annotated(tmp_path, monkeypatch, capsys):
    (tmp_path / "profiles" / "dev").mkdir(parents=True)
    (tmp_path / "profiles" / "dev" / "config.patch.json").write_text(
        json.dumps({"llm.model": "deepseek-chat"}), encoding="utf-8"
    )
    monkeypatch.setenv("ARTPM_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("HOME", str(tmp_path / "nonexistent-home"))

    assert main(["--profile", "dev", "--dump-config", "--no-home"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["llm"]["value"]["model"] == "deepseek-chat"
    assert payload["llm"]["layer"] == "profile:dev"


def test_cli_missing_profile_returns_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARTPM_PROFILE_DIR", str(tmp_path / "profiles"))

    assert main(["--profile", "ghost", "--dump-config", "--no-home"]) == 1
    assert "config error" in capsys.readouterr().err


# ── Config integration ──


def test_config_applies_active_profile_before_env(monkeypatch):
    """ARTPM_PROFILE patch is applied, then env overrides still win."""
    import tempfile
    from pathlib import Path

    from artpm_agent.config import Config

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "profiles" / "dev").mkdir(parents=True)
        (root / "profiles" / "dev" / "config.patch.json").write_text(
            json.dumps({"llm": {"provider": "deepseek", "model": "deepseek-chat"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("ARTPM_PROFILE", "dev")
        monkeypatch.setenv("ARTPM_PROFILE_DIR", str(root / "profiles"))
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)

        config = Config()

        assert config.config["llm"]["provider"] == "deepseek"
        assert config.config["llm"]["model"] == "deepseek-chat"
