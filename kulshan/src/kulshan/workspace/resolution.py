"""Workspace resolution logic."""
from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

import tomli_w

from kulshan.workspace.config import (
    WorkspaceConfig,
    create_default_workspace_config,
    read_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.context import WorkspaceContext
from kulshan.workspace.errors import WorkspaceNotFoundError, WorkspaceConfigError
from kulshan.workspace.paths import (
    get_config_file_path,
    get_workspace_path,
    get_workspaces_root,
)
from kulshan.workspace.validation import validate_workspace_name

logger = logging.getLogger(__name__)

# Track whether migration has been attempted this process to avoid
# repeated checks on every resolve_workspace() call.
_migration_attempted: bool = False


def get_active_workspace_name() -> str | None:
    """
    Read the active workspace name from config.toml.
    
    Returns:
        Active workspace name, or None if not set.
    """
    config_path = get_config_file_path()
    if not config_path.exists():
        return None
    
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("active_workspace")
    except Exception:
        return None


def set_active_workspace_name(name: str) -> None:
    """
    Save the active workspace name to config.toml.
    
    Args:
        name: Workspace name to set as active.
    """
    config_path = get_config_file_path()
    config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    
    # Read existing config or start fresh
    data: dict = {}
    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            pass
    
    data["active_workspace"] = name
    
    # Write atomically
    tmp_path = config_path.with_suffix(".toml.tmp")
    try:
        with open(tmp_path, "wb") as f:
            tomli_w.dump(data, f)
        os.replace(tmp_path, config_path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def list_workspaces() -> list[str]:
    """
    List all workspace names.
    
    Returns:
        List of workspace directory names.
    """
    root = get_workspaces_root()
    if not root.exists():
        return []
    
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "workspace.toml").exists()
    )


def workspace_exists(name: str) -> bool:
    """
    Check if a workspace exists.
    
    Args:
        name: Workspace name.
        
    Returns:
        True if workspace directory and config exist.
    """
    workspace_path = get_workspace_path(name)
    return workspace_path.exists() and (workspace_path / "workspace.toml").exists()


def ensure_default_workspace() -> Path:
    """
    Ensure the default workspace exists.
    
    Creates the default unbound workspace if no workspaces exist.
    Does NOT run database migration (that's handled separately).
    
    Returns:
        Path to the default workspace.
    """
    default_path = get_workspace_path("default")
    
    if workspace_exists("default"):
        return default_path
    
    # Create default workspace directory
    default_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    
    # Create default configuration
    config = create_default_workspace_config()
    write_workspace_config(default_path, config)
    
    return default_path


def ensure_workspace_infrastructure() -> None:
    """
    Centralized workspace infrastructure initialization.

    Called once per process before workspace resolution. Ensures:
    1. Default workspace exists.
    2. Legacy databases are detected and migrated safely.

    Requirements:
    - No AWS session or STS calls.
    - Safe to run on every invocation (idempotent, once-per-process guard).
    - Migration failures do not prevent access to a valid workspace.
    - Does not print on repeated success (only on first migration or failure).
    """
    global _migration_attempted
    if _migration_attempted:
        return
    _migration_attempted = True

    # Ensure default workspace exists first
    ensure_default_workspace()

    # Run migration (safe, idempotent)
    try:
        from kulshan.workspace.migration import migrate_legacy_to_default_workspace

        report = migrate_legacy_to_default_workspace()

        if report.any_failed:
            # Log warning — don't block workspace access
            if report.main_history.status == "failed":
                logger.warning(
                    "Legacy main history migration failed: %s. "
                    "Original database preserved at legacy location.",
                    report.main_history.error,
                )
            if report.security_history.status == "failed":
                logger.warning(
                    "Legacy security history migration failed: %s. "
                    "Original database preserved at legacy location.",
                    report.security_history.error,
                )
    except Exception as e:
        # Migration must never prevent workspace access
        logger.warning("Workspace migration check failed: %s", e)


def resolve_workspace(
    workspace_name: str | None = None,
) -> WorkspaceContext:
    """
    Resolve workspace by name without making AWS calls.
    
    Resolution order:
    1. workspace_name parameter (from --workspace)
    2. KULSHAN_WORKSPACE environment variable
    3. Saved active workspace from config.toml
    4. "default"
    
    Side effects:
    - Creates default workspace if no workspaces exist
    
    Args:
        workspace_name: Explicit workspace name (from CLI).
        
    Returns:
        WorkspaceContext with resolved paths and configuration.
        
    Raises:
        WorkspaceNotFoundError: If named workspace doesn't exist.
        WorkspaceConfigError: If workspace configuration is invalid.
    """
    # 1. Explicit parameter
    resolved_name = workspace_name
    
    # 2. Environment variable
    if resolved_name is None:
        resolved_name = os.environ.get("KULSHAN_WORKSPACE")
    
    # 3. Saved active workspace
    if resolved_name is None:
        resolved_name = get_active_workspace_name()
    
    # 4. Default
    if resolved_name is None:
        resolved_name = "default"
    
    # Validate name (allow "default")
    validate_workspace_name(resolved_name, allow_default=True)
    
    # Run infrastructure initialization (once per process)
    ensure_workspace_infrastructure()
    
    # Get workspace path
    workspace_path = get_workspace_path(resolved_name)
    
    # Check existence
    if not workspace_path.exists():
        raise WorkspaceNotFoundError(resolved_name)
    
    config_path = workspace_path / "workspace.toml"
    if not config_path.exists():
        raise WorkspaceNotFoundError(resolved_name)
    
    # Read configuration
    config = read_workspace_config(workspace_path)
    
    # Build context
    return WorkspaceContext.from_path(workspace_path, config)


def _reset_migration_guard() -> None:
    """Reset the once-per-process migration guard. For testing only."""
    global _migration_attempted
    _migration_attempted = False
