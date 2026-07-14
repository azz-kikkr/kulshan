"""Tests for legacy database migration into default workspace.

Covers all 10 upgrade scenarios:
1. Fresh installation with neither legacy database.
2. Only main history exists.
3. Only security history exists.
4. Both databases exist.
5. Destination already exists (skip, don't overwrite).
6. One database succeeds while the other fails.
7. A previous successful migration left a .migrated backup.
8. Repeated execution is harmless (idempotent).
9. Destination database is corrupt or incomplete.
10. Legacy history contains scans from several credential accounts.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from kulshan.workspace.config import (
    WorkspaceConfig,
    WorkspaceMigrationStatus,
    write_workspace_config,
)
from kulshan.workspace.migration import (
    MigrationReport,
    SingleMigrationResult,
    _check_integrity,
    _count_rows,
    _sqlite_backup,
    migrate_legacy_to_default_workspace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Main history schema (matches kulshan/src/kulshan/history/__init__.py)
_MAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    account_id TEXT,
    regions TEXT,
    duration_seconds REAL,
    overall_score INTEGER,
    overall_grade TEXT,
    total_findings INTEGER DEFAULT 0,
    critical_findings INTEGER DEFAULT 0,
    high_findings INTEGER DEFAULT 0,
    medium_findings INTEGER DEFAULT 0,
    low_findings INTEGER DEFAULT 0,
    pack_scores TEXT,
    kulshan_version TEXT,
    full_result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_timestamp ON scans(timestamp);
CREATE INDEX IF NOT EXISTS idx_scans_account ON scans(account_id);
"""

