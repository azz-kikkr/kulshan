"""Workspace-related errors."""
from __future__ import annotations


class WorkspaceError(Exception):
    """Base class for workspace errors."""


class WorkspaceNotFoundError(WorkspaceError):
    """Workspace does not exist."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Workspace not found: {name}")


class WorkspaceExistsError(WorkspaceError):
    """Workspace already exists."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Workspace already exists: {name}")


class WorkspaceConfigError(WorkspaceError):
    """Workspace configuration is invalid or corrupt."""

    def __init__(self, name: str, detail: str):
        self.name = name
        self.detail = detail
        super().__init__(f"Invalid workspace configuration for '{name}': {detail}")


class WorkspaceValidationError(WorkspaceError):
    """Workspace name or configuration value is invalid."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class ConnectionConflictError(WorkspaceError):
    """Conflicting --connection and --profile selectors."""

    def __init__(
        self,
        workspace_name: str,
        connection_name: str,
        profile: str,
        connection_profile: str,
    ):
        self.workspace_name = workspace_name
        self.connection_name = connection_name
        self.profile = profile
        self.connection_profile = connection_profile
        super().__init__(
            f"Conflicting selectors for workspace '{workspace_name}': "
            f"--connection '{connection_name}' uses profile '{connection_profile}', "
            f"but --profile '{profile}' was also specified."
        )


class ConnectionNotFoundError(WorkspaceError):
    """Named connection does not exist in workspace."""

    def __init__(self, workspace_name: str, connection_name: str):
        self.workspace_name = workspace_name
        self.connection_name = connection_name
        super().__init__(
            f"Connection '{connection_name}' not found in workspace '{workspace_name}'."
        )


class ProfileNotConfiguredError(WorkspaceError):
    """Profile is not configured as a connection in the workspace."""

    def __init__(self, workspace_name: str, profile: str, available_profiles: list[str]):
        self.workspace_name = workspace_name
        self.profile = profile
        self.available_profiles = available_profiles
        profiles_str = ", ".join(available_profiles) if available_profiles else "(none)"
        super().__init__(
            f"Profile '{profile}' is not configured for workspace '{workspace_name}'. "
            f"Configured profiles: {profiles_str}"
        )


class WorkspaceCredentialMismatchError(WorkspaceError):
    """AWS credentials do not match workspace configuration."""

    def __init__(
        self,
        workspace_name: str,
        connection_name: str | None,
        profile: str | None,
        expected_account: str,
        actual_account: str,
    ):
        self.workspace_name = workspace_name
        self.connection_name = connection_name
        self.profile = profile
        self.expected_account = expected_account
        self.actual_account = actual_account
        super().__init__(
            f"Credential mismatch for workspace '{workspace_name}': "
            f"expected account {expected_account}, got {actual_account}."
        )


class RoleArnConflictError(WorkspaceError):
    """--role-arn conflicts with connection configuration."""

    def __init__(
        self,
        workspace_name: str,
        connection_name: str,
        connection_role: str | None,
        provided_role: str,
    ):
        self.workspace_name = workspace_name
        self.connection_name = connection_name
        self.connection_role = connection_role
        self.provided_role = provided_role
        if connection_role:
            msg = (
                f"Role ARN conflict for workspace '{workspace_name}', "
                f"connection '{connection_name}': connection has role '{connection_role}', "
                f"but --role-arn '{provided_role}' was provided."
            )
        else:
            msg = (
                f"Role ARN not allowed for workspace '{workspace_name}', "
                f"connection '{connection_name}': connection has no configured role, "
                f"but --role-arn '{provided_role}' was provided."
            )
        super().__init__(msg)
