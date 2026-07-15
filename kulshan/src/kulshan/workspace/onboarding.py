"""Automatic AWS environment onboarding.

On first use of an AWS identity, Kulshan:
1. Verifies STS credentials (GetCallerIdentity).
2. Generates a stable workspace ID from STS account + ARN.
3. Creates a readable display name from the principal/role.
4. Creates a bound workspace with a single connection.
5. Registers the mapping for future automatic routing.

On subsequent use, Kulshan looks up the registry by verified identity
and routes to the correct workspace automatically.

Supports both:
- `aws login` → `kulshan report` (no profile, default credentials)
- `kulshan --profile X report` (explicit profile)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
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
    compute_workspace_dir_name_v2,
    find_entry_by_workspace_dir,
    lookup_workspace,
    lookup_workspace_by_identity,
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


def generate_display_name(
    account_id: str,
    arn: str,
    profile: str | None = None,
) -> str:
    """Generate a readable display name from the AWS identity.

    Derives the human-readable prefix from the STS ARN:
    - Role: 'readonly-role' from arn:aws:sts::123:assumed-role/readonly-role/session
    - User: 'admin-user' from arn:aws:iam::123:user/admin-user
    - Federated: 'federated-user' from arn:aws:sts::123:federated-user/name

    Falls back to profile name, then 'aws' as prefix.

    Appends a nature word selected deterministically from a hash.

    Args:
        account_id: Verified STS account ID.
        arn: Verified STS ARN.
        profile: Optional profile name (used as fallback prefix).

    Returns:
        Human-readable display name like 'readonly-role-cedar'.
    """
    prefix = _extract_principal_name(arn)
    if not prefix and profile:
        prefix = profile[:32]
    if not prefix:
        prefix = "aws"

    # Truncate prefix
    prefix = prefix[:32]

    # Deterministic word from identity hash
    message = f"{account_id}\n{arn}".encode("utf-8")
    hash_bytes = hmac.new(_NAME_HMAC_KEY, message, hashlib.sha256).digest()
    word = pick_word(hash_bytes)

    return f"{prefix}-{word}"


def _extract_principal_name(arn: str) -> str:
    """Extract a readable principal name from an STS ARN.

    Examples:
        arn:aws:sts::123456789012:assumed-role/ReadOnlyRole/session → ReadOnlyRole
        arn:aws:iam::123456789012:user/admin → admin
        arn:aws:iam::123456789012:root → root
        arn:aws:sts::123456789012:federated-user/john → federated-john

    Returns:
        A sanitized, lowercase, hyphenated name suitable for display.
        Empty string if parsing fails.
    """
    if not arn or ":" not in arn:
        return ""

    try:
        # ARN format: arn:partition:service:region:account:resource
        parts = arn.split(":")
        if len(parts) < 6:
            return ""
        resource = parts[5]

        # assumed-role/RoleName/SessionName
        if resource.startswith("assumed-role/"):
            segments = resource.split("/")
            if len(segments) >= 2:
                return _sanitize_display_prefix(segments[1])

        # user/UserName or user/path/UserName
        if resource.startswith("user/"):
            segments = resource.split("/")
            return _sanitize_display_prefix(segments[-1])

        # federated-user/Name
        if resource.startswith("federated-user/"):
            segments = resource.split("/")
            if len(segments) >= 2:
                return _sanitize_display_prefix(f"federated-{segments[-1]}")

        # root
        if resource == "root":
            return "root"

        # Fallback: use the whole resource
        return _sanitize_display_prefix(resource)
    except Exception:
        return ""


def _sanitize_display_prefix(name: str) -> str:
    """Sanitize a name for use as a display name prefix.

    Converts to lowercase, replaces non-alphanumeric with hyphens,
    removes leading/trailing hyphens.
    """
    name = name.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    return name[:32] if name else ""


def auto_onboard(
    profile: str | None,
    role_arn: str | None = None,
) -> OnboardingResult:
    """Perform automatic onboarding or routing for an AWS identity.

    Flow:
    1. Create and verify STS session (GetCallerIdentity).
    2. Look up existing workspace by verified identity (v2 key).
    3. Fall back to v1 profile-based lookup for backward compat.
    4. If found, load and return existing workspace context.
    5. If not found, create new workspace and register it.

    Args:
        profile: AWS CLI profile name (None for default credential chain).
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

    account_id = verified.account_id
    arn = verified.arn

    # Step 2: Look up by v2 identity key (account_id + arn)
    existing = lookup_workspace_by_identity(account_id, arn)
    if existing:
        return _load_existing_workspace(existing, verified)

    # Step 3: Fall back to v1 lookup (profile-based) for backward compat
    if profile:
        existing = lookup_workspace(profile, role_arn, account_id)
        if existing:
            return _load_existing_workspace(existing, verified)

    # Step 4: Create new workspace
    return _create_onboarded_workspace(profile, role_arn, account_id, arn, verified)


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
            verified.arn,
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
    profile: str | None,
    role_arn: str | None,
    account_id: str,
    arn: str,
    verified: VerifiedAwsSession,
) -> OnboardingResult:
    """Create a new auto-onboarded workspace.

    Args:
        profile: AWS CLI profile name (may be None).
        role_arn: Optional role ARN.
        account_id: Verified STS account ID.
        arn: Verified STS ARN.
        verified: Already-verified AWS session.

    Returns:
        OnboardingResult for the new workspace.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Generate stable directory name from verified identity
    workspace_dir = compute_workspace_dir_name_v2(account_id, arn)

    # Generate readable display name from ARN
    display_name = generate_display_name(account_id, arn, profile)

    # Derive connection name
    conn_name = _sanitize_connection_name(profile or _extract_principal_name(arn) or "primary")

    # Build workspace config
    connection = AwsConnection(
        name=conn_name,
        profile=profile or "default",
        expected_session_account_id=account_id,
        role_arn=role_arn,
    )

    aws_config = WorkspaceAwsConfig(
        payer_account_id=None,  # Unverified — will be bound when CUR evidence appears
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

    # Register in the identity registry (both v1 and v2 keys)
    register_workspace(
        profile=profile,
        role_arn=role_arn,
        account_id=account_id,
        workspace_dir=workspace_dir,
        display_name=display_name,
        created_at=now,
        arn=arn,
    )

    # Build context
    context = WorkspaceContext.from_path(workspace_path, config)

    logger.info(
        "Auto-onboarded workspace '%s' (%s) for identity %s, account %s",
        display_name,
        workspace_dir,
        arn,
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
    """Bind the true payer/management account to a workspace from CUR evidence.

    Called when CUR data reveals exactly one valid bill_payer_account_id.

    Rules:
    1. If the workspace has no verified payer (None) → bind it, record source.
    2. If already bound to the same payer → return True (no-op).
    3. If bound to a different payer → raise PayerBindingConflictError.
    4. Does NOT rename the workspace or change display name.
    5. Does NOT merge with another workspace.
    6. Does NOT change the ws_<hex> directory.

    Args:
        workspace_dir: Workspace directory name (e.g. 'ws_7f3a842c').
        payer_account_id: The 12-digit payer account ID from CUR.

    Returns:
        True if the binding was applied or already correct.

    Raises:
        PayerBindingConflictError: If workspace is already bound to a different payer.
        PayerBindingError: If workspace not found or has no AWS config.
    """
    from datetime import datetime, timezone

    from kulshan.workspace.config import read_workspace_config, write_workspace_config
    from kulshan.workspace.validation import validate_account_id

    validate_account_id(payer_account_id, "payer_account_id")

    workspace_path = get_workspace_path(workspace_dir)
    if not workspace_path.exists() or not (workspace_path / "workspace.toml").exists():
        raise PayerBindingError(f"Workspace '{workspace_dir}' not found.")

    config = read_workspace_config(workspace_path)
    if config.aws is None:
        raise PayerBindingError(
            f"Workspace '{workspace_dir}' has no AWS configuration."
        )

    old_payer = config.aws.payer_account_id

    # Rule 6: already bound to same payer — no-op
    if old_payer == payer_account_id:
        return True

    # Rule 7: bound to a DIFFERENT verified payer — conflict
    if old_payer is not None:
        raise PayerBindingConflictError(
            workspace_dir=workspace_dir,
            existing_payer=old_payer,
            new_payer=payer_account_id,
        )

    # Rule 1: no payer yet — bind it
    config.aws.payer_account_id = payer_account_id
    config.aws.payer_binding_source = "cur"
    config.aws.payer_bound_at = datetime.now(timezone.utc).isoformat()
    write_workspace_config(workspace_path, config)

    logger.info(
        "Bound payer account %s to workspace %s (source: CUR)",
        payer_account_id,
        workspace_dir,
    )
    return True


class OnboardingError(Exception):
    """Automatic onboarding failed."""

    def __init__(self, message: str):
        super().__init__(message)


class PayerBindingError(Exception):
    """Payer binding operation failed (workspace not found, no AWS config)."""

    def __init__(self, message: str):
        super().__init__(message)


class PayerBindingConflictError(Exception):
    """CUR payer conflicts with an already-bound workspace payer."""

    def __init__(self, workspace_dir: str, existing_payer: str, new_payer: str):
        self.workspace_dir = workspace_dir
        self.existing_payer = existing_payer
        self.new_payer = new_payer
        super().__init__(
            f"Payer conflict for workspace '{workspace_dir}': "
            f"already bound to {existing_payer}, CUR shows {new_payer}."
        )
