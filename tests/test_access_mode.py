from __future__ import annotations

import pytest

from artpm_agent.security import (
    ACCESS_MODE_CONTROLLED,
    ACCESS_MODE_FULL,
    access_decision,
    effective_access_risk,
    normalize_access_mode,
)


class ReadyPermissionStore:
    def create_request(self, **_kwargs):
        raise AssertionError("full-access preauthorization should not create a request")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ACCESS_MODE_FULL, ACCESS_MODE_FULL),
        (" FULL_ACCESS ", ACCESS_MODE_FULL),
        (ACCESS_MODE_CONTROLLED, ACCESS_MODE_CONTROLLED),
        ("unknown", ACCESS_MODE_CONTROLLED),
        (None, ACCESS_MODE_CONTROLLED),
    ],
)
def test_access_mode_normalization_fails_closed(value, expected):
    assert normalize_access_mode(value) == expected


def test_full_access_allows_only_trusted_low_or_medium_user_actions():
    context = {
        "permission_mode": ACCESS_MODE_FULL,
        "permission_store": ReadyPermissionStore(),
    }

    assert access_decision(
        context,
        read_only=False,
        requires_approval=True,
        risk="medium",
        required_role="user",
        auto_approval_allowed=True,
    ) == "allow"

    for risk in ("high", "critical", "untrusted"):
        assert access_decision(
            context,
            read_only=False,
            requires_approval=True,
            risk=risk,
            required_role="user",
            auto_approval_allowed=True,
        ) == "confirm"

    assert access_decision(
        context,
        read_only=False,
        requires_approval=True,
        risk="medium",
        required_role="admin",
        auto_approval_allowed=True,
    ) == "confirm"


@pytest.mark.parametrize(
    "context",
    [
        {"permission_mode": ACCESS_MODE_CONTROLLED, "permission_store": ReadyPermissionStore()},
        {"permission_mode": ACCESS_MODE_FULL},
        {"permission_mode": "forged", "permission_store": ReadyPermissionStore()},
    ],
)
def test_write_access_fails_closed_without_a_valid_full_access_runtime(context):
    assert access_decision(
        context,
        read_only=False,
        requires_approval=True,
        risk="medium",
        required_role="user",
        auto_approval_allowed=True,
    ) == "confirm"


def test_read_only_action_does_not_need_an_access_grant():
    assert access_decision(
        None,
        read_only=True,
        requires_approval=False,
        risk="low",
    ) == "allow"


@pytest.mark.parametrize(
    ("action", "arguments", "expected"),
    [
        ("record_progress", {}, "medium"),
        ("publish_report", {}, "high"),
        ("record_progress", {"action": "delete"}, "high"),
        ("workspace_tool", {"command": "whoami"}, "critical"),
        ("record_progress", {"operation": "send"}, "untrusted"),
    ],
)
def test_effective_access_risk_applies_server_owned_floors(
    action,
    arguments,
    expected,
):
    declared = "untrusted" if expected == "untrusted" else "medium"
    assert effective_access_risk(
        declared,
        action=action,
        arguments=arguments,
    ) == expected
