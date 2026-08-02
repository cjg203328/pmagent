"""Isolated pyttsx3 process used by the local voice fallback."""

from __future__ import annotations

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(2)
    text = sys.stdin.buffer.read().decode("utf-8").strip()
    if not text:
        raise SystemExit(2)

    try:
        import pyttsx3
    except ImportError as error:
        raise SystemExit(3) from error

    path = Path(sys.argv[1])
    engine = pyttsx3.init()
    try:
        engine.save_to_file(text, str(path))
        engine.runAndWait()
    finally:
        engine.stop()


if __name__ == "__main__":  # pragma: no cover - exercised as a child process
    main()
