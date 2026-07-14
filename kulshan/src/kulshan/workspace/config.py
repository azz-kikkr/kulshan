"""Workspace configuration data models and TOML I/O."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

import tomli_w

from kulshan.workspace.errors import WorkspaceConfigError, WorkspaceValidationError
from kulshan.workspace.validation import (
    validate_account_id,
    validate_connection_name,
    validate_profile_name,
)


# Current schema version
SCHEMA_VERSION = 1

# Migration status values
MigrationStatusValue = Literal["not_found", "pending", "migrated", "failed"]


@dataclass
class AwsConnection:
    """A named AWS connection within a workspace."""

    name: str
    profile: str
    expected_session_account_id: str
    role_arn: str | None = None

    def __post_init__(self):
        validate_connection_name(self.name)
        validate_profile_name(self.profile)
        validate_account_id(
            self.expected_session_account_id, "expected_session_account_id"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        d: dict[str, Any] = {
            "name": self.name,
            "profile": self.profile,
            "expected_session_account_id": self.expected_session_account_id,
        }
        if self.role_arn:
            d["role_arn"] = self.role_arn
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any], workspace_name: str) -> AwsConnection:
        """Create from dictionary (TOML deserialization)."""
        try:
            return cls(
                name=data.get("name", ""),
                profile=data.get("profile", ""),
                expected_session_account_id=data.get("expected_session_account_id", ""),
                role_arn=data.get("role_arn"),
            )
        except Exception as e:
            raise WorkspaceConfigError(workspace_name, f"Invalid connection: {e}") from e


@dataclass
class WorkspaceAwsConfig:
    """AWS configuration for a bound workspace."""

    payer_account_id: str
    default_connection: str
    connections: list[AwsConnection] = field(default_factory=list)

    def __post_init__(self):
        validate_account_id(self.payer_account_id, "payer_account_id")
        validate_connection_name(self.default_connection)

        # Validate connection names are unique
        names = [c.name for c in self.connections]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise WorkspaceValidationError(
                f"Duplicate connection names: {', '.join(set(dupes))}"
            )

        # Validate default_connection references an existing connection
        if self.connections and not any(
            c.name == self.default_connection for c in self.connections
        ):
            raise WorkspaceValidationError(
                f"default_connection '{self.default_connection}' does not match "
                f"any configured connection: {names}"
            )

    def get_connection(self, name: str) -> AwsConnection | None:
        """Get connection by name."""
        return next((c for c in self.connections if c.name == name), None)

    def get_connection_by_profile(self, profile: str) -> AwsConnection | None:
        """Get connection by profile name.

        Returns:
            The connection if exactly one matches, None if zero match.

        Raises:
            AmbiguousProfileError: If more than one connection uses the profile.
        """
        from kulshan.workspace.errors import AmbiguousProfileError

        matches = [c for c in self.connections if c.profile == profile]
        if len(matches) == 0:
            return None
        if len(matches) == 1:
            return matches[0]
        # Multiple matches — ambiguous
        raise AmbiguousProfileError(
            workspace_name="(unknown)",  # caller should supply context
            profile=profile,
            matching_connections=[c.name for c in matches],
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        return {
            "payer_account_id": self.payer_account_id,
            "default_connection": self.default_connection,
            "connections": [c.to_dict() for c in self.connections],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], workspace_name: str) -> WorkspaceAwsConfig:
        """Create from dictionary (TOML deserialization)."""
        try:
            connections = [
                AwsConnection.from_dict(c, workspace_name)
                for c in data.get("connections", [])
            ]
            return cls(
                payer_account_id=data.get("payer_account_id", ""),
                default_connection=data.get("default_connection", ""),
                connections=connections,
            )
        except WorkspaceConfigError:
            raise
        except Exception as e:
            raise WorkspaceConfigError(
                workspace_name, f"Invalid AWS configuration: {e}"
            ) from e


@dataclass
class WorkspaceMigrationStatus:
    """Migration status for legacy databases."""

    main_history: MigrationStatusValue = "pending"
    security_history: MigrationStatusValue = "pending"

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary for TOML serialization."""
        return {
            "main_history": self.main_history,
            "security_history": self.security_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceMigrationStatus:
        """Create from dictionary (TOML deserialization)."""
        return cls(
            main_history=data.get("main_history", "pending"),
            security_history=data.get("security_history", "pending"),
        )


@dataclass
class WorkspaceConfig:
    """Workspace configuration stored in workspace.toml."""

    name: str
    schema_version: int = SCHEMA_VERSION
    display_name: str | None = None
    created_at: str | None = None
    binding_mode: Literal["bound", "unbound"] = "unbound"
    migration: WorkspaceMigrationStatus | None = None
    aws: WorkspaceAwsConfig | None = None

    @property
    def is_bound(self) -> bool:
        """True if workspace has configured AWS connections."""
        return self.binding_mode == "bound" and self.aws is not None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "binding_mode": self.binding_mode,
        }
        if self.display_name:
            d["display_name"] = self.display_name
        if self.created_at:
            d["created_at"] = self.created_at
        if self.migration:
            d["migration"] = self.migration.to_dict()
        if self.aws:
            d["aws"] = self.aws.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any], workspace_name: str) -> WorkspaceConfig:
        """Create from dictionary (TOML deserialization)."""
        name = data.get("name", workspace_name)
        if name != workspace_name:
            raise WorkspaceConfigError(
                workspace_name,
                f"Config name '{name}' does not match directory name '{workspace_name}'",
            )

        migration = None
        if "migration" in data:
            migration = WorkspaceMigrationStatus.from_dict(data["migration"])

        aws = None
        if "aws" in data:
            aws = WorkspaceAwsConfig.from_dict(data["aws"], workspace_name)

        binding_mode = data.get("binding_mode", "unbound")
        if binding_mode not in ("bound", "unbound"):
            raise WorkspaceConfigError(
                workspace_name,
                f"Invalid binding_mode: {binding_mode}. Must be 'bound' or 'unbound'.",
            )

        # Validate bound workspace has AWS config
        if binding_mode == "bound" and aws is None:
            raise WorkspaceConfigError(
                workspace_name,
                "Bound workspace must have AWS configuration.",
            )

        # Validate bound workspace has at least one connection
        if binding_mode == "bound" and aws is not None and not aws.connections:
            raise WorkspaceConfigError(
                workspace_name,
                "Bound workspace must have at least one AWS connection.",
            )

        # Only 'default' may be unbound — named workspaces must be bound
        if binding_mode == "unbound" and workspace_name != "default":
            raise WorkspaceConfigError(
                workspace_name,
                "Only the 'default' workspace may be unbound. "
                "Named workspaces must have binding_mode='bound' with AWS configuration.",
            )

        # Validate schema version
        schema_version = data.get("schema_version", SCHEMA_VERSION)
        if schema_version != SCHEMA_VERSION:
            raise WorkspaceConfigError(
                workspace_name,
                f"Unsupported schema_version {schema_version} "
                f"(this version of Kulshan supports version {SCHEMA_VERSION}).",
            )

        return cls(
            name=name,
            schema_version=schema_version,
            display_name=data.get("display_name"),
            created_at=data.get("created_at"),
            binding_mode=binding_mode,
            migration=migration,
            aws=aws,
        )


