"""Automatic AWS environment onboarding.

On first use of an AWS profile, Kulshan:
1. Verifies STS credentials.
2. Generates a stable workspace ID from profile + role_arn + account.
3. Creates a readable display name (e.g. 'acme-finops-cedar').
4. Creates a bound workspace with a single connection.
5. Registers the mapping for future automatic routing.

On subsequent use, Kulshan looks up the registry and routes to the
correct workspace automatically — no manual workspace management needed.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    write_workspace_config,
)
from kulshan.workspace.context import WorkspaceContext
from kulshan.workspace.paths import get_workspace_path
from kulshan.workspace.registry import (
    compute_workspace_dir_name,
    find_entry_by_workspace_dir,
    lookup_workspace,
    register_workspace,
    RegistryEntry,
)
from kulshan.workspace.sts import (
    StsVerificationError,
    VerifiedAwsSession,
    create_verified_session,
)
from kulshan.workspace.wordlist import pick_word

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# HMAC key for readable name word selection (domain separation)
_NAME_HMAC_KEY = b"kulshan-workspace-name-v1"


class OnboardingResult:
    """Result of automatic onboarding or routing lookup."""

    def __init__(
        self,
        workspace_context: WorkspaceContext,
        verified_session: VerifiedAwsSession,
        display_name: str,
        is_new: bool,
    ):
        self.workspace_context = workspace_context
        self.verified_session = verified_session
        self.display_name = display_name
        self.is_new = is_new

    @property
    def profile(self) -> str | None:
        return self.verified_session.resolved_profile

    @property
    def account_id(self) -> str:
        return self.verified_session.account_id


def generate_display_name(profile: str, role_arn: str | None, account_id: str) -> str:
    """Generate a readable display name like 'acme-finops-cedar'.

    Combines the profile name with a nature word selected deterministically
    from the identity hash.

    Args:
        profile: AWS CLI profile name.
        role_arn: Optional role ARN.
        account_id: Verified STS account ID.

    Returns:
        Human-readable display name.
    """
    message = f"{profile}\n{role_arn or ''}\n{account_id}".encode("utf-8")
    hash_bytes = hmac.new(_NAME_HMAC_KEY, message, hashlib.sha256).digest()
    word = pick_word(hash_bytes)
    # Use the profile as-is for the prefix, truncate if very long
    prefix = profile[:32] if len(profile) > 32 else profile
    return f"{prefix}-{word}"


def auto_onboard(
    profile: str | None,
    role_arn: str | None = None,
) -> OnboardingResult:
    """Perform automatic onboarding or routing for a profile.

    Flow:
    1. Create and verify STS session.
    2. Look up existing workspace in registry.
    3. If found, load and return existing workspace context.
    4. If not found, create new workspace and register it.

    Args:
        profile: AWS CLI profile name (None for default chain).
        role_arn: Optional IAM role ARN to assume.

    Returns:
        OnboardingResult with workspace context and verified session.

    Raises:
        StsVerificationError: If credential verification fails.
        OnboardingError: If workspace creation fails.
    """
    # Step 1: Verify credentials
    verified = create_verified_session(
        profile=profile,
        role_arn=role_arn,
    )

    effective_profile = profile or _get_effective_profile_name()
    account_id = verified.account_id

    # Step 2: Check registry for existing workspace
    existing = lookup_workspace(effective_profile, role_arn, account_id)
    if existing:
        return _load_existing_workspace(existing, verified)

    # Step 3: Create new workspace
    return _create_onboarded_workspace(
        effective_profile, role_arn, account_id, verified
    )


def _get_effective_profile_name() -> str:
    """Get a usable profile name when none was explicitly provided.

    Falls back to AWS_PROFILE env var, then 'default'.
    """
    import os

    return os.environ.get("AWS_PROFILE", "default")


def _load_existing_workspace(
    entry: RegistryEntry,
    verified: VerifiedAwsSession,
) -> OnboardingResult:
    """Load an existing registered workspace.

    Args:
        entry: Registry entry for the workspace.
        verified: Already-verified AWS session.

    Returns:
        OnboardingResult pointing to the existing workspace.

    Raises:
        OnboardingError: If workspace files are missing or corrupt.
    """
    from kulshan.workspace.config import read_workspace_config

    workspace_path = get_workspace_path(entry.workspace_dir)

    if not workspace_path.exists() or not (workspace_path / "workspace.toml").exists():
        # Workspace directory was deleted but registry entry remains.
        # Re-create it.
        logger.info(
            "Registry entry exists for %s but workspace directory missing. "
            "Re-creating workspace.",
            entry.workspace_dir,
        )
        return _create_onboarded_workspace(
            entry.profile,
            entry.role_arn,
            entry.account_id,
            verified,
        )

    config = read_workspace_config(workspace_path)
    context = WorkspaceContext.from_path(workspace_path, config)

    return OnboardingResult(
        workspace_context=context,
        verified_session=verified,
        display_name=entry.display_name,
        is_new=False,
    )


def _create_onboarded_workspace(
    profile: str,
    role_arn: str | None,
    account_id: str,
    verified: VerifiedAwsSession,
) -> OnboardingResult:
    """Create a new auto-onboarded workspace.

    Args:
        profile: AWS CLI profile name.
        role_arn: Optional role ARN.
        account_id: Verified STS account ID.
        verified: Already-verified AWS session.

    Returns:
        OnboardingResult for the new workspace.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Generate stable directory name
    workspace_dir = compute_workspace_dir_name(profile, role_arn, account_id)

    # Generate readable display name
    display_name = generate_display_name(profile, role_arn, account_id)

    # Derive connection name from profile
    conn_name = _sanitize_connection_name(profile)

    # Build workspace config
    connection = AwsConnection(
        name=conn_name,
        profile=profile,
        expected_session_account_id=account_id,
        role_arn=role_arn,
    )

    aws_config = WorkspaceAwsConfig(
        payer_account_id=account_id,  # Use session account as initial payer
        default_connection=conn_name,
        connections=[connection],
    )

    config = WorkspaceConfig(
        name=workspace_dir,
        display_name=display_name,
        created_at=now,
        binding_mode="bound",
        aws=aws_config,
    )

    # Create workspace directory and write config
    workspace_path = get_workspace_path(workspace_dir)
    workspace_path.mkdir(mode=0o700, parents=True, exist_ok=True)

    try:
        write_workspace_config(workspace_path, config)
    except Exception as e:
        # Clean up on failure
        try:
            (workspace_path / "workspace.toml").unlink(missing_ok=True)
            workspace_path.rmdir()
        except OSError:
            pass
        raise OnboardingError(
            f"Failed to write workspace configuration: {e}"
        ) from e

    # Register in the profile registry
    register_workspace(
        profile=profile,
        role_arn=role_arn,
        account_id=account_id,
        workspace_dir=workspace_dir,
        display_name=display_name,
        created_at=now,
    )

    # Build context
    context = WorkspaceContext.from_path(workspace_path, config)

    logger.info(
        "Auto-onboarded workspace '%s' (%s) for profile '%s', account %s",
        display_name,
        workspace_dir,
        profile,
        account_id,
    )

    return OnboardingResult(
        workspace_context=context,
        verified_session=verified,
        display_name=display_name,
        is_new=True,
    )


