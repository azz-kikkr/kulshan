"""Tests for workspace configuration and resolution.

Tests cover:
- Account ID validation (exactly 12 digits)
- Workspace name validation (pattern, reserved names, path traversal)
- TOML parsing and atomic writes
- Resolution precedence (--workspace > env > config > default)
- Automatic default workspace creation
- Path traversal prevention
- Malformed configuration handling
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kulshan.cli import main
from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    WorkspaceMigrationStatus,
    create_default_workspace_config,
    read_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.context import WorkspaceContext
from kulshan.workspace.errors import (
    WorkspaceConfigError,
    WorkspaceNotFoundError,
    WorkspaceValidationError,
)
from kulshan.workspace.resolution import (
    ensure_default_workspace,
    get_active_workspace_name,
    list_workspaces,
    resolve_workspace,
    set_active_workspace_name,
    workspace_exists,
    _reset_migration_guard,
)
from kulshan.workspace.validation import (
    validate_account_id,
    validate_connection_name,
    validate_profile_name,
    validate_workspace_name,
)


# ---------------------------------------------------------------------------
# Account ID Validation Tests
# ---------------------------------------------------------------------------


class TestValidateAccountId:
    """Tests for 12-digit account ID validation."""

    def test_valid_12_digits(self):
        assert validate_account_id("123456789012") == "123456789012"

    def test_valid_all_zeros(self):
        assert validate_account_id("000000000000") == "000000000000"

    def test_valid_all_nines(self):
        assert validate_account_id("999999999999") == "999999999999"

    def test_invalid_11_digits(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_account_id("12345678901")
        assert "exactly 12 digits" in str(exc.value)

    def test_invalid_13_digits(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_account_id("1234567890123")
        assert "exactly 12 digits" in str(exc.value)

    def test_invalid_contains_letters(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_account_id("12345678901a")
        assert "only digits" in str(exc.value)

    def test_invalid_empty(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_account_id("")
        assert "cannot be empty" in str(exc.value)

    def test_invalid_spaces(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_account_id("123 456 7890")
        assert "only digits" in str(exc.value)

    def test_custom_field_name_in_error(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_account_id("bad", field_name="payer_account_id")
        assert "payer_account_id" in str(exc.value)


# ---------------------------------------------------------------------------
# Workspace Name Validation Tests
# ---------------------------------------------------------------------------


class TestValidateWorkspaceName:
    """Tests for workspace name validation."""

    def test_valid_simple(self):
        assert validate_workspace_name("customer-a") == "customer-a"

    def test_valid_underscore(self):
        assert validate_workspace_name("customer_a") == "customer_a"

    def test_valid_numbers(self):
        assert validate_workspace_name("customer123") == "customer123"

    def test_valid_mixed(self):
        assert validate_workspace_name("cust-123_prod") == "cust-123_prod"

    def test_valid_single_char(self):
        assert validate_workspace_name("a") == "a"

    def test_valid_64_chars(self):
        name = "a" * 64
        assert validate_workspace_name(name) == name

    def test_invalid_empty(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name("")
        assert "cannot be empty" in str(exc.value)

    def test_invalid_65_chars(self):
        name = "a" * 65
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name(name)
        assert "1-64 chars" in str(exc.value)

    def test_invalid_starts_with_hyphen(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name("-customer")
        assert "alphanumeric" in str(exc.value)

    def test_invalid_starts_with_underscore(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name("_customer")
        assert "alphanumeric" in str(exc.value)

    def test_invalid_forward_slash(self):
        """Path traversal prevention: forward slash."""
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name("../escape")
        assert "path separators" in str(exc.value)

    def test_invalid_backslash(self):
        """Path traversal prevention: backslash."""
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name("..\\escape")
        assert "path separators" in str(exc.value)

    def test_invalid_dot(self):
        """Reserved name: single dot."""
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name(".")
        assert "reserved" in str(exc.value)

    def test_invalid_dot_dot(self):
        """Reserved name: double dot."""
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name("..")
        assert "reserved" in str(exc.value)

    def test_default_reserved_without_flag(self):
        """'default' is reserved unless allow_default=True."""
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name("default")
        assert "reserved" in str(exc.value)

    def test_default_allowed_with_flag(self):
        """'default' allowed when allow_default=True."""
        assert validate_workspace_name("default", allow_default=True) == "default"

    def test_invalid_special_chars(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_workspace_name("customer@a")
        assert "alphanumeric" in str(exc.value)


# ---------------------------------------------------------------------------
# Connection and Profile Name Validation Tests
# ---------------------------------------------------------------------------


class TestValidateConnectionName:
    """Tests for connection name validation."""

    def test_valid(self):
        assert validate_connection_name("finops") == "finops"

    def test_invalid_empty(self):
        with pytest.raises(WorkspaceValidationError):
            validate_connection_name("")


class TestValidateProfileName:
    """Tests for AWS profile name validation."""

    def test_valid(self):
        assert validate_profile_name("customer-a-audit") == "customer-a-audit"

    def test_valid_with_spaces(self):
        assert validate_profile_name("AWS Profile Name") == "AWS Profile Name"

    def test_invalid_empty(self):
        with pytest.raises(WorkspaceValidationError):
            validate_profile_name("")

    def test_invalid_too_long(self):
        with pytest.raises(WorkspaceValidationError) as exc:
            validate_profile_name("x" * 300)
        assert "too long" in str(exc.value)

    def test_invalid_path_separator(self):
        with pytest.raises(WorkspaceValidationError):
            validate_profile_name("path/to/profile")

    def test_invalid_newline(self):
        with pytest.raises(WorkspaceValidationError):
            validate_profile_name("profile\nname")


# ---------------------------------------------------------------------------
# TOML I/O and Configuration Tests
# ---------------------------------------------------------------------------


class TestWorkspaceConfig:
    """Tests for workspace configuration data models and TOML I/O."""

    def test_create_default_workspace_config(self):
        config = create_default_workspace_config()
        assert config.name == "default"
        assert config.binding_mode == "unbound"
        assert config.aws is None
        assert config.created_at is not None
        assert config.display_name == "Default"

    def test_unbound_config_roundtrip(self, tmp_path):
        """Unbound workspace config survives write/read cycle."""
        ws_dir = tmp_path / "default"
        ws_dir.mkdir()
        config = WorkspaceConfig(
            name="default",
            binding_mode="unbound",
            display_name="Test Workspace",
        )
        write_workspace_config(ws_dir, config)
        loaded = read_workspace_config(ws_dir)

        assert loaded.name == "default"
        assert loaded.binding_mode == "unbound"
        assert loaded.display_name == "Test Workspace"
        assert loaded.aws is None

    def test_bound_config_roundtrip(self, tmp_path):
        """Bound workspace config with connections survives write/read."""
        ws_dir = tmp_path / "customer-a"
        ws_dir.mkdir()

        config = WorkspaceConfig(
            name="customer-a",
            binding_mode="bound",
            display_name="Customer A",
            aws=WorkspaceAwsConfig(
                payer_account_id="111122223333",
                default_connection="finops",
                connections=[
                    AwsConnection(
                        name="finops",
                        profile="customer-a-audit",
                        expected_session_account_id="111122223333",
                        role_arn="arn:aws:iam::111122223333:role/KulshanAudit",
                    ),
                ],
            ),
        )
        write_workspace_config(ws_dir, config)
        loaded = read_workspace_config(ws_dir)

        assert loaded.name == "customer-a"
        assert loaded.binding_mode == "bound"
        assert loaded.is_bound is True
        assert loaded.aws is not None
        assert loaded.aws.payer_account_id == "111122223333"
        assert loaded.aws.default_connection == "finops"
        assert len(loaded.aws.connections) == 1
        conn = loaded.aws.connections[0]
        assert conn.name == "finops"
        assert conn.profile == "customer-a-audit"
        assert conn.expected_session_account_id == "111122223333"
        assert conn.role_arn == "arn:aws:iam::111122223333:role/KulshanAudit"

    def test_migration_status_roundtrip(self, tmp_path):
        """Migration status fields survive write/read."""
        config = WorkspaceConfig(
            name="default",
            binding_mode="unbound",
            migration=WorkspaceMigrationStatus(
                main_history="migrated",
                security_history="failed",
            ),
        )
        ws_dir = tmp_path / "default"
        ws_dir.mkdir()
        write_workspace_config(ws_dir, config)
        loaded = read_workspace_config(ws_dir)

        assert loaded.migration is not None
        assert loaded.migration.main_history == "migrated"
        assert loaded.migration.security_history == "failed"

    def test_binding_mode_independent_of_migration(self, tmp_path):
        """Binding mode and migration state are independent."""
        config = WorkspaceConfig(
            name="default",
            binding_mode="unbound",
            migration=WorkspaceMigrationStatus(
                main_history="migrated",
                security_history="migrated",
            ),
        )
        ws_dir = tmp_path / "default"
        ws_dir.mkdir()
        write_workspace_config(ws_dir, config)
        loaded = read_workspace_config(ws_dir)

        # Unbound workspace can have completed migration
        assert loaded.binding_mode == "unbound"
        assert loaded.migration.main_history == "migrated"

    def test_bound_without_aws_config_raises(self):
        """Bound workspace without AWS configuration is invalid."""
        data = {
            "schema_version": 1,
            "name": "bad-ws",
            "binding_mode": "bound",
        }
        with pytest.raises(WorkspaceConfigError) as exc:
            WorkspaceConfig.from_dict(data, "bad-ws")
        assert "must have AWS configuration" in str(exc.value)

    def test_bound_without_connections_raises(self):
        """Bound workspace with AWS config but no connections is invalid."""
        data = {
            "schema_version": 1,
            "name": "bad-ws",
            "binding_mode": "bound",
            "aws": {
                "payer_account_id": "111122223333",
                "default_connection": "main",
                "connections": [],
            },
        }
        with pytest.raises(WorkspaceConfigError) as exc:
            WorkspaceConfig.from_dict(data, "bad-ws")
        assert "at least one AWS connection" in str(exc.value)

    def test_config_name_mismatch_raises(self):
        """Config name must match the directory name."""
        data = {
            "schema_version": 1,
            "name": "wrong-name",
            "binding_mode": "unbound",
        }
        with pytest.raises(WorkspaceConfigError) as exc:
            WorkspaceConfig.from_dict(data, "actual-dir")
        assert "does not match" in str(exc.value)

    def test_invalid_binding_mode_raises(self):
        """Invalid binding_mode value is rejected."""
        data = {
            "schema_version": 1,
            "name": "test-ws",
            "binding_mode": "invalid",
        }
        with pytest.raises(WorkspaceConfigError) as exc:
            WorkspaceConfig.from_dict(data, "test-ws")
        assert "Invalid binding_mode" in str(exc.value)

    def test_missing_workspace_toml_raises(self, tmp_path):
        """Reading config from directory without workspace.toml fails."""
        with pytest.raises(WorkspaceConfigError) as exc:
            read_workspace_config(tmp_path)
        assert "workspace.toml not found" in str(exc.value)

    def test_invalid_toml_raises(self, tmp_path):
        """Malformed TOML file raises error."""
        (tmp_path / "workspace.toml").write_text("this is not [valid toml")
        with pytest.raises(WorkspaceConfigError) as exc:
            read_workspace_config(tmp_path)
        assert "Failed to parse TOML" in str(exc.value)


# ---------------------------------------------------------------------------
# Atomic Write Tests
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    """Tests for atomic file writing behavior."""

    def test_write_creates_workspace_toml(self, tmp_path):
        """write_workspace_config creates workspace.toml in target dir."""
        config = create_default_workspace_config()
        config.name = "test-ws"
        ws_dir = tmp_path / "test-ws"
        ws_dir.mkdir()
        write_workspace_config(ws_dir, config)

        config_path = ws_dir / "workspace.toml"
        assert config_path.exists()
        assert config_path.stat().st_size > 0

    def test_write_no_temp_file_left_on_success(self, tmp_path):
        """After successful write, no temp files remain."""
        config = create_default_workspace_config()
        config.name = "test-ws"
        ws_dir = tmp_path / "test-ws"
        ws_dir.mkdir()
        write_workspace_config(ws_dir, config)

        # Only workspace.toml should exist
        files = list(ws_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "workspace.toml"

    def test_write_overwrites_existing(self, tmp_path):
        """Writing config overwrites existing workspace.toml."""
        ws_dir = tmp_path / "default"
        ws_dir.mkdir()

        config1 = WorkspaceConfig(name="default", display_name="First")
        write_workspace_config(ws_dir, config1)

        config2 = WorkspaceConfig(name="default", display_name="Second")
        write_workspace_config(ws_dir, config2)

        loaded = read_workspace_config(ws_dir)
        assert loaded.display_name == "Second"


# ---------------------------------------------------------------------------
# Resolution Precedence Tests
# ---------------------------------------------------------------------------


class TestResolveWorkspace:
    """Tests for workspace resolution logic and precedence."""

    def _setup_workspaces(self, tmp_path):
        """Create a workspaces root with default and custom workspaces."""
        ws_root = tmp_path / "workspaces"
        config_dir = tmp_path / "config"

        # Create default workspace
        default_dir = ws_root / "default"
        default_dir.mkdir(parents=True)
        config = create_default_workspace_config()
        write_workspace_config(default_dir, config)

        # Create customer-a workspace (bound)
        cust_dir = ws_root / "customer-a"
        cust_dir.mkdir(parents=True)
        cust_config = WorkspaceConfig(
            name="customer-a",
            binding_mode="bound",
            display_name="Customer A",
            aws=WorkspaceAwsConfig(
                payer_account_id="111122223333",
                default_connection="main",
                connections=[
                    AwsConnection(
                        name="main",
                        profile="cust-a",
                        expected_session_account_id="111122223333",
                    )
                ],
            ),
        )
        write_workspace_config(cust_dir, cust_config)

        return ws_root, config_dir

    def test_explicit_name_takes_precedence(self, tmp_path):
        """--workspace parameter wins over env and config."""
        _reset_migration_guard()
        ws_root, config_dir = self._setup_workspaces(tmp_path)

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=config_dir / "config.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "noexist.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "noexist2.db"), \
             patch.dict(os.environ, {"KULSHAN_WORKSPACE": "default"}):
            ctx = resolve_workspace("customer-a")
            assert ctx.name == "customer-a"

    def test_env_var_when_no_explicit(self, tmp_path):
        """KULSHAN_WORKSPACE env var wins when no explicit param."""
        _reset_migration_guard()
        ws_root, config_dir = self._setup_workspaces(tmp_path)

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=config_dir / "config.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "noexist.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "noexist2.db"), \
             patch.dict(os.environ, {"KULSHAN_WORKSPACE": "customer-a"}):
            ctx = resolve_workspace(None)
            assert ctx.name == "customer-a"

    def test_config_active_when_no_env(self, tmp_path):
        """Saved active workspace wins when no env var."""
        _reset_migration_guard()
        ws_root, config_dir = self._setup_workspaces(tmp_path)
        config_dir.mkdir(parents=True, exist_ok=True)

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=config_dir / "config.toml"), \
             patch("kulshan.workspace.resolution.get_active_workspace_name", return_value="customer-a"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "noexist.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "noexist2.db"), \
             patch.dict(os.environ, {}, clear=True):
            # Remove KULSHAN_WORKSPACE if present
            os.environ.pop("KULSHAN_WORKSPACE", None)
            ctx = resolve_workspace(None)
            assert ctx.name == "customer-a"

    def test_falls_back_to_default(self, tmp_path):
        """Falls back to 'default' when nothing else specified."""
        _reset_migration_guard()
        ws_root = tmp_path / "workspaces"
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=config_dir / "config.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "noexist.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "noexist2.db"), \
             patch.dict(os.environ, {}, clear=True):
            os.environ.pop("KULSHAN_WORKSPACE", None)
            ctx = resolve_workspace(None)
            assert ctx.name == "default"
            # Default workspace should have been created
            assert (ws_root / "default" / "workspace.toml").exists()

    def test_nonexistent_workspace_raises(self, tmp_path):
        """Requesting a non-existent workspace raises error."""
        ws_root = tmp_path / "workspaces"
        ws_root.mkdir(parents=True)

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "config.toml"):
            with pytest.raises(WorkspaceNotFoundError):
                resolve_workspace("nonexistent")


# ---------------------------------------------------------------------------
# Default Workspace Creation Tests
# ---------------------------------------------------------------------------


class TestEnsureDefaultWorkspace:
    """Tests for automatic default workspace creation."""

    def test_creates_default_when_none_exist(self, tmp_path):
        """Creates default workspace when workspaces root is empty."""
        ws_root = tmp_path / "workspaces"

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n):
            path = ensure_default_workspace()

        assert path.name == "default"
        assert (path / "workspace.toml").exists()
        config = read_workspace_config(path)
        assert config.name == "default"
        assert config.binding_mode == "unbound"

    def test_idempotent_when_default_exists(self, tmp_path):
        """Does not overwrite existing default workspace."""
        ws_root = tmp_path / "workspaces"
        default_dir = ws_root / "default"
        default_dir.mkdir(parents=True)
        config = WorkspaceConfig(
            name="default",
            display_name="My Custom Default",
            binding_mode="unbound",
        )
        write_workspace_config(default_dir, config)

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n):
            ensure_default_workspace()

        # Display name should be preserved (not overwritten)
        loaded = read_workspace_config(default_dir)
        assert loaded.display_name == "My Custom Default"


# ---------------------------------------------------------------------------
# Workspace List and Active Workspace Tests
# ---------------------------------------------------------------------------


class TestListWorkspaces:
    """Tests for listing workspaces."""

    def test_empty_when_root_missing(self, tmp_path):
        """Returns empty list when workspaces root doesn't exist."""
        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=tmp_path / "nope"):
            assert list_workspaces() == []

    def test_lists_valid_workspaces(self, tmp_path):
        """Lists only directories with workspace.toml."""
        ws_root = tmp_path / "workspaces"
        # Valid workspace
        ws1 = ws_root / "alpha"
        ws1.mkdir(parents=True)
        write_workspace_config(ws1, WorkspaceConfig(name="alpha"))
        # Valid workspace
        ws2 = ws_root / "beta"
        ws2.mkdir(parents=True)
        write_workspace_config(ws2, WorkspaceConfig(name="beta"))
        # Invalid: directory without workspace.toml
        (ws_root / "orphan").mkdir()

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root):
            result = list_workspaces()
            assert result == ["alpha", "beta"]

    def test_sorted_alphabetically(self, tmp_path):
        """Workspace list is sorted."""
        ws_root = tmp_path / "workspaces"
        for name in ["zeta", "alpha", "middle"]:
            d = ws_root / name
            d.mkdir(parents=True)
            write_workspace_config(d, WorkspaceConfig(name=name))

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root):
            assert list_workspaces() == ["alpha", "middle", "zeta"]


