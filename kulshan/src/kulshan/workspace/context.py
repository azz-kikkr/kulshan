"""Workspace context representing a resolved workspace."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kulshan.workspace.config import WorkspaceConfig


@dataclass
class WorkspaceContext:
    """
    Resolved workspace with paths and configuration.
    
    This is the result of workspace resolution and contains all
    information needed to access workspace storage. It does NOT
    contain an AWS session or require STS calls to create.
    """

    name: str
    path: Path
    config: WorkspaceConfig
    history_db_path: Path
    security_history_db_path: Path

    @property
    def is_bound(self) -> bool:
        """True if workspace has configured AWS connections."""
        return self.config.is_bound

    @property
    def binding_mode(self) -> str:
        """Return 'bound' or 'unbound'."""
        return self.config.binding_mode

    @property
    def display_name(self) -> str:
        """Return display name or name."""
        return self.config.display_name or self.config.name

    @property
    def payer_account_id(self) -> str | None:
        """Return payer account ID if bound."""
        if self.config.aws:
            return self.config.aws.payer_account_id
        return None

    @classmethod
    def from_path(cls, workspace_path: Path, config: WorkspaceConfig) -> WorkspaceContext:
        """
        Create WorkspaceContext from a workspace directory and config.
        
        Args:
            workspace_path: Path to workspace directory.
            config: Parsed workspace configuration.
            
        Returns:
            WorkspaceContext with resolved paths.
        """
        return cls(
            name=config.name,
            path=workspace_path,
            config=config,
            history_db_path=workspace_path / "history.db",
            security_history_db_path=workspace_path / "security-history.db",
        )