def _sanitize_connection_name(profile: str) -> str:
    """Convert a profile name to a valid connection name.

    Connection names must match [a-zA-Z0-9][a-zA-Z0-9_-]{0,63}.
    Replace invalid chars with hyphens, truncate to 64 chars.

    Args:
        profile: AWS CLI profile name.

    Returns:
        Sanitized connection name.
    """
    import re

    # Replace invalid characters with hyphens
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", profile)
    # Ensure starts with alphanumeric
    name = re.sub(r"^[^a-zA-Z0-9]+", "", name)
    # Truncate
    name = name[:64] if len(name) > 64 else name
    # Fallback if empty
    if not name:
        name = "primary"
    return name


def bind_payer_account(
    workspace_dir: str,
    payer_account_id: str,
) -> bool:
    """Bind the true payer/management account to a workspace.

    Called when CUR data reveals the actual bill_payer_account_id.
    Updates only the payer_account_id in workspace.toml.

    Does NOT:
    - Rename the workspace or change its display name.
    - Merge workspaces that share the same payer.
    - Change the workspace directory or internal ID.

    Args:
        workspace_dir: Workspace directory name (e.g. 'ws_7f3a842c').
        payer_account_id: The 12-digit payer account ID from CUR.

    Returns:
        True if the binding was applied, False if workspace not found
        or already had a different non-session payer bound.
    """
    from kulshan.workspace.config import read_workspace_config, write_workspace_config
    from kulshan.workspace.validation import validate_account_id

    validate_account_id(payer_account_id, "payer_account_id")

    workspace_path = get_workspace_path(workspace_dir)
    if not workspace_path.exists() or not (workspace_path / "workspace.toml").exists():
        logger.warning("bind_payer_account: workspace %s not found", workspace_dir)
        return False

    config = read_workspace_config(workspace_path)
    if config.aws is None:
        return False

    old_payer = config.aws.payer_account_id
    if old_payer == payer_account_id:
        return True  # Already correct

    # Update the payer account
    config.aws.payer_account_id = payer_account_id
    write_workspace_config(workspace_path, config)

    logger.info(
        "Bound payer account %s to workspace %s (was %s)",
        payer_account_id,
        workspace_dir,
        old_payer,
    )
    return True


class OnboardingError(Exception):
    """Automatic onboarding failed."""

    def __init__(self, message: str):
        super().__init__(message)
