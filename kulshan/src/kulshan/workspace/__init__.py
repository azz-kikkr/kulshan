"""Workspace management for multi-payer isolation.

A workspace provides physical isolation of scan history and configuration
for a single payer or billing environment. Each workspace has its own
history databases and AWS connection configuration.

Public API:
    resolve_workspace() - Resolve workspace by name (no AWS calls)
    resolve_workspace_with_profile() - Resolve with auto-routing awareness
    auto_onboard() - Automatic environment onboarding
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
from kulshan.workspace.resolution import resolve_workspace, resolve_workspace_with_profile
from kulshan.workspace.onboarding import auto_onboard, OnboardingResult, OnboardingError
from kulshan.workspace.errors import (
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceExistsError,
    WorkspaceConfigError,
    WorkspaceValidationError,
)

__all__ = [
    "resolve_workspace",
    "resolve_workspace_with_profile",
    "auto_onboard",
    "OnboardingResult",
    "OnboardingError",
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
