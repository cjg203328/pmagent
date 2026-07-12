"""Coordinate explicit artifact requests through a strict LLM JSON plan."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .generator import WorkspaceArtifactGenerator


MAX_PLAN_COLUMNS = 100
MAX_PLAN_ROWS = 10_000
MAX_PLAN_PARAGRAPHS = 2_000
MAX_PLAN_FILENAME_CHARS = 120
MAX_PLAN_JSON_CHARS = 4_000_000
MAX_PROMPT_CHARS = 8_000
MAX_PLAN_TEXT_CHARS = 32_000

PlanFormat = Literal["xlsx", "docx"]
PlanScalar = StrictStr | StrictInt | StrictFloat | StrictBool | None
PlanText = Annotated[
    str,
    StringConstraints(max_length=MAX_PLAN_TEXT_CHARS),
]
Filename = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PLAN_FILENAME_CHARS,
    ),
]

_SAFE_ACTION = re.compile(
    r"(?:生成|创建|导出|制作|新建|\b(?:generate|create|export)\b)",
    re.IGNORECASE,
)
_XLSX_TARGET = re.compile(
    r"(?:\b(?:excel|xlsx|spreadsheet)\b|电子表格|表格)",
    re.IGNORECASE,
)
_DOCX_TARGET = re.compile(
    r"(?:\b(?:word|docx|document)\b|文档)",
    re.IGNORECASE,
)
_SENSITIVE_ACTION = (
    r"(?:删除|移除|清空|覆盖|替换|修改|编辑|更新|追加|改写|重写|"
    r"改(?:一下|动)?(?!进)|"
    r"\b(?:delete|remove|overwrite|replace|modify|edit|update|append)\b)"
)
_ARTIFACT_OBJECT = (
    r"(?:(?:现有|已有|这个|这份|该|原|旧|existing|current)\s*)?"
    r"(?:excel|xlsx|word|docx|spreadsheet|document|电子表格|表格|文档|文件|"
    r"[^\s/\\]+\.(?:xlsx|docx))"
)
_SENSITIVE_REQUEST = re.compile(
    rf"(?:{_SENSITIVE_ACTION}\s*{_ARTIFACT_OBJECT}|"
    rf"{_ARTIFACT_OBJECT}\s*(?:文件)?\s*{_SENSITIVE_ACTION})",
    re.IGNORECASE,
)
_NEGATED_SENSITIVE = re.compile(
    r"(?:不要|不得|无需|避免|不需要|do\s+not|don't)\s*"
    + _SENSITIVE_ACTION,
    re.IGNORECASE,
)
_JSON_FENCE = re.compile(
    r"\A\s*```(?:json)?\s*(.*?)\s*```\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
_XLSX_COLUMNS = re.compile(
    r"(?:列|字段)(?:为|包括|包含)?[：:\s]*"
    r"(.+?)(?=[，,]\s*(?:包含|数据|内容)(?:为|包括|包含)?[：:]?|$)",
    re.IGNORECASE,
)
_EXPLICIT_VALUES = re.compile(
    r"(?:包含|数据(?:为|包括|包含)?|内容(?:为|包括|包含)?)"
    r"[：:\s]*(.+)$",
    re.IGNORECASE,
)
_DOCX_CONTENT = re.compile(
    r"(?:正文|内容)(?:(?:为|是)[：:\s]*|[：:\s]+)(.+)$",
    re.IGNORECASE | re.DOTALL,
)


class StrictPlanModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class XlsxTablePlan(StrictPlanModel):
    sheet_name: PlanText = "Sheet1"
    columns: list[PlanText] = Field(
        min_length=1,
        max_length=MAX_PLAN_COLUMNS,
    )
    rows: list[list[PlanScalar] | dict[StrictStr, PlanScalar]] = Field(
        default_factory=list,
        max_length=MAX_PLAN_ROWS,
    )

    @model_validator(mode="after")
    def validate_table(self) -> XlsxTablePlan:
        columns = [column.strip() for column in self.columns]
        if any(not column for column in columns):
            raise ValueError("table columns must be non-empty")
        if len(columns) != len(set(columns)):
            raise ValueError("table columns must be unique")
        column_set = set(columns)
        for index, row in enumerate(self.rows):
            if isinstance(row, list) and len(row) != len(columns):
                raise ValueError(
                    f"table row {index} must contain {len(columns)} cells"
                )
            if isinstance(row, dict) and set(row) - column_set:
                raise ValueError(f"table row {index} contains unknown columns")
        return self


class DocxParagraphPlan(StrictPlanModel):
    text: PlanText
    kind: Literal["paragraph", "heading", "bullet", "numbered"] = "paragraph"
    level: int = Field(default=1, ge=1, le=9)
    bold: StrictBool = False
    italic: StrictBool = False


class ArtifactPlan(StrictPlanModel):
    format: PlanFormat
    filename: Filename
    table: XlsxTablePlan | None = None
    paragraphs: list[DocxParagraphPlan] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PLAN_PARAGRAPHS,
    )

    @field_validator("filename")
    @classmethod
    def filename_is_not_a_path(cls, value: str) -> str:
        if "/" in value or "\\" in value or re.match(r"^[A-Za-z]:", value):
            raise ValueError("filename must not contain a path")
        return value

    @model_validator(mode="after")
    def fields_match_format(self) -> ArtifactPlan:
        if self.format == "xlsx":
            if self.table is None or self.paragraphs is not None:
                raise ValueError("xlsx plans require table and forbid paragraphs")
        elif self.paragraphs is None or self.table is not None:
            raise ValueError("docx plans require paragraphs and forbid table")
        return self


@dataclass(frozen=True)
class ArtifactCoordinationResult:
    """Chat-ready coordinator outcome."""

    matched: bool
    rejected: bool
    message: str
    requested_format: PlanFormat | None = None
    artifact: dict[str, Any] | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "rejected": self.rejected,
            "message": self.message,
            "requested_format": self.requested_format,
            "artifact": dict(self.artifact) if self.artifact is not None else None,
            "error_code": self.error_code,
        }


class ArtifactCoordinator:
    """Generate only new artifacts for explicit, non-destructive requests."""

    def __init__(self, generator: WorkspaceArtifactGenerator, llm: Any) -> None:
        if not isinstance(generator, WorkspaceArtifactGenerator):
            raise TypeError("generator must be a WorkspaceArtifactGenerator")
        chat = getattr(llm, "chat", None)
        if not callable(chat):
            raise TypeError("llm must expose a callable chat method")
        self.generator = generator
        self.llm = llm

    @staticmethod
    def _detect_format(prompt: str) -> tuple[PlanFormat | None, str | None]:
        has_xlsx = bool(_XLSX_TARGET.search(prompt))
        has_docx = bool(_DOCX_TARGET.search(prompt))
        if has_xlsx and has_docx:
            return None, "ambiguous_format"
        if has_xlsx:
            return "xlsx", None
        if has_docx:
            return "docx", None
        return None, None

    @staticmethod
    def _is_sensitive_request(prompt: str) -> bool:
        without_negated_constraints = _NEGATED_SENSITIVE.sub("", prompt)
        return bool(_SENSITIVE_REQUEST.search(without_negated_constraints))

    @staticmethod
    def _system_prompt(expected_format: PlanFormat) -> str:
        common = (
            "你只负责把用户的明确新文件生成请求转换成一个 JSON 对象。"
            "只输出 JSON，不要解释、不要 Markdown。不得提供路径，不得请求覆盖、"
            "删除或修改现有文件。所有值必须是 JSON 标量。"
        )
        if expected_format == "xlsx":
            schema = (
                '{"format":"xlsx","filename":"name.xlsx",'
                '"table":{"sheet_name":"Sheet1","columns":["列"],'
                '"rows":[["值"]]}}'
                f"。最多 {MAX_PLAN_COLUMNS} 列、{MAX_PLAN_ROWS} 行。"
            )
        else:
            schema = (
                '{"format":"docx","filename":"name.docx",'
                '"paragraphs":[{"text":"内容","kind":"paragraph",'
                '"level":1,"bold":false,"italic":false}]}'
                f"。最多 {MAX_PLAN_PARAGRAPHS} 段。"
            )
        return common + "严格使用以下结构：" + schema

    @staticmethod
    def _json_payload(raw_response: Any) -> dict[str, Any]:
        if not isinstance(raw_response, str) or not raw_response.strip():
            raise ValueError("LLM returned an empty artifact plan")
        text = raw_response.strip()
        if len(text) > MAX_PLAN_JSON_CHARS:
            raise ValueError("LLM artifact plan is too large")
        fence = _JSON_FENCE.fullmatch(text)
        if fence:
            text = fence.group(1).strip()

        def reject_constant(value: str) -> None:
            raise ValueError(f"unsupported JSON constant: {value}")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON key: {key}")
                result[key] = value
            return result

        parsed = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
        if not isinstance(parsed, dict):
            raise ValueError("artifact plan must be a JSON object")
        return parsed

    @staticmethod
    def _fallback_plan(
        prompt: str,
        expected_format: PlanFormat,
    ) -> ArtifactPlan | None:
        """Build only from explicit fields; never infer missing business data."""
        if expected_format == "xlsx":
            column_match = _XLSX_COLUMNS.search(prompt)
            if column_match is None:
                return None
            columns = [
                item.strip()
                for item in re.split(r"[、,，|/]", column_match.group(1))
                if item.strip()
            ]
            if not columns or len(columns) > MAX_PLAN_COLUMNS:
                return None
            rows: list[list[PlanScalar]] = []
            values_match = _EXPLICIT_VALUES.search(prompt)
            if values_match is not None:
                values = [
                    item.strip()
                    for item in re.split(r"[、,，|/]", values_match.group(1))
                    if item.strip()
                ]
                if not values or len(values) % len(columns) != 0:
                    return None
                rows = [
                    values[index : index + len(columns)]
                    for index in range(0, len(values), len(columns))
                ]
            stem = "项目任务清单" if "项目任务清单" in prompt else "新建表格"
            return ArtifactPlan(
                format="xlsx",
                filename=f"{stem}.xlsx",
                table=XlsxTablePlan(
                    sheet_name="项目",
                    columns=columns,
                    rows=rows,
                ),
            )

        content_match = _DOCX_CONTENT.search(prompt)
        if content_match is None or not content_match.group(1).strip():
            return None
        return ArtifactPlan(
            format="docx",
            filename="新建文档.docx",
            paragraphs=[
                DocxParagraphPlan(text=content_match.group(1).strip())
            ],
        )

    def _generate_plan(
        self,
        plan: ArtifactPlan,
    ) -> tuple[dict[str, Any], str, str]:
        if plan.format == "xlsx":
            table = plan.table.model_dump(mode="python")
            artifact = self.generator.generate_xlsx(plan.filename, table)
            return (
                artifact,
                f"{artifact['rows']} 行、{artifact['columns']} 列",
                "Excel",
            )
        paragraphs = [
            item.model_dump(mode="python") for item in plan.paragraphs
        ]
        artifact = self.generator.generate_docx(plan.filename, paragraphs)
        return artifact, f"{artifact['paragraphs']} 段", "Word"

    def process(self, prompt: str) -> ArtifactCoordinationResult:
        """Detect, plan, validate, and generate one new artifact."""
        if not isinstance(prompt, str) or not prompt.strip():
            return ArtifactCoordinationResult(
                matched=False,
                rejected=False,
                message="未检测到明确的文件生成请求。",
                error_code="not_matched",
            )
        prompt = prompt.strip()
        if len(prompt) > MAX_PROMPT_CHARS:
            return ArtifactCoordinationResult(
                matched=True,
                rejected=True,
                message="请求内容过长，无法安全生成文件。",
                error_code="prompt_too_large",
            )

        expected_format, detection_error = self._detect_format(prompt)
        if detection_error == "ambiguous_format":
            return ArtifactCoordinationResult(
                matched=True,
                rejected=True,
                message="请一次只选择 Excel 或 Word 一种文件格式。",
                error_code=detection_error,
            )
        if expected_format is None:
            return ArtifactCoordinationResult(
                matched=False,
                rejected=False,
                message="未检测到明确的 Excel 或 Word 生成请求。",
                error_code="not_matched",
            )
        if self._is_sensitive_request(prompt):
            return ArtifactCoordinationResult(
                matched=True,
                rejected=True,
                requested_format=expected_format,
                message=(
                    "当前只支持生成新的版本化文件，不能删除、覆盖或修改现有文件。"
                ),
                error_code="destructive_request",
            )
        if not _SAFE_ACTION.search(prompt):
            return ArtifactCoordinationResult(
                matched=False,
                rejected=False,
                requested_format=expected_format,
                message="请明确说明需要生成、创建或导出文件。",
                error_code="not_matched",
            )

        explicit_plan = self._fallback_plan(prompt, expected_format)
        if explicit_plan is not None:
            try:
                artifact, description, format_name = self._generate_plan(
                    explicit_plan
                )
                return ArtifactCoordinationResult(
                    matched=True,
                    rejected=False,
                    requested_format=expected_format,
                    message=(
                        "已按消息中的明确字段生成"
                        f" {format_name} 文件：{artifact['name']}"
                        f"（{description}）。"
                    ),
                    artifact=artifact,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                return ArtifactCoordinationResult(
                    matched=True,
                    rejected=False,
                    requested_format=expected_format,
                    message="明确字段有效，但文件生成失败；未覆盖任何已有文件。",
                    error_code="generation_failed",
                )

        try:
            raw_plan = self.llm.chat(
                prompt,
                system_prompt=self._system_prompt(expected_format),
            )
        except Exception:
            fallback_plan = self._fallback_plan(prompt, expected_format)
            if fallback_plan is not None:
                try:
                    artifact, description, format_name = self._generate_plan(
                        fallback_plan
                    )
                    return ArtifactCoordinationResult(
                        matched=True,
                        rejected=False,
                        requested_format=expected_format,
                        message=(
                            "模型服务暂时不可用，已按消息中的明确字段生成基础"
                            f" {format_name} 文件：{artifact['name']}"
                            f"（{description}）。"
                        ),
                        artifact=artifact,
                        error_code="llm_fallback",
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    pass
            return ArtifactCoordinationResult(
                matched=True,
                rejected=False,
                requested_format=expected_format,
                message="未能生成文件：模型服务暂时不可用。",
                error_code="llm_error",
            )

        try:
            plan = ArtifactPlan.model_validate(self._json_payload(raw_plan))
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
            return ArtifactCoordinationResult(
                matched=True,
                rejected=False,
                requested_format=expected_format,
                message="未能生成文件：模型返回的结构化计划无效。",
                error_code="invalid_plan",
            )

        if plan.format != expected_format:
            return ArtifactCoordinationResult(
                matched=True,
                rejected=False,
                requested_format=expected_format,
                message="未能生成文件：计划格式与请求格式不一致。",
                error_code="format_mismatch",
            )

        try:
            artifact, description, format_name = self._generate_plan(plan)
        except (OSError, RuntimeError, TypeError, ValueError):
            return ArtifactCoordinationResult(
                matched=True,
                rejected=False,
                requested_format=expected_format,
                message="文件计划有效，但生成失败；未覆盖任何已有文件。",
                error_code="generation_failed",
            )

        return ArtifactCoordinationResult(
            matched=True,
            rejected=False,
            requested_format=expected_format,
            message=f"已生成 {format_name} 文件：{artifact['name']}（{description}）。",
            artifact=artifact,
        )


__all__ = [
    "ArtifactCoordinationResult",
    "ArtifactCoordinator",
    "ArtifactPlan",
    "DocxParagraphPlan",
    "XlsxTablePlan",
]
