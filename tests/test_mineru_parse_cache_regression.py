"""Regression contracts for the content-addressed MinerU parse cache.

These tests use the public converter with a deterministic fake CLI.  They keep
slow model downloads and network calls out of the suite while exercising the
real cache key, marker, artifact loading, and single-flight lock.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from time import sleep
from types import SimpleNamespace

from artpm_agent.utils.mineru_adapter import MinerUConfig, MinerUDocumentConverter


def _write_artifacts(output_root: Path, stem: str) -> None:
    document_dir = output_root / stem / "auto"
    document_dir.mkdir(parents=True, exist_ok=True)
    (document_dir / f"{stem}.md").write_text(
        "# Parsed document\n\nStable cache fixture.",
        encoding="utf-8",
    )
    (document_dir / f"{stem}_content_list.json").write_text(
        '[{"type":"text","text":"Stable cache fixture.","page_idx":0}]',
        encoding="utf-8",
    )
    (document_dir / f"{stem}_middle.json").write_text(
        '{"pdf_info":[{"page_idx":0}],"_backend":"pipeline",'
        '"_version_name":"3.4.4"}',
        encoding="utf-8",
    )


class _FakeMinerURunner:
    def __init__(self, source: Path, *, fail_first: bool = False, delay: float = 0.0):
        self.source = source
        self.fail_first = fail_first
        self.delay = delay
        self.conversion_calls = 0
        self._lock = Lock()

    def __call__(self, args, **_kwargs):
        if args[-1] == "--version":
            return SimpleNamespace(
                returncode=0,
                stdout="mineru, version 3.4.4",
                stderr="",
            )

        with self._lock:
            self.conversion_calls += 1
            call_number = self.conversion_calls
        if self.delay:
            sleep(self.delay)
        if self.fail_first and call_number == 1:
            return SimpleNamespace(returncode=2, stdout="", stderr="parse failed")

        output_root = Path(args[args.index("-o") + 1])
        _write_artifacts(output_root, self.source.stem)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _converter(tmp_path: Path, source: Path, runner) -> MinerUDocumentConverter:
    return MinerUDocumentConverter(
        MinerUConfig.from_mapping(
            {
                "command": ["E:/python/python.exe", "-m", "mineru"],
                "output_dir": str(tmp_path / "mineru-output"),
                "cache_enabled": True,
            }
        ),
        runner=runner,
    )


def test_repeated_unchanged_file_executes_parser_once(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"version-one")
    runner = _FakeMinerURunner(source)
    converter = _converter(tmp_path, source, runner)

    first = converter.convert(source)
    second = converter.convert(source)

    assert first.success is True
    assert first.cache_hit is False
    assert second.success is True
    assert second.cache_hit is True
    assert runner.conversion_calls == 1


def test_file_content_change_invalidates_cached_parse(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"version-one")
    runner = _FakeMinerURunner(source)
    converter = _converter(tmp_path, source, runner)

    first = converter.convert(source)
    source.write_bytes(b"version-two-with-different-content")
    changed = converter.convert(source)
    repeated = converter.convert(source)

    assert first.success is True
    assert changed.success is True
    assert changed.cache_hit is False
    assert repeated.cache_hit is True
    assert runner.conversion_calls == 2


def test_concurrent_requests_share_one_conversion(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"same-content")
    runner = _FakeMinerURunner(source, delay=0.05)
    converter = _converter(tmp_path, source, runner)
    workers = 6
    barrier = Barrier(workers)

    def convert_once():
        barrier.wait()
        return converter.convert(source)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _index: convert_once(), range(workers)))

    assert all(result.success for result in results)
    assert runner.conversion_calls == 1
    assert sum(result.cache_hit for result in results) == workers - 1


def test_failed_conversion_is_not_cached(tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"retryable-content")
    runner = _FakeMinerURunner(source, fail_first=True)
    converter = _converter(tmp_path, source, runner)

    failed = converter.convert(source)
    retried = converter.convert(source)
    cached = converter.convert(source)

    assert failed.success is False
    assert failed.cache_hit is False
    assert retried.success is True
    assert retried.cache_hit is False
    assert cached.success is True
    assert cached.cache_hit is True
    assert runner.conversion_calls == 2
