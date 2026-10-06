"""Configuration admin tests.

Two properties matter most and are pinned here:

1. **No widening.** Only allowlisted keys can be read or written, so a request
   can never introduce an arbitrary environment variable.
2. **No plaintext secrets.** A configured secret is reported as a boolean plus
   a masked preview; the value itself never leaves the process.
"""

from __future__ import annotations

import os

import pytest

from artpm_agent.api import config_admin
from artpm_agent.api.config_admin import (
    CONFIG_GROUPS,
    ConfigValidationError,
    _coerce,
    _mask,
    apply_config,
    known_keys,
    read_config,
)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Point the writer at a throwaway .env and keep os.environ clean."""

    path = tmp_path / ".env"
    # newline="\n" is required: write_text in text mode would translate the LF
    # below into CRLF on Windows and hide the line-ending behaviour under test.
    path.write_text("# managed\nLLM_PROVIDER=anthropic\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(config_admin, "_env_path", lambda: path)

    touched = [field.key for group in CONFIG_GROUPS for field in group.fields]
    saved = {key: os.environ.get(key) for key in touched}
    for key in touched:
        monkeypatch.delenv(key, raising=False)
    yield path
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_read_reports_every_allowlisted_key(env_file):
    config = read_config()
    reported = {field["key"] for group in config["groups"] for field in group["fields"]}
    assert reported == set(known_keys())
    assert config["env_path"].endswith(".env")
    assert config["groups"], "groups must not be empty"


def test_secret_value_is_never_returned(env_file, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-super-secret-value")

    config = read_config()
    secret = next(
        field
        for group in config["groups"]
        for field in group["fields"]
        if field["key"] == "ANTHROPIC_API_KEY"
    )

    assert secret["value"] == ""
    assert secret["configured"] is True
    assert "super-secret-value" not in secret["preview"]
    assert secret["preview"].endswith("alue")


def test_unconfigured_secret_reports_not_configured(env_file):
    config = read_config()
    secret = next(
        field
        for group in config["groups"]
        for field in group["fields"]
        if field["key"] == "OPENAI_API_KEY"
    )
    assert secret["value"] == ""
    assert secret["configured"] is False
    assert secret["preview"] == ""


def test_non_secret_values_are_returned_verbatim(env_file, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
    config = read_config()
    model = next(
        field
        for group in config["groups"]
        for field in group["fields"]
        if field["key"] == "LLM_MODEL"
    )
    assert model["value"] == "claude-3-5-sonnet-20241022"


def test_apply_rejects_unknown_keys_before_writing(env_file):
    before = env_file.read_text(encoding="utf-8")
    with pytest.raises(ConfigValidationError) as error:
        apply_config({"EVIL_INJECTED_VAR": "1"})
    assert "EVIL_INJECTED_VAR" in str(error.value)
    assert env_file.read_text(encoding="utf-8") == before


def test_apply_rejects_an_empty_batch(env_file):
    with pytest.raises(ConfigValidationError):
        apply_config({})


def test_apply_persists_and_exposes_the_new_value(env_file):
    result = apply_config({"LLM_MODEL": "claude-3-5-haiku-20241022"})

    assert result["applied"] == ["LLM_MODEL"]
    text = env_file.read_text(encoding="utf-8")
    # python-dotenv may quote a value that contains punctuation, so assert on
    # the content and on the round-trip rather than on exact quoting.
    assert "claude-3-5-haiku-20241022" in text
    assert os.environ["LLM_MODEL"] == "claude-3-5-haiku-20241022"
    # Comments in the existing file must survive the write.
    assert "# managed" in text

    reloaded = read_config()
    model = next(
        field
        for group in reloaded["groups"]
        for field in group["fields"]
        if field["key"] == "LLM_MODEL"
    )
    assert model["value"] == "claude-3-5-haiku-20241022"


def test_apply_clears_a_value_when_submitted_empty(env_file, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-existing")
    apply_config({"ANTHROPIC_API_KEY": ""})

    # An emptied key is either removed or left blank; both read as unconfigured.
    assert (os.environ.get("ANTHROPIC_API_KEY") or "") == ""
    assert "sk-ant-existing" not in env_file.read_text(encoding="utf-8")

    config = read_config()
    secret = next(
        field
        for group in config["groups"]
        for field in group["fields"]
        if field["key"] == "ANTHROPIC_API_KEY"
    )
    assert secret["configured"] is False


def test_cleared_value_uses_the_bare_assignment_form(env_file, monkeypatch):
    """An emptied key must read ``KEY=``, not dotenv's ``KEY=''``."""

    monkeypatch.setenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
    apply_config({"LLM_MODEL": ""})

    line = next(
        item
        for item in env_file.read_text(encoding="utf-8").splitlines()
        if item.startswith("LLM_MODEL=")
    )
    assert line == "LLM_MODEL="


