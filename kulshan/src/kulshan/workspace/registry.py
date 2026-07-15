"""Identity-based workspace registry.

Maintains a persistent mapping from AWS identity (STS account + ARN)
to workspace directory names.

Stored at: <data_dir>/registry.toml

The registry enables automatic routing: on subsequent runs, Kulshan
finds the correct workspace by verifying STS identity, not by profile
name. This supports the `aws login` → `kulshan report` workflow where
no profile name may be available.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

import tomli_w

from kulshan.workspace.paths import get_data_dir

logger = logging.getLogger(__name__)

# Stable HMAC key for workspace identity derivation.
# NOT a secret — provides domain separation so the same identity
# produces a consistent, unique hash.
# This constant MUST NOT change across versions; doing so would orphan
# existing workspace directories. Stability across restarts is guaranteed
# because the key is compiled into the package, not randomly generated.
_HMAC_KEY = b"kulshan-workspace-id-v1"

# V2 identity key uses account_id + ARN (the STS-verified identity).
_HMAC_KEY_V2 = b"kulshan-workspace-id-v2"


def canonicalize_arn(arn: str) -> str:
    """Normalize an STS ARN to a stable canonical form.

    Assumed-role ARNs contain a volatile session name that changes
    between logins. This function strips the session name and converts
    to the stable IAM role ARN.

    Examples:
        arn:aws:sts::123:assumed-role/ReadOnlyRole/session-123
          → arn:aws:iam::123:role/ReadOnlyRole

        arn:aws:sts::123:assumed-role/team/finops/ReadOnlyRole/session-123
          → arn:aws:iam::123:role/team/finops/ReadOnlyRole

        arn:aws:iam::123:user/alice  → unchanged
        arn:aws:iam::123:root        → unchanged

    Args:
        arn: Raw ARN from STS GetCallerIdentity.

    Returns:
        Canonical ARN suitable for stable identity hashing.
    """
    if not arn:
        return arn

    # Split into arn:partition:service:region:account:resource
    parts = arn.split(":")
    if len(parts) < 6:
        return arn

    partition = parts[1]
    service = parts[2]
    account = parts[4]
    resource = ":".join(parts[5:])  # rejoin in case resource has colons

    # Only assumed-role ARNs need normalization
    if service == "sts" and resource.startswith("assumed-role/"):
        # assumed-role/[path/]RoleName/SessionName
        # Strip the last segment (session name)
        segments = resource.split("/")
        # segments[0] = "assumed-role", segments[1:-1] = role path + name, segments[-1] = session
        if len(segments) >= 3:
            role_path = "/".join(segments[1:-1])  # everything between assumed-role/ and session
            return f"arn:{partition}:iam::{account}:role/{role_path}"

    return arn


@dataclass
class RegistryEntry:
    """A single identity-to-workspace mapping."""

    workspace_dir: str
    profile: str | None
    role_arn: str | None
    account_id: str
    arn: str | None
    display_name: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to TOML-compatible dict."""
        d: dict[str, Any] = {
            "workspace_dir": self.workspace_dir,
            "account_id": self.account_id,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }
        if self.profile:
            d["profile"] = self.profile
        if self.role_arn:
            d["role_arn"] = self.role_arn
        if self.arn:
            d["arn"] = self.arn
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegistryEntry:
        """Deserialize from TOML dict."""
        return cls(
            workspace_dir=data["workspace_dir"],
            profile=data.get("profile"),
            role_arn=data.get("role_arn"),
            account_id=data["account_id"],
            arn=data.get("arn"),
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at", ""),
        )


def compute_identity_key(
    profile: str,
    role_arn: str | None,
    account_id: str,
) -> str:
    """Compute the v1 composite identity key (legacy, profile-based).

    Kept for backward compatibility with existing registries.

    Args:
        profile: AWS CLI profile name.
        role_arn: Optional IAM role ARN (None becomes empty string).
        account_id: Verified STS account ID (12 digits).

    Returns:
        Hex string (first 16 chars of HMAC-SHA256 digest).
    """
    message = f"{profile}\n{role_arn or ''}\n{account_id}".encode("utf-8")
    digest = hmac.new(_HMAC_KEY, message, hashlib.sha256).hexdigest()
    return digest[:16]


