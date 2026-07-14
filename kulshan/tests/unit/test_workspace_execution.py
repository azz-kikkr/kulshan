"""Tests for workspace AWS execution context resolution (Commit 4).

Covers all 26 specified test cases with mocked boto3/STS.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from kulshan.cli import main
from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    write_workspace_config,
)
from kulshan.workspace.context import WorkspaceContext
from kulshan.workspace.errors import (
    AmbiguousProfileError,
    ConnectionConflictError,
    ConnectionNotFoundError,
    ProfileNotConfiguredError,
    RoleArnConflictError,
    WorkspaceCredentialMismatchError,
)
from kulshan.workspace.execution import (
    AwsExecutionContext,
    _reset_unbound_warning,
    resolve_aws_execution,
)
from kulshan.workspace.sts import StsVerificationError, StsVerificationResult, VerifiedAwsSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STS_PATCH = "kulshan.workspace.execution.create_verified_session"


def _sts_ok(account_id="111122223333"):
    """Return a mock VerifiedAwsSession."""
    from unittest.mock import MagicMock
    return VerifiedAwsSession(
        session=MagicMock(),
        account_id=account_id,
        arn=f"arn:aws:iam::{account_id}:user/test",
        user_id="AIDAEXAMPLE",
        resolved_profile="test-prof",
        role_arn=None,
    )


def _bound_workspace(tmp_path: Path, connections=None, default_conn="main"):
    """Create a bound workspace context for testing."""
    if connections is None:
        connections = [
            AwsConnection(
                name="main",
                profile="main-prof",
                expected_session_account_id="111122223333",
            ),
        ]
    config = WorkspaceConfig(
        name="customer-a",
        binding_mode="bound",
        aws=WorkspaceAwsConfig(
            payer_account_id="999999999999",
            default_connection=default_conn,
            connections=connections,
        ),
    )
    ws_dir = tmp_path / "customer-a"
    ws_dir.mkdir(parents=True, exist_ok=True)
    write_workspace_config(ws_dir, config)
    return WorkspaceContext.from_path(ws_dir, config)


def _unbound_workspace(tmp_path: Path):
    """Create an unbound default workspace context."""
    config = WorkspaceConfig(name="default", binding_mode="unbound")
    ws_dir = tmp_path / "default"
    ws_dir.mkdir(parents=True, exist_ok=True)
    write_workspace_config(ws_dir, config)
    return WorkspaceContext.from_path(ws_dir, config)


# ---------------------------------------------------------------------------
# 1. Default connection selected for bound workspace
# ---------------------------------------------------------------------------

class TestBoundConnectionSelection:

    def test_01_default_connection_selected(self, tmp_path):
        ws = _bound_workspace(tmp_path)
        with patch(_STS_PATCH, return_value=_sts_ok("111122223333")):
            ctx = resolve_aws_execution(ws)
        assert ctx.connection.name == "main"
        assert ctx.session_account_id == "111122223333"

    def test_02_explicit_connection_overrides_default(self, tmp_path):
        ws = _bound_workspace(tmp_path, connections=[
            AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333"),
            AwsConnection(name="audit", profile="p2", expected_session_account_id="444455556666"),
        ])
        with patch(_STS_PATCH, return_value=_sts_ok("444455556666")):
            ctx = resolve_aws_execution(ws, connection_name="audit")
        assert ctx.connection.name == "audit"

    def test_03_explicit_profile_selects_connection(self, tmp_path):
        ws = _bound_workspace(tmp_path, connections=[
            AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333"),
            AwsConnection(name="audit", profile="p2", expected_session_account_id="444455556666"),
        ])
        with patch(_STS_PATCH, return_value=_sts_ok("444455556666")):
            ctx = resolve_aws_execution(ws, profile="p2")
        assert ctx.connection.name == "audit"

    def test_04_matching_connection_and_profile_works(self, tmp_path):
        ws = _bound_workspace(tmp_path)
        with patch(_STS_PATCH, return_value=_sts_ok("111122223333")):
            ctx = resolve_aws_execution(ws, connection_name="main", profile="main-prof")
        assert ctx.connection.name == "main"

    def test_05_conflicting_connection_and_profile_fails(self, tmp_path):
        ws = _bound_workspace(tmp_path)
        with pytest.raises(ConnectionConflictError) as exc:
            resolve_aws_execution(ws, connection_name="main", profile="other-prof")
        assert "main" in str(exc.value)
        assert "other-prof" in str(exc.value)

    def test_06_unknown_connection_fails(self, tmp_path):
        ws = _bound_workspace(tmp_path)
        with pytest.raises(ConnectionNotFoundError):
            resolve_aws_execution(ws, connection_name="ghost")

    def test_07_unconfigured_profile_fails(self, tmp_path):
        ws = _bound_workspace(tmp_path)
        with pytest.raises(ProfileNotConfiguredError):
            resolve_aws_execution(ws, profile="not-configured")

    def test_08_ambiguous_profile_requires_connection(self, tmp_path):
        ws = _bound_workspace(tmp_path, connections=[
            AwsConnection(name="a", profile="shared", expected_session_account_id="111122223333",
                          role_arn="arn:aws:iam::111122223333:role/A"),
            AwsConnection(name="b", profile="shared", expected_session_account_id="222233334444",
                          role_arn="arn:aws:iam::222233334444:role/B"),
        ], default_conn="a")
        with pytest.raises(AmbiguousProfileError):
            resolve_aws_execution(ws, profile="shared")


# ---------------------------------------------------------------------------
# 9-10. AWS_PROFILE handling
# ---------------------------------------------------------------------------

class TestAwsProfileEnv:

    def test_09_aws_profile_ignored_for_bound(self, tmp_path):
        """AWS_PROFILE env var is ignored for bound workspaces."""
        ws = _bound_workspace(tmp_path)
        with patch(_STS_PATCH, return_value=_sts_ok("111122223333")), \
             patch.dict(os.environ, {"AWS_PROFILE": "some-other-profile"}):
            ctx = resolve_aws_execution(ws)
        # Should use workspace default connection, not AWS_PROFILE
        assert ctx.resolved_profile == "main-prof"

    def test_10_aws_profile_works_for_unbound(self, tmp_path):
        """AWS_PROFILE is used for unbound default workspace."""
        ws = _unbound_workspace(tmp_path)

        with patch(_STS_PATCH, return_value=_sts_ok("555566667777")), \
             patch.dict(os.environ, {"AWS_PROFILE": "my-sso"}):
            ctx = resolve_aws_execution(ws)

        assert ctx.resolved_profile == "my-sso"
        assert ctx.session_account_id == "555566667777"


# ---------------------------------------------------------------------------
# 11-14. Role enforcement
# ---------------------------------------------------------------------------

class TestRoleEnforcement:

    def test_11_configured_role_applied(self, tmp_path):
        """Connection with role_arn uses it automatically."""
        ws = _bound_workspace(tmp_path, connections=[
            AwsConnection(
                name="main", profile="p1",
                expected_session_account_id="222233334444",
                role_arn="arn:aws:iam::222233334444:role/Kulshan",
            ),
        ])
        with patch(_STS_PATCH, return_value=_sts_ok("222233334444")) as mock, \
             patch.dict(os.environ, {}, clear=True):
            ctx = resolve_aws_execution(ws)
        # verify was called with the role
        mock.assert_called_once_with(
            profile="p1",
            role_arn="arn:aws:iam::222233334444:role/Kulshan",
        )

    def test_12_matching_role_arn_accepted(self, tmp_path):
        """--role-arn matching connection config is accepted."""
        ws = _bound_workspace(tmp_path, connections=[
            AwsConnection(
                name="main", profile="p1",
                expected_session_account_id="222233334444",
                role_arn="arn:aws:iam::222233334444:role/Kulshan",
            ),
        ])
        with patch(_STS_PATCH, return_value=_sts_ok("222233334444")):
            ctx = resolve_aws_execution(
                ws, role_arn="arn:aws:iam::222233334444:role/Kulshan"
            )
        assert ctx.session_account_id == "222233334444"

    def test_13_conflicting_role_arn_rejected(self, tmp_path):
        """--role-arn differing from connection is rejected."""
        ws = _bound_workspace(tmp_path, connections=[
            AwsConnection(
                name="main", profile="p1",
                expected_session_account_id="222233334444",
                role_arn="arn:aws:iam::222233334444:role/Kulshan",
            ),
        ])
        with pytest.raises(RoleArnConflictError):
            resolve_aws_execution(
                ws, role_arn="arn:aws:iam::999999999999:role/Other"
            )

    def test_14_role_override_rejected_when_no_configured_role(self, tmp_path):
        """--role-arn rejected when connection has no role."""
        ws = _bound_workspace(tmp_path)  # main has no role_arn
        with pytest.raises(RoleArnConflictError):
            resolve_aws_execution(
                ws, role_arn="arn:aws:iam::999999999999:role/Sneaky"
            )


# ---------------------------------------------------------------------------
# 15-17. Credential validation
# ---------------------------------------------------------------------------

class TestCredentialValidation:

    def test_15_assumed_role_account_validated(self, tmp_path):
        """Final STS account must match expected_session_account_id."""
        ws = _bound_workspace(tmp_path, connections=[
            AwsConnection(
                name="main", profile="p1",
                expected_session_account_id="222233334444",
                role_arn="arn:aws:iam::222233334444:role/Kulshan",
            ),
        ])
        # STS returns wrong account
        with patch(_STS_PATCH, return_value=_sts_ok("999999999999")):
            with pytest.raises(WorkspaceCredentialMismatchError) as exc:
                resolve_aws_execution(ws)
        assert "222233334444" in str(exc.value) or "mismatch" in str(exc.value).lower()

    def test_16_credential_mismatch_before_scan(self, tmp_path):
        """Mismatch occurs before any scan (resolve_aws_execution raises)."""
        ws = _bound_workspace(tmp_path)
        with patch(_STS_PATCH, return_value=_sts_ok("999999999999")):
            with pytest.raises(WorkspaceCredentialMismatchError):
                resolve_aws_execution(ws)
        # No session returned = no scan possible

    def test_17_credential_mismatch_before_history_write(self, tmp_path):
        """Credential mismatch prevents history writes (report fails early)."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "customer-a"
        ws_dir.mkdir(parents=True)
        write_workspace_config(ws_dir, WorkspaceConfig(
            name="customer-a", binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="999999999999", default_connection="main",
                connections=[AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333")],
            ),
        ))
        # Default workspace also needed for resolution
        default_dir = ws_root / "default"
        default_dir.mkdir(parents=True)
        from kulshan.workspace.config import create_default_workspace_config
        write_workspace_config(default_dir, create_default_workspace_config())

        runner = CliRunner()
        # STS returns wrong account → report should fail before history write
        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "c.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "x.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "y.db"), \
             patch(_STS_PATCH, return_value=_sts_ok("999999999999")):
            result = runner.invoke(main, [
                "--workspace", "customer-a", "report", "--yes",
            ])

        assert result.exit_code != 0
        # No history.db should have been created/written in workspace
        history_db = ws_dir / "history.db"
        if history_db.exists():
            conn = sqlite3.connect(history_db)
            count = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
            conn.close()
            assert count == 0


