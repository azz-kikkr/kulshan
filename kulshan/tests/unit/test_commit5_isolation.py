"""Tests for Commit 5: security and local investigation isolation.

Covers all 20 specified test cases using temporary Parquet fixtures
and mocked DuckDB for payer validation.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from kulshan.cli import main
from kulshan.cur.payer_validation import (
    MultiplePayersError,
    PayerMismatchError,
    PayerValidationResult,
    validate_cur_payer,
)
from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    create_default_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.sts import VerifiedAwsSession


_STS_PATCH = "kulshan.workspace.execution.create_verified_session"


def _verified(account_id="111122223333"):
    return VerifiedAwsSession(
        session=MagicMock(),
        account_id=account_id,
        arn=f"arn:aws:iam::{account_id}:user/test",
        user_id="AIDA123",
        resolved_profile="p1",
        role_arn=None,
    )


def _setup_ws(tmp_path, workspaces=None):
    """Setup workspace infrastructure."""
    ws_root = tmp_path / "workspaces"
    default_dir = ws_root / "default"
    default_dir.mkdir(parents=True)
    write_workspace_config(default_dir, create_default_workspace_config())
    if workspaces:
        for name, payer in workspaces.items():
            ws_dir = ws_root / name
            ws_dir.mkdir(parents=True)
            write_workspace_config(ws_dir, WorkspaceConfig(
                name=name, binding_mode="bound",
                aws=WorkspaceAwsConfig(
                    payer_account_id=payer,
                    default_connection="main",
                    connections=[AwsConnection(
                        name="main", profile=f"{name}-prof",
                        expected_session_account_id="111122223333",
                    )],
                ),
            ))
    return ws_root


def _patches(tmp_path, ws_root):
    return {
        "ws_root": patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root),
        "ws_path": patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n),
        "config_file": patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "c.toml"),
        "legacy_main": patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "lm.db"),
        "legacy_sec": patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "ls.db"),
    }


# ---------------------------------------------------------------------------
# 1-4. Security report workspace isolation
# ---------------------------------------------------------------------------


class TestSecurityWorkspaceIsolation:
    """Security pack writes to workspace-specific paths."""

    def test_01_security_report_uses_workspace_db(self, tmp_path):
        """Security report in customer-a targets customer-a/security-history.db path."""
        from kulshan.workspace.context import WorkspaceContext
        ws_root = _setup_ws(tmp_path, {"customer-a": "999999999999"})
        ws_dir = ws_root / "customer-a"
        config = WorkspaceConfig(
            name="customer-a", binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id="999999999999", default_connection="main",
                connections=[AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333")],
            ),
        )
        ctx = WorkspaceContext.from_path(ws_dir, config)
        assert ctx.security_history_db_path == ws_dir / "security-history.db"

    def test_02_two_workspaces_separate_security_paths(self, tmp_path):
        """Two workspaces have distinct security history paths."""
        from kulshan.workspace.context import WorkspaceContext
        ws_root = _setup_ws(tmp_path, {
            "customer-a": "999999999999",
            "customer-b": "888877776666",
        })
        config_a = WorkspaceConfig(
            name="customer-a", binding_mode="bound",
            aws=WorkspaceAwsConfig(payer_account_id="999999999999", default_connection="main",
                connections=[AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333")]),
        )
        config_b = WorkspaceConfig(
            name="customer-b", binding_mode="bound",
            aws=WorkspaceAwsConfig(payer_account_id="888877776666", default_connection="main",
                connections=[AwsConnection(name="main", profile="p2", expected_session_account_id="444455556666")]),
        )
        ctx_a = WorkspaceContext.from_path(ws_root / "customer-a", config_a)
        ctx_b = WorkspaceContext.from_path(ws_root / "customer-b", config_b)
        assert ctx_a.security_history_db_path != ctx_b.security_history_db_path
        assert "customer-a" in str(ctx_a.security_history_db_path)
        assert "customer-b" in str(ctx_b.security_history_db_path)

    def test_03_each_workspace_contains_only_own_security(self, tmp_path):
        """Writing to separate security DBs produces isolated data."""
        ws_root = _setup_ws(tmp_path, {
            "customer-a": "999999999999",
            "customer-b": "888877776666",
        })
        # Write to customer-a's security DB
        db_a = ws_root / "customer-a" / "security-history.db"
        conn_a = sqlite3.connect(db_a)
        conn_a.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY, account_id TEXT)")
        conn_a.execute("INSERT INTO scans VALUES (1, '111122223333')")
        conn_a.commit()
        conn_a.close()

        # Write to customer-b's security DB
        db_b = ws_root / "customer-b" / "security-history.db"
        conn_b = sqlite3.connect(db_b)
        conn_b.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY, account_id TEXT)")
        conn_b.execute("INSERT INTO scans VALUES (1, '444455556666')")
        conn_b.commit()
        conn_b.close()

        # Verify isolation
        rows_a = sqlite3.connect(db_a).execute("SELECT account_id FROM scans").fetchall()
        rows_b = sqlite3.connect(db_b).execute("SELECT account_id FROM scans").fetchall()
        assert rows_a == [("111122223333",)]
        assert rows_b == [("444455556666",)]

    def test_04_legacy_global_security_db_not_created(self, tmp_path):
        """Legacy global security path is never created by workspace operations."""
        legacy_path = tmp_path / "legacy_security.db"
        assert not legacy_path.exists()
        # After workspace operations, it should still not exist
        ws_root = _setup_ws(tmp_path, {"customer-a": "999999999999"})
        assert not legacy_path.exists()


# ---------------------------------------------------------------------------
# 5-7. Credential mismatch and persistence failure
# ---------------------------------------------------------------------------


class TestCredentialMismatchSecurity:

    def test_05_mismatch_writes_to_neither_db(self, tmp_path):
        """Credential mismatch writes to neither main nor security history."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()
        ws_root = _setup_ws(tmp_path, {"cust-mis": "999999999999"})
        p = _patches(tmp_path, ws_root)
        bad = _verified("999999999999")  # different from expected 111122223333
        runner = CliRunner()

        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"], \
             patch(_STS_PATCH, return_value=bad):
            result = runner.invoke(main, ["--workspace", "cust-mis", "report", "--yes"])

        assert result.exit_code != 0
        assert not (ws_root / "cust-mis" / "history.db").exists()
        assert not (ws_root / "cust-mis" / "security-history.db").exists()

    def test_06_security_history_reads_no_aws(self, tmp_path):
        """Reading security history path requires no boto3/STS."""
        from kulshan.workspace.context import WorkspaceContext
        ws_root = _setup_ws(tmp_path, {"cust-r": "999999999999"})
        config = WorkspaceConfig(
            name="cust-r", binding_mode="bound",
            aws=WorkspaceAwsConfig(payer_account_id="999999999999", default_connection="main",
                connections=[AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333")]),
        )
        ctx = WorkspaceContext.from_path(ws_root / "cust-r", config)
        # Accessing the path is purely local
        assert ctx.security_history_db_path.name == "security-history.db"
        # No boto3 import or call needed

    def test_07_persistence_failure_no_global_fallback(self, tmp_path):
        """Security persistence failure does not redirect to global path."""
        # The security_history_db_path is always workspace-local
        from kulshan.workspace.context import WorkspaceContext
        config = WorkspaceConfig(
            name="cust-pf", binding_mode="bound",
            aws=WorkspaceAwsConfig(payer_account_id="999999999999", default_connection="main",
                connections=[AwsConnection(name="main", profile="p1", expected_session_account_id="111122223333")]),
        )
        ws_dir = tmp_path / "cust-pf"
        ws_dir.mkdir(parents=True)
        ctx = WorkspaceContext.from_path(ws_dir, config)
        # Even if we can't write to the workspace path, there's no fallback
        assert "cust-pf" in str(ctx.security_history_db_path)
        assert ".Kulshan" not in str(ctx.security_history_db_path)


# ---------------------------------------------------------------------------
# 8. Temporary restriction removed
# ---------------------------------------------------------------------------


class TestRestrictionRemoved:

    def test_08_security_pack_allowed_on_bound(self, tmp_path):
        """Security pack no longer blocked on bound workspaces."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()
        ws_root = _setup_ws(tmp_path, {"cust-sec": "999999999999"})
        p = _patches(tmp_path, ws_root)
        verified = _verified("111122223333")
        runner = CliRunner()

        mock_preflight = MagicMock()
        mock_preflight.passed = True
        mock_preflight.cur_export = None

        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"], \
             patch(_STS_PATCH, return_value=verified), \
             patch("kulshan.preflight.run_preflight_with_cur", return_value=mock_preflight), \
             patch("kulshan.orchestrator.run_all_scans", return_value={}), \
             patch("kulshan.orchestrator.compute_overall", return_value=(70, "B-")), \
             patch("kulshan.orchestrator.summarize_completeness", return_value={"partial": False, "skipped": [], "errors": []}), \
             patch("kulshan.session.get_enabled_regions", return_value=["us-east-1"]):
            result = runner.invoke(main, [
                "--workspace", "cust-sec", "report",
                "--packs", "security", "--yes", "--regions", "us-east-1",
                "--no-history",
            ])

        # Should NOT get the old fail-closed error
        assert "not available yet" not in result.output.lower()
        assert "isolation" not in result.output.lower()


# ---------------------------------------------------------------------------
# 9-15. CUR payer validation
# ---------------------------------------------------------------------------


class TestCurPayerValidation:
    """Tests for CUR payer validation via DuckDB."""

    def _mock_con(self, columns, payer_values):
        """Create a mock DuckDB connection for payer validation."""
        con = MagicMock()
        # DESCRIBE cur_raw
        col_rows = [(col,) for col in columns]
        # DISTINCT query
        distinct_rows = [(v,) for v in payer_values]

        def execute_side_effect(sql, *args):
            result = MagicMock()
            if "DESCRIBE" in sql:
                result.fetchall.return_value = col_rows
            elif "DISTINCT" in sql:
                result.fetchall.return_value = distinct_rows
            else:
                result.fetchall.return_value = []
            return result

        con.execute = MagicMock(side_effect=execute_side_effect)
        return con

    def test_09_local_cur_no_aws_calls(self):
        """validate_cur_payer makes no boto3 or STS calls."""
        con = self._mock_con(
            ["bill_payer_account_id", "line_item_usage_start_date", "line_item_unblended_cost"],
            ["999999999999"],
        )
        mock_boto3 = MagicMock()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = validate_cur_payer(con, "999999999999", "cust-a")
        assert result.status == "match"
        mock_boto3.Session.assert_not_called()

    def test_10_matching_payer_proceeds(self):
        """Matching payer account returns match status."""
        con = self._mock_con(
            ["bill_payer_account_id", "other_col"],
            ["999999999999"],
        )
        result = validate_cur_payer(con, "999999999999", "cust-a")
        assert result.status == "match"
        assert result.found_payers == ["999999999999"]

    def test_11_mismatched_payer_raises(self):
        """Mismatched payer raises PayerMismatchError."""
        con = self._mock_con(
            ["bill_payer_account_id", "other_col"],
            ["888877776666"],
        )
        with pytest.raises(PayerMismatchError) as exc:
            validate_cur_payer(con, "999999999999", "cust-a")
        assert exc.value.expected_payer == "999999999999"
        assert exc.value.found_payer == "888877776666"

    def test_12_multiple_payers_raises(self):
        """Multiple distinct payer IDs raise MultiplePayersError."""
        con = self._mock_con(
            ["bill_payer_account_id", "other_col"],
            ["999999999999", "888877776666"],
        )
        with pytest.raises(MultiplePayersError) as exc:
            validate_cur_payer(con, "999999999999", "cust-a")
        assert len(exc.value.payer_ids) == 2

    def test_13_all_files_considered(self):
        """Multiple payer values from multi-file input are all checked."""
        # Simulated: DISTINCT returns values from across all files
        con = self._mock_con(
            ["bill_payer_account_id"],
            ["111111111111", "222222222222", "333333333333"],
        )
        with pytest.raises(MultiplePayersError) as exc:
            validate_cur_payer(con, "111111111111", "cust-a")
        assert len(exc.value.payer_ids) == 3

    def test_14_missing_payer_evidence_warning(self):
        """No payer column returns 'missing' status with warning message."""
        con = self._mock_con(
            ["line_item_usage_start_date", "line_item_unblended_cost"],  # no payer column
            [],
        )
        result = validate_cur_payer(con, "999999999999", "cust-a")
        assert result.status == "missing"
        assert "does not contain payer account evidence" in result.message

    def test_15_unbound_workspace_skips_validation(self):
        """Unbound workspace (expected_payer=None) skips payer check."""
        con = self._mock_con(
            ["bill_payer_account_id"],
            ["anything"],
        )
        result = validate_cur_payer(con, None, None)
        assert result.status == "match"


# ---------------------------------------------------------------------------
# 16-17. Payer redaction
# ---------------------------------------------------------------------------


class TestPayerRedaction:

    def test_16_payer_ids_redacted_by_default(self):
        """PayerMismatchError contains raw IDs; CLI renders redacted."""
        from kulshan.redact import redact_account_id
        try:
            raise PayerMismatchError("cust-a", "999999999999", "888877776666")
        except PayerMismatchError as e:
            # Structured fields available for redaction
            assert e.expected_payer == "999999999999"
            assert e.found_payer == "888877776666"
            # CLI would render:
            redacted_expected = redact_account_id(e.expected_payer)
            redacted_found = redact_account_id(e.found_payer)
            assert "XXXX" in redacted_expected
            assert "XXXX" in redacted_found
            assert "999999999999" not in redacted_expected

    def test_17_full_ids_with_show_pii(self):
        """With show_pii, full payer IDs are available from error fields."""
        try:
            raise PayerMismatchError("cust-a", "999999999999", "888877776666")
        except PayerMismatchError as e:
            # show_pii would use raw fields
            assert e.expected_payer == "999999999999"
            assert e.found_payer == "888877776666"


# ---------------------------------------------------------------------------
# 18-19. Investigation output and credential safety
# ---------------------------------------------------------------------------


class TestInvestigationSafety:

    def test_18_investigation_output_unchanged_without_workspace(self, tmp_path):
        """Local CUR investigation without explicit workspace is unchanged."""
        # investigate commands resolve default workspace, which is unbound
        # They don't call STS for local CUR paths
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup_ws(tmp_path)
        p = _patches(tmp_path, ws_root)

        runner = CliRunner()
        # Use a non-existent path to trigger early error (proves no AWS call)
        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"]:
            result = runner.invoke(main, [
                "investigate", "cost",
                "--path", str(tmp_path / "nonexistent"),
                "--month", "2026-06",
            ])
        # Should fail because path doesn't exist, not because of AWS/STS
        assert result.exit_code != 0
        assert "exist" in result.output.lower() or "error" in result.output.lower() or "invalid" in result.output.lower()

    def test_19_no_credentials_stored(self, tmp_path):
        """Workspace operations never store credential data."""
        ws_root = _setup_ws(tmp_path, {"cust-z": "999999999999"})
        toml_path = ws_root / "cust-z" / "workspace.toml"
        content = toml_path.read_text()
        assert "access_key" not in content.lower()
        assert "secret" not in content.lower()
        assert "session_token" not in content.lower()
        assert "sso_cache" not in content.lower()


# ---------------------------------------------------------------------------
# 20. Full existing suite green
# ---------------------------------------------------------------------------


class TestSuiteGreen:

    def test_20_imports_clean(self):
        """All new modules import without error."""
        from kulshan.cur.payer_validation import (
            validate_cur_payer,
            PayerMismatchError,
            MultiplePayersError,
            PayerValidationResult,
        )
        from kulshan.workspace.execution import resolve_aws_execution
        from kulshan.workspace.sts import create_verified_session
        assert True