def create_default_workspace_config() -> WorkspaceConfig:
    """Create configuration for the default unbound workspace."""
    return WorkspaceConfig(
        name="default",
        display_name="Default",
        created_at=datetime.now(timezone.utc).isoformat(),
        binding_mode="unbound",
    )


def read_workspace_config(workspace_path: Path) -> WorkspaceConfig:
    """
    Read workspace configuration from workspace.toml.
    
    Args:
        workspace_path: Path to workspace directory.
        
    Returns:
        Parsed WorkspaceConfig.
        
    Raises:
        WorkspaceConfigError: If config is missing or invalid.
    """
    config_path = workspace_path / "workspace.toml"
    workspace_name = workspace_path.name

    if not config_path.exists():
        raise WorkspaceConfigError(workspace_name, "workspace.toml not found")

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        raise WorkspaceConfigError(workspace_name, f"Failed to parse TOML: {e}") from e

    return WorkspaceConfig.from_dict(data, workspace_name)


def write_workspace_config(workspace_path: Path, config: WorkspaceConfig) -> None:
    """
    Write workspace configuration atomically.
    
    Uses a temporary file and atomic rename to prevent partial writes.
    
    Args:
        workspace_path: Path to workspace directory.
        config: Configuration to write.
    """
    config_path = workspace_path / "workspace.toml"
    data = config.to_dict()

    # Write to temporary file in same directory (for atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".toml.tmp",
        prefix="workspace_",
        dir=workspace_path,
    )
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(data, f)
        # Atomic rename
        os.replace(tmp_path, config_path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
