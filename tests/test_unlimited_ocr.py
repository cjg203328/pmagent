from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import Request

from PIL import Image
import pytest


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from utils.unlimited_ocr import (
    UnlimitedOCRClient,
    UnlimitedOCRConfig,
    UnlimitedOCRConfigurationError,
    normalize_unlimited_ocr_base_url,
    validate_remote_image_url,
)


class FakeResponse:
    def __init__(self, body: bytes = b"", lines: list[bytes] | None = None):
        self.body = body
        self.lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]

    def __iter__(self):
        return iter(self.lines)


class FakeOpener:
    def __init__(self, responses: list[FakeResponse | Exception]):
        self.responses = list(responses)
        self.calls: list[tuple[Request, float]] = []

    def open(self, request: Request, timeout: float):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _png(path: Path, color: str = "red") -> Path:
    Image.new("RGB", (12, 8), color).save(path, "PNG")
    return path


def _completion(text: str) -> FakeResponse:
    return FakeResponse(
        json.dumps({"choices": [{"message": {"content": text}}]}).encode()
    )


def _config(**overrides) -> UnlimitedOCRConfig:
    values = {
        "enabled": True,
        "base_url": "http://127.0.0.1:10000",
        "model": "Unlimited-OCR",
        "timeout": 30,
        "max_output": 1000,
        "allow_remote": False,
    }
    values.update(overrides)
    return UnlimitedOCRConfig.from_mapping(values)


def test_config_supports_nested_mapping_and_environment_names():
    nested = UnlimitedOCRConfig.from_mapping(
        {
            "unlimited_ocr": {
                "enabled": "true",
                "base_url": "http://127.0.0.1:10000/v1",
                "model": "custom-ocr",
                "timeout": "9999",
                "max_output": "0",
                "allow_remote": "yes",
                "api_key": "optional-secret",
            }
        }
    )
    assert nested.enabled is True
    assert nested.timeout == 1200
    assert nested.max_output == 1
    assert nested.allow_remote is True
    assert nested.api_key == "optional-secret"

    from_env = UnlimitedOCRConfig.from_env(
        {
            "UNLIMITED_OCR_ENABLED": "1",
            "UNLIMITED_OCR_BASE_URL": "http://localhost:10000",
            "UNLIMITED_OCR_MODEL": "env-ocr",
            "UNLIMITED_OCR_TIMEOUT": "90",
            "UNLIMITED_OCR_MAX_OUTPUT": "4096",
            "UNLIMITED_OCR_ALLOW_REMOTE": "false",
        }
    )
    assert from_env == UnlimitedOCRConfig(
        enabled=True,
        base_url="http://localhost:10000",
        model="env-ocr",
        timeout=90,
        max_output=4096,
        allow_remote=False,
        api_key="",
    )


def test_base_url_normalization_accepts_local_service_and_rejects_unsafe_shapes():
    assert (
        normalize_unlimited_ocr_base_url("http://127.0.0.1:10000/v1/")
        == "http://127.0.0.1:10000/v1"
    )
    assert (
        normalize_unlimited_ocr_base_url("https://ocr.example.com/root")
        == "https://ocr.example.com/root"
    )
    with pytest.raises(UnlimitedOCRConfigurationError, match="complete"):
        normalize_unlimited_ocr_base_url("127.0.0.1:10000")
    with pytest.raises(UnlimitedOCRConfigurationError, match="credentials"):
        normalize_unlimited_ocr_base_url("https://user:pass@ocr.example.com")
    with pytest.raises(UnlimitedOCRConfigurationError, match="query"):
        normalize_unlimited_ocr_base_url("https://ocr.example.com?token=secret")
    with pytest.raises(UnlimitedOCRConfigurationError, match="unsafe"):
        normalize_unlimited_ocr_base_url("http://169.254.169.254/latest")


