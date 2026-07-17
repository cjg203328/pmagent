"""Strict workspace Agent Profile contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


DEFAULT_WORKSPACE_ID = "local-default"
DEFAULT_PROFILE_ID = "local-default"

# Executable response-style rules. The label alone is ambiguous; the model needs
# a concrete instruction so "balanced" / "detailed" don't silently collapse into
# the hardcoded "concise" line elsewhere in the system prompt.
RESPONSE_STYLE_RULES: dict[str, str] = {
    "concise": (
        "回答先给结论，通常用 3–6 个要点；省略铺垫，不展开不必需的背景、示例或客套。"
    ),
    "balanced": (
        "回答先给结论，再给关键依据与下一步动作；需要解释时简明展开，不堆砌细节。"
    ),
    "detailed": (
        "回答给出完整假设、推理过程、风险与可执行步骤；适合需要深度分析或复盘的场景。"
    ),
}


def response_style_rule(style: str | None) -> str:
    """Return the executable instruction for a response style (default balanced)."""
    if style not in RESPONSE_STYLE_RULES:
        style = "balanced"
    return RESPONSE_STYLE_RULES[style]

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class StrictProfileModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class AgentIdentity(StrictProfileModel):
    """Structured identity fields; no tool or security permissions live here."""

    display_name: ShortText = "ArtPM Agent"
    role: ShortText = "游戏美术项目管理智能体"
    domain: ShortText = "游戏美术资产项目管理"
    response_style: Literal["concise", "balanced", "detailed"] = "balanced"
    language: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=2,
            max_length=16,
            pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$",
        ),
    ] = "zh-CN"
    guidance: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=1000),
    ] = "优先给出可核验的结论、假设和下一步动作。"


class QuotePolicy(StrictProfileModel):
    """Workspace-owned quote rates and risk thresholds."""

    overhead_rate: float = Field(default=0.15, ge=0, le=1)
    tax_rate: float = Field(default=0.06, ge=0, le=1)
    high_risk_below: float = Field(default=0.05, ge=-1, le=1)
    medium_risk_below: float = Field(default=0.15, ge=-1, le=1)
    currency: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=3,
            to_upper=True,
            pattern=r"^[A-Z]{3}$",
        ),
    ] = "CNY"

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> QuotePolicy:
        if self.high_risk_below >= self.medium_risk_below:
            raise ValueError("high_risk_below must be below medium_risk_below")
        return self


class AgentProfile(StrictProfileModel):
    """Immutable effective profile snapshot for one workspace revision."""

    workspace_id: Identifier = DEFAULT_WORKSPACE_ID
    profile_id: Identifier = DEFAULT_PROFILE_ID
    revision: int = Field(default=1, ge=1)
    identity: AgentIdentity = Field(default_factory=AgentIdentity)
    quote_policy: QuotePolicy = Field(default_factory=QuotePolicy)
    updated_at: str

    def quote_skill_inputs(self) -> dict[str, object]:
        """Return the explicit inputs expected by quote-related Skills."""
        policy = self.quote_policy
        return {
            "overhead_rate": policy.overhead_rate,
            "tax_rate": policy.tax_rate,
            "high_risk_below": policy.high_risk_below,
            "medium_risk_below": policy.medium_risk_below,
            "currency": policy.currency,
            "cost_config": {
                "overhead_rate": policy.overhead_rate,
                "tax_rate": policy.tax_rate,
                "currency": policy.currency,
                "risk_thresholds": {
                    "high_below": policy.high_risk_below,
                    "medium_below": policy.medium_risk_below,
                },
            },
        }

    def system_prompt_fragment(self) -> str:
        """Render trusted business identity without granting new capabilities."""
        identity = self.identity
        policy = self.quote_policy
        style_label = {
            "concise": "简洁",
            "balanced": "平衡",
            "detailed": "详细",
        }[identity.response_style]
        style_rule = response_style_rule(identity.response_style)
        return (
            "Workspace Agent Profile（可信业务配置，不改变工具权限或系统安全规则）：\n"
            f"- 名称：{identity.display_name}\n"
            f"- 角色：{identity.role}\n"
            f"- 领域：{identity.domain}\n"
            f"- 默认语言：{identity.language}\n"
            f"- 回答风格：{style_label}\n"
            f"  - 执行要求：{style_rule}\n"
            f"- 业务指引：{identity.guidance or '无'}\n"
            f"- 管理费率：{policy.overhead_rate:.2%}\n"
            f"- 税率：{policy.tax_rate:.2%}\n"
            f"- 高风险阈值：利润率低于 {policy.high_risk_below:.2%}\n"
            f"- 中风险阈值：利润率低于 {policy.medium_risk_below:.2%}\n"
            f"- 币种：{policy.currency}"
        )


class AgentIdentityPatch(StrictProfileModel):
    display_name: ShortText | None = None
    role: ShortText | None = None
    domain: ShortText | None = None
    response_style: Literal["concise", "balanced", "detailed"] | None = None
    language: str | None = Field(default=None, min_length=2, max_length=16)
    guidance: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def has_changes(self) -> AgentIdentityPatch:
        if not self.model_fields_set:
            raise ValueError("identity patch must contain at least one change")
        return self


class QuotePolicyPatch(StrictProfileModel):
    overhead_rate: float | None = Field(default=None, ge=0, le=1)
    tax_rate: float | None = Field(default=None, ge=0, le=1)
    high_risk_below: float | None = Field(default=None, ge=-1, le=1)
    medium_risk_below: float | None = Field(default=None, ge=-1, le=1)
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )

    @model_validator(mode="after")
    def has_changes(self) -> QuotePolicyPatch:
        if not self.model_fields_set:
            raise ValueError("quote policy patch must contain at least one change")
        return self


class AgentProfilePatch(StrictProfileModel):
    identity: AgentIdentityPatch | None = None
    quote_policy: QuotePolicyPatch | None = None

    @model_validator(mode="after")
    def has_changes(self) -> AgentProfilePatch:
        if self.identity is None and self.quote_policy is None:
            raise ValueError("profile patch must contain at least one change")
        return self


ProposalStatus = Literal["pending", "confirmed", "rejected", "conflict"]


class ProfileChangeProposal(StrictProfileModel):
    """A conversation-originated change that is inert until confirmation."""

    id: Identifier
    idempotency_key: Identifier
    workspace_id: Identifier
    profile_id: Identifier
    conversation_id: Identifier | None = None
    turn_id: Identifier | None = None
    base_revision: int = Field(ge=1)
    status: ProposalStatus
    patch: AgentProfilePatch
    summary: str = Field(min_length=1, max_length=500)
    actor: str | None = None
    applied_revision: int | None = Field(default=None, ge=1)
    created_at: str
    decided_at: str | None = None


def apply_profile_patch(
    profile: AgentProfile,
    patch: AgentProfilePatch,
    *,
    revision: int,
    updated_at: str,
) -> AgentProfile:
    """Apply a validated patch and revalidate cross-field policy invariants."""
    identity = profile.identity
    if patch.identity is not None:
        identity = AgentIdentity.model_validate(
            {
                **identity.model_dump(mode="python"),
                **patch.identity.model_dump(
                    mode="python",
                    exclude_none=True,
                    exclude_unset=True,
                ),
            }
        )
    quote_policy = profile.quote_policy
    if patch.quote_policy is not None:
        quote_policy = QuotePolicy.model_validate(
            {
                **quote_policy.model_dump(mode="python"),
                **patch.quote_policy.model_dump(
                    mode="python",
                    exclude_none=True,
                    exclude_unset=True,
                ),
            }
        )
    return AgentProfile(
        workspace_id=profile.workspace_id,
        profile_id=profile.profile_id,
        revision=revision,
        identity=identity,
        quote_policy=quote_policy,
        updated_at=updated_at,
    )
