"""Tests for automatic AWS environment onboarding and routing.

Tests cover:
- Workspace ID generation (deterministic, stable)
- Readable display name generation
- Registry CRUD operations
- Profile-aware resolution priority
- Auto-onboard flow with mocked STS
- Workspace rename command
- Registry lookup and routing on subsequent runs
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    read_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.context import WorkspaceContext
from kulshan.workspace.onboarding import (
    OnboardingError,
    OnboardingResult,
    auto_onboard,
    generate_display_name,
    _sanitize_connection_name,
)
from kulshan.workspace.registry import (
    RegistryEntry,
    compute_identity_key,
    compute_workspace_dir_name,
    find_entry_by_workspace_dir,
    get_registry_path,
    list_registry_entries,
    lookup_workspace,
    register_workspace,
    update_display_name,
    _read_registry,
    _write_registry,
)
from kulshan.workspace.resolution import (
    resolve_workspace_with_profile,
    _reset_migration_guard,
)
from kulshan.workspace.sts import StsVerificationError, VerifiedAwsSession
from kulshan.workspace.wordlist import WORDS, pick_word


# ---------------------------------------------------------------------------
# Wordlist Tests
# ---------------------------------------------------------------------------


class TestWordlist:
    """Tests for the nature word picker."""

    def test_word_count(self):
        assert len(WORDS) == 64

    def test_all_words_are_strings(self):
        for word in WORDS:
            assert isinstance(word, str)
            assert len(word) >= 3
            assert len(word) <= 7

    def test_pick_word_deterministic(self):
        """Same hash bytes always produce the same word."""
        h = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        assert pick_word(h) == pick_word(h)

    def test_pick_word_different_inputs(self):
        """Different hash bytes usually produce different words."""
        w1 = pick_word(b"\x00\x00\x00\x00")
        w2 = pick_word(b"\xff\xff\xff\xff")
        # Could theoretically collide but unlikely with 64 words
        assert w1 in WORDS
        assert w2 in WORDS

    def test_pick_word_wraps_around(self):
        """Index wraps around word list length."""
        # Any 4 bytes should produce a valid word
        import struct
        for i in range(0, 256, 37):
            h = struct.pack(">I", i * 1000000)
            word = pick_word(h)
            assert word in WORDS


# ---------------------------------------------------------------------------
# Identity Key / Workspace Dir Tests
# ---------------------------------------------------------------------------


class TestIdentityKey:
    """Tests for the HMAC-based identity key generation."""

    def test_deterministic(self):
        """Same inputs always produce the same key."""
        k1 = compute_identity_key("my-profile", None, "123456789012")
        k2 = compute_identity_key("my-profile", None, "123456789012")
        assert k1 == k2

    def test_different_profiles(self):
        """Different profiles produce different keys."""
        k1 = compute_identity_key("profile-a", None, "123456789012")
        k2 = compute_identity_key("profile-b", None, "123456789012")
        assert k1 != k2

    def test_different_accounts(self):
        """Different account IDs produce different keys."""
        k1 = compute_identity_key("my-profile", None, "111111111111")
        k2 = compute_identity_key("my-profile", None, "222222222222")
        assert k1 != k2

    def test_role_arn_matters(self):
        """Presence of role ARN changes the key."""
        k1 = compute_identity_key("my-profile", None, "123456789012")
        k2 = compute_identity_key(
            "my-profile",
            "arn:aws:iam::123456789012:role/admin",
            "123456789012",
        )
        assert k1 != k2

    def test_key_is_hex_16_chars(self):
        """Identity key is exactly 16 hex characters."""
        k = compute_identity_key("test", None, "000000000000")
        assert len(k) == 16
        assert all(c in "0123456789abcdef" for c in k)


class TestWorkspaceDirName:
    """Tests for workspace directory name generation."""

    def test_format(self):
        """Directory name starts with ws_ and has 8 hex chars."""
        name = compute_workspace_dir_name("acme-finops", None, "999999999999")
        assert name.startswith("ws_")
        assert len(name) == 11  # "ws_" + 8 hex chars
        assert all(c in "0123456789abcdef" for c in name[3:])

    def test_deterministic(self):
        """Same inputs always produce the same directory name."""
        n1 = compute_workspace_dir_name("prod", None, "123456789012")
        n2 = compute_workspace_dir_name("prod", None, "123456789012")
        assert n1 == n2

    def test_passes_workspace_validation(self):
        """Generated names pass workspace name validation."""
        from kulshan.workspace.validation import validate_workspace_name

        name = compute_workspace_dir_name("test-profile", None, "123456789012")
        # Should not raise
        validate_workspace_name(name, allow_default=True)


# ---------------------------------------------------------------------------
# Display Name Generation Tests
# ---------------------------------------------------------------------------


class TestDisplayNameGeneration:
    """Tests for readable display name generation."""

    def test_format(self):
        """Display name is 'profile-word'."""
        name = generate_display_name("acme-finops", None, "999999999999")
        parts = name.rsplit("-", 1)
        assert len(parts) == 2
        assert parts[0] == "acme-finops"
        assert parts[1] in WORDS

    def test_deterministic(self):
        """Same inputs always produce the same display name."""
        n1 = generate_display_name("prod", None, "123456789012")
        n2 = generate_display_name("prod", None, "123456789012")
        assert n1 == n2

    def test_truncates_long_profile(self):
        """Profiles longer than 32 chars are truncated."""
        long_profile = "a" * 50
        name = generate_display_name(long_profile, None, "123456789012")
        prefix = name.rsplit("-", 1)[0]
        assert len(prefix) <= 32

    def test_role_arn_changes_word(self):
        """Different role ARN produces different word (usually)."""
        n1 = generate_display_name("prof", None, "123456789012")
        n2 = generate_display_name(
            "prof",
            "arn:aws:iam::123456789012:role/admin",
            "123456789012",
        )
        # Different inputs — names should differ
        # (could collide but extremely unlikely)
        assert n1 != n2


# ---------------------------------------------------------------------------
# Connection Name Sanitization Tests
# ---------------------------------------------------------------------------


class TestSanitizeConnectionName:
    """Tests for profile-to-connection name sanitization."""

    def test_simple_profile(self):
        assert _sanitize_connection_name("my-profile") == "my-profile"

    def test_profile_with_slashes(self):
        name = _sanitize_connection_name("org/my-profile")
        assert "/" not in name
        assert name == "org-my-profile"

    def test_profile_with_spaces(self):
        name = _sanitize_connection_name("my profile")
        assert " " not in name

    def test_empty_profile_fallback(self):
        assert _sanitize_connection_name("///") == "primary"

    def test_truncates_long_name(self):
        long_name = "a" * 100
        name = _sanitize_connection_name(long_name)
        assert len(name) <= 64


# ---------------------------------------------------------------------------
# Registry Tests
# ---------------------------------------------------------------------------


class TestRegistry:
    """Tests for the profile-to-workspace registry."""

    @pytest.fixture(autouse=True)
    def _isolate_registry(self, tmp_path, monkeypatch):
        """Point registry to a temp directory."""
        monkeypatch.setattr(
            "kulshan.workspace.registry.get_data_dir",
            lambda: tmp_path,
        )

    def test_empty_registry(self):
        """Empty registry returns no entries."""
        assert list_registry_entries() == []

    def test_register_and_lookup(self):
        """Register a workspace then look it up."""
        entry = register_workspace(
            profile="acme",
            role_arn=None,
            account_id="123456789012",
            workspace_dir="ws_abcd1234",
            display_name="acme-cedar",
            created_at="2025-01-01T00:00:00Z",
        )
        assert entry.workspace_dir == "ws_abcd1234"
        assert entry.display_name == "acme-cedar"

        found = lookup_workspace("acme", None, "123456789012")
        assert found is not None
        assert found.workspace_dir == "ws_abcd1234"
        assert found.profile == "acme"
        assert found.account_id == "123456789012"

    def test_lookup_not_found(self):
        """Lookup returns None for unregistered profile."""
        assert lookup_workspace("nonexistent", None, "000000000000") is None

    def test_update_display_name(self):
        """Display name can be updated."""
        register_workspace(
            profile="test",
            role_arn=None,
            account_id="111111111111",
            workspace_dir="ws_11111111",
            display_name="test-oak",
            created_at="2025-01-01T00:00:00Z",
        )
        result = update_display_name("test", None, "111111111111", "Test Corp")
        assert result is True

        found = lookup_workspace("test", None, "111111111111")
        assert found.display_name == "Test Corp"

    def test_update_display_name_not_found(self):
        """Update returns False for missing entry."""
        result = update_display_name("ghost", None, "000000000000", "X")
        assert result is False

    def test_find_by_workspace_dir(self):
        """Find registry entry by workspace directory name."""
        register_workspace(
            profile="prod",
            role_arn=None,
            account_id="222222222222",
            workspace_dir="ws_22222222",
            display_name="prod-fern",
            created_at="2025-01-01T00:00:00Z",
        )
        entry = find_entry_by_workspace_dir("ws_22222222")
        assert entry is not None
        assert entry.profile == "prod"

    def test_find_by_workspace_dir_not_found(self):
        """Find returns None for unknown directory."""
        assert find_entry_by_workspace_dir("ws_nonexist") is None

    def test_multiple_entries(self):
        """Multiple profiles can be registered."""
        register_workspace("a", None, "111111111111", "ws_a", "a-oak", "2025-01-01T00:00:00Z")
        register_workspace("b", None, "222222222222", "ws_b", "b-pine", "2025-01-01T00:00:00Z")

        entries = list_registry_entries()
        assert len(entries) == 2
        dirs = {e.workspace_dir for e in entries}
        assert dirs == {"ws_a", "ws_b"}

    def test_role_arn_stored(self):
        """Role ARN is stored and retrievable."""
        register_workspace(
            profile="cross",
            role_arn="arn:aws:iam::333333333333:role/audit",
            account_id="333333333333",
            workspace_dir="ws_33333333",
            display_name="cross-maple",
            created_at="2025-01-01T00:00:00Z",
        )
        found = lookup_workspace(
            "cross",
            "arn:aws:iam::333333333333:role/audit",
            "333333333333",
        )
        assert found is not None
        assert found.role_arn == "arn:aws:iam::333333333333:role/audit"

    def test_corrupt_registry_handled(self, tmp_path):
        """Corrupt registry file returns empty results."""
        reg_path = tmp_path / "registry.toml"
        reg_path.write_text("this is not valid toml {{{{")
        assert list_registry_entries() == []


# ---------------------------------------------------------------------------
# Auto-Onboard Tests
# ---------------------------------------------------------------------------


def _mock_verified_session(account_id="999999999999", profile="acme"):
    """Create a mock VerifiedAwsSession."""
    return VerifiedAwsSession(
        session=MagicMock(),
        account_id=account_id,
        arn=f"arn:aws:iam::{account_id}:user/test",
        user_id="AIDAEXAMPLE",
        resolved_profile=profile,
        role_arn=None,
    )


class TestAutoOnboard:
    """Tests for the auto_onboard function."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        """Isolate all filesystem operations to tmp_path."""
        self.tmp_path = tmp_path
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()

        monkeypatch.setattr(
            "kulshan.workspace.registry.get_data_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: self.workspaces_root / name,
        )
        monkeypatch.setattr(
            "kulshan.workspace.paths.get_workspaces_root",
            lambda: self.workspaces_root,
        )

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_first_run_creates_workspace(self, mock_sts):
        """First run with a profile creates a new workspace."""
        mock_sts.return_value = _mock_verified_session()

        result = auto_onboard(profile="acme")

        assert result.is_new is True
        assert result.account_id == "999999999999"
        assert "acme" in result.display_name
        assert result.workspace_context.is_bound

        # Workspace directory was created
        ws_dir = result.workspace_context.path
        assert ws_dir.exists()
        assert (ws_dir / "workspace.toml").exists()

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_second_run_reuses_workspace(self, mock_sts):
        """Second run finds existing workspace via registry."""
        mock_sts.return_value = _mock_verified_session()

        # First run — creates
        r1 = auto_onboard(profile="acme")
        assert r1.is_new is True

        # Second run — reuses
        r2 = auto_onboard(profile="acme")
        assert r2.is_new is False
        assert r2.workspace_context.path == r1.workspace_context.path
        assert r2.display_name == r1.display_name

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_different_profiles_get_different_workspaces(self, mock_sts):
        """Different profiles create separate workspaces."""
        mock_sts.side_effect = [
            _mock_verified_session(account_id="111111111111", profile="alpha"),
            _mock_verified_session(account_id="222222222222", profile="beta"),
        ]

        r1 = auto_onboard(profile="alpha")
        r2 = auto_onboard(profile="beta")

        assert r1.workspace_context.path != r2.workspace_context.path
        assert r1.display_name != r2.display_name

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_sts_failure_propagates(self, mock_sts):
        """STS verification failure is raised to caller."""
        mock_sts.side_effect = StsVerificationError("Expired session")

        with pytest.raises(StsVerificationError, match="Expired session"):
            auto_onboard(profile="bad-profile")

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_workspace_config_is_valid(self, mock_sts):
        """Created workspace has valid bound configuration."""
        mock_sts.return_value = _mock_verified_session(
            account_id="444444444444", profile="my-prod"
        )

        result = auto_onboard(profile="my-prod")
        config = result.workspace_context.config

        assert config.binding_mode == "bound"
        assert config.aws is not None
        assert config.aws.payer_account_id == "444444444444"
        assert len(config.aws.connections) == 1
        assert config.aws.connections[0].profile == "my-prod"
        assert config.aws.connections[0].expected_session_account_id == "444444444444"

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_deleted_workspace_recreated(self, mock_sts):
        """If workspace dir is deleted but registry remains, recreate it."""
        mock_sts.return_value = _mock_verified_session()

        # Create workspace
        r1 = auto_onboard(profile="acme")
        ws_path = r1.workspace_context.path

        # Delete workspace directory (simulate accidental deletion)
        import shutil
        shutil.rmtree(ws_path)

        # Second run should recreate (treated as new since dir was gone)
        r2 = auto_onboard(profile="acme")
        assert r2.is_new is True  # directory was re-created
        assert r2.workspace_context.path.exists()

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_role_arn_included_in_workspace(self, mock_sts):
        """Role ARN is stored in the workspace connection."""
        mock_sts.return_value = VerifiedAwsSession(
            session=MagicMock(),
            account_id="555555555555",
            arn="arn:aws:sts::555555555555:assumed-role/audit/kulshan-exec",
            user_id="AROAEXAMPLE:kulshan-exec",
            resolved_profile="cross-account",
            role_arn="arn:aws:iam::555555555555:role/audit",
        )

        result = auto_onboard(
            profile="cross-account",
            role_arn="arn:aws:iam::555555555555:role/audit",
        )
        conn = result.workspace_context.config.aws.connections[0]
        assert conn.role_arn == "arn:aws:iam::555555555555:role/audit"


# ---------------------------------------------------------------------------
# Resolution with Profile Tests
# ---------------------------------------------------------------------------


class TestResolveWorkspaceWithProfile:
    """Tests for resolve_workspace_with_profile()."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        """Isolate filesystem to tmp_path."""
        self.tmp_path = tmp_path
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()
        self.config_dir = tmp_path / "config"
        self.config_dir.mkdir()

        monkeypatch.setattr(
            "kulshan.workspace.paths.get_workspaces_root",
            lambda: self.workspaces_root,
        )
        monkeypatch.setattr(
            "kulshan.workspace.paths.get_data_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "kulshan.workspace.paths.get_config_dir",
            lambda: self.config_dir,
        )
        monkeypatch.setattr(
            "kulshan.workspace.registry.get_data_dir",
            lambda: tmp_path,
        )
        # Reset migration guard
        _reset_migration_guard()

        # Patch migration to be a no-op
        monkeypatch.setattr(
            "kulshan.workspace.resolution.ensure_workspace_infrastructure",
            lambda: None,
        )

        # Remove env vars that could interfere
        monkeypatch.delenv("KULSHAN_WORKSPACE", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)

    def _create_workspace(self, name, display_name=None, profile=None):
        """Helper to create a workspace directory."""
        ws_path = self.workspaces_root / name
        ws_path.mkdir(parents=True, exist_ok=True)

        if profile:
            conn = AwsConnection(
                name=profile,
                profile=profile,
                expected_session_account_id="123456789012",
            )
            aws_config = WorkspaceAwsConfig(
                payer_account_id="123456789012",
                default_connection=profile,
                connections=[conn],
            )
            config = WorkspaceConfig(
                name=name,
                display_name=display_name or name,
                binding_mode="bound",
                aws=aws_config,
            )
        else:
            config = WorkspaceConfig(
                name=name if name == "default" else name,
                display_name=display_name,
                binding_mode="unbound" if name == "default" else "bound",
                aws=None if name == "default" else WorkspaceAwsConfig(
                    payer_account_id="123456789012",
                    default_connection="main",
                    connections=[AwsConnection(
                        name="main", profile="test", expected_session_account_id="123456789012"
                    )],
                ),
            )
        write_workspace_config(ws_path, config)
        return ws_path

    def test_returns_none_when_no_workspaces(self):
        """Returns None when nothing is configured."""
        result = resolve_workspace_with_profile(profile="acme")
        assert result is None

    def test_registry_lookup_by_profile(self):
        """Finds workspace via registry when profile matches."""
        # Create workspace and register it
        self._create_workspace("ws_aabbccdd", profile="acme")
        register_workspace(
            profile="acme",
            role_arn=None,
            account_id="123456789012",
            workspace_dir="ws_aabbccdd",
            display_name="acme-cedar",
            created_at="2025-01-01T00:00:00Z",
        )

        result = resolve_workspace_with_profile(profile="acme")
        assert result is not None
        assert result.name == "ws_aabbccdd"

    def test_single_registered_workspace_used_when_no_profile(self):
        """When no profile given but exactly one workspace registered, use it."""
        self._create_workspace("ws_11223344", profile="only-one")
        register_workspace(
            profile="only-one",
            role_arn=None,
            account_id="123456789012",
            workspace_dir="ws_11223344",
            display_name="only-one-oak",
            created_at="2025-01-01T00:00:00Z",
        )

        result = resolve_workspace_with_profile(profile=None)
        assert result is not None
        assert result.name == "ws_11223344"

    def test_returns_none_with_multiple_and_no_profile(self):
        """Returns None when multiple workspaces and no profile given."""
        self._create_workspace("ws_aaaaaaaa", profile="alpha")
        self._create_workspace("ws_bbbbbbbb", profile="beta")
        register_workspace("alpha", None, "111111111111", "ws_aaaaaaaa", "a", "2025-01-01T00:00:00Z")
        register_workspace("beta", None, "222222222222", "ws_bbbbbbbb", "b", "2025-01-01T00:00:00Z")

        result = resolve_workspace_with_profile(profile=None)
        assert result is None

    def test_explicit_workspace_overrides_all(self, monkeypatch):
        """--workspace always uses the named workspace."""
        self._create_workspace("default")
        self._create_workspace("ws_explicit", profile="expl")
        register_workspace("expl", None, "123456789012", "ws_explicit", "expl-fern", "2025-01-01T00:00:00Z")

        # Even with a different profile, explicit workspace wins
        result = resolve_workspace_with_profile(
            workspace_name="ws_explicit",
            profile="something-else",
        )
        assert result is not None
        assert result.name == "ws_explicit"


# ---------------------------------------------------------------------------
# Workspace Rename CLI Tests
# ---------------------------------------------------------------------------


class TestWorkspaceRenameCli:
    """Tests for the workspace rename command."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        """Isolate filesystem."""
        self.tmp_path = tmp_path
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()

        monkeypatch.setattr(
            "kulshan.workspace.paths.get_workspaces_root",
            lambda: self.workspaces_root,
        )
        monkeypatch.setattr(
            "kulshan.workspace.paths.get_data_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "kulshan.workspace.paths.get_config_dir",
            lambda: tmp_path / "config",
        )
        monkeypatch.setattr(
            "kulshan.workspace.registry.get_data_dir",
            lambda: tmp_path,
        )
        _reset_migration_guard()
        monkeypatch.setattr(
            "kulshan.workspace.resolution.ensure_workspace_infrastructure",
            lambda: None,
        )

    def _create_bound_workspace(self, name, display_name=None):
        """Helper to create a bound workspace."""
        ws_path = self.workspaces_root / name
        ws_path.mkdir(parents=True, exist_ok=True)
        config = WorkspaceConfig(
            name=name,
            display_name=display_name or name,
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="123456789012",
                default_connection="main",
                connections=[AwsConnection(
                    name="main",
                    profile="test",
                    expected_session_account_id="123456789012",
                )],
            ),
        )
        write_workspace_config(ws_path, config)
        return ws_path

    def test_rename_by_directory_name(self):
        """Rename using the workspace directory name."""
        from click.testing import CliRunner
        from kulshan.workspace.cli import workspace

        self._create_bound_workspace("ws_aabbccdd", "old-name")

        runner = CliRunner()
        result = runner.invoke(workspace, ["rename", "ws_aabbccdd", "Acme Corp"])
        assert result.exit_code == 0
        assert "Acme Corp" in result.output

        # Verify config was updated
        config = read_workspace_config(self.workspaces_root / "ws_aabbccdd")
        assert config.display_name == "Acme Corp"

    def test_rename_updates_registry(self):
        """Rename also updates the registry entry."""
        from click.testing import CliRunner
        from kulshan.workspace.cli import workspace

        self._create_bound_workspace("ws_11223344", "prod-oak")
        register_workspace(
            "prod", None, "123456789012",
            "ws_11223344", "prod-oak", "2025-01-01T00:00:00Z",
        )

        runner = CliRunner()
        result = runner.invoke(workspace, ["rename", "ws_11223344", "Production"])
        assert result.exit_code == 0

        # Registry should also be updated
        entry = find_entry_by_workspace_dir("ws_11223344")
        assert entry.display_name == "Production"

    def test_rename_nonexistent_workspace(self):
        """Rename fails for non-existent workspace."""
        from click.testing import CliRunner
        from kulshan.workspace.cli import workspace

        runner = CliRunner()
        result = runner.invoke(workspace, ["rename", "ws_nonexist", "New Name"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_rename_empty_name_rejected(self):
        """Empty display name is rejected."""
        from click.testing import CliRunner
        from kulshan.workspace.cli import workspace

        self._create_bound_workspace("ws_aabbccdd")

        runner = CliRunner()
        result = runner.invoke(workspace, ["rename", "ws_aabbccdd", "   "])
        assert result.exit_code != 0
        assert "empty" in result.output.lower()


# ---------------------------------------------------------------------------
# Routing Priority Tests (required by spec)
# ---------------------------------------------------------------------------


class TestRoutingPriority:
    """Tests that verify the correct routing priority.

    Priority order:
    1. --workspace
    2. KULSHAN_WORKSPACE
    3. --profile / AWS_PROFILE → registry match → auto-create
    4. Active workspace (only when no profile)
    5. Single configured workspace (only when no profile)
    6. Unbound default fallback
    """

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        """Isolate filesystem to tmp_path."""
        self.tmp_path = tmp_path
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()
        self.config_dir = tmp_path / "config"
        self.config_dir.mkdir()

        monkeypatch.setattr(
            "kulshan.workspace.paths.get_workspaces_root",
            lambda: self.workspaces_root,
        )
        monkeypatch.setattr(
            "kulshan.workspace.paths.get_data_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "kulshan.workspace.paths.get_config_dir",
            lambda: self.config_dir,
        )
        monkeypatch.setattr(
            "kulshan.workspace.registry.get_data_dir",
            lambda: tmp_path,
        )
        _reset_migration_guard()
        monkeypatch.setattr(
            "kulshan.workspace.resolution.ensure_workspace_infrastructure",
            lambda: None,
        )
        monkeypatch.delenv("KULSHAN_WORKSPACE", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)

    def _create_workspace(self, name, profile, account_id="123456789012"):
        """Create a bound workspace and register it."""
        ws_path = self.workspaces_root / name
        ws_path.mkdir(parents=True, exist_ok=True)
        config = WorkspaceConfig(
            name=name,
            display_name=f"{profile}-oak",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id=account_id,
                default_connection=profile,
                connections=[AwsConnection(
                    name=profile,
                    profile=profile,
                    expected_session_account_id=account_id,
                )],
            ),
        )
        write_workspace_config(ws_path, config)
        return ws_path

    def _set_active(self, name):
        """Set active workspace in config.toml."""
        from kulshan.workspace.resolution import set_active_workspace_name
        set_active_workspace_name(name)

    def test_active_customer_a_plus_aws_profile_customer_b_routes_to_b(self, monkeypatch):
        """Active workspace customer-a, AWS_PROFILE=customer-b → routes to customer-b.

        Profile ALWAYS takes priority over active workspace.
        """
        # Set up customer-a as active
        self._create_workspace("ws_aaaa1111", "customer-a", "111111111111")
        register_workspace(
            "customer-a", None, "111111111111",
            "ws_aaaa1111", "customer-a-oak", "2025-01-01T00:00:00Z",
        )
        self._set_active("ws_aaaa1111")

        # Set up customer-b in registry
        self._create_workspace("ws_bbbb2222", "customer-b", "222222222222")
        register_workspace(
            "customer-b", None, "222222222222",
            "ws_bbbb2222", "customer-b-pine", "2025-01-01T00:00:00Z",
        )

        # Simulate AWS_PROFILE=customer-b
        monkeypatch.setenv("AWS_PROFILE", "customer-b")

        # Profile takes priority — routes to customer-b's workspace
        result = resolve_workspace_with_profile(profile=None, role_arn=None)
        assert result is not None
        assert result.name == "ws_bbbb2222"

    def test_unknown_profile_with_one_existing_workspace_creates_new(self):
        """Unknown profile with one existing workspace → returns None (auto-create).

        An unknown profile must never be routed into the existing workspace.
        """
        # Set up an existing workspace for customer-a
        self._create_workspace("ws_aaaa1111", "customer-a", "111111111111")
        register_workspace(
            "customer-a", None, "111111111111",
            "ws_aaaa1111", "customer-a-oak", "2025-01-01T00:00:00Z",
        )

        # Unknown profile "new-client" → should NOT route to customer-a
        result = resolve_workspace_with_profile(profile="new-client", role_arn=None)
        assert result is None  # Signals auto-create

    def test_same_profile_with_two_roles_remains_separate(self):
        """Same profile with different roles → separate workspaces.

        The identity key includes role_arn, so different roles produce
        different workspace IDs.
        """
        role_a = "arn:aws:iam::111111111111:role/admin"
        role_b = "arn:aws:iam::111111111111:role/readonly"

        # Register workspace for role A
        self._create_workspace("ws_role_a", "shared-profile", "111111111111")
        register_workspace(
            "shared-profile", role_a, "111111111111",
            "ws_role_a", "shared-admin", "2025-01-01T00:00:00Z",
        )

        # Register workspace for role B
        self._create_workspace("ws_role_b", "shared-profile", "111111111111")
        register_workspace(
            "shared-profile", role_b, "111111111111",
            "ws_role_b", "shared-readonly", "2025-01-01T00:00:00Z",
        )

        # Resolve with role A → gets workspace A
        result_a = resolve_workspace_with_profile(
            profile="shared-profile", role_arn=role_a,
        )
        assert result_a is not None
        assert result_a.name == "ws_role_a"

        # Resolve with role B → gets workspace B
        result_b = resolve_workspace_with_profile(
            profile="shared-profile", role_arn=role_b,
        )
        assert result_b is not None
        assert result_b.name == "ws_role_b"

    def test_profile_account_changes_cannot_reuse_old_database(self):
        """If a profile now returns a different STS account, it cannot reuse old workspace.

        The identity key includes account_id, so a changed account produces
        a different key and does NOT match the old registry entry.
        """
        # Register workspace for profile "acme" with account 111...
        self._create_workspace("ws_old", "acme", "111111111111")
        register_workspace(
            "acme", None, "111111111111",
            "ws_old", "acme-oak", "2025-01-01T00:00:00Z",
        )

        # Now "acme" profile returns account 222... (different account)
        # The registry lookup by profile alone would find ws_old,
        # but the identity key (profile+role+account) won't match.
        # resolve_workspace_with_profile does a pre-STS lookup by profile
        # name. The auto_onboard function will verify via STS and create
        # a new workspace since the full identity key differs.

        # Verify that looking up with a different account won't find old entry
        found = lookup_workspace("acme", None, "222222222222")
        assert found is None  # Different account → no match

        # And the original is still there for the original account
        found_original = lookup_workspace("acme", None, "111111111111")
        assert found_original is not None
        assert found_original.workspace_dir == "ws_old"

    def test_no_profile_allows_active_workspace_fallback(self):
        """No profile supplied → active workspace is used.

        The active workspace fallback ONLY applies when no profile is supplied.
        """
        self._create_workspace("ws_active", "some-profile", "111111111111")
        register_workspace(
            "some-profile", None, "111111111111",
            "ws_active", "some-oak", "2025-01-01T00:00:00Z",
        )
        self._set_active("ws_active")

        # No profile → falls back to active workspace
        result = resolve_workspace_with_profile(profile=None, role_arn=None)
        assert result is not None
        assert result.name == "ws_active"


# ---------------------------------------------------------------------------
# Design Invariant Tests
# ---------------------------------------------------------------------------


class TestDesignInvariants:
    """Tests confirming design invariants hold."""

    def test_display_name_is_not_database_identity(self, tmp_path, monkeypatch):
        """The display name is never used as a path component.

        The ws_<hex> directory name is the sole database identity.
        """
        monkeypatch.setattr(
            "kulshan.workspace.registry.get_data_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: tmp_path / "workspaces" / name,
        )
        (tmp_path / "workspaces").mkdir()

        with patch("kulshan.workspace.onboarding.create_verified_session") as mock_sts:
            mock_sts.return_value = _mock_verified_session(
                account_id="123456789012", profile="my-profile"
            )
            result = auto_onboard(profile="my-profile")

        # Directory name is ws_<hex>, NOT the display name
        dir_name = result.workspace_context.path.name
        assert dir_name.startswith("ws_")
        assert result.display_name not in dir_name

    def test_duplicate_display_names_have_unique_directories(self, tmp_path, monkeypatch):
        """Two profiles that happen to generate the same display name
        still get unique workspace directories.

        Directory uniqueness comes from the identity key (profile + role + account),
        not from the display name.
        """
        monkeypatch.setattr(
            "kulshan.workspace.registry.get_data_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: tmp_path / "workspaces" / name,
        )
        (tmp_path / "workspaces").mkdir()

        with patch("kulshan.workspace.onboarding.create_verified_session") as mock_sts:
            mock_sts.return_value = _mock_verified_session(
                account_id="111111111111", profile="alpha"
            )
            r1 = auto_onboard(profile="alpha")

            mock_sts.return_value = _mock_verified_session(
                account_id="222222222222", profile="beta"
            )
            r2 = auto_onboard(profile="beta")

        # Even if display names collided, directories are different
        assert r1.workspace_context.path != r2.workspace_context.path
        assert r1.workspace_context.path.name != r2.workspace_context.path.name

    def test_hmac_key_produces_stable_ids(self):
        """The HMAC key is a constant — same inputs always produce same ID."""
        # Run twice to prove stability across calls
        id1 = compute_workspace_dir_name("stable-test", None, "999999999999")
        id2 = compute_workspace_dir_name("stable-test", None, "999999999999")
        assert id1 == id2

        # And the identity key too
        k1 = compute_identity_key("stable-test", None, "999999999999")
        k2 = compute_identity_key("stable-test", None, "999999999999")
        assert k1 == k2


# ---------------------------------------------------------------------------
# Payer Account Binding Tests
# ---------------------------------------------------------------------------


class TestBindPayerAccount:
    """Tests for bind_payer_account()."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        """Isolate filesystem."""
        self.tmp_path = tmp_path
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()

        monkeypatch.setattr(
            "kulshan.workspace.paths.get_workspaces_root",
            lambda: self.workspaces_root,
        )
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: self.workspaces_root / name,
        )

    def _create_workspace(self, name, payer="123456789012"):
        """Create a bound workspace."""
        ws_path = self.workspaces_root / name
        ws_path.mkdir(parents=True, exist_ok=True)
        config = WorkspaceConfig(
            name=name,
            display_name=f"{name}-display",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id=payer,
                default_connection="main",
                connections=[AwsConnection(
                    name="main",
                    profile="test",
                    expected_session_account_id="123456789012",
                )],
            ),
        )
        write_workspace_config(ws_path, config)
        return ws_path

    def test_bind_payer_updates_config(self):
        """bind_payer_account updates payer_account_id in workspace.toml."""
        from kulshan.workspace.onboarding import bind_payer_account

        self._create_workspace("ws_test1234", payer="111111111111")

        result = bind_payer_account("ws_test1234", "999999999999")
        assert result is True

        # Verify config was updated
        config = read_workspace_config(self.workspaces_root / "ws_test1234")
        assert config.aws.payer_account_id == "999999999999"

    def test_bind_payer_does_not_rename_workspace(self):
        """Binding payer account does not change display name."""
        from kulshan.workspace.onboarding import bind_payer_account

        self._create_workspace("ws_test5678", payer="111111111111")

        # Read original display name
        config_before = read_workspace_config(self.workspaces_root / "ws_test5678")
        original_display = config_before.display_name

        bind_payer_account("ws_test5678", "999999999999")

        config_after = read_workspace_config(self.workspaces_root / "ws_test5678")
        assert config_after.display_name == original_display

    def test_bind_payer_idempotent(self):
        """Binding the same payer again returns True without error."""
        from kulshan.workspace.onboarding import bind_payer_account

        self._create_workspace("ws_idem", payer="999999999999")

        result = bind_payer_account("ws_idem", "999999999999")
        assert result is True

    def test_bind_payer_missing_workspace(self):
        """Binding to a non-existent workspace returns False."""
        from kulshan.workspace.onboarding import bind_payer_account

        result = bind_payer_account("ws_nonexist", "999999999999")
        assert result is False