def test_local_image_uses_shared_validation_and_exact_openai_payload(tmp_path):
    image = _png(tmp_path / "private-name.png")
    opener = FakeOpener([_completion("# parsed document")])
    client = UnlimitedOCRClient(_config(), opener=opener)

    result = client.parse([image])

    assert result.ok is True
    assert result.text == "# parsed document"
    request, timeout = opener.calls[0]
    assert request.full_url == "http://127.0.0.1:10000/v1/chat/completions"
    assert timeout == 30
    assert request.get_header("Authorization") is None
    payload = json.loads(request.data)
    assert payload["model"] == "Unlimited-OCR"
    assert payload["temperature"] == 0
    # max_output is a local response-character cap. Sending it as max_tokens
    # would exceed the 32K model context once image input tokens are included.
    assert "max_tokens" not in payload
    assert payload["skip_special_tokens"] is False
    assert payload["images_config"] == {"image_mode": "gundam"}
    assert payload["custom_params"] == {"ngram_size": 35, "window_size": 128}
    assert "custom_logit_processor" not in payload
    assert payload["stream"] is False
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "document parsing."}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    serialized = json.dumps(payload)
    assert str(tmp_path) not in serialized
    assert image.name not in serialized


def test_multi_image_requires_base_mode_and_uses_multi_page_prompt(tmp_path):
    paths = [_png(tmp_path / f"page-{index}.png") for index in range(2)]
    client = UnlimitedOCRClient(_config(), opener=FakeOpener([]))
    blocked = client.parse(paths, image_mode="gundam")
    assert blocked.degraded is True
    assert "Multiple images require base mode" in blocked.error

    opener = FakeOpener([_completion("pages")])
    client = UnlimitedOCRClient(_config(), opener=opener)
    result = client.parse(paths, image_mode="base")
    assert result.ok is True
    payload = json.loads(opener.calls[0][0].data)
    assert payload["images_config"] == {"image_mode": "base"}
    assert payload["custom_params"] == {"ngram_size": 35, "window_size": 1024}
    assert payload["messages"][0]["content"][0]["text"] == "Multi page parsing."
    assert len(payload["messages"][0]["content"]) == 3


def test_trusted_custom_processor_is_validated_and_forwarded(tmp_path):
    image = _png(tmp_path / "processor.png")
    processor = json.dumps({"callable": "00ff"})
    opener = FakeOpener([_completion("parsed")])
    result = UnlimitedOCRClient(
        _config(custom_logit_processor=processor), opener=opener
    ).parse([image])
    assert result.ok is True
    payload = json.loads(opener.calls[0][0].data)
    assert payload["custom_logit_processor"] == processor

    invalid = UnlimitedOCRClient(
        _config(custom_logit_processor='{"callable":"not-hex"}'),
        opener=FakeOpener([]),
    ).parse([image])
    assert invalid.degraded is True
    assert "hex string" in (invalid.error or "")


def test_remote_images_are_opt_in_and_ssrf_checked():
    disabled = UnlimitedOCRClient(_config(), opener=FakeOpener([]))
    result = disabled.parse(["https://93.184.216.34/document.png"])
    assert result.degraded is True
    assert result.error == "Remote images are disabled"

    enabled = UnlimitedOCRClient(
        _config(allow_remote=True),
        opener=FakeOpener([]),
        remote_resolver=lambda *_: [],
    )
    for url in (
        "http://127.0.0.1/private.png",
        "http://169.254.169.254/latest.png",
        "http://localhost/private.png",
        "http://service.local/private.png",
    ):
        result = enabled.parse([url])
        assert result.degraded is True
        assert "public" in (result.error or "")


def test_public_remote_image_can_be_sent_when_explicitly_enabled():
    def resolver(*_):
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    url = "https://images.example.com/doc.png?version=1"
    assert validate_remote_image_url(url, resolver=resolver) == url
    opener = FakeOpener([_completion("remote result")])
    client = UnlimitedOCRClient(
        _config(allow_remote=True),
        opener=opener,
        remote_resolver=resolver,
    )
    assert client.parse([url]).text == "remote result"
    payload = json.loads(opener.calls[0][0].data)
    assert payload["messages"][0]["content"][1]["image_url"]["url"] == url


