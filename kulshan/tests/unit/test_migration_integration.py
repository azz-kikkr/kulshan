"""Integration tests proving the migration fix is a complete product feature.

Proving points:
1. Workspace resolution invokes migration automatically.
2. Migrated history is immediately visible from the default workspace store.
3. Named unbound workspace configuration is rejected.
4. Source and destination scan ID sets are identical.
5. Existing valid destination is reconciled safely.
6. Existing corrupt destination produces a failure without modifying the source.
7. Existing .migrated backup is never overwritten.
8. Migration still makes no AWS or STS calls.
"""
from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    WorkspaceMigrationStatus,
    read_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.errors import (
    AmbiguousProfileError,
    WorkspaceConfigError,
)
from kulshan.workspace.migration import (
    _get_id_set,
    migrate_legacy_to_default_workspace,
)
from kulshan.workspace.resolution import (
    _reset_migration_guard,
    resolve_workspace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _create_main_db(path: Path, scans: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_MAIN_SCHEMA)
    for scan in scans:
        conn.execute(
            """INSERT INTO scans (id, timestamp, account_id, regions,
               duration_seconds, overall_score, overall_grade,
               total_findings, kulshan_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scan["id"],
                scan.get("timestamp", "2026-06-01T00:00:00Z"),
                scan.get("account_id", "111122223333"),
                '["us-east-1"]',
                5.0, 75, "C", 3, "0.2.0",
            ),
        )
    conn.commit()
    conn.close()
    return path


def _setup_default_workspace(tmp_path: Path) -> Path:
    ws_root = tmp_path / "workspaces"
    default_dir = ws_root / "default"
    default_dir.mkdir(parents=True)
    config = WorkspaceConfig(
        name="default",
        binding_mode="unbound",
        migration=WorkspaceMigrationStatus(
            main_history="pending",
            security_history="pending",
        ),
    )
    write_workspace_config(default_dir, config)
    return ws_root


def _common_patches(tmp_path, ws_root):
    """Return dict of context managers for patching paths."""
    legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
    legacy_security = tmp_path / "legacy" / ".Kulshan" / "security" / "history.db"
    default_dir = ws_root / "default"
    return {
        "ws_root": patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root),
        "ws_path": patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n),
        "config_file": patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "config" / "config.toml"),
        "legacy_main": patch("kulshan.workspace.migration.get_legacy_history_path", return_value=legacy_main),
        "legacy_security": patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=legacy_security),
        "mig_ensure": patch("kulshan.workspace.migration.ensure_default_workspace", return_value=default_dir),
    }


# ---------------------------------------------------------------------------
# 1. Workspace resolution invokes migration automatically
# ---------------------------------------------------------------------------


class TestResolutionInvokesMigration:
    """resolve_workspace() triggers migration for legacy databases."""

    def test_resolve_workspace_migrates_legacy_db(self, tmp_path):
        _reset_migration_guard()
        ws_root = _setup_default_workspace(tmp_path)
        patches = _common_patches(tmp_path, ws_root)

        # Create legacy main history
        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "scan-auto-1"}])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], \
             patch.dict(os.environ, {}, clear=True):
            os.environ.pop("KULSHAN_WORKSPACE", None)
            ctx = resolve_workspace(None)

        # Migration should have run — dest has data
        dest = ws_root / "default" / "history.db"
        assert dest.exists()
        conn = sqlite3.connect(dest)
        row = conn.execute("SELECT id FROM scans").fetchone()
        conn.close()
        assert row[0] == "scan-auto-1"

        # Source renamed
        assert not legacy_main.exists()
        assert legacy_main.with_suffix(".db.migrated").exists()


# ---------------------------------------------------------------------------
# 2. Migrated history is immediately visible from default workspace store
# ---------------------------------------------------------------------------


class TestMigratedHistoryVisible:
    """After migration, history is accessible via workspace paths."""

    def test_history_db_path_in_context(self, tmp_path):
        _reset_migration_guard()
        ws_root = _setup_default_workspace(tmp_path)
        patches = _common_patches(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [
            {"id": "s1", "account_id": "111122223333"},
            {"id": "s2", "account_id": "444455556666"},
        ])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], \
             patch.dict(os.environ, {}, clear=True):
            os.environ.pop("KULSHAN_WORKSPACE", None)
            ctx = resolve_workspace(None)

        # Context points to workspace history
        assert ctx.history_db_path == ws_root / "default" / "history.db"
        assert ctx.history_db_path.exists()

        # Data is readable
        conn = sqlite3.connect(ctx.history_db_path)
        rows = conn.execute("SELECT id FROM scans ORDER BY id").fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["s1", "s2"]


# ---------------------------------------------------------------------------
# 3. Named unbound workspace configuration is rejected
# ---------------------------------------------------------------------------


class TestNamedUnboundRejected:
    """Only default may be unbound. Named workspaces must be bound."""

    def test_named_unbound_rejected(self):
        data = {
            "schema_version": 1,
            "name": "customer-a",
            "binding_mode": "unbound",
        }
        with pytest.raises(WorkspaceConfigError) as exc:
            WorkspaceConfig.from_dict(data, "customer-a")
        assert "Only the 'default' workspace may be unbound" in str(exc.value)

    def test_default_unbound_accepted(self):
        data = {
            "schema_version": 1,
            "name": "default",
            "binding_mode": "unbound",
        }
        config = WorkspaceConfig.from_dict(data, "default")
        assert config.binding_mode == "unbound"

    def test_named_bound_with_aws_accepted(self):
        data = {
            "schema_version": 1,
            "name": "customer-a",
            "binding_mode": "bound",
            "aws": {
                "payer_account_id": "111122223333",
                "default_connection": "main",
                "connections": [{
                    "name": "main",
                    "profile": "cust-a",
                    "expected_session_account_id": "111122223333",
                }],
            },
        }
        config = WorkspaceConfig.from_dict(data, "customer-a")
        assert config.binding_mode == "bound"
        assert config.is_bound


# ---------------------------------------------------------------------------
# 4. Source and destination scan ID sets are identical
# ---------------------------------------------------------------------------


class TestIdSetVerification:
    """Migration verifies full ID set identity, not just counts."""

    def test_id_sets_match_after_migration(self, tmp_path):
        ws_root = _setup_default_workspace(tmp_path)
        patches = _common_patches(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        scan_ids = [f"id-{i:04d}" for i in range(20)]
        scans = [{"id": sid, "account_id": "111122223333"} for sid in scan_ids]
        _create_main_db(legacy_main, scans)

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["mig_ensure"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "migrated"

        # Verify ID sets
        dest = ws_root / "default" / "history.db"
        source_ids = set(scan_ids)
        dest_ids = _get_id_set(dest, "scans")
        assert source_ids == dest_ids


# ---------------------------------------------------------------------------
# 5. Existing valid destination is reconciled safely
# ---------------------------------------------------------------------------


class TestExistingDestReconciled:
    """When destination matches source, reconcile metadata safely."""

    def test_matching_dest_reconciled_as_migrated(self, tmp_path):
        ws_root = _setup_default_workspace(tmp_path)
        patches = _common_patches(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [
            {"id": "scan-1"},
            {"id": "scan-2"},
        ])

        # Pre-copy to destination (simulating interrupted previous migration)
        dest = ws_root / "default" / "history.db"
        _create_main_db(dest, [
            {"id": "scan-1"},
            {"id": "scan-2"},
        ])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["mig_ensure"]:
            report = migrate_legacy_to_default_workspace()

        # Should reconcile as migrated
        assert report.main_history.status == "migrated"
        assert report.main_history.row_count == 2

        # Source should be renamed to .migrated
        assert not legacy_main.exists()
        assert legacy_main.with_suffix(".db.migrated").exists()


# ---------------------------------------------------------------------------
# 6. Existing corrupt destination fails without modifying source
# ---------------------------------------------------------------------------


class TestCorruptDestFails:
    """Corrupt destination fails safely, source untouched."""

    def test_corrupt_dest_fails_preserves_source(self, tmp_path):
        ws_root = _setup_default_workspace(tmp_path)
        patches = _common_patches(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "valid-scan"}])

        # Write corrupt data to destination
        dest = ws_root / "default" / "history.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"corrupt database content here!!!!")

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["mig_ensure"]:
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "failed"
        assert "corrupt" in report.main_history.error.lower()

        # Source is preserved unchanged
        assert legacy_main.exists()
        conn = sqlite3.connect(legacy_main)
        row = conn.execute("SELECT id FROM scans").fetchone()
        conn.close()
        assert row[0] == "valid-scan"


# ---------------------------------------------------------------------------
# 7. Existing .migrated backup is never overwritten
# ---------------------------------------------------------------------------


class TestMigratedBackupProtection:
    """Never overwrite an existing .migrated backup file."""

    def test_existing_migrated_backup_preserved(self, tmp_path):
        ws_root = _setup_default_workspace(tmp_path)
        patches = _common_patches(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "new-scan"}])

        # Create existing .migrated backup with different content
        migrated_path = legacy_main.with_suffix(".db.migrated")
        _create_main_db(migrated_path, [{"id": "old-scan"}])

        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["mig_ensure"]:
            report = migrate_legacy_to_default_workspace()

        # Migration succeeds (backup to dest works)
        assert report.main_history.status == "migrated"

        # Both files preserved — source NOT renamed over existing backup
        assert legacy_main.exists()  # source preserved (couldn't rename)
        assert migrated_path.exists()  # original backup untouched

        # Verify backup still has old content
        conn = sqlite3.connect(migrated_path)
        row = conn.execute("SELECT id FROM scans").fetchone()
        conn.close()
        assert row[0] == "old-scan"


# ---------------------------------------------------------------------------
# 8. Migration makes no AWS or STS calls
# ---------------------------------------------------------------------------


class TestNoAwsCalls:
    """Migration is purely local — no network calls."""

    def test_no_boto3_imported_during_migration(self, tmp_path):
        ws_root = _setup_default_workspace(tmp_path)
        patches = _common_patches(tmp_path, ws_root)

        legacy_main = tmp_path / "legacy" / "Kulshan" / "history.db"
        _create_main_db(legacy_main, [{"id": "local-scan"}])

        # Patch boto3.client to detect any calls
        mock_boto3 = MagicMock()
        with patches["ws_root"], patches["ws_path"], patches["config_file"], \
             patches["legacy_main"], patches["legacy_security"], patches["mig_ensure"], \
             patch.dict("sys.modules", {"boto3": mock_boto3}):
            report = migrate_legacy_to_default_workspace()

        assert report.main_history.status == "migrated"
        # boto3 should never have been called
        mock_boto3.client.assert_not_called()
        mock_boto3.Session.assert_not_called()


# ---------------------------------------------------------------------------
# Profile ambiguity error
# ---------------------------------------------------------------------------


class TestProfileAmbiguity:
    """get_connection_by_profile raises on ambiguous profiles."""

    def test_single_match_returns_connection(self):
        aws = WorkspaceAwsConfig(
            payer_account_id="111122223333",
            default_connection="payer-a",
            connections=[
                AwsConnection(
                    name="payer-a",
                    profile="shared-sso",
                    expected_session_account_id="111122223333",
                    role_arn="arn:aws:iam::111122223333:role/Kulshan",
                ),
                AwsConnection(
                    name="payer-b",
                    profile="other-sso",
                    expected_session_account_id="222233334444",
                    role_arn="arn:aws:iam::222233334444:role/Kulshan",
                ),
            ],
        )
        conn = aws.get_connection_by_profile("shared-sso")
        assert conn is not None
        assert conn.name == "payer-a"

    def test_zero_matches_returns_none(self):
        aws = WorkspaceAwsConfig(
            payer_account_id="111122223333",
            default_connection="main",
            connections=[
                AwsConnection(
                    name="main",
                    profile="cust-a",
                    expected_session_account_id="111122223333",
                ),
            ],
        )
        assert aws.get_connection_by_profile("nonexistent") is None

    def test_multiple_matches_raises_ambiguity(self):
        aws = WorkspaceAwsConfig(
            payer_account_id="111122223333",
            default_connection="payer-a",
            connections=[
                AwsConnection(
                    name="payer-a",
                    profile="shared-sso",
                    expected_session_account_id="111122223333",
                    role_arn="arn:aws:iam::111122223333:role/Kulshan",
                ),
                AwsConnection(
                    name="payer-b",
                    profile="shared-sso",
                    expected_session_account_id="222233334444",
                    role_arn="arn:aws:iam::222233334444:role/Kulshan",
                ),
            ],
        )
        with pytest.raises(AmbiguousProfileError) as exc:
            aws.get_connection_by_profile("shared-sso")
        assert "shared-sso" in str(exc.value)
        assert "payer-a" in str(exc.value)
        assert "payer-b" in str(exc.value)
        assert "--connection" in str(exc.value)
