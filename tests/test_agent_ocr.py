from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from agent import ArtPMAgent


class FailingGenerationClient:
    def __init__(self):
        self.chat_calls = 0
        self.vision_calls = 0

    def chat(self, *args, **kwargs):
        self.chat_calls += 1
        raise TimeoutError("generation service unavailable")

    def chat_with_images(self, *args, **kwargs):
        self.vision_calls += 1
        raise AssertionError("OCR text should avoid the vision request")


def test_agent_uses_ocr_text_before_vision_and_degrades_transparently(tmp_path):
    image_path = tmp_path / "brief.png"
    image_path.write_bytes(b"placeholder")
    generation = FailingGenerationClient()
    agent = object.__new__(ArtPMAgent)
    agent.llm_client = generation
    agent.router = SimpleNamespace(skills={})
    agent.config = SimpleNamespace(get=lambda key, default=None: default)
    agent._build_system_prompt = lambda profile, knowledge: "trusted system prompt"
    agent._parse_context_attachments = lambda user_input, context: (
        [
            {
                "name": image_path.name,
                "file_path": str(image_path),
                "success": True,
                "document_type": "图片资料",
                "extracted_data": {
                    "ocr": {"engine": "unlimited-ocr", "ok": True}
                },
                "raw_text": "报价金额：100000\n制作成本：60000",
                "requires_vision": False,
                "ocr_available": True,
            }
        ],
        "<attachment_data>ocr</attachment_data>",
    )

    response = agent.chat(
        "请评估图片中的报价",
        {"file_paths": [str(image_path)], "conversation_history": []},
    )

    assert "已保留附件提取结果" in response
    assert "报价金额：100000" in response
    assert generation.chat_calls == 1
    assert generation.vision_calls == 0


def test_visual_question_keeps_vision_even_when_ocr_text_exists(tmp_path):
    image_path = tmp_path / "reference.png"
    image_path.write_bytes(b"placeholder")

    class VisionClient:
        def __init__(self):
            self.chat_calls = 0
            self.vision_calls = 0

        def chat(self, *args, **kwargs):
            self.chat_calls += 1
            raise AssertionError("visual question must not use text-only chat")

        def chat_with_images(self, prompt, image_paths, **kwargs):
            self.vision_calls += 1
            assert image_paths == [str(image_path)]
            assert "OCR caption" in prompt
            return "视觉比较结果"

    vision = VisionClient()
    agent = object.__new__(ArtPMAgent)
    agent.llm_client = vision
    agent.router = SimpleNamespace(skills={})
    agent.config = SimpleNamespace(get=lambda key, default=None: default)
    agent._build_system_prompt = lambda profile, knowledge: "trusted system prompt"
    agent._parse_context_attachments = lambda user_input, context: (
        [
            {
                "name": image_path.name,
                "file_path": str(image_path),
                "success": True,
                "raw_text": "OCR caption",
                "requires_vision": False,
                "ocr_available": True,
            }
        ],
        "<attachment_data>OCR caption</attachment_data>",
    )

    response = agent.chat(
        "比较这张参考图的颜色和构图",
        {"file_paths": [str(image_path)], "conversation_history": []},
    )

    assert response == "视觉比较结果"
    assert vision.chat_calls == 0
    assert vision.vision_calls == 1


def test_agent_rejects_scanned_pdf_without_ocr_text(tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"placeholder")
    agent = object.__new__(ArtPMAgent)
    agent.process_document = lambda file_path, hint: {
        "success": True,
        "document_type": "PDF资料",
        "extracted_data": {"page_count": 2, "pages_requiring_ocr": [1, 2]},
        "raw_text": "",
    }

    parsed, context = agent._parse_context_attachments(
        "请读取 PDF", {"file_paths": [str(pdf_path)]}
    )

    assert context.startswith("以下是应用刚刚")
    assert parsed[0]["success"] is False
    assert "没有可用的 OCR" in parsed[0]["error"]