def compute_identity_key_v2(
    account_id: str,
    arn: str,
) -> str:
    """Compute the v2 identity key based on verified STS identity.

    The v2 key uses account_id + canonical ARN (the stable AWS identity).
    The ARN is canonicalized to remove volatile session names.

    Args:
        account_id: Verified STS account ID (12 digits).
        arn: Verified STS ARN (raw — will be canonicalized internally).

    Returns:
        Hex string prefixed with "v2_" (16 chars total).
    """
    canonical = canonicalize_arn(arn)
    message = f"{account_id}\n{canonical}".encode("utf-8")
    digest = hmac.new(_HMAC_KEY_V2, message, hashlib.sha256).hexdigest()
    return f"v2_{digest[:13]}"


def compute_workspace_dir_name(
    profile: str,
    role_arn: str | None,
    account_id: str,
) -> str:
    """Generate the stable workspace directory name (v1, legacy).

    Format: ws_<first 8 hex chars of identity key>

    Args:
        profile: AWS CLI profile name.
        role_arn: Optional IAM role ARN.
        account_id: Verified STS account ID.

    Returns:
        Directory name like 'ws_7f3a842c'.
    """
    key = compute_identity_key(profile, role_arn, account_id)
    return f"ws_{key[:8]}"


def compute_workspace_dir_name_v2(
    account_id: str,
    arn: str,
) -> str:
    """Generate the stable workspace directory name from verified identity.

    Format: ws_<first 8 hex chars of v2 identity hash>

    The ARN is canonicalized before hashing to ensure stability
    across different session names.

    Args:
        account_id: Verified STS account ID.
        arn: Verified STS ARN (raw — will be canonicalized).

    Returns:
        Directory name like 'ws_a1b2c3d4'.
    """
    canonical = canonicalize_arn(arn)
    message = f"{account_id}\n{canonical}".encode("utf-8")
    digest = hmac.new(_HMAC_KEY_V2, message, hashlib.sha256).hexdigest()
    return f"ws_{digest[:8]}"


def get_registry_path() -> Path:
    """Return path to the registry file."""
    return get_data_dir() / "registry.toml"


def _read_registry() -> dict[str, Any]:
    """Read the registry file, returning empty dict if missing/corrupt."""
    path = get_registry_path()
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.warning("Failed to read registry.toml: %s", e)
        return {}


def _write_registry(data: dict[str, Any]) -> None:
    """Write registry atomically."""
    path = get_registry_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        suffix=".toml.tmp",
        prefix="registry_",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def lookup_workspace(
    profile: str,
    role_arn: str | None,
    account_id: str,
) -> RegistryEntry | None:
    """Look up a workspace by v1 identity key (legacy profile-based).

    Args:
        profile: AWS CLI profile name.
        role_arn: Optional role ARN.
        account_id: Verified STS account ID.

    Returns:
        RegistryEntry if found, None otherwise.
    """
    key = compute_identity_key(profile, role_arn, account_id)
    data = _read_registry()
    entries = data.get("entries", {})
    if key in entries:
        try:
            return RegistryEntry.from_dict(entries[key])
        except (KeyError, TypeError) as e:
            logger.warning("Corrupt registry entry for key %s: %s", key, e)
            return None
    return None


def lookup_workspace_by_identity(
    account_id: str,
    arn: str,
) -> RegistryEntry | None:
    """Look up a workspace by v2 identity key (STS-verified).

    Lookup order:
    1. Canonical identity key (account_id + canonicalized ARN).
    2. Scan all entries for one whose stored ARN canonicalizes to the
       same principal and account. If found, migrate to canonical key.

    Args:
        account_id: Verified STS account ID.
        arn: Raw STS ARN (from GetCallerIdentity).

    Returns:
        RegistryEntry if found, None otherwise.
    """
    canonical_key = compute_identity_key_v2(account_id, arn)
    data = _read_registry()
    entries = data.get("entries", {})

    # 1. Try canonical key
    if canonical_key in entries:
        try:
            return RegistryEntry.from_dict(entries[canonical_key])
        except (KeyError, TypeError) as e:
            logger.warning("Corrupt registry entry for key %s: %s", canonical_key, e)
            return None

    # 2. Scan for old entries whose stored ARN canonicalizes to the same identity
    target_canonical = canonicalize_arn(arn)
    for key, entry_data in list(entries.items()):
        if key == canonical_key:
            continue
        try:
            entry = RegistryEntry.from_dict(entry_data)
        except (KeyError, TypeError):
            continue

        if entry.account_id != account_id:
            continue

        # Check if the stored ARN canonicalizes to the same principal
        if entry.arn and canonicalize_arn(entry.arn) == target_canonical:
            # Migrate: register under canonical key, remove old key
            entries[canonical_key] = entries.pop(key)
            _write_registry(data)
            logger.info(
                "Migrated registry entry from raw-ARN key %s to canonical key %s "
                "for workspace %s",
                key, canonical_key, entry.workspace_dir,
            )
            return entry

    return None


