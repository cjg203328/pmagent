from datetime import datetime

import pytest

from artpm_agent.ui_formatters import (
    artifact_subtitle,
    current_model_id,
    format_cn_date,
    format_money,
    format_size,
    history_limits,
    normalize_agent_response,
    response_model_id,
)


def test_formatters_cover_common_display_values():
    value = datetime(2026, 9, 14, 8, 5)
    assert format_cn_date(value) == "9月14日"
    assert format_cn_date(value, include_time=True) == "2026年9月14日 08:05"
    assert format_money(12500, compact=True) == "¥1.2万"
    assert format_money(1200) == "¥1,200"
    assert format_size(2048) == "2.0 KB"
    assert format_size(0) == ""


def test_response_helpers_use_runtime_model_when_available():
    class Agent:
        last_response_model = " fallback-model "
        config = {"llm.model": "primary-model"}

    assert current_model_id(Agent()) == "primary-model"
    assert response_model_id(Agent()) == "fallback-model"
    assert history_limits(Agent()) == (12, 8000)
    assert artifact_subtitle({"format": "xlsx", "rows": 4, "columns": 2}) == "4 行 · 2 列"


def test_normalize_response_rejects_blank_values():
    assert normalize_agent_response("  ok ") == "ok"
    with pytest.raises(ValueError, match="有效回答"):
        normalize_agent_response(" ")
