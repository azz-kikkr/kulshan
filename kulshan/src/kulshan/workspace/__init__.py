"""Workspace management for multi-payer isolation.

A workspace provides physical isolation of scan history and configuration
for a single payer or billing environment. Each workspace has its own
history databases and AWS connection configuration.

Public API:
    resolve_workspace() - Resolve workspace by name (no AWS calls)
    WorkspaceContext - Resolved workspace with paths
    WorkspaceConfig - Workspace configuration from workspace.toml
"""
from __future__ import annotations

from kulshan.workspace.context import WorkspaceContext
from kulshan.workspace.config import (
    WorkspaceConfig,
    WorkspaceAwsConfig,
    AwsConnection,
    WorkspaceMigrationStatus,
)
from kulshan.workspace.resolution import resolve_workspace
from kulshan.workspace.errors import (
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceExistsError,
    WorkspaceConfigError,
    WorkspaceValidationError,
)

__all__ = [
    "resolve_workspace",
    "WorkspaceContext",
    "WorkspaceConfig",
    "WorkspaceAwsConfig",
    "AwsConnection",
    "WorkspaceMigrationStatus",
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspaceExistsError",
    "WorkspaceConfigError",
    "WorkspaceValidationError",
]
