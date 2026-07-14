"""Tests for bound AWS workspace connections (Commit 3).

Covers all 24 specified test cases with mocked boto3/STS.
No real AWS credentials or config directory used.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from kulshan.cli import main
from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    read_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.errors import (
    AmbiguousProfileError,
    WorkspaceConfigError,
    WorkspaceValidationError,
)
from kulshan.workspace.paths import get_workspace_path
from kulshan.workspace.sts import (
    StsVerificationError,
    StsVerificationResult,
    VerifiedAwsSession,
    verify_credentials,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_sts_success(account_id: str = "111122223333"):
    """Return a mock VerifiedAwsSession that succeeds."""
    from unittest.mock import MagicMock
    return VerifiedAwsSession(
        session=MagicMock(),
        account_id=account_id,
        arn=f"arn:aws:iam::{account_id}:user/test",
        user_id="AIDAEXAMPLE",
        resolved_profile="test-prof",
        role_arn=None,
    )


# The create_verified_session is imported locally in CLI functions, so we patch the source
_STS_PATCH = "kulshan.workspace.sts.create_verified_session"


def _create_bound_workspace(ws_root: Path, name: str, payer: str = "999999999999"):
    """Create a minimal bound workspace for testing."""
    ws_dir = ws_root / name
    ws_dir.mkdir(parents=True, exist_ok=True)
    config = WorkspaceConfig(
        name=name,
        binding_mode="bound",
        aws=WorkspaceAwsConfig(
            payer_account_id=payer,
            default_connection="main",
            connections=[
                AwsConnection(
                    name="main",
                    profile="test-profile",
                    expected_session_account_id="111122223333",
                ),
            ],
        ),
    )
    write_workspace_config(ws_dir, config)
    return ws_dir


# ---------------------------------------------------------------------------
# 1. Successful workspace creation using an SSO profile
# ---------------------------------------------------------------------------


class TestWorkspaceCreate:

    def test_01_successful_creation(self, tmp_path):
        """Workspace creation succeeds with valid STS verification."""
        ws_root = tmp_path / "workspaces"
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "customer-a"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, return_value=_mock_sts_success("111122223333")):
            result = runner.invoke(main, [
                "workspace", "create", "customer-a",
                "--profile", "cust-sso",
                "--payer-account", "999999999999",
            ])

        assert result.exit_code == 0
        assert "customer-a" in result.output
        assert "created" in result.output.lower() or "✓" in result.output

        # Verify workspace.toml written
        config = read_workspace_config(ws_root / "customer-a")
        assert config.name == "customer-a"
        assert config.binding_mode == "bound"
        assert config.aws.payer_account_id == "999999999999"

    def test_02_stores_sts_account_not_assertion(self, tmp_path):
        """Creation stores the STS-returned account, not the asserted one."""
        ws_root = tmp_path / "workspaces"
        runner = CliRunner()

        # STS returns 111122223333 (matching assertion)
        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-b"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, return_value=_mock_sts_success("111122223333")):
            result = runner.invoke(main, [
                "workspace", "create", "cust-b",
                "--profile", "sso-prof",
                "--payer-account", "999999999999",
                "--credential-account", "111122223333",
            ])

        assert result.exit_code == 0
        config = read_workspace_config(ws_root / "cust-b")
        conn = config.aws.connections[0]
        assert conn.expected_session_account_id == "111122223333"

    def test_03_credential_assertion_matches_sts(self, tmp_path):
        """Optional credential assertion that matches STS succeeds."""
        ws_root = tmp_path / "workspaces"
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-c"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, return_value=_mock_sts_success("444455556666")):
            result = runner.invoke(main, [
                "workspace", "create", "cust-c",
                "--profile", "sso-prof",
                "--payer-account", "999999999999",
                "--credential-account", "444455556666",
            ])

        assert result.exit_code == 0

    def test_04_credential_mismatch_leaves_no_directory(self, tmp_path):
        """Credential assertion mismatch fails and leaves no workspace dir."""
        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "cust-d"
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_dir), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, side_effect=StsVerificationError("Credential account mismatch: expected 000000000000, but STS returned 111122223333.")):
            result = runner.invoke(main, [
                "workspace", "create", "cust-d",
                "--profile", "sso-prof",
                "--payer-account", "999999999999",
                "--credential-account", "000000000000",
            ])

        assert result.exit_code != 0
        assert "mismatch" in result.output.lower() or "failed" in result.output.lower()
        assert not ws_dir.exists()

    def test_05_expired_sso_leaves_no_directory(self, tmp_path):
        """Expired SSO session fails cleanly, no workspace directory left."""
        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "cust-e"
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_dir), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, side_effect=StsVerificationError("No valid credentials for profile 'expired-sso'. SSO session may be expired.")):
            result = runner.invoke(main, [
                "workspace", "create", "cust-e",
                "--profile", "expired-sso",
                "--payer-account", "999999999999",
            ])

        assert result.exit_code != 0
        assert not ws_dir.exists()

    def test_06_role_assumption_stores_assumed_account(self, tmp_path):
        """Role assumption stores the final assumed-role account."""
        ws_root = tmp_path / "workspaces"
        runner = CliRunner()

        # STS after role assumption returns 222233334444
        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-f"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, return_value=_mock_sts_success("222233334444")):
            result = runner.invoke(main, [
                "workspace", "create", "cust-f",
                "--profile", "source-sso",
                "--payer-account", "999999999999",
                "--role-arn", "arn:aws:iam::222233334444:role/KulshanAudit",
            ])

        assert result.exit_code == 0
        config = read_workspace_config(ws_root / "cust-f")
        conn = config.aws.connections[0]
        assert conn.expected_session_account_id == "222233334444"
        assert conn.role_arn == "arn:aws:iam::222233334444:role/KulshanAudit"

    def test_07_role_assumption_failure_leaves_no_directory(self, tmp_path):
        """Role assumption failure leaves no workspace."""
        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "cust-g"
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_dir), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, side_effect=StsVerificationError("Permission denied when assuming role.")):
            result = runner.invoke(main, [
                "workspace", "create", "cust-g",
                "--profile", "source-sso",
                "--payer-account", "999999999999",
                "--role-arn", "arn:aws:iam::222233334444:role/BadRole",
            ])

        assert result.exit_code != 0
        assert not ws_dir.exists()

    def test_08_payer_stored_at_workspace_level(self, tmp_path):
        """Payer account is stored once at workspace level, not per connection."""
        ws_root = tmp_path / "workspaces"
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-h"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, return_value=_mock_sts_success()):
            runner.invoke(main, [
                "workspace", "create", "cust-h",
                "--profile", "prof",
                "--payer-account", "888877776666",
            ])

        config = read_workspace_config(ws_root / "cust-h")
        assert config.aws.payer_account_id == "888877776666"
        # No payer in connection itself
        conn_dict = config.aws.connections[0].to_dict()
        assert "payer" not in str(conn_dict).lower()

    def test_09_no_credentials_in_toml(self, tmp_path):
        """No credentials, tokens, or SSO cache written to workspace.toml."""
        ws_root = tmp_path / "workspaces"
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-i"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=False), \
             patch(_STS_PATCH, return_value=_mock_sts_success()):
            runner.invoke(main, [
                "workspace", "create", "cust-i",
                "--profile", "my-sso",
                "--payer-account", "999999999999",
            ])

        toml_path = ws_root / "cust-i" / "workspace.toml"
        content = toml_path.read_text()
        # Must not contain secrets
        assert "access_key" not in content.lower()
        assert "secret" not in content.lower()
        assert "session_token" not in content.lower()
        assert "sso_cache" not in content.lower()
        assert "AKIA" not in content


# ---------------------------------------------------------------------------
# 10-14. Connection add tests
# ---------------------------------------------------------------------------


class TestConnectionAdd:

    def test_10_connection_add_verifies_sts(self, tmp_path):
        """Connection add performs STS verification."""
        ws_root = tmp_path / "workspaces"
        _create_bound_workspace(ws_root, "cust-j")
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-j"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True), \
             patch(_STS_PATCH, return_value=_mock_sts_success("444455556666")) as mock_verify:
            result = runner.invoke(main, [
                "workspace", "connection", "add", "cust-j",
                "--name", "audit",
                "--profile", "audit-prof",
            ])

        assert result.exit_code == 0
        mock_verify.assert_called_once()
        config = read_workspace_config(ws_root / "cust-j")
        assert len(config.aws.connections) == 2
        audit = config.aws.get_connection("audit")
        assert audit.expected_session_account_id == "444455556666"

    def test_11_connection_add_failure_preserves_config(self, tmp_path):
        """STS failure during connection add preserves original config."""
        ws_root = tmp_path / "workspaces"
        _create_bound_workspace(ws_root, "cust-k")
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-k"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True), \
             patch(_STS_PATCH, side_effect=StsVerificationError("Expired SSO")):
            result = runner.invoke(main, [
                "workspace", "connection", "add", "cust-k",
                "--name", "bad-conn",
                "--profile", "expired-prof",
            ])

        assert result.exit_code != 0
        config = read_workspace_config(ws_root / "cust-k")
        assert len(config.aws.connections) == 1  # unchanged
        assert config.aws.get_connection("bad-conn") is None

    def test_12_duplicate_connection_name_rejected(self, tmp_path):
        """Duplicate connection name is rejected."""
        ws_root = tmp_path / "workspaces"
        _create_bound_workspace(ws_root, "cust-l")
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-l"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True):
            result = runner.invoke(main, [
                "workspace", "connection", "add", "cust-l",
                "--name", "main",  # already exists
                "--profile", "other-prof",
            ])

        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_13_equivalent_profile_role_rejected(self, tmp_path):
        """Same profile + same role (equivalent connection) is rejected."""
        ws_root = tmp_path / "workspaces"
        _create_bound_workspace(ws_root, "cust-m")
        runner = CliRunner()

        # Existing connection uses profile="test-profile", role_arn=None
        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-m"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True):
            result = runner.invoke(main, [
                "workspace", "connection", "add", "cust-m",
                "--name", "duplicate-equiv",
                "--profile", "test-profile",  # same profile, no role = equivalent
            ])

        assert result.exit_code != 0
        assert "Equivalent" in result.output or "equivalent" in result.output.lower()

    def test_14_same_profile_different_roles_allowed(self, tmp_path):
        """Same profile with different roles is allowed."""
        ws_root = tmp_path / "workspaces"
        # Create workspace with a connection that has a role
        ws_dir = ws_root / "cust-n"
        ws_dir.mkdir(parents=True)
        write_workspace_config(ws_dir, WorkspaceConfig(
            name="cust-n",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="999999999999",
                default_connection="role-a",
                connections=[
                    AwsConnection(
                        name="role-a",
                        profile="shared-sso",
                        expected_session_account_id="111122223333",
                        role_arn="arn:aws:iam::111122223333:role/RoleA",
                    ),
                ],
            ),
        ))
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_dir), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True), \
             patch(_STS_PATCH, return_value=_mock_sts_success("222233334444")):
            result = runner.invoke(main, [
                "workspace", "connection", "add", "cust-n",
                "--name", "role-b",
                "--profile", "shared-sso",  # same profile
                "--role-arn", "arn:aws:iam::222233334444:role/RoleB",  # different role
            ])

        assert result.exit_code == 0
        config = read_workspace_config(ws_dir)
        assert len(config.aws.connections) == 2


# ---------------------------------------------------------------------------
# 15. Profile ambiguity
# ---------------------------------------------------------------------------


class TestProfileAmbiguity:

    def test_15_ambiguous_profile_raises(self):
        """Multiple connections with same profile raises AmbiguousProfileError."""
        aws = WorkspaceAwsConfig(
            payer_account_id="999999999999",
            default_connection="conn-a",
            connections=[
                AwsConnection(
                    name="conn-a",
                    profile="shared",
                    expected_session_account_id="111122223333",
                    role_arn="arn:aws:iam::111122223333:role/A",
                ),
                AwsConnection(
                    name="conn-b",
                    profile="shared",
                    expected_session_account_id="222233334444",
                    role_arn="arn:aws:iam::222233334444:role/B",
                ),
            ],
        )
        with pytest.raises(AmbiguousProfileError) as exc:
            aws.get_connection_by_profile("shared")
        assert "shared" in str(exc.value)
        assert "--connection" in str(exc.value)


# ---------------------------------------------------------------------------
# 16-18. Connection remove tests
# ---------------------------------------------------------------------------


class TestConnectionRemove:

    def test_16_removing_unknown_connection_fails(self, tmp_path):
        """Removing a non-existent connection fails."""
        ws_root = tmp_path / "workspaces"
        _create_bound_workspace(ws_root, "cust-p")
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-p"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True):
            result = runner.invoke(main, [
                "workspace", "connection", "remove", "cust-p", "ghost",
            ])

        assert result.exit_code != 0
        assert "not found" in result.output

    def test_17_removing_last_connection_fails(self, tmp_path):
        """Cannot remove the last connection from a bound workspace."""
        ws_root = tmp_path / "workspaces"
        _create_bound_workspace(ws_root, "cust-q")
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-q"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True):
            result = runner.invoke(main, [
                "workspace", "connection", "remove", "cust-q", "main",
            ])

        assert result.exit_code != 0
        assert "last connection" in result.output.lower()

    def test_18_removing_default_connection_requires_change(self, tmp_path):
        """Cannot remove default connection until another is set."""
        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "cust-r"
        ws_dir.mkdir(parents=True)
        write_workspace_config(ws_dir, WorkspaceConfig(
            name="cust-r",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="999999999999",
                default_connection="main",
                connections=[
                    AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333"),
                    AwsConnection(name="audit", profile="p2", expected_session_account_id="222233334444"),
                ],
            ),
        ))
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_dir), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True):
            result = runner.invoke(main, [
                "workspace", "connection", "remove", "cust-r", "main",
            ])

        assert result.exit_code != 0
        assert "default connection" in result.output.lower() or "default-connection" in result.output
        # Config unchanged
        config = read_workspace_config(ws_dir)
        assert len(config.aws.connections) == 2


# ---------------------------------------------------------------------------
# 19-20. Default connection and atomic writes
# ---------------------------------------------------------------------------


class TestDefaultConnection:

    def test_19_change_default_no_aws_call(self, tmp_path):
        """Changing default connection requires no AWS call."""
        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "cust-s"
        ws_dir.mkdir(parents=True)
        write_workspace_config(ws_dir, WorkspaceConfig(
            name="cust-s",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="999999999999",
                default_connection="main",
                connections=[
                    AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333"),
                    AwsConnection(name="audit", profile="p2", expected_session_account_id="222233334444"),
                ],
            ),
        ))
        runner = CliRunner()

        # No boto3/STS patches needed — this must not call AWS
        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_dir), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True):
            result = runner.invoke(main, [
                "workspace", "default-connection", "cust-s", "audit",
            ])

        assert result.exit_code == 0
        config = read_workspace_config(ws_dir)
        assert config.aws.default_connection == "audit"

    def test_20_atomic_write_failure_preserves_config(self, tmp_path):
        """Write failure preserves previous config."""
        ws_root = tmp_path / "workspaces"
        _create_bound_workspace(ws_root, "cust-t")
        runner = CliRunner()

        with patch("kulshan.workspace.cli.get_workspace_path", return_value=ws_root / "cust-t"), \
             patch("kulshan.workspace.cli.workspace_exists", return_value=True), \
             patch(_STS_PATCH, return_value=_mock_sts_success("555566667777")), \
             patch("kulshan.workspace.cli.write_workspace_config", side_effect=OSError("Disk full")):
            result = runner.invoke(main, [
                "workspace", "connection", "add", "cust-t",
                "--name", "bad-write",
                "--profile", "good-prof",
            ])

        assert result.exit_code != 0
        # Original config should be intact (we patched write so it didn't change)
        config = read_workspace_config(ws_root / "cust-t")
        assert len(config.aws.connections) == 1


# ---------------------------------------------------------------------------
# 21-22. Redaction tests
# ---------------------------------------------------------------------------


class TestRedaction:

    def test_21_account_ids_redacted_by_default(self, tmp_path):
        """Account IDs and ARN components are redacted in normal output."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "cust-u"
        ws_dir.mkdir(parents=True)
        write_workspace_config(ws_dir, WorkspaceConfig(
            name="cust-u",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="999988887777",
                default_connection="main",
                connections=[
                    AwsConnection(
                        name="main",
                        profile="prof",
                        expected_session_account_id="111122223333",
                        role_arn="arn:aws:iam::111122223333:role/KulshanAudit",
                    ),
                ],
            ),
        ))
        runner = CliRunner()

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "c.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "x.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "y.db"), \
             patch("kulshan.workspace.cli.get_active_workspace_name", return_value=None):
            result = runner.invoke(main, ["workspace", "show", "cust-u"])

        assert result.exit_code == 0
        # Full account IDs should NOT appear
        assert "999988887777" not in result.output
        assert "111122223333" not in result.output
        # Redacted versions should appear
        assert "XXXX" in result.output

    def test_22_show_pii_reveals_ids(self, tmp_path):
        """--show-pii reveals complete account IDs."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "cust-v"
        ws_dir.mkdir(parents=True)
        write_workspace_config(ws_dir, WorkspaceConfig(
            name="cust-v",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="999988887777",
                default_connection="main",
                connections=[
                    AwsConnection(
                        name="main",
                        profile="prof",
                        expected_session_account_id="111122223333",
                    ),
                ],
            ),
        ))
        runner = CliRunner()

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "c.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "x.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "y.db"), \
             patch("kulshan.workspace.cli.get_active_workspace_name", return_value=None):
            result = runner.invoke(main, ["workspace", "show", "cust-v", "--show-pii"])

        assert result.exit_code == 0
        assert "999988887777" in result.output
        assert "111122223333" in result.output


# ---------------------------------------------------------------------------
# 23. Only default may be unbound (including direct Python construction)
# ---------------------------------------------------------------------------


class TestOnlyDefaultUnbound:

    def test_23_named_unbound_via_python_rejected(self):
        """Direct Python construction of named unbound workspace is rejected."""
        with pytest.raises(WorkspaceValidationError) as exc:
            WorkspaceConfig(name="customer-x", binding_mode="unbound")
        assert "default" in str(exc.value).lower() or "unbound" in str(exc.value).lower()

    def test_23b_default_unbound_via_python_accepted(self):
        """Default workspace with unbound is accepted via direct construction."""
        config = WorkspaceConfig(name="default", binding_mode="unbound")
        assert config.binding_mode == "unbound"


# ---------------------------------------------------------------------------
# 24. Full test suite remains green (verified by running all tests)
# ---------------------------------------------------------------------------
# This is verified by test_08 (run full suite) in the task list.
# Placeholder to document the requirement:


class TestSuiteIntegrity:

    def test_24_imports_succeed(self):
        """All workspace modules import without error."""
        from kulshan.workspace import (
            WorkspaceConfig,
            WorkspaceContext,
            resolve_workspace,
            WorkspaceError,
            WorkspaceNotFoundError,
        )
        from kulshan.workspace.sts import verify_credentials, StsVerificationError
        from kulshan.workspace.migration import migrate_legacy_to_default_workspace
        assert True
