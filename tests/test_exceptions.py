"""Tests for the current public exception utilities."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import artpm_agent.utils.exceptions as exceptions_module
from artpm_agent.utils.exceptions import (
    ArtPMError,
    DatabaseError,
    DataValidationError,
    LLMError,
    MCPError,
    ParserError,
    SkillExecutionError,
    handle_skill_errors,
)


def test_artpm_error_defaults_and_explicit_context() -> None:
    default_error = ArtPMError("operation failed")
    contextual_error = ArtPMError(
        "invalid request",
        code="INVALID_REQUEST",
        details={"request_id": "request-1"},
    )

    assert str(default_error) == "operation failed"
    assert default_error.message == "operation failed"
    assert default_error.code == "UNKNOWN"
    assert default_error.details == {}
    assert contextual_error.code == "INVALID_REQUEST"
    assert contextual_error.details == {"request_id": "request-1"}


def test_artpm_error_default_details_are_isolated() -> None:
    first = ArtPMError("first")
    second = ArtPMError("second")

    first.details["request_id"] = "request-1"

    assert second.details == {}


def test_skill_execution_error_has_stable_code_and_context() -> None:
    error = SkillExecutionError(
        "profit_calculation",
        "division by zero",
        input_param="cost",
        request_id="request-2",
    )

    assert isinstance(error, ArtPMError)
    assert error.code == "SKILL_EXECUTION_ERROR"
    assert error.details == {
        "skill_name": "profit_calculation",
        "input_param": "cost",
        "request_id": "request-2",
    }
    assert "'profit_calculation'" in str(error)
    assert "division by zero" in str(error)


def test_data_validation_error_has_stable_code_and_field() -> None:
    error = DataValidationError("email", "invalid format")

    assert error.code == "DATA_VALIDATION_ERROR"
    assert error.details == {"field": "email"}
    assert "email" in str(error)
    assert "invalid format" in str(error)


@pytest.mark.parametrize("error_type", [DatabaseError, ParserError, LLMError, MCPError])
def test_simple_domain_errors_preserve_artpm_error_contract(error_type) -> None:
    error = error_type("service unavailable", details={"request_id": "request-3"})

    assert isinstance(error, ArtPMError)
    assert error.code == "UNKNOWN"
    assert error.details == {"request_id": "request-3"}
    assert str(error) == "service unavailable"


@pytest.mark.asyncio
async def test_handle_skill_errors_returns_success_and_preserves_metadata() -> None:
    @handle_skill_errors("quote_calculator")
    async def execute(amount: int, *, currency: str = "CNY") -> str:
        """Execute one quote calculation."""
        return f"{amount} {currency}"

    assert await execute(120, currency="USD") == "120 USD"
    assert execute.__name__ == "execute"
    assert execute.__doc__ == "Execute one quote calculation."


@pytest.mark.asyncio
async def test_handle_skill_errors_reraises_known_error_unchanged(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(exceptions_module, "logger", logger)
    original = DataValidationError("amount", "must be positive")

    @handle_skill_errors("quote_calculator")
    async def execute() -> None:
        raise original

    with pytest.raises(DataValidationError) as exc_info:
        await execute()

    assert exc_info.value is original
    logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_handle_skill_errors_normalizes_unexpected_error(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(exceptions_module, "logger", logger)

    @handle_skill_errors("quote_calculator")
    async def execute() -> None:
        raise RuntimeError("provider failed")

    with pytest.raises(SkillExecutionError) as exc_info:
        await execute()

    error = exc_info.value
    assert error.code == "SKILL_EXECUTION_ERROR"
    assert error.details == {
        "skill_name": "quote_calculator",
        "original_exception": "RuntimeError",
    }
    assert isinstance(error.__context__, RuntimeError)
    logger.error.assert_called_once()
    assert logger.error.call_args.kwargs == {"exc_info": True}
