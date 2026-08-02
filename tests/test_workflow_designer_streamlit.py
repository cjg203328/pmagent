from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

import artpm_agent.views.workflow_designer as workflow_designer
from artpm_agent.workflows.risk_policy import DEFAULT_SKILL_CAPABILITIES


APP_FILE = str(Path(__file__).resolve().parents[1] / "artpm_agent" / "app.py")


def test_visual_workflow_editor_saves_runnable_custom_definition():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.pills(key="sidebar_nav_pills").set_value("设置").run(timeout=30)

    assert not app.exception
    app.text_input(key="workflow_designer_id").set_value("visual_test_flow")
    app.text_input(key="workflow_designer_name").set_value("可视化测试流程")
    app.text_area(key="workflow_designer_description").set_value(
        "由可视化编排器保存并通过服务端校验。"
    )
    app.text_input(key="workflow_designer_keywords").set_value("可视化测试")
    app.button(key="save_visual_workflow").click().run(timeout=30)

    assert not app.exception
    definition = app.session_state["workflow_store"].get_definition(
        "visual_test_flow"
    )
    assert definition is not None
    assert definition.source == "custom"
    assert definition.version == 1
    step = definition.steps[0]
    assert step.capability in DEFAULT_SKILL_CAPABILITIES[step.skill_id]


def test_visual_workflow_canvas_is_not_hidden_with_marker(monkeypatch):
    rendered: list[str] = []

    monkeypatch.setattr(
        workflow_designer.st,
        "markdown",
        lambda body, **_kwargs: rendered.append(body),
    )
    option = next(
        option
        for option in workflow_designer.list_capability_options()
        if option.skill_id == "data_analyzer"
    )

    workflow_designer._render_canvas(
        [
            {
                "id": "step_1",
                "skill_id": option.skill_id,
                "capability": option.capability,
            }
        ],
        {(option.skill_id, option.capability): option},
    )

    assert len(rendered) == 2
    assert "workflow-designer-marker" in rendered[0]
    assert "pm-workflow-canvas" in rendered[1]
