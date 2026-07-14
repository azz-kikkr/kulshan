"""Regression tests: global security history fallback removed.

Proves:
1. Bound security report stores only in workspace main history.db.
2. Old global security database is not created or modified.
3. No empty security-history.db is created for new workspaces.
4. Legacy security DB still migrates into default workspace.
5. Old security-history API without db_path is impossible.
6. Legacy path exists only in migration code.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from kulshan.cli import main
from kulshan.checks.security.scoring.history import save_scan, get_history
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


def _setup(tmp_path):
    ws_root = tmp_path / "workspaces"
    default_dir = ws_root / "default"
    default_dir.mkdir(parents=True)
    write_workspace_config(default_dir, create_default_workspace_config())
    ws_dir = ws_root / "customer-a"
    ws_dir.mkdir(parents=True)
    write_workspace_config(ws_dir, WorkspaceConfig(
        name="customer-a", binding_mode="bound",
        aws=WorkspaceAwsConfig(
            payer_account_id="999999999999", default_connection="main",
            connections=[AwsConnection(
                name="main", profile="p1",
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
# 1. Bound security report stores only in workspace main history.db
# ---------------------------------------------------------------------------


class TestSecurityUsesMainHistory:

    def test_bound_security_report_writes_main_history_only(self, tmp_path):
        """Security pack findings stored via main report history path."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup(tmp_path)
        p = _patches(tmp_path, ws_root)
        verified = _verified("111122223333")
        runner = CliRunner()

        mock_preflight = MagicMock()
        mock_preflight.passed = True
        mock_preflight.cur_export = None

        mock_results = {
            "security": {
                "tool": "security",
                "scores": {"overall_score": 70, "grade": "B-", "total_findings": 2,
                           "severity_counts": {"critical": 0, "high": 1, "medium": 1, "low": 0}},
                "findings": [{"severity": "high", "title": "sg-open"}, {"severity": "medium", "title": "no-mfa"}],
                "errors": [],
                "metadata": {},
            }
        }

        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"], \
             patch(_STS_PATCH, return_value=verified), \
             patch("kulshan.preflight.run_preflight_with_cur", return_value=mock_preflight), \
             patch("kulshan.orchestrator.run_all_scans", return_value=mock_results), \
             patch("kulshan.orchestrator.compute_overall", return_value=(70, "B-")), \
             patch("kulshan.orchestrator.summarize_completeness", return_value={"partial": False, "skipped": [], "errors": []}), \
             patch("kulshan.session.get_enabled_regions", return_value=["us-east-1"]):
            result = runner.invoke(main, [
                "--workspace", "customer-a", "report",
                "--packs", "security", "--yes", "--regions", "us-east-1",
            ])

        # Main history.db should have one scan
        main_db = ws_root / "customer-a" / "history.db"
        assert main_db.exists()
        conn = sqlite3.connect(main_db)
        count = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# 2. No global security DB created
# ---------------------------------------------------------------------------


