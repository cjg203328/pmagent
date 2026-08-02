from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from docx import Document

from artpm_agent.agent import ArtPMAgent
from artpm_agent.skills.skill_router import DocumentClassifierParser
from artpm_agent.utils.chat_attachments import DEFAULT_ALLOWED_EXTENSIONS
from artpm_agent.utils.mineru_adapter import (
    MINERU_SUPPORTED_SUFFIXES,
    MinerUConfig,
    MinerUConversionResult,
    MinerUDocumentConverter,
)


def _write_local_artifacts(output_dir: Path, stem: str) -> None:
    document_dir = output_dir / stem / "pipeline"
    document_dir.mkdir(parents=True)
    (document_dir / f"{stem}.md").write_text(
        "# MinerU result\n\nA precise paragraph.",
        encoding="utf-8",
    )
    (document_dir / f"{stem}_content_list.json").write_text(
        '[{"type":"text","text":"A precise paragraph.","page_idx":0}]',
        encoding="utf-8",
    )
    (document_dir / f"{stem}_content_list_v2.json").write_text(
        "[[{" 
        '"type":"paragraph","content":{"paragraph_content":[{"type":"text","content":"A precise paragraph."}]},'
        '"bbox":[0,0,1,1]}]]',
        encoding="utf-8",
    )
    (document_dir / f"{stem}_middle.json").write_text(
        '{"pdf_info":[{"page_idx":0}],"_backend":"pipeline","_version_name":"3.4.4"}',
        encoding="utf-8",
    )


def test_mineru_local_cli_result_is_normalized_and_cached(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"fake-pdf")
    calls = []

    def runner(args, **kwargs):
        calls.append((list(args), kwargs))
        if args[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="mineru, version 3.4.4", stderr="")
        output_dir = Path(args[args.index("-o") + 1])
        _write_local_artifacts(output_dir, source.stem)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    converter = MinerUDocumentConverter(
        MinerUConfig.from_mapping(
            {
                "command": ["E:/python/python.exe", "-m", "mineru"],
                "output_dir": str(tmp_path / "mineru-output"),
                "cache_enabled": True,
            }
        ),
        runner=runner,
    )

    first = converter.convert(source, user_hint="do not leak this hint")
    second = converter.convert(source)

    assert first.success is True
    assert first.runtime_version == "3.4.4"
    assert first.structured_data["summary"]["page_count"] == 1
    assert first.structured_data["content_list"][0]["type"] == "text"
    assert first.markdown.startswith("# MinerU result")
    assert first.artifacts["markdown"].endswith(".md")
    assert second.success is True
    assert second.cache_hit is True
    assert len([call for call in calls if call[0][-1] != "--version"]) == 1
    assert all("do not leak" not in token for token in calls[1][0])
    assert calls[1][1]["shell"] is False


def test_mineru_archive_rejects_path_traversal(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"fake-pdf")
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../escape.md", "bad")

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "application/zip"}
        content = payload.getvalue()

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            del chunk_size
            return [self.content]

        def json(self):
            return {}

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"version": "3.4.4", "protocol_version": 2},
            )

        def post(self, *_args, **_kwargs):
            return FakeResponse()

        def close(self):
            return None

    converter = MinerUDocumentConverter(
        {
            "mode": "remote",
            "api_url": "http://127.0.0.1:8000",
            "output_dir": str(tmp_path / "mineru-output"),
        },
        session=FakeSession(),
    )

    result = converter.convert(source)

    assert result.success is False
    assert "unsafe path" in (result.error or "")
    assert not (tmp_path / "escape.md").exists()