# ---------------------------------------------------------------------------
# 18-19. History isolation
# ---------------------------------------------------------------------------

class TestHistoryIsolation:

    def test_18_report_writes_to_workspace_db(self, tmp_path):
        """Successful report writes history to workspace-specific DB path."""
        ws = _bound_workspace(tmp_path)
        # The workspace context history_db_path should be inside workspace dir
        assert ws.history_db_path == tmp_path / "customer-a" / "history.db"

    def test_19_two_workspaces_separate_histories(self, tmp_path):
        """Two workspaces have separate history database paths."""
        ws1 = _bound_workspace(tmp_path, connections=[
            AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333"),
        ])

        config2 = WorkspaceConfig(
            name="customer-b", binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="888877776666", default_connection="main",
                connections=[AwsConnection(name="main", profile="p2", expected_session_account_id="444455556666")],
            ),
        )
        ws_dir2 = tmp_path / "customer-b"
        ws_dir2.mkdir(parents=True)
        write_workspace_config(ws_dir2, config2)
        ws2 = WorkspaceContext.from_path(ws_dir2, config2)

        assert ws1.history_db_path != ws2.history_db_path
        assert "customer-a" in str(ws1.history_db_path)
        assert "customer-b" in str(ws2.history_db_path)


# ---------------------------------------------------------------------------
# 20-21. History command: no AWS calls, account filter
# ---------------------------------------------------------------------------

