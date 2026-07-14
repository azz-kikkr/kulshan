"""Profile-to-workspace registry.

Maintains a persistent mapping from composite identity keys
(profile + role_arn + STS account_id) to workspace directory names.

Stored at: <data_dir>/registry.toml

The registry is the lookup table that enables automatic routing:
on subsequent runs, Kulshan finds the correct workspace without
requiring --workspace or any manual configuration.
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
# NOT a secret — provides domain separation so the same input triple
# (profile, role_arn, account_id) produces a consistent, unique hash.
# This constant MUST NOT change across versions; doing so would orphan
# existing workspace directories. Stability across restarts is guaranteed
# because the key is compiled into the package, not randomly generated.
_HMAC_KEY = b"kulshan-workspace-id-v1"


@dataclass
class RegistryEntry:
    """A single profile-to-workspace mapping."""

    workspace_dir: str
    profile: str
    role_arn: str | None
    account_id: str
    display_name: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to TOML-compatible dict."""
        d: dict[str, Any] = {
            "workspace_dir": self.workspace_dir,
            "profile": self.profile,
            "account_id": self.account_id,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }
        if self.role_arn:
            d["role_arn"] = self.role_arn
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegistryEntry:
        """Deserialize from TOML dict."""
        return cls(
            workspace_dir=data["workspace_dir"],
            profile=data["profile"],
            role_arn=data.get("role_arn"),
            account_id=data["account_id"],
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at", ""),
        )


def compute_identity_key(
    profile: str,
    role_arn: str | None,
    account_id: str,
) -> str:
    """Compute the composite identity key for registry lookup.

    This is the stable string used as the TOML key in registry.toml.
    It's a truncated HMAC-SHA256 of the concatenated identity fields.

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


def compute_workspace_dir_name(
    profile: str,
    role_arn: str | None,
    account_id: str,
) -> str:
    """Generate the stable workspace directory name.

    Format: ws_<first 8 hex chars of identity key>

    This is the actual directory name under workspaces/.
    It never changes, even if the display name is updated.

    Args:
        profile: AWS CLI profile name.
        role_arn: Optional IAM role ARN.
        account_id: Verified STS account ID.

    Returns:
        Directory name like 'ws_7f3a842c'.
    """
    key = compute_identity_key(profile, role_arn, account_id)
    return f"ws_{key[:8]}"


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
    """Look up a workspace by identity key.

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


def register_workspace(
    profile: str,
    role_arn: str | None,
    account_id: str,
    workspace_dir: str,
    display_name: str,
    created_at: str,
) -> RegistryEntry:
    """Register a new workspace mapping.

    Args:
        profile: AWS CLI profile name.
        role_arn: Optional role ARN.
        account_id: Verified STS account ID.
        workspace_dir: Workspace directory name (ws_<hex>).
        display_name: Human-readable name.
        created_at: ISO timestamp.

    Returns:
        The created RegistryEntry.
    """
    key = compute_identity_key(profile, role_arn, account_id)
    entry = RegistryEntry(
        workspace_dir=workspace_dir,
        profile=profile,
        role_arn=role_arn,
        account_id=account_id,
        display_name=display_name,
        created_at=created_at,
    )

    data = _read_registry()
    if "entries" not in data:
        data["entries"] = {}
    data["entries"][key] = entry.to_dict()
    _write_registry(data)

    return entry


def update_display_name(
    profile: str,
    role_arn: str | None,
    account_id: str,
    new_display_name: str,
) -> bool:
    """Update the display name of a registry entry.

    Args:
        profile: AWS CLI profile name.
        role_arn: Optional role ARN.
        account_id: Verified STS account ID.
        new_display_name: New display name.

    Returns:
        True if entry was found and updated, False otherwise.
    """
    key = compute_identity_key(profile, role_arn, account_id)
    data = _read_registry()
    entries = data.get("entries", {})
    if key not in entries:
        return False

    entries[key]["display_name"] = new_display_name
    _write_registry(data)
    return True


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