def test_mineru_remote_async_task_is_polled_and_materialized(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"fake-pdf")

    class FakeResponse:
        def __init__(self, payload, *, content_type="application/json"):
            self.payload = payload
            self.headers = {"Content-Type": content_type}
            self.status_code = 200
            self.content = (
                payload
                if isinstance(payload, bytes)
                else json.dumps(payload).encode("utf-8")
            )

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            del chunk_size
            return [self.content]

        def json(self):
            return self.payload

    class FakeSession:
        def __init__(self):
            self.urls = []

        def post(self, url, **_kwargs):
            self.urls.append(url)
            return FakeResponse(
                {
                    "task_id": "task-1",
                    "status": "pending",
                    "status_url": "/tasks/task-1",
                    "result_url": "/tasks/task-1/result",
                }
            )

        def get(self, url, **_kwargs):
            self.urls.append(url)
            if url.endswith("/health"):
                return FakeResponse({"version": "3.4.4", "protocol_version": 2})
            if url.endswith("/result"):
                return FakeResponse(
                    {
                        "results": {
                            "brief": {
                                "md_content": "# Remote result",
                                "content_list": [
                                    {"type": "text", "text": "Remote result", "page_idx": 0}
                                ],
                            }
                        }
                    }
                )
            return FakeResponse({"task_id": "task-1", "status": "completed"})

        def close(self):
            return None

    session = FakeSession()
    converter = MinerUDocumentConverter(
        {
            "mode": "remote",
            "api_url": "http://127.0.0.1:8000",
            "output_dir": str(tmp_path / "mineru-output"),
        },
        session=session,
    )

    result = converter.convert(source)

    assert result.success is True
    assert result.markdown == "# Remote result"
    assert result.structured_data["summary"]["block_counts"] == {"text": 1}
    assert session.urls.count("http://127.0.0.1:8000/health") == 1
    assert "http://127.0.0.1:8000/tasks/task-1/result" in session.urls


def test_mineru_cloud_config_requires_https_and_supports_custom_auth(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"fake-pdf")

    insecure = MinerUDocumentConverter(
        {
            "mode": "remote",
            "api_url": "http://mineru.example.com/api/v1",
            "output_dir": str(tmp_path / "insecure"),
        }
    )
    insecure_result = insecure.convert(source)
    assert insecure_result.success is False
    assert "HTTPS" in (insecure_result.error or "")

    class FakeResponse:
        headers = {"Content-Type": "application/json"}
        status_code = 200
        content = b'{"results":{"brief":{"md_content":"# Cloud"}}}'

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            del chunk_size
            return [self.content]

        def json(self):
            return {"version": "3.4.4", "protocol_version": 2}

    class FakeSession:
        def __init__(self):
            self.requests = []

        def get(self, url, **kwargs):
            self.requests.append(("GET", url, kwargs))
            return FakeResponse()

        def post(self, url, **kwargs):
            self.requests.append(("POST", url, kwargs))
            return FakeResponse()

        def close(self):
            return None

    session = FakeSession()
    cloud = MinerUDocumentConverter(
        {
            "mode": "remote",
            "api_url": "https://mineru.example.com/api/v1",
            "api_key": "cloud-token",
            "api_key_header": "X-API-Key",
            "api_key_prefix": "",
            "output_dir": str(tmp_path / "cloud"),
        },
        session=session,
    )
    cloud_result = cloud.convert(source)

    assert cloud_result.success is True
    assert cloud_result.markdown == "# Cloud"
    method, url, kwargs = session.requests[-1]
    assert method == "POST"
    assert url == "https://mineru.example.com/api/v1/file_parse"
    assert kwargs["headers"]["X-API-Key"] == "cloud-token"


