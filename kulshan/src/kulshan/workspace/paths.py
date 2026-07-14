"""Platform-aware paths for workspace storage and configuration."""
from __future__ import annotations

from pathlib import Path

import platformdirs


def get_config_dir() -> Path:
    """
    Return Kulshan's configuration directory.
    
    This is where user preferences like active workspace are stored.
    
    Platform paths:
    - Linux: ~/.config/kulshan/
    - macOS: ~/Library/Application Support/kulshan/
    - Windows: C:\\Users\\<user>\\AppData\\Local\\kulshan\\
    """
    return Path(platformdirs.user_config_dir("kulshan", "missionfinops"))


def get_data_dir() -> Path:
    """
    Return Kulshan's data directory.
    
    This is where workspaces and their databases are stored.
    
    Platform paths:
    - Linux: ~/.local/share/kulshan/
    - macOS: ~/Library/Application Support/kulshan/
    - Windows: C:\\Users\\<user>\\AppData\\Local\\kulshan\\
    """
    return Path(platformdirs.user_data_dir("kulshan", "missionfinops"))


def get_workspaces_root() -> Path:
    """
    Return the root directory containing all workspaces.
    
    Platform paths:
    - Linux: ~/.local/share/kulshan/workspaces/
    - macOS: ~/Library/Application Support/kulshan/workspaces/
    - Windows: C:\\Users\\<user>\\AppData\\Local\\kulshan\\workspaces\\
    """
    return get_data_dir() / "workspaces"


def get_workspace_path(workspace_name: str) -> Path:
    """
    Return the path to a specific workspace directory.
    
    Args:
        workspace_name: Name of the workspace.
        
    Returns:
        Path to the workspace directory (may not exist).
    """
    return get_workspaces_root() / workspace_name


def get_config_file_path() -> Path:
    """
    Return path to Kulshan's main configuration file.
    
    This stores global settings like active workspace.
    
    Platform paths:
    - Linux: ~/.config/kulshan/config.toml
    - macOS: ~/Library/Application Support/kulshan/config.toml
    - Windows: C:\\Users\\<user>\\AppData\\Local\\kulshan\\config.toml
    """
    return get_config_dir() / "config.toml"


def get_legacy_history_path() -> Path:
    """
    Return the legacy main history database path.
    
    This is where history.db was stored before workspace support.
    Used for migration.
    
    Platform paths:
    - Linux: ~/.local/share/Kulshan/history.db
    - macOS: ~/Library/Application Support/Kulshan/history.db
    - Windows: C:\\Users\\<user>\\AppData\\Local\\Kulshan\\history.db
    
    Note: Uses "Kulshan" (capitalized) for backward compatibility.
    """
    return Path(platformdirs.user_data_dir("Kulshan", "missionfinops")) / "history.db"


def get_legacy_security_history_path() -> Path:
    """
    Return the legacy security history database path.
    
    This is where security/history.db was stored before workspace support.
    Used for migration.
    
    Path: ~/.Kulshan/security/history.db (all platforms)
    
    Note: Uses home directory directly for backward compatibility.
    """
    return Path.home() / ".Kulshan" / "security" / "history.db"
