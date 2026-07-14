"""Validation utilities for workspace configuration."""
from __future__ import annotations

import re

from kulshan.workspace.errors import WorkspaceValidationError


# Valid workspace names: alphanumeric, hyphens, underscores, 1-64 chars
_WORKSPACE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

# Reserved workspace names
_RESERVED_NAMES = frozenset({".", "..", "default"})


def validate_account_id(value: str, field_name: str = "Account ID") -> str:
    """
    Validate that a value is exactly 12 decimal digits.
    
    Args:
        value: The account ID string to validate.
        field_name: Name of the field for error messages.
        
    Returns:
        The validated account ID (unchanged).
        
    Raises:
        WorkspaceValidationError: If validation fails.
    """
    if not value:
        raise WorkspaceValidationError(f"{field_name} cannot be empty.")
    if not value.isdigit():
        raise WorkspaceValidationError(
            f"{field_name} must contain only digits, got: {value}"
        )
    if len(value) != 12:
        raise WorkspaceValidationError(
            f"{field_name} must be exactly 12 digits, got {len(value)} digits: {value}"
        )
    return value


def validate_workspace_name(name: str, allow_default: bool = False) -> str:
    """
    Validate workspace name for safety and consistency.
    
    Args:
        name: The workspace name to validate.
        allow_default: If True, allow "default" as a name (for internal use).
        
    Returns:
        The validated name (unchanged).
        
    Raises:
        WorkspaceValidationError: If validation fails.
    """
    if not name:
        raise WorkspaceValidationError("Workspace name cannot be empty.")
    
    # Check for path traversal attempts
    if "/" in name or "\\" in name:
        raise WorkspaceValidationError(
            f"Workspace name cannot contain path separators: {name}"
        )
    
    # Check reserved names
    if name.lower() in _RESERVED_NAMES:
        if name.lower() == "default" and allow_default:
            return name
        raise WorkspaceValidationError(
            f"Workspace name '{name}' is reserved."
        )
    
    # Check pattern
    if not _WORKSPACE_NAME_PATTERN.match(name):
        raise WorkspaceValidationError(
            f"Workspace name must start with alphanumeric and contain only "
            f"alphanumeric, hyphens, or underscores (1-64 chars): {name}"
        )
    
    return name


def validate_connection_name(name: str) -> str:
    """
    Validate connection name.
    
    Args:
        name: The connection name to validate.
        
    Returns:
        The validated name (unchanged).
        
    Raises:
        WorkspaceValidationError: If validation fails.
    """
    if not name:
        raise WorkspaceValidationError("Connection name cannot be empty.")
    
    if not _WORKSPACE_NAME_PATTERN.match(name):
        raise WorkspaceValidationError(
            f"Connection name must start with alphanumeric and contain only "
            f"alphanumeric, hyphens, or underscores (1-64 chars): {name}"
        )
    
    return name


def validate_profile_name(name: str) -> str:
    """
    Validate AWS profile name.
    
    AWS profile names are fairly permissive, but we reject obviously
    problematic values.
    
    Args:
        name: The profile name to validate.
        
    Returns:
        The validated name (unchanged).
        
    Raises:
        WorkspaceValidationError: If validation fails.
    """
    if not name:
        raise WorkspaceValidationError("Profile name cannot be empty.")
    
    if len(name) > 256:
        raise WorkspaceValidationError(
            f"Profile name too long (max 256 chars): {len(name)}"
        )
    
    # Reject control characters and path separators
    if any(c in name for c in "\x00\n\r/\\"):
        raise WorkspaceValidationError(
            f"Profile name contains invalid characters: {name}"
        )
    
    return name
