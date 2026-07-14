"""AWS execution context resolution for workspaces.

Resolves the correct AWS session for a workspace command based on
connection configuration, CLI arguments, and STS verification.

This is the single authoritative implementation for runtime credential
resolution. Both workspace creation (sts.py) and runtime execution
share the same STS verification path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import boto3

from kulshan.workspace.config import AwsConnection
from kulshan.workspace.context import WorkspaceContext
from kulshan.workspace.errors import (
    AmbiguousProfileError,
    ConnectionConflictError,
    ConnectionNotFoundError,
    ProfileNotConfiguredError,
    RoleArnConflictError,
    WorkspaceCredentialMismatchError,
)
from kulshan.workspace.sts import (
    StsVerificationError,
    StsVerificationResult,
    VerifiedAwsSession,
    create_verified_session,
    verify_credentials,
)

logger = logging.getLogger(__name__)


@dataclass
class AwsExecutionContext:
    """Resolved AWS execution context for a workspace command."""

    workspace: WorkspaceContext
    connection: AwsConnection | None
    session: "boto3.Session"
    resolved_profile: str | None
    session_account_id: str
    payer_account_id: str | None
    is_unbound: bool = False


def resolve_aws_execution(
    workspace: WorkspaceContext,
    connection_name: str | None = None,
    profile: str | None = None,
    role_arn: str | None = None,
    show_pii: bool = False,
) -> AwsExecutionContext:
    """
    Resolve AWS execution context for a workspace.

    For bound workspaces:
      1. Resolve connection (--connection > --profile > default_connection)
      2. Validate --role-arn against connection config
      3. Create session, assume role, call STS
      4. Validate returned account matches expected_session_account_id

    For unbound default:
      1. Use --profile or AWS_PROFILE or default chain
      2. Allow --role-arn freely
      3. Show unbound warning once

    Args:
        workspace: Resolved workspace context.
        connection_name: Explicit --connection selector.
        profile: Explicit --profile selector.
        role_arn: Explicit --role-arn.
        show_pii: Whether to show full account IDs in errors.

    Returns:
        AwsExecutionContext with the verified session.

    Raises:
        ConnectionNotFoundError: Unknown --connection.
        ProfileNotConfiguredError: --profile not in workspace.
        AmbiguousProfileError: Profile matches multiple connections.
        ConnectionConflictError: --connection and --profile conflict.
        RoleArnConflictError: --role-arn conflicts with connection.
        WorkspaceCredentialMismatchError: STS account != expected.
        StsVerificationError: STS call failed.
    """
    if workspace.is_bound:
        return _resolve_bound(
            workspace, connection_name, profile, role_arn, show_pii
        )
    else:
        return _resolve_unbound(
            workspace, profile, role_arn
        )


def _resolve_bound(
    workspace: WorkspaceContext,
    connection_name: str | None,
    profile: str | None,
    role_arn: str | None,
    show_pii: bool,
) -> AwsExecutionContext:
    """Resolve execution for a bound workspace."""
    aws_config = workspace.config.aws
    assert aws_config is not None  # guaranteed by is_bound

    # --- Step 1: Resolve connection ---
    connection: AwsConnection | None = None

    if connection_name and profile:
        # Both supplied — must identify the same connection
        conn_by_name = aws_config.get_connection(connection_name)
        if conn_by_name is None:
            raise ConnectionNotFoundError(workspace.name, connection_name)
        if conn_by_name.profile != profile:
            raise ConnectionConflictError(
                workspace.name, connection_name, profile, conn_by_name.profile
            )
        connection = conn_by_name

    elif connection_name:
        connection = aws_config.get_connection(connection_name)
        if connection is None:
            raise ConnectionNotFoundError(workspace.name, connection_name)

    elif profile:
        # Lookup by profile — may be ambiguous
        connection = aws_config.get_connection_by_profile(profile)
        if connection is None:
            available = [c.profile for c in aws_config.connections]
            raise ProfileNotConfiguredError(workspace.name, profile, available)

    else:
        # Use default connection
        connection = aws_config.get_connection(aws_config.default_connection)
        if connection is None:
            raise ConnectionNotFoundError(
                workspace.name, aws_config.default_connection
            )

    # --- Step 2: Validate role_arn ---
    effective_role = connection.role_arn

    if role_arn:
        if connection.role_arn is None:
            raise RoleArnConflictError(
                workspace.name, connection.name, None, role_arn
            )
        if role_arn != connection.role_arn:
            raise RoleArnConflictError(
                workspace.name, connection.name, connection.role_arn, role_arn
            )

    # --- Step 3: Create verified session (single call, no double-creation) ---
    verified = create_verified_session(
        profile=connection.profile,
        role_arn=effective_role,
    )

    # --- Step 4: Validate account ---
    if verified.account_id != connection.expected_session_account_id:
        raise WorkspaceCredentialMismatchError(
            workspace_name=workspace.name,
            connection_name=connection.name,
            profile=connection.profile,
            expected_account=connection.expected_session_account_id,
            actual_account=verified.account_id,
        )

    return AwsExecutionContext(
        workspace=workspace,
        connection=connection,
        session=verified.session,
        resolved_profile=connection.profile,
        session_account_id=verified.account_id,
        payer_account_id=aws_config.payer_account_id,
    )


def _resolve_unbound(
    workspace: WorkspaceContext,
    profile: str | None,
    role_arn: str | None,
) -> AwsExecutionContext:
    """Resolve execution for the unbound default workspace."""
    import os

    # Use --profile, or AWS_PROFILE, or default chain
    effective_profile = profile or os.environ.get("AWS_PROFILE")

    # Single verified session (no double creation)
    verified = create_verified_session(
        profile=effective_profile,
        role_arn=role_arn,
    )

    return AwsExecutionContext(
        workspace=workspace,
        connection=None,
        session=verified.session,
        resolved_profile=effective_profile,
        session_account_id=verified.account_id,
        payer_account_id=None,
        is_unbound=True,
    )


def _reset_unbound_warning() -> None:
    """No-op kept for test compatibility. Warning is now per-invocation."""
    pass