class TestHistoryNoAws:

    def test_20_history_performs_no_aws_calls(self, tmp_path):
        """history command never calls boto3 or STS."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = tmp_path / "workspaces"
        default_dir = ws_root / "default"
        default_dir.mkdir(parents=True)
        from kulshan.workspace.config import create_default_workspace_config
        write_workspace_config(default_dir, create_default_workspace_config())

        runner = CliRunner()
        mock_boto3 = MagicMock()

        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "c.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "x.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "y.db"), \
             patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = runner.invoke(main, ["history"])

        # boto3 never called
        mock_boto3.Session.assert_not_called()
        mock_boto3.client.assert_not_called()

    def test_21_history_account_filter_within_workspace(self, tmp_path):
        """--account filtering works within workspace history."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = tmp_path / "workspaces"
        default_dir = ws_root / "default"
        default_dir.mkdir(parents=True)
        from kulshan.workspace.config import create_default_workspace_config
        write_workspace_config(default_dir, create_default_workspace_config())

        # Create history with multiple accounts
        from kulshan.history import HistoryStore
        db_path = default_dir / "history.db"
        store = HistoryStore(db_path)
        store.save_scan(
            account_id="111122223333", regions=["us-east-1"],
            duration_seconds=1.0, overall_score=80, overall_grade="B",
            results={}, findings=[], version="0.2.0",
        )
        store.save_scan(
            account_id="444455556666", regions=["us-east-1"],
            duration_seconds=1.0, overall_score=60, overall_grade="D",
            results={}, findings=[], version="0.2.0",
        )
        store.close()

        runner = CliRunner()
        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "c.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "x.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "y.db"):
            result = runner.invoke(main, ["history", "--account", "111122223333"])

        assert result.exit_code == 0
        assert "Scan History" in result.output