# Security history schema (matches checks/security/scoring/history.py)
_SECURITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    overall_score REAL,
    overall_grade TEXT,
    total_findings INTEGER,
    critical INTEGER,
    high INTEGER,
    medium INTEGER,
    low INTEGER,
    category_scores TEXT,
    exposure_score REAL,
    scan_duration REAL,
    regions INTEGER,
    summary TEXT
);
"""


def _create_main_db(path: Path, scans: list[dict] | None = None) -> Path:
    """Create a main history database with optional scan rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_MAIN_SCHEMA)
    if scans:
        for scan in scans:
            conn.execute(
                """INSERT INTO scans (id, timestamp, account_id, regions,
                   duration_seconds, overall_score, overall_grade,
                   total_findings, critical_findings, high_findings,
                   medium_findings, low_findings, kulshan_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan.get("id", "abc123"),
                    scan.get("timestamp", "2026-06-01T00:00:00Z"),
                    scan.get("account_id", "111122223333"),
                    scan.get("regions", '["us-east-1"]'),
                    scan.get("duration_seconds", 5.0),
                    scan.get("overall_score", 75),
                    scan.get("overall_grade", "C"),
                    scan.get("total_findings", 3),
                    scan.get("critical_findings", 0),
                    scan.get("high_findings", 1),
                    scan.get("medium_findings", 1),
                    scan.get("low_findings", 1),
                    scan.get("kulshan_version", "0.2.0"),
                ),
            )
    conn.commit()
    conn.close()
    return path


def _create_security_db(path: Path, scans: list[dict] | None = None) -> Path:
    """Create a security history database with optional scan rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SECURITY_SCHEMA)
    if scans:
        for scan in scans:
            conn.execute(
                """INSERT INTO scans (account_id, scan_date, overall_score,
                   overall_grade, total_findings, critical, high, medium, low,
                   exposure_score, scan_duration, regions)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan.get("account_id", "111122223333"),
                    scan.get("scan_date", "2026-06-01T00:00:00"),
                    scan.get("overall_score", 65.0),
                    scan.get("overall_grade", "D"),
                    scan.get("total_findings", 10),
                    scan.get("critical", 1),
                    scan.get("high", 2),
                    scan.get("medium", 3),
                    scan.get("low", 4),
                    scan.get("exposure_score", 0.5),
                    scan.get("scan_duration", 12.0),
                    scan.get("regions", 3),
                ),
            )
    conn.commit()
    conn.close()
    return path


def _setup_workspace(tmp_path: Path) -> Path:
    """Create a default workspace directory with config."""
    ws_root = tmp_path / "workspaces"
    default_dir = ws_root / "default"
    default_dir.mkdir(parents=True)
    config = WorkspaceConfig(
        name="default",
        display_name="Default",
        binding_mode="unbound",
        migration=WorkspaceMigrationStatus(
            main_history="pending",
            security_history="pending",
        ),
    )
    write_workspace_config(default_dir, config)
    return ws_root


def _patch_paths(tmp_path, ws_root):
    """Return context managers that patch all path functions."""
    legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
    legacy_security = tmp_path / "legacy" / ".Kulshan" / "security" / "history.db"
    default_dir = ws_root / "default"
    return {
        "ws_root": patch(
            "kulshan.workspace.resolution.get_workspaces_root",
            return_value=ws_root,
        ),
        "ws_path": patch(
            "kulshan.workspace.resolution.get_workspace_path",
            side_effect=lambda n: ws_root / n,
        ),
        "config_file": patch(
            "kulshan.workspace.resolution.get_config_file_path",
            return_value=tmp_path / "config" / "config.toml",
        ),
        "legacy_main": patch(
            "kulshan.workspace.migration.get_legacy_history_path",
            return_value=legacy_main,
        ),
        "legacy_security": patch(
            "kulshan.workspace.migration.get_legacy_security_history_path",
            return_value=legacy_security,
        ),
        "ensure_default": patch(
            "kulshan.workspace.migration.ensure_default_workspace",
            return_value=default_dir,
        ),
    }


# ---------------------------------------------------------------------------
# Scenario 1: Fresh installation with neither legacy database
# ---------------------------------------------------------------------------


class TestScenario1FreshInstall:
    """Fresh install — no legacy databases exist."""

    def test_returns_not_found_for_both(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "not_found"
        assert report.security_history.status == "not_found"
        assert not report.any_migrated
        assert not report.any_failed


# ---------------------------------------------------------------------------
# Scenario 2: Only main history exists
# ---------------------------------------------------------------------------


class TestScenario2OnlyMainExists:
    """Only main history database exists."""

    def test_migrates_main_only(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        # Create main history
        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "scan001", "account_id": "111122223333"}])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "migrated"
        assert report.main_history.row_count == 1
        assert report.security_history.status == "not_found"

        # Source renamed to .migrated
        assert not legacy_main.exists()
        assert legacy_main.with_suffix(".db.migrated").exists()

        # Destination has the data
        dest = ws_root / "default" / "history.db"
        assert dest.exists()
        assert _count_rows(dest, "scans") == 1


# ---------------------------------------------------------------------------
# Scenario 3: Only security history exists
# ---------------------------------------------------------------------------


class TestScenario3OnlySecurityExists:
    """Only security history database exists."""

    def test_migrates_security_only(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        # Create security history
        legacy_security = tmp_path / "legacy" / ".Kulshan" / "security" / "history.db"
        _create_security_db(legacy_security, [{"account_id": "444455556666"}])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "not_found"
        assert report.security_history.status == "migrated"
        assert report.security_history.row_count == 1

        # Source renamed
        assert not legacy_security.exists()
        assert legacy_security.with_suffix(".db.migrated").exists()

        # Destination has data
        dest = ws_root / "default" / "security-history.db"
        assert dest.exists()
        assert _count_rows(dest, "scans") == 1


# ---------------------------------------------------------------------------
# Scenario 4: Both databases exist
# ---------------------------------------------------------------------------


class TestScenario4BothExist:
    """Both legacy databases exist and are migrated."""

    def test_migrates_both(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        legacy_security = tmp_path / "legacy" / ".Kulshan" / "security" / "history.db"
        _create_main_db(legacy_main, [
            {"id": "s1", "account_id": "111122223333"},
            {"id": "s2", "account_id": "444455556666"},
        ])
        _create_security_db(legacy_security, [
            {"account_id": "111122223333"},
        ])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "migrated"
        assert report.main_history.row_count == 2
        assert report.security_history.status == "migrated"
        assert report.security_history.row_count == 1
        assert report.any_migrated
        assert not report.any_failed


# ---------------------------------------------------------------------------
# Scenario 5: Destination already exists
# ---------------------------------------------------------------------------


class TestScenario5DestinationExists:
    """Destination already has data — migration should skip."""

    def test_skips_when_dest_has_data(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        # Create legacy source
        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "new-scan"}])

        # Pre-populate destination with existing data
        dest = ws_root / "default" / "history.db"
        _create_main_db(dest, [{"id": "existing-scan"}])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        # Should skip, not overwrite
        assert report.main_history.status == "skipped"
        assert report.main_history.error is not None
        assert "different data" in report.main_history.error

        # Source is preserved (not renamed)
        assert legacy_main.exists()

        # Destination retains original data
        assert _count_rows(dest, "scans") == 1


# ---------------------------------------------------------------------------
# Scenario 6: One succeeds, the other fails
# ---------------------------------------------------------------------------


class TestScenario6PartialSuccess:
    """One database migrates successfully while the other fails."""

    def test_main_succeeds_security_fails(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        # Create valid main history
        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "good-scan"}])

        # Create corrupt security history
        legacy_security = tmp_path / "legacy" / ".Kulshan" / "security" / "history.db"
        legacy_security.parent.mkdir(parents=True, exist_ok=True)
        legacy_security.write_bytes(b"this is not a sqlite database at all")

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "migrated"
        assert report.security_history.status == "failed"
        assert report.any_migrated
        assert report.any_failed

        # Main source renamed, security source preserved
        assert not legacy_main.exists()
        assert legacy_security.exists()


# ---------------------------------------------------------------------------
# Scenario 7: Previous .migrated backup exists
# ---------------------------------------------------------------------------


class TestScenario7PreviousMigratedBackup:
    """A previous successful migration left a .migrated file."""

    def test_detects_previous_migration(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        # No source, but .migrated exists from a previous run
        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        migrated_path = legacy_main.with_suffix(".db.migrated")
        migrated_path.parent.mkdir(parents=True, exist_ok=True)
        _create_main_db(migrated_path, [{"id": "old-scan"}])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        # Should recognize as already migrated
        assert report.main_history.status == "migrated"


# ---------------------------------------------------------------------------
# Scenario 8: Repeated execution is idempotent
# ---------------------------------------------------------------------------


class TestScenario8Idempotent:
    """Running migration multiple times produces consistent results."""

    def test_second_run_is_harmless(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "scan-1"}])

        # First migration
        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report1 = migrate_legacy_to_default_workspace()

        assert report1.main_history.status == "migrated"

        # Second migration — config now says "migrated"
        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report2 = migrate_legacy_to_default_workspace()

        # Should skip (already migrated)
        assert report2.main_history.status == "skipped"

        # Data unchanged
        dest = ws_root / "default" / "history.db"
        assert _count_rows(dest, "scans") == 1


# ---------------------------------------------------------------------------
# Scenario 9: Destination database is corrupt or incomplete
# ---------------------------------------------------------------------------


class TestScenario9DestinationCorrupt:
    """Destination exists but is corrupt/empty — migration should proceed."""

    def test_proceeds_when_dest_exists_but_empty(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "scan-x"}])

        # Create empty destination file (0 bytes or no table)
        dest = ws_root / "default" / "history.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "migrated"
        assert _count_rows(dest, "scans") == 1

    def test_proceeds_when_dest_has_no_scans_table(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "scan-y"}])

        # Create destination with a different table (no scans)
        dest = ws_root / "default" / "history.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(dest)
        conn.execute("CREATE TABLE other_table (x TEXT)")
        conn.commit()
        conn.close()

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "migrated"
        assert _count_rows(dest, "scans") == 1


# ---------------------------------------------------------------------------
# Scenario 10: Multi-account scans preserved unchanged
# ---------------------------------------------------------------------------


class TestScenario10MultiAccountPreserved:
    """Legacy history with scans from several accounts is preserved intact."""

    def test_all_accounts_preserved(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        scans = [
            {"id": "s1", "account_id": "111122223333", "overall_score": 80},
            {"id": "s2", "account_id": "444455556666", "overall_score": 60},
            {"id": "s3", "account_id": "777788889999", "overall_score": 90},
            {"id": "s4", "account_id": "111122223333", "overall_score": 85},
            {"id": "s5", "account_id": "444455556666", "overall_score": 70},
        ]
        _create_main_db(legacy_main, scans)

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "migrated"
        assert report.main_history.row_count == 5

        # Verify all scans are preserved with correct data
        dest = ws_root / "default" / "history.db"
        conn = sqlite3.connect(dest)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, account_id, overall_score FROM scans ORDER BY id"
        ).fetchall()
        conn.close()

        assert len(rows) == 5
        row_data = [(r["id"], r["account_id"], r["overall_score"]) for r in rows]
        assert ("s1", "111122223333", 80) in row_data
        assert ("s2", "444455556666", 60) in row_data
        assert ("s3", "777788889999", 90) in row_data
        assert ("s4", "111122223333", 85) in row_data
        assert ("s5", "444455556666", 70) in row_data


# ---------------------------------------------------------------------------
# SQLite helper unit tests
# ---------------------------------------------------------------------------


class TestSQLiteHelpers:
    """Tests for low-level SQLite helper functions."""

    def test_integrity_check_valid_db(self, tmp_path):
        db = tmp_path / "good.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.commit()
        conn.close()
        assert _check_integrity(db) is None

    def test_integrity_check_corrupt_file(self, tmp_path):
        db = tmp_path / "bad.db"
        db.write_bytes(b"not a database")
        result = _check_integrity(db)
        assert result is not None

    def test_count_rows_existing_table(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE items (id INTEGER)")
        conn.execute("INSERT INTO items VALUES (1)")
        conn.execute("INSERT INTO items VALUES (2)")
        conn.commit()
        conn.close()
        assert _count_rows(db, "items") == 2

    def test_count_rows_missing_table(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE other (x TEXT)")
        conn.commit()
        conn.close()
        assert _count_rows(db, "scans") is None

    def test_sqlite_backup_success(self, tmp_path):
        src = tmp_path / "src.db"
        dst = tmp_path / "dst.db"
        conn = sqlite3.connect(src)
        conn.execute("CREATE TABLE data (val TEXT)")
        conn.execute("INSERT INTO data VALUES ('hello')")
        conn.commit()
        conn.close()

        error = _sqlite_backup(src, dst)
        assert error is None
        assert dst.exists()

        # Verify content
        conn = sqlite3.connect(dst)
        row = conn.execute("SELECT val FROM data").fetchone()
        conn.close()
        assert row[0] == "hello"

    def test_sqlite_backup_nonexistent_source(self, tmp_path):
        """Backup from non-existent source creates empty dest (SQLite behavior)."""
        src = tmp_path / "missing.db"
        dst = tmp_path / "dst.db"
        # SQLite connect() creates the file, so backup "succeeds" with empty db
        # This is fine — the migration logic checks source existence first
        error = _sqlite_backup(src, dst)
        # On most systems this will succeed (empty->empty copy)
        # The real guard is _migrate_single_database checking source_path.exists()
        assert dst.exists() or error is not None


# ---------------------------------------------------------------------------
# Migration status tracking tests
# ---------------------------------------------------------------------------


class TestMigrationStatusTracking:
    """Verify migration status is written to workspace config."""

    def test_status_written_after_migration(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "scan-z"}])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            migrate_legacy_to_default_workspace()

        # Read config and verify status
        from kulshan.workspace.config import read_workspace_config
        config = read_workspace_config(ws_root / "default")
        assert config.migration is not None
        assert config.migration.main_history == "migrated"
        assert config.migration.security_history == "not_found"

    def test_failed_status_written(self, tmp_path):
        ws_root = _setup_workspace(tmp_path)
        patches = _patch_paths(tmp_path, ws_root)

        # Create corrupt main history
        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        legacy_main.parent.mkdir(parents=True, exist_ok=True)
        legacy_main.write_bytes(b"corrupt data here")

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["ensure_default"]:
            migrate_legacy_to_default_workspace()

        from kulshan.workspace.config import read_workspace_config
        config = read_workspace_config(ws_root / "default")
        assert config.migration.main_history == "failed"