def test_mineru_bounds_structured_output(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"fake-pdf")

    def runner(args, **kwargs):
        if args[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="3.4.4", stderr="")
        output_dir = Path(args[args.index("-o") + 1])
        document_dir = output_dir / source.stem
        document_dir.mkdir(parents=True)
        (document_dir / f"{source.stem}.md").write_text("# result", encoding="utf-8")
        blocks = [
            {"type": "text", "text": "x" * 2000, "page_idx": 0}
            for _ in range(200)
        ]
        (document_dir / f"{source.stem}_content_list.json").write_text(
        json.dumps(blocks),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    converter = MinerUDocumentConverter(
        {
            "command": ["E:/python/python.exe"],
            "output_dir": str(tmp_path / "mineru-output"),
            "max_structured_bytes": 16384,
        },
        runner=runner,
    )
    result = converter.convert(source)

    assert result.success is True
    encoded = json.dumps(
        result.structured_data,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= 16384
    assert result.warnings


def test_document_skill_prefers_mineru_and_preserves_contract(tmp_path):
    source = tmp_path / "slides.pptx"
    source.write_bytes(b"pptx-placeholder")

    class FakeConverter:
        def convert(self, path, *, user_hint=""):
            assert Path(path) == source.resolve()
            assert user_hint == "extract tables"
            return MinerUConversionResult(
                success=True,
                markdown="# Slides\n\n| A | B |",
                structured_data={"summary": {"page_count": 2, "block_counts": {"table": 1}}},
                source_format=".pptx",
                backend="pipeline",
                mode="local",
                runtime_version="3.4.4",
                attempted=True,
            )

    parsed = DocumentClassifierParser(
        {"mineru_converter": FakeConverter()}
    ).run({"file_path": str(source), "user_hint": "extract tables"})

    assert parsed["success"] is True
    assert parsed["preprocessor"]["name"] == "mineru"
    assert parsed["markdown"].startswith("# Slides")
    assert parsed["extracted_data"]["mineru"]["summary"]["page_count"] == 2


def test_document_skill_falls_back_when_mineru_is_unavailable(tmp_path):
    source = tmp_path / "notes.docx"
    document = Document()
    document.add_paragraph("fallback text")
    document.save(source)

    class UnavailableConverter:
        def convert(self, *_args, **_kwargs):
            return MinerUConversionResult(
                success=False,
                source_format=".docx",
                mode="auto",
                attempted=False,
                error="MinerU CLI is not installed",
            )

    parsed = DocumentClassifierParser(
        {"mineru_converter": UnavailableConverter()}
    ).run({"file_path": str(source)})

    assert parsed["success"] is True
    assert "fallback text" in parsed["raw_text"]
    assert "preprocessor" not in parsed


def test_mineru_conversion_result_exposes_agent_shape(tmp_path):
    source = tmp_path / "capture.png"
    source.write_bytes(b"image")
    result = MinerUConversionResult(
        success=True,
        markdown="# image",
        structured_data={
            "integration_schema": "artpm-mineru-v1",
            "summary": {"page_count": 1, "block_counts": {"image": 1}},
        },
        source_format=".png",
        backend="pipeline",
        mode="local",
        runtime_version="3.4.4",
    ).as_document_result(source, source="local")

    assert result["markdown"] == "# image"
    assert result["extracted_data"]["mineru"]["integration_schema"] == "artpm-mineru-v1"
    assert result["preprocessor"]["name"] == "mineru"
    assert result["ocr_available"] is True


def test_mineru_result_exposes_langchain_documents_without_absolute_path(tmp_path):
    source = tmp_path / "contract.pdf"
    conversion = MinerUConversionResult(
        success=True,
        markdown="# Contract",
        structured_data={
            "content_list": [
                {
                    "type": "text",
                    "text": "Payment term: net 30 days.",
                    "page_idx": 2,
                    "bbox": [10, 20, 300, 80],
                }
            ]
        },
        backend="pipeline",
        runtime_version="3.4.4",
    )

    documents = conversion.to_langchain_documents(source_name=str(source.resolve()))

    assert len(documents) == 1
    assert documents[0].page_content == "Payment term: net 30 days."
    assert documents[0].metadata["source"] == "contract.pdf"
    assert documents[0].metadata["page"] == 2
    assert str(tmp_path) not in str(documents[0].metadata)


def test_agent_does_not_reparse_normalized_mineru_result(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"placeholder")
    expected = {
        "success": True,
        "document_type": "PDF document",
        "raw_text": "# MinerU",
        "markdown": "# MinerU",
        "extracted_data": {"mineru": {"summary": {"page_count": 1}}},
        "preprocessor": {"name": "mineru"},
    }

    class Router:
        def execute_skill(self, _name, _inputs):
            return expected

    class LocalConverter:
        def convert(self, *_args, **_kwargs):
            raise AssertionError("local converter must not run after MinerU")

    agent = object.__new__(ArtPMAgent)
    agent.router = Router()
    agent.markdown_converter = LocalConverter()

    result = agent.process_document(str(source), "analyze")

    assert result is expected


def test_mineru_extensions_are_uploadable():
    assert {suffix.lstrip(".") for suffix in MINERU_SUPPORTED_SUFFIXES} <= set(
        DEFAULT_ALLOWED_EXTENSIONS
    )