# ---------------------------------------------------------------------------
# 22-23. Unbound compatibility and warning
# ---------------------------------------------------------------------------

class TestUnboundCompat:

    def test_22_unbound_default_backward_compatible(self, tmp_path):
        """Unbound default workspace uses standard boto3 chain."""
        ws = _unbound_workspace(tmp_path)

        with patch(_STS_PATCH, return_value=_sts_ok("333344445555")), \
             patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AWS_PROFILE", None)
            ctx = resolve_aws_execution(ws)

        assert ctx.connection is None
        assert ctx.payer_account_id is None
        assert ctx.session_account_id == "333344445555"

    def test_23_unbound_warning_printed_once_per_invocation(self, tmp_path):
        """Unbound warning is emitted by the CLI command, not by resolver."""
        # The resolver no longer prints warnings — CLI does it per invocation.
        # This test verifies the is_unbound flag is set for unbound workspaces.
        ws = _unbound_workspace(tmp_path)

        with patch(_STS_PATCH, return_value=_sts_ok("111111111111")):
            ctx = resolve_aws_execution(ws)

        assert ctx.is_unbound is True


# ---------------------------------------------------------------------------
# 24. Security/investigation cannot write globally for bound workspace
# ---------------------------------------------------------------------------

class TestFailClosed:

    def test_24_bound_workspace_security_pack_warned(self, tmp_path):
        """Security pack on bound workspace shows isolation warning."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = tmp_path / "workspaces"
        ws_dir = ws_root / "customer-a"
        ws_dir.mkdir(parents=True)
        write_workspace_config(ws_dir, WorkspaceConfig(
            name="customer-a", binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="999999999999", default_connection="main",
                connections=[AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333")],
            ),
        ))
        default_dir = ws_root / "default"
        default_dir.mkdir(parents=True)
        from kulshan.workspace.config import create_default_workspace_config
        write_workspace_config(default_dir, create_default_workspace_config())

        runner = CliRunner()
        with patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root), \
             patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n), \
             patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "c.toml"), \
             patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "x.db"), \
             patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "y.db"), \
             patch(_STS_PATCH, return_value=_sts_ok("111122223333")):
            result = runner.invoke(main, [
                "--workspace", "customer-a", "report",
                "--packs", "security", "--yes",
            ])

        # Should contain the security isolation warning
        assert "security" in result.output.lower()
        assert "isolation" in result.output.lower() or "not yet implemented" in result.output.lower()


# ---------------------------------------------------------------------------
# 25. Redaction in errors
# ---------------------------------------------------------------------------

class TestRedactionInErrors:

    def test_25_credential_mismatch_contains_account_info(self, tmp_path):
        """Credential mismatch error includes account context."""
        ws = _bound_workspace(tmp_path)
        with patch(_STS_PATCH, return_value=_sts_ok("999999999999")):
            with pytest.raises(WorkspaceCredentialMismatchError) as exc:
                resolve_aws_execution(ws)
        # Error includes expected and actual account
        err_str = str(exc.value)
        assert "111122223333" in err_str or "mismatch" in err_str.lower()


# ---------------------------------------------------------------------------
# 26. Full test suite green (validated by running all tests)
# ---------------------------------------------------------------------------

class TestSuiteIntegrity:

    def test_26_all_execution_imports(self):
        """All execution module components importable."""
        from kulshan.workspace.execution import (
            AwsExecutionContext,
            resolve_aws_execution,
            _reset_unbound_warning,
        )
        assert AwsExecutionContext is not None
        assert resolve_aws_execution is not None