def test_values_with_whitespace_survive_a_round_trip(env_file):
    apply_config({"LLM_MODEL": "claude 3.5 sonnet"})

    config = read_config()
    model = next(
        field
        for group in config["groups"]
        for field in group["fields"]
        if field["key"] == "LLM_MODEL"
    )
    assert model["value"] == "claude 3.5 sonnet"


def test_lf_file_stays_lf_after_a_write(env_file):
    """An LF .env must not be rewritten as CRLF by a one-key edit."""

    assert b"\r\n" not in env_file.read_bytes()
    apply_config({"LLM_MODEL": "claude-3-5-haiku-20241022"})
    assert b"\r\n" not in env_file.read_bytes()


def test_crlf_file_keeps_its_style(env_file):
    env_file.write_bytes(b"# managed\r\nLLM_PROVIDER=anthropic\r\n")
    apply_config({"LLM_MODEL": "claude-3-5-haiku-20241022"})

    raw = env_file.read_bytes()
    assert b"\r\n" in raw
    # No bare LF should be left behind by the normalization step.
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0


def test_clearing_a_value_also_preserves_lf(env_file, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
    apply_config({"LLM_MODEL": ""})

    raw = env_file.read_bytes()
    assert b"\r\n" not in raw
    assert b"LLM_MODEL=\n" in raw


def test_secret_can_be_written_and_is_then_masked(env_file):
    apply_config({"ANTHROPIC_API_KEY": "sk-ant-brand-new-key"})
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-brand-new-key"

    config = read_config()
    secret = next(
        field
        for group in config["groups"]
        for field in group["fields"]
        if field["key"] == "ANTHROPIC_API_KEY"
    )
    assert secret["configured"] is True
    assert "brand-new-key" not in secret["preview"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("LLM_MAX_TOKENS", "not-a-number"),
        ("LLM_REQUEST_TIMEOUT_SECONDS", "abc"),
        ("ARTPM_RESPONSE_CACHE", "maybe"),
        ("LLM_PROVIDER", "not-a-real-provider"),
        ("LLM_FRAMEWORK", "carrier-pigeon"),
    ],
)
def test_invalid_values_are_rejected(env_file, key, value):
    with pytest.raises(ConfigValidationError):
        apply_config({key: value})


def test_a_rejected_batch_writes_nothing(env_file):
    before = env_file.read_text(encoding="utf-8")
    with pytest.raises(ConfigValidationError):
        # The second key is invalid, so the first must not be persisted either.
        apply_config({"LLM_MODEL": "some-model", "LLM_MAX_TOKENS": "nope"})
    assert env_file.read_text(encoding="utf-8") == before


def test_line_breaks_in_text_values_are_rejected(env_file):
    with pytest.raises(ConfigValidationError):
        apply_config({"LLM_MODEL": "line1\nLLM_PROVIDER=openai"})


def test_coerce_normalizes_booleans_and_numbers():
    assert _coerce(config_admin._FIELDS_BY_KEY["ARTPM_RESPONSE_CACHE"], "YES") == "true"
    assert _coerce(config_admin._FIELDS_BY_KEY["ARTPM_RESPONSE_CACHE"], "off") == "false"
    assert _coerce(config_admin._FIELDS_BY_KEY["ARTPM_RESPONSE_CACHE"], True) == "true"
    assert _coerce(config_admin._FIELDS_BY_KEY["LLM_MAX_TOKENS"], " 1200 ") == "1200"
    assert _coerce(config_admin._FIELDS_BY_KEY["LLM_REQUEST_TIMEOUT_SECONDS"], "12.5") == "12.5"
    assert _coerce(config_admin._FIELDS_BY_KEY["LLM_MODEL"], "  ") == ""


def test_mask_never_reveals_short_secrets():
    assert _mask("") == ""
    assert _mask("abc") == "•••"
    assert _mask("sk-ant-1234567890").endswith("7890")
    assert "1234" not in _mask("sk-ant-1234567890")