class TestActiveWorkspace:
    """Tests for get/set active workspace name."""

    def test_returns_none_when_no_config(self, tmp_path):
        """Returns None when config file doesn't exist."""
        with patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "config.toml"):
            assert get_active_workspace_name() is None

    def test_set_and_get_roundtrip(self, tmp_path):
        """set_active_workspace_name persists value for get."""
        config_path = tmp_path / "config.toml"
        with patch("kulshan.workspace.resolution.get_config_file_path", return_value=config_path):
            set_active_workspace_name("customer-a")
            assert get_active_workspace_name() == "customer-a"

    def test_overwrite_active(self, tmp_path):
        """Changing active workspace updates the stored value."""
        config_path = tmp_path / "config.toml"
        with patch("kulshan.workspace.resolution.get_config_file_path", return_value=config_path):
            set_active_workspace_name("first")
            set_active_workspace_name("second")
            assert get_active_workspace_name() == "second"


# ---------------------------------------------------------------------------
# WorkspaceContext Tests
# ---------------------------------------------------------------------------


class TestWorkspaceContext:
    """Tests for WorkspaceContext dataclass."""

    def test_from_path_unbound(self, tmp_path):
        """Context from unbound workspace has correct paths."""
        config = WorkspaceConfig(name="test-ws", binding_mode="unbound")
        ctx = WorkspaceContext.from_path(tmp_path, config)

        assert ctx.name == "test-ws"
        assert ctx.path == tmp_path
        assert ctx.is_bound is False
        assert ctx.binding_mode == "unbound"
        assert ctx.payer_account_id is None
        assert ctx.history_db_path == tmp_path / "history.db"
        assert ctx.security_history_db_path == tmp_path / "security-history.db"

    def test_from_path_bound(self, tmp_path):
        """Context from bound workspace exposes payer account."""
        config = WorkspaceConfig(
            name="customer-a",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="111122223333",
                default_connection="main",
                connections=[
                    AwsConnection(
                        name="main",
                        profile="cust-a",
                        expected_session_account_id="111122223333",
                    )
                ],
            ),
        )
        ctx = WorkspaceContext.from_path(tmp_path, config)

        assert ctx.is_bound is True
        assert ctx.payer_account_id == "111122223333"
        assert ctx.display_name == "customer-a"


