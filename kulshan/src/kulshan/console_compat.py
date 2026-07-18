"""Console compatibility helpers."""

from __future__ import annotations

import sys
from typing import Any


def configure_windows_utf8(stdout: Any = None, stderr: Any = None) -> None:
    """Make Windows CLI streams Unicode-safe when the host uses CP1252."""
    if sys.platform != "win32":
        return

    for stream in (stdout or sys.stdout, stderr or sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
