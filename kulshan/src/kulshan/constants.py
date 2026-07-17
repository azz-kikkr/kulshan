"""
Kulshan constants and exit codes.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """CLI exit codes."""

    SUCCESS = 0
    FINDING_FAIL = 1
    RUNTIME_ERROR = 2
    CONFIG_ERROR = 3


# Placeholder endpoints (configured at deploy time; not yet active)
SIGNING_ENDPOINT: str = "https://api.missionfinops.com/v1/license/sign"