# ---------------------------------------------------------------------------
# CLI Integration Tests
# ---------------------------------------------------------------------------


class TestWorkspaceCLI:
    """Tests for workspace CLI commands via CliRunner."""

    def test_workspace_option_invalid_name_rejected(self):
        """--workspace with invalid name produces error."""
        runner = CliRunner()
        result = runner.invoke(main, ["--workspace", "../escape", "history"])
        assert result.exit_code != 0
        assert "path separators" in result.output or "Invalid" in result.output

    def test_workspace_option_valid_name_accepted(self, tmp_path):
        """--workspace with valid name is accepted (stores in ctx.obj)."""
        ws_root = tmp_path / "workspaces"
        default_dir = ws_root / "default"
        default_dir.mkdir(parents=True)
        write_workspace_config(default_dir, create_default_workspace_config())

        runner = CliRunner()
        with patch("kulshan.workspace.paths.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.paths.get_config_file_path", return_value=tmp_path / "config.toml"):
            # Just invoke help to verify --workspace doesn't cause error
            result = runner.invoke(main, ["--workspace", "default", "--help"])
            assert result.exit_code == 0

    def test_workspace_list_empty(self, tmp_path):
        """workspace list with no workspaces shows empty message."""
        runner = CliRunner()
        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=tmp_path / "empty"), \
             patch("kulshan.workspace.resolution.get_active_workspace_name", return_value=None):
            # The list command imports from resolution
            with patch("kulshan.workspace.cli.list_workspaces", return_value=[]):
                result = runner.invoke(main, ["workspace", "list"])
                assert result.exit_code == 0
                assert "No workspaces" in result.output

    def test_workspace_list_shows_workspaces(self, tmp_path):
        """workspace list shows configured workspaces."""
        ws_root = tmp_path / "workspaces"
        ws1 = ws_root / "default"
        ws1.mkdir(parents=True)
        write_workspace_config(ws1, create_default_workspace_config())
        ws2 = ws_root / "customer-a"
        ws2.mkdir(parents=True)
        write_workspace_config(
            ws2, WorkspaceConfig(name="customer-a", display_name="Customer A")
        )

        runner = CliRunner()
        with patch("kulshan.workspace.cli.list_workspaces", return_value=["customer-a", "default"]), \
             patch("kulshan.workspace.cli.get_active_workspace_name", return_value="default"), \
             patch("kulshan.workspace.cli.get_workspace_path") as mock_path:
            mock_path.side_effect = lambda n: ws_root / n
            result = runner.invoke(main, ["workspace", "list"])
            assert result.exit_code == 0
            assert "customer-a" in result.output
            assert "default" in result.output

    def test_workspace_use_valid(self, tmp_path):
        """workspace use sets active workspace."""
        ws_root = tmp_path / "workspaces"
        ws = ws_root / "customer-a"
        ws.mkdir(parents=True)
        write_workspace_config(ws, WorkspaceConfig(name="customer-a"))
        config_path = tmp_path / "config.toml"

        runner = CliRunner()
        with patch("kulshan.workspace.cli.workspace_exists", return_value=True), \
             patch("kulshan.workspace.cli.set_active_workspace_name") as mock_set:
            result = runner.invoke(main, ["workspace", "use", "customer-a"])
            assert result.exit_code == 0
            assert "customer-a" in result.output
            mock_set.assert_called_once_with("customer-a")

    def test_workspace_use_invalid_name(self):
        """workspace use with invalid name shows error."""
        runner = CliRunner()
        result = runner.invoke(main, ["workspace", "use", "../bad"])
        assert result.exit_code != 0

    def test_workspace_use_nonexistent(self):
        """workspace use with non-existent workspace shows error."""
        runner = CliRunner()
        with patch("kulshan.workspace.cli.workspace_exists", return_value=False):
            result = runner.invoke(main, ["workspace", "use", "ghost"])
            assert result.exit_code != 0
            assert "not found" in result.output

    def test_workspace_show_active(self, tmp_path):
        """workspace show displays active workspace details."""
        ws_root = tmp_path / "workspaces"
        default_dir = ws_root / "default"
        default_dir.mkdir(parents=True)
        config = create_default_workspace_config()
        write_workspace_config(default_dir, config)

        runner = CliRunner()
        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", return_value=default_dir), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "config.toml"), \
             patch("kulshan.workspace.cli.get_active_workspace_name", return_value="default"), \
             patch("kulshan.workspace.cli.resolve_workspace") as mock_resolve:
            mock_resolve.return_value = WorkspaceContext.from_path(default_dir, config)
            result = runner.invoke(main, ["workspace", "show"])
            assert result.exit_code == 0
            assert "default" in result.output
            assert "unbound" in result.output


