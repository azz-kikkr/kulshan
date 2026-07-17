"""
Kulshan exception hierarchy.
"""

from __future__ import annotations


class SuiteError(Exception):
    """Base exception for all Kulshan errors."""


class SessionError(SuiteError):
    """Raised when a boto3 session cannot be created or a role cannot be assumed."""


class LicenseError(SuiteError):
    """Raised for license validation failures."""


class LicenseTierError(LicenseError):
    """Raised when the current tier does not permit the requested feature."""


class ConfigError(SuiteError):
    """Raised for configuration parsing or validation failures."""