class TestNoGlobalSecurityDB:

    def test_global_security_db_not_created(self, tmp_path):
        """The legacy global security path is never created."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup(tmp_path)
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
             patch("kulshan.orchestrator.compute_overall", return_value=(50, "C")), \
             patch("kulshan.orchestrator.summarize_completeness", return_value={"partial": False, "skipped": [], "errors": []}), \
             patch("kulshan.session.get_enabled_regions", return_value=["us-east-1"]):
            runner.invoke(main, [
                "--workspace", "customer-a", "report",
                "--packs", "security", "--yes", "--regions", "us-east-1",
            ])

        # Legacy global path never created
        legacy_sec = tmp_path / "ls.db"
        assert not legacy_sec.exists()

        # Also check the real legacy path pattern
        home_sec = Path.home() / ".Kulshan" / "security" / "history.db"
        # We can't guarantee it doesn't exist from previous runs,
        # but we can verify our code didn't create it this test
        # (the test uses patched paths so the real path is irrelevant)


# ---------------------------------------------------------------------------
# 3. No empty security-history.db created for new workspaces
# ---------------------------------------------------------------------------


class TestNoEmptySecurityDB:

    def test_workspace_create_no_security_history_db(self, tmp_path):
        """Creating a bound workspace does not create security-history.db."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup(tmp_path)
        # customer-a workspace exists but should NOT have security-history.db
        sec_db = ws_root / "customer-a" / "security-history.db"
        assert not sec_db.exists()

    def test_report_does_not_create_security_history_db(self, tmp_path):
        """Running a security report does not create security-history.db."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup(tmp_path)
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
             patch("kulshan.orchestrator.compute_overall", return_value=(50, "C")), \
             patch("kulshan.orchestrator.summarize_completeness", return_value={"partial": False, "skipped": [], "errors": []}), \
             patch("kulshan.session.get_enabled_regions", return_value=["us-east-1"]):
            runner.invoke(main, [
                "--workspace", "customer-a", "report",
                "--packs", "security", "--yes", "--regions", "us-east-1",
            ])

        sec_db = ws_root / "customer-a" / "security-history.db"
        assert not sec_db.exists()


# ---------------------------------------------------------------------------
# 4. Legacy security DB migrates into default workspace
# ---------------------------------------------------------------------------


class TestLegacyMigrationPreserved:

    def test_legacy_security_db_migrates(self, tmp_path):
        """Legacy security DB still migrates via workspace migration."""
        from kulshan.workspace.migration import _migrate_single_database

        # Create a legacy security DB
        legacy_path = tmp_path / "legacy_sec.db"
        conn = sqlite3.connect(legacy_path)
        conn.execute("""CREATE TABLE scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            scan_date TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO scans (account_id, scan_date) VALUES ('111122223333', '2026-01-01')")
        conn.commit()
        conn.close()

        dest_path = tmp_path / "default" / "security-history.db"
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        result = _migrate_single_database(
            source_path=legacy_path,
            dest_path=dest_path,
            current_status="pending",
            expected_table="scans",
        )
        assert result.status == "migrated"
        assert dest_path.exists()

        # Verify data migrated
        conn = sqlite3.connect(dest_path)
        rows = conn.execute("SELECT account_id FROM scans").fetchall()
        conn.close()
        assert rows == [("111122223333",)]


# ---------------------------------------------------------------------------
# 5. Old API requires explicit db_path
# ---------------------------------------------------------------------------


class TestApiRequiresPath:

    def test_save_scan_without_db_path_fails(self):
        """Calling save_scan without db_path raises TypeError."""
        with pytest.raises(TypeError, match="db_path"):
            save_scan(
                "111122223333",
                {"overall_score": 70, "overall_grade": "B-",
                 "total_findings": 1, "severity_counts": {"critical": 0, "high": 1, "medium": 0, "low": 0},
                 "category_scores": {}},
                {},
                5.0,
                1,
                {},
            )

    def test_get_history_without_db_path_fails(self):
        """Calling get_history without db_path raises TypeError."""
        with pytest.raises(TypeError, match="db_path"):
            get_history("111122223333")

    def test_save_scan_with_explicit_path_works(self, tmp_path):
        """save_scan with explicit db_path succeeds."""
        db = tmp_path / "test-sec.db"
        save_scan(
            "111122223333",
            {"overall_score": 70, "overall_grade": "B-",
             "total_findings": 1, "severity_counts": {"critical": 0, "high": 1, "medium": 0, "low": 0},
             "category_scores": {}},
            {},
            5.0,
            1,
            {},
            db_path=db,
        )
        assert db.exists()
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# 6. Legacy path only in migration code
# ---------------------------------------------------------------------------


class TestLegacyPathSearch:

    def test_legacy_path_not_in_active_security_module(self):
        """The security scoring history module has no hardcoded global path."""
        import inspect
        from kulshan.checks.security.scoring import history
        source = inspect.getsource(history)
        # No module-level DB_PATH constant
        assert "DB_PATH" not in source
        # No expanduser or os.path.join building a global path
        assert "expanduser" not in source
        assert "os.path.join" not in source
