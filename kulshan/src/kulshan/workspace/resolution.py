"""Workspace resolution logic."""
from __future__ import annotations

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
    
    # Ensure default exists if that's what we're resolving
    if resolved_name == "default":
        ensure_default_workspace()
    
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