def test_health_check_is_bounded_and_api_key_is_optional():
    disabled_opener = FakeOpener([])
    health = UnlimitedOCRClient(
        UnlimitedOCRConfig(enabled=False), opener=disabled_opener
    ).health()
    assert health.configured is False
    assert health.available is False
    assert disabled_opener.calls == []

    health_body = FakeResponse(b"ok")
    models_body = FakeResponse(
        json.dumps({"data": [{"id": "Unlimited-OCR"}]}).encode()
    )
    opener = FakeOpener([health_body, models_body])
    client = UnlimitedOCRClient(_config(api_key="secret"), opener=opener)
    health = client.health()
    assert health.configured is True
    assert health.available is True
    assert health.model_available is True
    request, timeout = opener.calls[0]
    assert request.full_url.endswith("/health")
    assert request.get_header("Authorization") == "Bearer secret"
    assert timeout == 5
    assert opener.calls[1][0].full_url.endswith("/v1/models")

    unavailable = UnlimitedOCRClient(
        _config(), opener=FakeOpener([URLError("offline")])
    ).health()
    assert unavailable.available is False
    assert unavailable.detail == "service unavailable"

    missing_model = UnlimitedOCRClient(
        _config(),
        opener=FakeOpener(
            [
                FakeResponse(b"ok"),
                FakeResponse(json.dumps({"data": []}).encode()),
            ]
        ),
    ).health()
    assert missing_model.available is True
    assert missing_model.model_available is False


def test_ready_uses_a_fast_cached_liveness_probe():
    opener = FakeOpener([FakeResponse(b"ok")])
    client = UnlimitedOCRClient(_config(timeout=30), opener=opener)

    assert client.ready() is True
    assert client.ready() is True
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert request.full_url.endswith("/health")
    assert timeout == 2

    offline_opener = FakeOpener([URLError("offline")])
    offline = UnlimitedOCRClient(_config(), opener=offline_opener)
    assert offline.ready() is False
    assert offline.ready() is False
    assert len(offline_opener.calls) == 1


def test_streaming_sse_parsing_and_output_limit(tmp_path):
    image = _png(tmp_path / "stream.png")
    lines = [
        b": keepalive\n",
        b'data: {"choices":[{"delta":{"content":"abc"}}]}\n',
        b'data:{"choices":[{"delta":{"content":"def"}}]}\n',
        b"data: [DONE]\n",
    ]
    opener = FakeOpener([FakeResponse(lines=lines)])
    deltas: list[str] = []
    client = UnlimitedOCRClient(_config(max_output=5), opener=opener)

    result = client.parse([image], stream=True, on_delta=deltas.append)

    assert result.ok is True
    assert result.text == "abcde"
    assert result.truncated is True
    assert deltas == ["abc", "de"]
    request = opener.calls[0][0]
    assert json.loads(request.data)["stream"] is True
    assert request.get_header("Accept") == "text/event-stream"


def test_non_stream_content_is_capped_and_malformed_response_degrades(tmp_path):
    image = _png(tmp_path / "cap.png")
    capped = UnlimitedOCRClient(
        _config(max_output=4), opener=FakeOpener([_completion("abcdef")])
    ).parse([image])
    assert capped.text == "abcd"
    assert capped.truncated is True

    malformed = UnlimitedOCRClient(
        _config(), opener=FakeOpener([FakeResponse(b"not-json")])
    ).parse([image])
    assert malformed.ok is False
    assert malformed.degraded is True
    assert malformed.error == "service returned an invalid response"


def test_unconfigured_or_failed_service_can_fall_back_without_throwing(tmp_path):
    image = _png(tmp_path / "fallback.png")
    fallback_calls = []

    def fallback(prompt, paths):
        fallback_calls.append((prompt, paths))
        return "fallback OCR"

    disabled = UnlimitedOCRClient(UnlimitedOCRConfig(enabled=False))
    result = disabled.parse([image], fallback=fallback)
    assert result.ok is True
    assert result.degraded is True
    assert result.source == "fallback"
    assert result.text == "fallback OCR"
    assert result.error == "Unlimited-OCR is disabled"
    assert fallback_calls == [("document parsing.", [str(image)])]

    failed = UnlimitedOCRClient(
        _config(), opener=FakeOpener([URLError("secret internal detail")])
    ).parse([image])
    assert failed.degraded is True
    assert failed.error == "service unavailable"
    assert "secret" not in failed.error

    multi_calls = []
    disabled.parse(
        [image, image],
        image_mode="base",
        fallback=lambda prompt, paths: multi_calls.append((prompt, paths)) or "multi",
    )
    assert multi_calls[0][0] == "Multi page parsing."


def test_adapter_has_no_local_model_runtime_dependency():
    source = (APP_ROOT / "utils" / "unlimited_ocr.py").read_text(encoding="utf-8")
    imported_roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "torch" not in imported_roots
    assert "sglang" not in imported_roots