def register_workspace(
    profile: str | None,
    role_arn: str | None,
    account_id: str,
    workspace_dir: str,
    display_name: str,
    created_at: str,
    arn: str | None = None,
) -> RegistryEntry:
    """Register a new workspace mapping.

    Registers under the v2 identity key (account_id + arn) when arn is
    provided. Also registers a v1 key (profile-based) for backward
    compatibility when a profile is available.

    Args:
        profile: AWS CLI profile name (may be None for default chain).
        role_arn: Optional role ARN.
        account_id: Verified STS account ID.
        workspace_dir: Workspace directory name (ws_<hex>).
        display_name: Human-readable name.
        created_at: ISO timestamp.
        arn: Verified STS ARN (for v2 identity key).

    Returns:
        The created RegistryEntry.
    """
    entry = RegistryEntry(
        workspace_dir=workspace_dir,
        profile=profile,
        role_arn=role_arn,
        account_id=account_id,
        arn=arn,
        display_name=display_name,
        created_at=created_at,
    )

    data = _read_registry()
    if "entries" not in data:
        data["entries"] = {}

    entry_dict = entry.to_dict()

    # Register under v2 key (primary, identity-based)
    if arn:
        v2_key = compute_identity_key_v2(account_id, arn)
        data["entries"][v2_key] = entry_dict

    # Also register under v1 key for backward compatibility
    if profile:
        v1_key = compute_identity_key(profile, role_arn, account_id)
        data["entries"][v1_key] = entry_dict

    _write_registry(data)
    return entry


def update_display_name(
    profile: str | None,
    role_arn: str | None,
    account_id: str,
    new_display_name: str,
    arn: str | None = None,
) -> bool:
    """Update the display name of a registry entry.

    Tries v2 key first (if arn provided), then v1 key (if profile provided).

    Args:
        profile: AWS CLI profile name (may be None).
        role_arn: Optional role ARN.
        account_id: Verified STS account ID.
        new_display_name: New display name.
        arn: Verified STS ARN (for v2 lookup).

    Returns:
        True if entry was found and updated, False otherwise.
    """
    data = _read_registry()
    entries = data.get("entries", {})
    updated = False

    # Try v2 key
    if arn:
        v2_key = compute_identity_key_v2(account_id, arn)
        if v2_key in entries:
            entries[v2_key]["display_name"] = new_display_name
            updated = True

    # Try v1 key
    if profile:
        v1_key = compute_identity_key(profile, role_arn, account_id)
        if v1_key in entries:
            entries[v1_key]["display_name"] = new_display_name
            updated = True

    if updated:
        _write_registry(data)
    return updated


def list_registry_entries() -> list[RegistryEntry]:
    """List all registry entries.

    Returns:
        List of all registered workspace mappings.
    """
    data = _read_registry()
    entries = data.get("entries", {})
    result = []
    for key, entry_data in entries.items():
        try:
            result.append(RegistryEntry.from_dict(entry_data))
        except (KeyError, TypeError) as e:
            logger.warning("Skipping corrupt registry entry %s: %s", key, e)
    return result


def find_entry_by_workspace_dir(workspace_dir: str) -> RegistryEntry | None:
    """Find a registry entry by workspace directory name.

    Args:
        workspace_dir: The workspace directory name (e.g. 'ws_7f3a842c').

    Returns:
        RegistryEntry if found, None otherwise.
    """
    data = _read_registry()
    entries = data.get("entries", {})
    for entry_data in entries.values():
        try:
            entry = RegistryEntry.from_dict(entry_data)
            if entry.workspace_dir == workspace_dir:
                return entry
        except (KeyError, TypeError):
            continue
    return None
