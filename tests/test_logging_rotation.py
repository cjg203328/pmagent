from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

import pytest

from artpm_agent.utils import logger as logger_module


@pytest.fixture
def isolated_root_logger(monkeypatch):
    root = logging.Logger("artpm-test-root")
    monkeypatch.setattr(logger_module.logging, "getLogger", lambda *_args: root)
    yield root
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()


def test_default_logging_uses_timed_rotation(tmp_path, isolated_root_logger):
    configured = logger_module.setup_logging(
        log_dir=tmp_path,
        log_file="application.log",
        console=False,
    )

    handler = configured.handlers[0]
    assert isinstance(handler, TimedRotatingFileHandler)
    assert handler.backupCount == logger_module.DEFAULT_LOG_BACKUP_COUNT
    assert handler._artpm_managed is True


def test_size_rotation_creates_bounded_backup_files(tmp_path, isolated_root_logger):
    configured = logger_module.setup_logging(
        level="INFO",
        log_dir=tmp_path,
        log_file="bounded.log",
        rotation="size",
        max_bytes=160,
        backup_count=2,
        console=False,
    )

    handler = configured.handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    for index in range(30):
        configured.info("rotation record %s %s", index, "x" * 60)
    handler.flush()

    assert (tmp_path / "bounded.log").exists()
    assert (tmp_path / "bounded.log.1").exists()
    assert len(list(tmp_path.glob("bounded.log*"))) <= 3


def test_logging_masks_sensitive_values(tmp_path, isolated_root_logger):
    configured = logger_module.setup_logging(
        log_dir=tmp_path,
        log_file="masked.log",
        rotation="none",
        console=False,
    )

    configured.warning("api_key=%s", "secret-key-value-1234567890")
    configured.handlers[0].flush()
    text = (tmp_path / "masked.log").read_text(encoding="utf-8")

    assert "secret-key-value-1234567890" not in text
    assert "api_key=***" in text


def test_logging_reads_rotation_environment(
    tmp_path, monkeypatch, isolated_root_logger
):
    monkeypatch.setenv("ARTPM_LOG_ROTATION", "size")
    monkeypatch.setenv("ARTPM_LOG_MAX_BYTES", "512")
    monkeypatch.setenv("ARTPM_LOG_BACKUP_COUNT", "3")
    monkeypatch.setenv("ARTPM_LOG_LEVEL", "WARNING")

    configured = logger_module.setup_logging(
        log_dir=tmp_path,
        log_file="environment.log",
        console=False,
    )

    handler = configured.handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 512
    assert handler.backupCount == 3
    assert configured.level == logging.WARNING


def test_logging_rejects_unsafe_file_name(tmp_path, isolated_root_logger):
    with pytest.raises(ValueError, match="file name"):
        logger_module.setup_logging(
            log_dir=tmp_path,
            log_file="../outside.log",
            console=False,
        )


@pytest.mark.parametrize("role", ["api", "ui", "voice"])
def test_default_log_file_is_isolated_by_process_role(
    role, tmp_path, monkeypatch, isolated_root_logger
):
    monkeypatch.setenv("ARTPM_PROCESS_ROLE", role)

    configured = logger_module.setup_logging(log_dir=tmp_path, console=False)

    handler = configured.handlers[0]
    assert handler.baseFilename == str(tmp_path / f"artpm-{role}.log")


def test_explicit_log_file_overrides_process_role(
    tmp_path, monkeypatch, isolated_root_logger
):
    monkeypatch.setenv("ARTPM_PROCESS_ROLE", "ui")

    configured = logger_module.setup_logging(
        log_dir=tmp_path,
        log_file="custom.log",
        console=False,
    )

    handler = configured.handlers[0]
    assert handler.baseFilename == str(tmp_path / "custom.log")
