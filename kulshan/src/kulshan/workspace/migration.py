"""Legacy database migration into default workspace.

Migrates pre-workspace history databases into the default workspace
using SQLite backup API. Handles main history and security history
independently — one can succeed while the other fails.

Migration steps per database:
1. Check if source exists
2. Run integrity_check on source
3. Use sqlite3.backup() to copy into destination
4. Verify table structure and row count match
5. Update workspace migration status
6. Rename source to source.migrated

Source is never deleted. On failure, source is preserved unchanged.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kulshan.workspace.config import (
    WorkspaceConfig,
    WorkspaceMigrationStatus,
    read_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.paths import (
    get_legacy_history_path,
    get_legacy_security_history_path,
    get_workspace_path,
)
from kulshan.workspace.resolution import ensure_default_workspace

logger = logging.getLogger(__name__)

MigrationResult = Literal["migrated", "not_found", "skipped", "failed"]


@dataclass
class SingleMigrationResult:
    """Result of migrating a single database."""

    status: MigrationResult
    source_path: Path | None = None
    dest_path: Path | None = None
    row_count: int = 0
    error: str | None = None


@dataclass
class MigrationReport:
    """Combined result of migrating both legacy databases."""

    main_history: SingleMigrationResult
    security_history: SingleMigrationResult

    @property
    def any_migrated(self) -> bool:
        return (
            self.main_history.status == "migrated"
            or self.security_history.status == "migrated"
        )

    @property
    def any_failed(self) -> bool:
        return (
            self.main_history.status == "failed"
            or self.security_history.status == "failed"
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def migrate_legacy_to_default_workspace() -> MigrationReport:
    """
    Migrate legacy history databases into the default workspace.

    This is the top-level function called during workspace resolution
    or explicitly by the user. It is idempotent — repeated calls are safe.

    Returns:
        MigrationReport with status for each database.
    """
    # Ensure default workspace exists
    workspace_path = ensure_default_workspace()
    config = read_workspace_config(workspace_path)

    # Initialize migration status if not present
    if config.migration is None:
        config.migration = WorkspaceMigrationStatus(
            main_history="pending",
            security_history="pending",
        )

    # Migrate main history
    main_result = _migrate_single_database(
        source_path=get_legacy_history_path(),
        dest_path=workspace_path / "history.db",
        current_status=config.migration.main_history,
        expected_table="scans",
    )

    # Migrate security history
    security_result = _migrate_single_database(
        source_path=get_legacy_security_history_path(),
        dest_path=workspace_path / "security-history.db",
        current_status=config.migration.security_history,
        expected_table="scans",
    )

    # Update migration status in workspace config
    config.migration.main_history = main_result.status
    config.migration.security_history = security_result.status
    write_workspace_config(workspace_path, config)

    return MigrationReport(
        main_history=main_result,
        security_history=security_result,
    )


# ---------------------------------------------------------------------------
# Single database migration
# ---------------------------------------------------------------------------


def _migrate_single_database(
    source_path: Path,
    dest_path: Path,
    current_status: str,
    expected_table: str,
) -> SingleMigrationResult:
    """
    Migrate a single legacy database using SQLite backup API.

    Handles all edge cases:
    - Source doesn't exist -> not_found
    - Already migrated -> skipped
    - Destination already has data -> skipped (don't overwrite)
    - Source fails integrity check -> failed
    - Backup or verification fails -> failed, source preserved

    Args:
        source_path: Path to the legacy database.
        dest_path: Destination path in workspace.
        current_status: Current migration status from config.
        expected_table: Table name to verify after backup.

    Returns:
        SingleMigrationResult with status and details.
    """
    # Already migrated — idempotent
    if current_status == "migrated":
        return SingleMigrationResult(
            status="skipped",
            source_path=source_path,
            dest_path=dest_path,
        )

    # Source doesn't exist
    if not source_path.exists():
        # Check if .migrated backup exists (previous successful migration)
        migrated_path = source_path.with_suffix(source_path.suffix + ".migrated")
        if migrated_path.exists():
            return SingleMigrationResult(
                status="migrated",
                source_path=source_path,
                dest_path=dest_path,
            )
        return SingleMigrationResult(
            status="not_found",
            source_path=source_path,
        )

    # Destination already exists — validate it properly
    if dest_path.exists() and dest_path.stat().st_size > 0:
        dest_integrity = _check_integrity(dest_path)
        if dest_integrity:
            # Corrupt destination — fail safely, don't ignore valid source
            return SingleMigrationResult(
                status="failed",
                source_path=source_path,
                dest_path=dest_path,
                error=(
                    f"Destination exists but is corrupt: {dest_integrity}. "
                    "Cannot migrate; manual intervention required."
                ),
            )

        dest_has_table = _count_rows(dest_path, expected_table) is not None
        if dest_has_table and _destination_has_data(dest_path, expected_table):
            # Destination has data — check if it matches source (reconcile)
            source_ids = _get_id_set(source_path, expected_table)
            dest_ids = _get_id_set(dest_path, expected_table)
            if source_ids == dest_ids:
                # Already migrated, metadata may be stale — reconcile
                migrated_path = source_path.with_suffix(
                    source_path.suffix + ".migrated"
                )
                if not migrated_path.exists():
                    try:
                        source_path.rename(migrated_path)
                    except OSError:
                        pass
                return SingleMigrationResult(
                    status="migrated",
                    source_path=source_path,
                    dest_path=dest_path,
                    row_count=len(dest_ids),
                )
            # Destination has different data — never overwrite
            return SingleMigrationResult(
                status="skipped",
                source_path=source_path,
                dest_path=dest_path,
                error=(
                    "Destination already contains different data; "
                    "skipping to avoid data loss."
                ),
            )

    # Run integrity check on source
    integrity_error = _check_integrity(source_path)
    if integrity_error:
        return SingleMigrationResult(
            status="failed",
            source_path=source_path,
            error=f"Source integrity check failed: {integrity_error}",
        )

    # Count rows in source for verification
    source_count = _count_rows(source_path, expected_table)
    if source_count is None:
        return SingleMigrationResult(
            status="failed",
            source_path=source_path,
            error=f"Source does not contain expected table '{expected_table}'.",
        )

    # Get source ID set for post-backup verification
    source_ids = _get_id_set(source_path, expected_table)

    # Perform SQLite backup
    backup_error = _sqlite_backup(source_path, dest_path)
    if backup_error:
        return SingleMigrationResult(
            status="failed",
            source_path=source_path,
            dest_path=dest_path,
            error=f"SQLite backup failed: {backup_error}",
        )

    # Verify destination integrity
    dest_integrity = _check_integrity(dest_path)
    if dest_integrity:
        try:
            dest_path.unlink()
        except OSError:
            pass
        return SingleMigrationResult(
            status="failed",
            source_path=source_path,
            dest_path=dest_path,
            error=f"Destination integrity check failed: {dest_integrity}",
        )

    # Verify destination row count
    dest_count = _count_rows(dest_path, expected_table)
    if dest_count != source_count:
        try:
            dest_path.unlink()
        except OSError:
            pass
        return SingleMigrationResult(
            status="failed",
            source_path=source_path,
            dest_path=dest_path,
            error=(
                f"Row count mismatch after backup: "
                f"source={source_count}, dest={dest_count}"
            ),
        )

    # Verify primary key set identity
    dest_ids = _get_id_set(dest_path, expected_table)
    if source_ids != dest_ids:
        try:
            dest_path.unlink()
        except OSError:
            pass
        return SingleMigrationResult(
            status="failed",
            source_path=source_path,
            dest_path=dest_path,
            error="Primary key set mismatch between source and destination.",
        )

    # Success — rename source to .migrated
    migrated_path = source_path.with_suffix(source_path.suffix + ".migrated")
    if migrated_path.exists():
        # Never overwrite an existing .migrated backup
        logger.warning(
            "Migration succeeded but .migrated backup already exists at %s. "
            "Source preserved at %s.",
            migrated_path,
            source_path,
        )
    else:
        try:
            source_path.rename(migrated_path)
        except OSError as e:
            # Migration succeeded even if rename fails — data is safe
            logger.warning(
                "Migration succeeded but could not rename source: %s", e
            )

    return SingleMigrationResult(
        status="migrated",
        source_path=source_path,
        dest_path=dest_path,
        row_count=source_count,
    )


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _check_integrity(db_path: Path) -> str | None:
    """
    Run PRAGMA integrity_check on a database.

    Returns:
        None if OK, error message string if failed.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result and result[0] == "ok":
                return None
            return result[0] if result else "Unknown integrity error"
        finally:
            conn.close()
    except sqlite3.Error as e:
        return str(e)


def _count_rows(db_path: Path, table_name: str) -> int | None:
    """
    Count rows in a table. Returns None if table doesn't exist.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            # Check table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            if cursor.fetchone() is None:
                return None
            # Count rows
            result = conn.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()
            return result[0] if result else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _destination_has_data(db_path: Path, table_name: str) -> bool:
    """Check if destination database already has rows in the expected table."""
    count = _count_rows(db_path, table_name)
    return count is not None and count > 0


def _get_id_set(db_path: Path, table_name: str) -> set[str]:
    """
    Get the complete primary key set from a table.

    Works for both main history (TEXT id) and security history (INTEGER id).

    Returns:
        Set of string-ified primary key values, or empty set on error.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                f"SELECT id FROM [{table_name}] ORDER BY id"
            ).fetchall()
            return {str(row[0]) for row in rows}
        finally:
            conn.close()
    except sqlite3.Error:
        return set()


def _sqlite_backup(source_path: Path, dest_path: Path) -> str | None:
    """
    Copy source database to destination using sqlite3.backup() API.

    This is the safest way to copy a SQLite database — it handles
    WAL journals, concurrent readers, and partial pages correctly.

    Returns:
        None on success, error message on failure.
    """
    try:
        # Ensure destination directory exists
        dest_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        source_conn = sqlite3.connect(source_path)
        try:
            dest_conn = sqlite3.connect(dest_path)
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        finally:
            source_conn.close()
        return None
    except sqlite3.Error as e:
        # Clean up partial destination on failure
        try:
            if dest_path.exists():
                dest_path.unlink()
        except OSError:
            pass
        return str(e)
    except OSError as e:
        return str(e)
