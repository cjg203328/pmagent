import json
from pathlib import Path
import sys

import pytest


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from artifacts import ArtifactCoordinator, WorkspaceArtifactGenerator


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def xlsx_plan(**updates):
    plan = {
        "format": "xlsx",
        "filename": "项目数据.xlsx",
        "table": {
            "sheet_name": "项目",
            "columns": ["名称", "金额"],
            "rows": [["角色", 1000], ["场景", 2000]],
        },
    }
    plan.update(updates)
    return plan


def docx_plan(**updates):
    plan = {
        "format": "docx",
        "filename": "项目总结.docx",
        "paragraphs": [
            {"text": "项目总结", "kind": "heading", "level": 1},
            {"text": "按计划交付。", "kind": "paragraph", "bold": True},
        ],
    }
    plan.update(updates)
    return plan


def make_coordinator(tmp_path, response, **generator_options):
    llm = FakeLLM(response)
    generator = WorkspaceArtifactGenerator(
        tmp_path / "workspace" / "artifacts",
        **generator_options,
    )
    return ArtifactCoordinator(generator, llm), llm, generator


def test_fenced_json_xlsx_plan_generates_artifact_and_user_message(tmp_path):
    response = f"```json\n{json.dumps(xlsx_plan(), ensure_ascii=False)}\n```"
    coordinator, llm, generator = make_coordinator(tmp_path, response)

    result = coordinator.process("请生成一个 Excel 表格，整理项目金额")

    assert result.matched is True
    assert result.rejected is False
    assert result.error_code is None
    assert result.requested_format == "xlsx"
    assert result.artifact["format"] == "xlsx"
    assert result.artifact["rows"] == 2
    assert Path(result.artifact["path"]).parent == generator.root
    assert "已生成 Excel 文件" in result.message
    assert "2 行、2 列" in result.message
    assert len(llm.calls) == 1
    assert "只输出 JSON" in llm.calls[0]["system_prompt"]
    assert "最多 100 列、10000 行" in llm.calls[0]["system_prompt"]


def test_raw_json_docx_plan_generates_artifact(tmp_path):
    coordinator, llm, _ = make_coordinator(
        tmp_path,
        json.dumps(docx_plan(), ensure_ascii=False),
    )

    result = coordinator.process("创建一份 Word 文档作为项目总结")

    assert result.matched is True
    assert result.rejected is False
    assert result.requested_format == "docx"
    assert result.artifact["format"] == "docx"
    assert result.artifact["paragraphs"] == 2
    assert "已生成 Word 文件" in result.message
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "prompt",
    [
        "分析这个 Excel 的内容",
        "Word 文档通常有哪些格式？",
        "帮我总结项目进展",
        "你好",
    ],
)
def test_non_generation_requests_do_not_call_llm(tmp_path, prompt):
    coordinator, llm, generator = make_coordinator(
        tmp_path,
        json.dumps(xlsx_plan()),
    )

    result = coordinator.process(prompt)

    assert result.matched is False
    assert result.artifact is None
    assert result.error_code == "not_matched"
    assert llm.calls == []
    assert not list(generator.root.iterdir())


@pytest.mark.parametrize(
    "prompt",
    [
        "删除现有 Excel 文件",
        "覆盖这个 XLSX 表格",
        "修改现有 Word 文档",
        "把 report.docx 删除",
        "更新该表格",
        "编辑 current Word document",
    ],
)
def test_destructive_existing_file_requests_are_rejected_before_llm(
    tmp_path,
    prompt,
):
    coordinator, llm, generator = make_coordinator(
        tmp_path,
        json.dumps(xlsx_plan()),
    )

    result = coordinator.process(prompt)

    assert result.matched is True
    assert result.rejected is True
    assert result.error_code == "destructive_request"
    assert "不能删除、覆盖或修改现有文件" in result.message
    assert result.artifact is None
    assert llm.calls == []
    assert not list(generator.root.iterdir())


def test_negated_overwrite_constraint_does_not_reject_new_generation(tmp_path):
    coordinator, llm, _ = make_coordinator(
        tmp_path,
        json.dumps(xlsx_plan(), ensure_ascii=False),
    )

    result = coordinator.process("生成新的 Excel 表格，不要覆盖现有文件")

    assert result.artifact["format"] == "xlsx"
    assert result.rejected is False
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "```json\n{bad}\n```",
        '{"format":"xlsx"} trailing prose',
        "[]",
        "",
        (
            '{"format":"xlsx","format":"docx","filename":"x.xlsx",'
            '"table":{"columns":["a"],"rows":[]}}'
        ),
        (
            '{"format":"xlsx","filename":"x.xlsx",'
            '"table":{"columns":["a"],"rows":[[NaN]]}}'
        ),
    ],
)
def test_invalid_json_or_shape_returns_error_without_file(tmp_path, response):
    coordinator, llm, generator = make_coordinator(tmp_path, response)

    result = coordinator.process("导出 Excel 表格")

    assert result.matched is True
    assert result.rejected is False
    assert result.error_code == "invalid_plan"
    assert result.artifact is None
    assert len(llm.calls) == 1
    assert not list(generator.root.iterdir())


