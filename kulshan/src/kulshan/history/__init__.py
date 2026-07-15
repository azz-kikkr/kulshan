"""Scan history: SQLite WAL storage for local scan tracking and trend analysis.

Stores a summary of every scan in a local SQLite database. Enables:
- `kulshan history` to show past scans
- Score trends over time
- Delta comparison between consecutive runs
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import platformdirs

# ---------------------------------------------------------------------------
# Database path resolution
# ---------------------------------------------------------------------------

def get_history_db_path() -> Path:
    """Return the default history database path (XDG-compliant)."""
    return Path(platformdirs.user_data_dir("Kulshan", "missionfinops")) / "history.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
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
    full_result_json TEXT,
    report_status TEXT DEFAULT 'complete'
);

CREATE INDEX IF NOT EXISTS idx_scans_timestamp ON scans(timestamp);
CREATE INDEX IF NOT EXISTS idx_scans_account ON scans(account_id);

CREATE TABLE IF NOT EXISTS scan_connections (
    scan_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    profile TEXT,
    session_account_id TEXT,
    role_arn TEXT,
    status TEXT NOT NULL,
    duration_seconds REAL,
    packs_attempted TEXT,
    packs_completed TEXT,
    error_code TEXT,
    PRIMARY KEY (scan_id, connection_name)
);
"""

# Migration: add columns/tables that may be missing in older databases
_MIGRATION_SQL = """
-- Add report_status if missing (safe: SQLite ignores duplicate ADD COLUMN)
ALTER TABLE scans ADD COLUMN report_status TEXT DEFAULT 'complete';
"""

_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scan_connections (
    scan_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    profile TEXT,
    session_account_id TEXT,
    role_arn TEXT,
    status TEXT NOT NULL,
    duration_seconds REAL,
    packs_attempted TEXT,
    packs_completed TEXT,
    error_code TEXT,
    PRIMARY KEY (scan_id, connection_name)
);
"""


# ---------------------------------------------------------------------------
# History store
# ---------------------------------------------------------------------------

class HistoryStore:
    """SQLite-backed scan history store."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else get_history_db_path()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            # Run safe migrations for older databases
            self._run_migrations()
            # Restrict file permissions to owner only
            with suppress(OSError):
                self.db_path.chmod(0o600)
        return self._conn

    def _run_migrations(self) -> None:
        """Run safe schema migrations for older databases."""
        conn = self._conn
        if conn is None:
            return
        # Add report_status column (safe: ignores if already exists)
        try:
            conn.execute("SELECT report_status FROM scans LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE scans ADD COLUMN report_status TEXT DEFAULT 'complete'")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # Create scan_connections table
        conn.executescript(_MIGRATION_TABLE_SQL)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def save_scan(
        self,
        account_id: str,
        regions: list[str],
        duration_seconds: float,
        overall_score: int,
        overall_grade: str,
        results: dict[str, Any],
        findings: list[dict],
        version: str = "",
        store_full_result: bool = False,
        report_status: str = "complete",
    ) -> str:
        """Save a scan result to history. Returns the scan ID."""
        conn = self._connect()
        scan_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        # Count severities
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for f in findings:
            sev = f.get("severity", "info")
            if sev in severity_counts:
                severity_counts[sev] += 1

        # Per-pack scores
        pack_scores = {}
        for key, result in results.items():
            scores = result.get("scores", {})
            pack_scores[key] = {
                "score": scores.get("overall_score", 0),
                "grade": scores.get("grade", "?"),
                "findings": scores.get("total_findings", 0),
            }

        full_json = None
        if store_full_result:
            full_json = json.dumps({
                "tools": results,
                "findings": findings,
                "overall_score": overall_score,
                "overall_grade": overall_grade,
            }, default=str)

        conn.execute(
            """INSERT INTO scans (id, timestamp, account_id, regions, duration_seconds,
               overall_score, overall_grade, total_findings, critical_findings,
               high_findings, medium_findings, low_findings, pack_scores,
               kulshan_version, full_result_json, report_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scan_id, now, account_id, json.dumps(regions), duration_seconds,
                overall_score, overall_grade, len(findings),
                severity_counts["critical"], severity_counts["high"],
                severity_counts["medium"], severity_counts["low"],
                json.dumps(pack_scores), version, full_json, report_status,
            ),
        )
        conn.commit()
        return scan_id

    def save_scan_connections(
        self,
        scan_id: str,
        connections: list[dict],
    ) -> None:
        """Save connection execution metadata for a consolidated scan.

        Args:
            scan_id: The parent scan ID.
            connections: List of dicts with connection execution metadata.
                Each must have: connection_name, status.
                Optional: profile, session_account_id, role_arn,
                          duration_seconds, packs_attempted, packs_completed, error_code.
        """
        conn = self._connect()
        for c in connections:
            conn.execute(
                """INSERT OR REPLACE INTO scan_connections
                   (scan_id, connection_name, profile, session_account_id,
                    role_arn, status, duration_seconds, packs_attempted,
                    packs_completed, error_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    c.get("connection_name", ""),
                    c.get("profile"),
                    c.get("session_account_id"),
                    c.get("role_arn"),
                    c.get("status", "unknown"),
                    c.get("duration_seconds"),
                    json.dumps(c.get("packs_attempted", [])),
                    json.dumps(c.get("packs_completed", [])),
                    c.get("error_code"),
                ),
            )
        conn.commit()

    def list_scans(
        self, limit: int = 20, account_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List recent scans, newest first."""
        conn = self._connect()
        if account_id:
            rows = conn.execute(
                "SELECT id, timestamp, account_id, overall_score, overall_grade, "
                "total_findings, critical_findings, high_findings, duration_seconds "
                "FROM scans WHERE account_id = ? ORDER BY timestamp DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, timestamp, account_id, overall_score, overall_grade, "
                "total_findings, critical_findings, high_findings, duration_seconds "
                "FROM scans ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        """Get a single scan by ID."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_previous_scan(self, account_id: str) -> dict[str, Any] | None:
        """Get the most recent scan before the current one for delta comparison."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM scans WHERE account_id = ? ORDER BY timestamp DESC LIMIT 1 OFFSET 1",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def compare_scans(self, current_id: str, previous_id: str) -> dict[str, Any]:
        """Compare two scans and return the delta."""
        current = self.get_scan(current_id)
        previous = self.get_scan(previous_id)
        if not current or not previous:
            return {"error": "Scan not found"}

        return {
            "score_delta": (current["overall_score"] or 0) - (previous["overall_score"] or 0),
            "findings_delta": (current["total_findings"] or 0) - (previous["total_findings"] or 0),
            "critical_delta": (current["critical_findings"] or 0)
            - (previous["critical_findings"] or 0),
            "current": {
                "id": current["id"],
                "score": current["overall_score"],
                "grade": current["overall_grade"],
                "findings": current["total_findings"],
                "timestamp": current["timestamp"],
            },
            "previous": {
                "id": previous["id"],
                "score": previous["overall_score"],
                "grade": previous["overall_grade"],
                "findings": previous["total_findings"],
                "timestamp": previous["timestamp"],
            },
        }

    def purge_old(self, retention_days: int = 365) -> int:
        """Delete scans older than retention_days."""
        conn = self._connect()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        cursor = conn.execute("DELETE FROM scans WHERE timestamp < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount

    def delete_all(self) -> int:
        """Delete every stored scan and return the number removed."""
        conn = self._connect()
        cursor = conn.execute("DELETE FROM scans")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        return cursor.rowcount
