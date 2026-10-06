"""Repository-wide pytest bootstrap.

Keep every temporary artifact inside the repository instead of the OS temp
directory. On Windows the OS temp directory lives on ``C:`` (for example
``C:\\Users\\<user>\\AppData\\Local\\Temp``), and pytest's ``tmp_path``
factories, plus any ``tempfile`` use in the tests, would otherwise leave
``pytest-of-<user>`` session directories and other leftovers there.

Redirecting ``tempfile.tempdir`` here (before pytest creates its session
base temp) keeps test runs strictly inside ``.cache/tmp``, which is already
excluded from version control.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_CACHE_TMP = _PROJECT_ROOT / ".cache" / "tmp"

_CACHE_TMP.mkdir(parents=True, exist_ok=True)

# tempfile consults ``tempfile.tempdir`` first; point it at the project cache.
tempfile.tempdir = str(_CACHE_TMP)

# Child processes started by the tests inherit these, so anything that reads
# the classic temp environment variables also stays inside the repository.
os.environ["TMP"] = str(_CACHE_TMP)
os.environ["TEMP"] = str(_CACHE_TMP)
os.environ["TMPDIR"] = str(_CACHE_TMP)