def test_llm_failure_returns_chat_ready_error_without_file(tmp_path):
    coordinator, llm, generator = make_coordinator(
        tmp_path,
        RuntimeError("provider unavailable"),
    )

    result = coordinator.process("创建 Excel 表格")

    assert result.error_code == "llm_error"
    assert "模型服务暂时不可用" in result.message
    assert result.artifact is None
    assert len(llm.calls) == 1
    assert not list(generator.root.iterdir())


def test_explicit_xlsx_columns_and_values_do_not_need_the_llm(tmp_path):
    coordinator, llm, _ = make_coordinator(
        tmp_path,
        RuntimeError("provider unavailable"),
    )

    result = coordinator.process(
        "生成一个 Excel 项目任务清单，列为任务、负责人、状态，"
        "包含角色建模、小李、进行中"
    )

    assert result.error_code is None
    assert result.artifact["format"] == "xlsx"
    assert result.artifact["rows"] == 1
    assert result.artifact["columns"] == 3
    assert "明确字段" in result.message
    assert len(llm.calls) == 0


def test_explicit_docx_body_skips_llm_but_empty_request_needs_it(tmp_path):
    coordinator, _, generator = make_coordinator(
        tmp_path,
        RuntimeError("provider unavailable"),
    )

    generated = coordinator.process("创建 Word 文档，正文为项目已按计划交付。")
    missing = coordinator.process("创建 Word 文档")

    assert generated.error_code is None
    assert generated.artifact["format"] == "docx"
    assert generated.artifact["paragraphs"] == 1
    assert missing.error_code == "llm_error"
    assert missing.artifact is None
    assert len(list(generator.root.glob("*.docx"))) == 1


def test_plan_format_must_match_deterministically_detected_request(tmp_path):
    coordinator, llm, generator = make_coordinator(
        tmp_path,
        json.dumps(docx_plan(), ensure_ascii=False),
    )

    result = coordinator.process("生成 Excel 项目表格")

    assert result.error_code == "format_mismatch"
    assert result.requested_format == "xlsx"
    assert result.artifact is None
    assert len(llm.calls) == 1
    assert not list(generator.root.iterdir())


@pytest.mark.parametrize(
    "plan",
    [
        xlsx_plan(
            table={
                "columns": [f"c{index}" for index in range(101)],
                "rows": [],
            }
        ),
        xlsx_plan(
            table={
                "columns": ["value"],
                "rows": [[index] for index in range(10_001)],
            }
        ),
        docx_plan(
            paragraphs=[{"text": "x"} for _ in range(2_001)],
        ),
    ],
)
def test_pydantic_plan_limits_reject_oversized_content(tmp_path, plan):
    coordinator, llm, generator = make_coordinator(
        tmp_path,
        json.dumps(plan, ensure_ascii=False),
    )
    prompt = (
        "创建 Word 文档"
        if plan["format"] == "docx"
        else "创建 Excel 表格"
    )

    result = coordinator.process(prompt)

    assert result.error_code == "invalid_plan"
    assert result.artifact is None
    assert len(llm.calls) == 1
    assert not list(generator.root.iterdir())


@pytest.mark.parametrize(
    "plan",
    [
        {**xlsx_plan(), "unexpected": True},
        xlsx_plan(filename="../escape.xlsx"),
        xlsx_plan(
            table={
                "columns": ["a", "b"],
                "rows": [[1]],
            }
        ),
    ],
)
def test_extra_fields_paths_and_row_width_are_invalid_plans(tmp_path, plan):
    coordinator, _, generator = make_coordinator(
        tmp_path,
        json.dumps(plan, ensure_ascii=False),
    )

    result = coordinator.process("生成 XLSX 表格")

    assert result.error_code == "invalid_plan"
    assert result.artifact is None
    assert not list(generator.root.iterdir())


def test_valid_plan_generation_failure_is_reported_without_overwrite(tmp_path):
    coordinator, _, generator = make_coordinator(
        tmp_path,
        json.dumps(xlsx_plan(), ensure_ascii=False),
        max_file_size=100,
    )

    result = coordinator.process("生成 Excel 表格")

    assert result.error_code == "generation_failed"
    assert "未覆盖任何已有文件" in result.message
    assert result.artifact is None
    assert not list(generator.root.iterdir())


def test_ambiguous_excel_and_word_request_is_rejected_without_llm(tmp_path):
    coordinator, llm, generator = make_coordinator(
        tmp_path,
        json.dumps(xlsx_plan()),
    )

    result = coordinator.process("同时生成 Excel 表格和 Word 文档")

    assert result.matched is True
    assert result.rejected is True
    assert result.error_code == "ambiguous_format"
    assert llm.calls == []
    assert not list(generator.root.iterdir())


def test_result_to_dict_copies_artifact_metadata(tmp_path):
    coordinator, _, _ = make_coordinator(
        tmp_path,
        json.dumps(docx_plan(), ensure_ascii=False),
    )
    result = coordinator.process("导出 DOCX 文档")

    serialized = result.to_dict()
    serialized["artifact"]["name"] = "changed"

    assert result.artifact["name"] == "项目总结.docx"
    assert serialized["requested_format"] == "docx"


def test_coordinator_requires_generator_and_llm_chat(tmp_path):
    generator = WorkspaceArtifactGenerator(tmp_path / "artifacts")

    with pytest.raises(TypeError):
        ArtifactCoordinator(object(), FakeLLM("{}"))
    with pytest.raises(TypeError):
        ArtifactCoordinator(generator, object())
