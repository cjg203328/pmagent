from types import SimpleNamespace

import requests

from artpm_agent import ui_feedback


class _Block:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeStreamlit:
    def __init__(self, *, clicked=None):
        self.session_state = {}
        self.clicked = clicked
        self.messages = []
        self.buttons = []

    def container(self, **_kwargs):
        return _Block()

    def expander(self, *_args, **_kwargs):
        return _Block()

    def columns(self, count, **_kwargs):
        return [_Block() for _ in range(count)]

    def button(self, label, *, key, **_kwargs):
        self.buttons.append((label, key))
        return key == self.clicked

    def markdown(self, value, **_kwargs):
        self.messages.append(value)

    def caption(self, value):
        self.messages.append(value)

    def error(self, value):
        self.messages.append(value)

    def warning(self, value):
        self.messages.append(value)

    def info(self, value):
        self.messages.append(value)

    def success(self, value):
        self.messages.append(value)


def test_build_error_info_replaces_template_error_id_and_hides_internals():
    info = ui_feedback.build_error_info(RuntimeError("internal stack secret"))

    assert info["error_id"] != "unknown"
    assert all("{error_id}" not in item for item in info["suggestions"])
    assert "internal stack secret" not in str(info)
    assert "original_error" not in info


def test_error_callback_returns_retry_action_without_rendering_raw_exception(monkeypatch):
    fake = _FakeStreamlit(clicked="callback_retry")
    monkeypatch.setattr(ui_feedback, "st", fake)

    action = ui_feedback.render_error_callback(
        {
            "message": "模型暂时不可用",
            "suggestions": ["稍后重试"],
            "severity": "error",
            "error_id": "abc123",
            "original_error": "do not show this",
        },
        key="callback",
        retry=True,
    )

    assert action == "retry"
    assert "do not show this" not in " ".join(map(str, fake.messages))


def test_error_callback_dismissal_is_persisted(monkeypatch):
    fake = _FakeStreamlit(clicked="callback_dismiss")
    monkeypatch.setattr(ui_feedback, "st", fake)

    action = ui_feedback.render_error_callback(
        {
            "message": "请求失败",
            "suggestions": ["稍后重试"],
            "severity": "warning",
            "error_id": "abc123",
        },
        key="callback",
    )

    assert action == "dismiss"
    assert fake.session_state["callback_dismissed"] is True


def test_new_error_occurrence_is_visible_after_previous_one_was_closed(monkeypatch):
    fake = _FakeStreamlit(clicked="callback_dismiss")
    monkeypatch.setattr(ui_feedback, "st", fake)
    ui_feedback.render_error_callback(
        {"message": "第一次失败", "error_id": "first"},
        key="callback",
    )

    fake.clicked = None
    fake.messages.clear()
    ui_feedback.render_error_callback(
        {"message": "第二次失败", "error_id": "second"},
        key="callback",
    )

    assert "第二次失败" in fake.messages
    assert fake.session_state.get("callback_dismissed") is not True


def test_action_callback_uses_clear_result_copy_and_reference(monkeypatch):
    fake = _FakeStreamlit(clicked="result_dismiss")
    monkeypatch.setattr(ui_feedback, "st", fake)

    action = ui_feedback.render_action_callback(
        {
            "title": "已拒绝操作",
            "message": "没有执行该请求。",
            "severity": "success",
            "reference": "abc123",
        },
        key="result",
    )

    assert action == "dismiss"
    assert any("已拒绝操作" in item for item in fake.messages)
    assert "没有执行该请求。" in fake.messages
    assert "参考编号：abc123" in fake.messages
    assert ("知道了", "result_dismiss") in fake.buttons


def test_attachment_errors_are_friendly_and_have_no_unresolved_templates():
    cases = [
        ValueError("Unsupported attachment extension: .exe"),
        ValueError("A message can contain at most 3 files"),
        ValueError("Attachment exceeds 52428800 bytes: huge.pdf"),
        ValueError("Attachment batch exceeds 104857600 bytes"),
        ValueError("Attachment is empty: blank.txt"),
    ]

    for error in cases:
        info = ui_feedback.build_attachment_error_info(error)
        user_copy = [info["message"], *info["suggestions"]]
        assert all("{" not in item for item in user_copy)
        rendered = " ".join(user_copy)
        assert str(error) not in rendered
        assert info["error_id"].startswith("upload-")


def test_backend_probe_distinguishes_required_link_failure(monkeypatch):
    monkeypatch.setenv("ARTPM_API_REQUIRED", "true")

    def get(_url, *, timeout):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", get)

    result = ui_feedback.probe_backend_health(base_url="http://api.test")

    assert result["status"] == "error"
    assert result["server"] == "not_running"


def test_runtime_init_failures_are_exposed_without_internal_details(monkeypatch):
    from artpm_agent import ui_helpers

    rendered = []
    monkeypatch.setattr(
        ui_helpers,
        "st",
        SimpleNamespace(
            session_state={
                "runtime_init_errors": {
                    "conversation_store": "会话记录",
                    "permission_store": "审批记录",
                }
            }
        ),
    )
    monkeypatch.setattr(
        ui_helpers,
        "render_error_callback",
        lambda info, **options: rendered.append((info, options)),
    )

    ui_helpers.render_runtime_init_status()

    info, options = rendered[0]
    assert info["severity"] == "error"
    assert "会话记录、审批记录" in info["message"]
    assert any(
        "当前会话可能无法持久保存" in suggestion
        for suggestion in info["suggestions"]
    )
    assert options["key"] == "runtime_init_status"