# ---------------------------------------------------------------------------
# Path Traversal Prevention Tests
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """Tests that path traversal is prevented at all layers."""

    def test_workspace_name_slash(self):
        with pytest.raises(WorkspaceValidationError):
            validate_workspace_name("../../etc/passwd")

    def test_workspace_name_backslash(self):
        with pytest.raises(WorkspaceValidationError):
            validate_workspace_name("..\\..\\windows\\system32")

    def test_resolve_workspace_rejects_traversal(self, tmp_path):
        """resolve_workspace validates name before path lookup."""
        ws_root = tmp_path / "workspaces"
        ws_root.mkdir(parents=True)

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "config.toml"):
            with pytest.raises(WorkspaceValidationError):
                resolve_workspace("../escape")

    def test_cli_workspace_option_rejects_traversal(self):
        """CLI --workspace rejects path traversal attempts."""
        runner = CliRunner()
        # Invoke a real command (not --help) so the option callback fires
        result = runner.invoke(main, ["--workspace", "../escape", "workspace", "list"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# AwsConnection Tests
# ---------------------------------------------------------------------------


class TestAwsConnection:
    """Tests for AwsConnection data model."""

    def test_valid_connection(self):
        conn = AwsConnection(
            name="finops",
            profile="customer-audit",
            expected_session_account_id="111122223333",
        )
        assert conn.name == "finops"
        assert conn.role_arn is None

    def test_connection_with_role_arn(self):
        conn = AwsConnection(
            name="finops",
            profile="customer-audit",
            expected_session_account_id="111122223333",
            role_arn="arn:aws:iam::111122223333:role/KulshanAudit",
        )
        assert conn.role_arn == "arn:aws:iam::111122223333:role/KulshanAudit"

    def test_invalid_account_id_in_connection(self):
        """Connection with bad account ID raises during __post_init__."""
        with pytest.raises(WorkspaceValidationError):
            AwsConnection(
                name="finops",
                profile="customer-audit",
                expected_session_account_id="bad",
            )

    def test_invalid_connection_name(self):
        with pytest.raises(WorkspaceValidationError):
            AwsConnection(
                name="",
                profile="customer-audit",
                expected_session_account_id="111122223333",
            )

    def test_to_dict_without_role(self):
        conn = AwsConnection(
            name="main",
            profile="prof",
            expected_session_account_id="111122223333",
        )
        d = conn.to_dict()
        assert "role_arn" not in d
        assert d["name"] == "main"

    def test_to_dict_with_role(self):
        conn = AwsConnection(
            name="main",
            profile="prof",
            expected_session_account_id="111122223333",
            role_arn="arn:aws:iam::111122223333:role/Audit",
        )
        d = conn.to_dict()
        assert d["role_arn"] == "arn:aws:iam::111122223333:role/Audit"


# ---------------------------------------------------------------------------
# WorkspaceAwsConfig Tests
# ---------------------------------------------------------------------------


class TestWorkspaceAwsConfig:
    """Tests for WorkspaceAwsConfig data model."""

    def test_get_connection_by_name(self):
        aws = WorkspaceAwsConfig(
            payer_account_id="111122223333",
            default_connection="finops",
            connections=[
                AwsConnection(
                    name="finops",
                    profile="cust-a",
                    expected_session_account_id="111122223333",
                ),
                AwsConnection(
                    name="readonly",
                    profile="cust-a-ro",
                    expected_session_account_id="111122223333",
                ),
            ],
        )
        conn = aws.get_connection("finops")
        assert conn is not None
        assert conn.profile == "cust-a"

        assert aws.get_connection("missing") is None

    def test_get_connection_by_profile(self):
        aws = WorkspaceAwsConfig(
            payer_account_id="111122223333",
            default_connection="finops",
            connections=[
                AwsConnection(
                    name="finops",
                    profile="cust-a",
                    expected_session_account_id="111122223333",
                ),
            ],
        )
        conn = aws.get_connection_by_profile("cust-a")
        assert conn is not None
        assert conn.name == "finops"

        assert aws.get_connection_by_profile("missing") is None

    def test_invalid_payer_account_id(self):
        with pytest.raises(WorkspaceValidationError):
            WorkspaceAwsConfig(
                payer_account_id="bad",
                default_connection="main",
                connections=[],
            )


# ---------------------------------------------------------------------------
# Extended Configuration Validation Tests (Check 5 & 6)
# ---------------------------------------------------------------------------


class TestSchemaVersionValidation:
    """Tests for schema_version enforcement."""

    def test_unsupported_schema_version_rejected(self):
        """Future schema version is rejected with clear error."""
        data = {
            "schema_version": 99,
            "name": "default",
            "binding_mode": "unbound",
        }
        with pytest.raises(WorkspaceConfigError) as exc:
            WorkspaceConfig.from_dict(data, "default")
        assert "Unsupported schema_version" in str(exc.value)
        assert "99" in str(exc.value)

    def test_schema_version_0_rejected(self):
        data = {
            "schema_version": 0,
            "name": "default",
            "binding_mode": "unbound",
        }
        with pytest.raises(WorkspaceConfigError) as exc:
            WorkspaceConfig.from_dict(data, "default")
        assert "Unsupported schema_version" in str(exc.value)

    def test_current_schema_version_accepted(self):
        from kulshan.workspace.config import SCHEMA_VERSION
        data = {
            "schema_version": SCHEMA_VERSION,
            "name": "default",
            "binding_mode": "unbound",
        }
        config = WorkspaceConfig.from_dict(data, "default")
        assert config.schema_version == SCHEMA_VERSION


class TestConnectionValidationEnforcement:
    """Tests for connection-level config validation."""

    def test_duplicate_connection_names_rejected(self):
        """Duplicate connection names within AWS config are rejected."""
        with pytest.raises(WorkspaceValidationError) as exc:
            WorkspaceAwsConfig(
                payer_account_id="111122223333",
                default_connection="finops",
                connections=[
                    AwsConnection(
                        name="finops",
                        profile="prof-a",
                        expected_session_account_id="111122223333",
                    ),
                    AwsConnection(
                        name="finops",
                        profile="prof-b",
                        expected_session_account_id="111122223333",
                    ),
                ],
            )
        assert "Duplicate connection names" in str(exc.value)

    def test_default_connection_must_exist(self):
        """default_connection must reference an existing connection."""
        with pytest.raises(WorkspaceValidationError) as exc:
            WorkspaceAwsConfig(
                payer_account_id="111122223333",
                default_connection="ghost",
                connections=[
                    AwsConnection(
                        name="finops",
                        profile="prof-a",
                        expected_session_account_id="111122223333",
                    ),
                ],
            )
        assert "does not match" in str(exc.value)
        assert "ghost" in str(exc.value)

    def test_default_connection_valid_when_matches(self):
        """default_connection accepted when it matches a connection."""
        aws = WorkspaceAwsConfig(
            payer_account_id="111122223333",
            default_connection="finops",
            connections=[
                AwsConnection(
                    name="finops",
                    profile="prof-a",
                    expected_session_account_id="111122223333",
                ),
            ],
        )
        assert aws.default_connection == "finops"

    def test_empty_connections_skips_default_check(self):
        """When no connections exist, default_connection is not validated."""
        # This is valid for workspace configs during construction
        aws = WorkspaceAwsConfig(
            payer_account_id="111122223333",
            default_connection="main",
            connections=[],
        )
        assert aws.default_connection == "main"
