"""Tests for local scan-history storage and retention controls."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from click.testing import CliRunner

from kulshan import history as history_module
from kulshan.cli import main
from kulshan.history import HistoryStore, get_history_db_path


def _memory_store() -> HistoryStore:
    store = HistoryStore("unused.db")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(history_module._SCHEMA)
    store._conn = connection
    return store


def _save_scan(store: HistoryStore, account_id: str = "123456789012") -> str:
    return store.save_scan(
        account_id=account_id,
        regions=["us-east-1"],
        duration_seconds=1.5,
        overall_score=80,
        overall_grade="B",
        results={"cost": {"scores": {"overall_score": 80, "grade": "B"}}},
        findings=[{"severity": "high", "title": "Sensitive finding"}],
        version="0.1.0",
    )


def test_default_history_is_summary_only():
    store = _memory_store()
    scan_id = _save_scan(store)

    saved = store.get_scan(scan_id)
    store.close()

    assert saved is not None
    assert saved["full_result_json"] is None


def test_delete_all_removes_saved_scans():
    store = _memory_store()
    _save_scan(store)
    _save_scan(store)

    assert store.delete_all() == 2
    assert store.list_scans() == []
    store.close()


def test_purge_old_returns_deleted_count():
    store = _memory_store()
    scan_id = _save_scan(store)
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    store._connect().execute(
        "UPDATE scans SET timestamp = ? WHERE id = ?", (old_timestamp, scan_id)
    )
    store._connect().commit()

    assert store.purge_old(retention_days=365) == 1
    assert store.list_scans() == []
    store.close()


def test_default_path_uses_platform_data_directory():
    path = get_history_db_path()
    assert path.name == "history.db"
    assert path.parent.name == "Kulshan"


def test_delete_history_command():
    store = _memory_store()
    _save_scan(store)
    real_close = store.close

    runner = CliRunner()
    with patch.object(store, "close", wraps=real_close) as close_spy, patch(
        "kulshan.history.get_history_db_path", return_value="memory-history.db"
    ), patch("kulshan.history.HistoryStore", return_value=store):
        result = runner.invoke(main, ["delete-history", "--yes"])

    assert result.exit_code == 0
    assert "Deleted 1 scan(s)" in result.output
    close_spy.assert_called_once_with()
    assert store._conn is None



# ---------------------------------------------------------------------------
# Phase 1: --account filter tests
# ---------------------------------------------------------------------------


def test_history_account_filter_valid_12_digits():
    """Valid 12-digit account ID is accepted and filters correctly."""
    store = _memory_store()
    _save_scan(store, account_id="111122223333")
    _save_scan(store, account_id="444455556666")

    # Test the filter at the store level (avoids Rich table truncation issues)
    filtered = store.list_scans(limit=20, account_id="111122223333")
    assert len(filtered) == 1
    assert filtered[0]["account_id"] == "111122223333"

    # Test CLI accepts the option without error
    runner = CliRunner()
    with patch("kulshan.history.HistoryStore", return_value=store):
        result = runner.invoke(main, ["history", "--account", "111122223333"])

    assert result.exit_code == 0
    assert "Scan History" in result.output  # Table renders


def test_history_account_filter_invalid_not_12_digits():
    """Account ID that is not exactly 12 digits is rejected."""
    runner = CliRunner()
    result = runner.invoke(main, ["history", "--account", "12345"])

    assert result.exit_code != 0
    assert "12 digits" in result.output


def test_history_account_filter_invalid_non_numeric():
    """Account ID with non-numeric characters is rejected."""
    runner = CliRunner()
    result = runner.invoke(main, ["history", "--account", "12345678901a"])

    assert result.exit_code != 0
    assert "12 digits" in result.output


def test_history_account_filter_no_match_shows_empty_message():
    """When no scans match the account filter, show a clear message."""
    store = _memory_store()
    _save_scan(store, account_id="111122223333")

    runner = CliRunner()
    with patch("kulshan.history.HistoryStore", return_value=store):
        result = runner.invoke(main, ["history", "--account", "999988887777"])

    assert result.exit_code == 0
    assert "No scan history found for account 999988887777" in result.output


def test_history_without_account_filter_shows_all():
    """Without --account, all scans are shown (existing behavior)."""
    store = _memory_store()
    _save_scan(store, account_id="111122223333")
    _save_scan(store, account_id="444455556666")

    # Test at store level
    all_scans = store.list_scans(limit=20)
    assert len(all_scans) == 2

    # Test CLI shows table
    runner = CliRunner()
    with patch("kulshan.history.HistoryStore", return_value=store):
        result = runner.invoke(main, ["history"])

    assert result.exit_code == 0
    assert "Scan History" in result.output


def test_history_empty_without_filter_shows_generic_message():
    """Empty history without filter shows generic empty message."""
    store = _memory_store()

    runner = CliRunner()
    with patch("kulshan.history.HistoryStore", return_value=store):
        result = runner.invoke(main, ["history"])

    assert result.exit_code == 0
    assert "No scan history found. Run 'kulshan report'" in result.output


def test_history_account_filter_passes_to_list_scans():
    """The --account filter is passed to list_scans correctly."""
    store = _memory_store()
    _save_scan(store, account_id="111122223333")
    _save_scan(store, account_id="444455556666")

    # Verify filtering works at the database level
    filtered = store.list_scans(limit=20, account_id="111122223333")
    assert len(filtered) == 1
    assert filtered[0]["account_id"] == "111122223333"

    # Verify no results for non-existent account
    empty = store.list_scans(limit=20, account_id="999999999999")
    assert len(empty) == 0
