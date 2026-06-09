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


def _save_scan(store: HistoryStore) -> str:
    return store.save_scan(
        account_id="123456789012",
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
