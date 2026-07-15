from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"

from artpm_agent.utils.image_validation import MAX_IMAGE_FILE_SIZE
from artpm_agent.utils.llm_client import AnthropicClient, BaseLLMClient, OpenAIClient


def _openai_client() -> OpenAIClient:
    client = object.__new__(OpenAIClient)
    BaseLLMClient.__init__(
        client,
        {
            "model": "vision-model",
            "temperature": 0,
            "max_tokens": 200,
            "retry_max_attempts": 1,
        },
    )
    client.client = Mock()
    client.client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="openai answer"))]
    )
    return client


def _anthropic_client() -> AnthropicClient:
    client = object.__new__(AnthropicClient)
    BaseLLMClient.__init__(
        client,
        {
            "model": "vision-model",
            "temperature": 0,
            "max_tokens": 200,
            "retry_max_attempts": 1,
        },
    )
    client.client = Mock()
    client.client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="anthropic answer")]
    )
    return client


def _png(path: Path, color: str) -> Path:
    Image.new("RGB", (10, 10), color).save(path, "PNG")
    return path


def test_load_image_validates_path_size_format_and_content(tmp_path):
    valid = _png(tmp_path / "valid.png", "red")
    media_type, encoded = OpenAIClient._load_image(str(valid))
    assert media_type == "image/png"
    assert base64.b64decode(encoded) == valid.read_bytes()

    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(FileNotFoundError):
        OpenAIClient._load_image(str(tmp_path / "missing.png"))
    with pytest.raises(FileNotFoundError):
        OpenAIClient._load_image(str(directory))

    gif_path = tmp_path / "unsupported.gif"
    Image.new("RGB", (5, 5), "blue").save(gif_path, "GIF")
    with pytest.raises(ValueError, match="不支持的图片格式"):
        OpenAIClient._load_image(str(gif_path))

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="无法识别"):
        OpenAIClient._load_image(str(corrupt))

    disguised = tmp_path / "disguised.png"
    Image.new("RGB", (5, 5), "green").save(disguised, "JPEG")
    with pytest.raises(ValueError, match="与扩展名"):
        OpenAIClient._load_image(str(disguised))

    oversized = tmp_path / "oversized.png"
    with oversized.open("wb") as stream:
        stream.seek(MAX_IMAGE_FILE_SIZE)
        stream.write(b"x")
    with pytest.raises(ValueError, match="15 MB"):
        OpenAIClient._load_image(str(oversized))


def test_openai_image_payload_embeds_at_most_three_and_no_local_paths(tmp_path):
    paths = [
        _png(tmp_path / f"private-{index}.png", color)
        for index, color in enumerate(("red", "green", "blue"), start=1)
    ]
    ignored_path = tmp_path / "private-ignored.png"
    client = _openai_client()

    response = client.chat_with_images(
        "比较这些图片",
        [*(str(path) for path in paths), str(ignored_path)],
        system_prompt="system",
        history=[{"role": "user", "content": "上一轮"}],
    )

    assert response == "openai answer"
    kwargs = client.client.chat.completions.create.call_args.kwargs
    user_content = kwargs["messages"][-1]["content"]
    image_blocks = [item for item in user_content if item["type"] == "image_url"]
    assert len(image_blocks) == 3
    assert all(
        item["image_url"]["url"].startswith("data:image/png;base64,")
        for item in image_blocks
    )
    serialized = json.dumps(kwargs, ensure_ascii=False, default=str)
    assert str(tmp_path) not in serialized
    assert all(path.name not in serialized for path in paths)
    assert ignored_path.name not in serialized


def test_anthropic_image_payload_embeds_at_most_three_and_no_local_paths(tmp_path):
    paths = [
        _png(tmp_path / f"private-{index}.png", color)
        for index, color in enumerate(("red", "green", "blue"), start=1)
    ]
    ignored_path = tmp_path / "private-ignored.png"
    client = _anthropic_client()

    response = client.chat_with_images(
        "比较这些图片",
        [*(str(path) for path in paths), str(ignored_path)],
        system_prompt="system",
        history=[{"role": "user", "content": "上一轮"}],
    )

    assert response == "anthropic answer"
    kwargs = client.client.messages.create.call_args.kwargs
    user_content = kwargs["messages"][-1]["content"]
    image_blocks = [item for item in user_content if item["type"] == "image"]
    assert len(image_blocks) == 3
    assert all(item["source"]["type"] == "base64" for item in image_blocks)
    assert all(item["source"]["media_type"] == "image/png" for item in image_blocks)
    serialized = json.dumps(kwargs, ensure_ascii=False, default=str)
    assert str(tmp_path) not in serialized
    assert all(path.name not in serialized for path in paths)
    assert ignored_path.name not in serialized
