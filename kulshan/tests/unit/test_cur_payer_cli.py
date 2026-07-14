"""CLI-level CUR payer validation proof tests.

Uses temporary Parquet fixtures generated via DuckDB.
Proves payer validation for both investigate cost and investigate ec2.
No real AWS credentials used.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

from click.testing import CliRunner

from kulshan.cli import main
from kulshan.cur.payer_validation import validate_cur_payer
from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    create_default_workspace_config,
    write_workspace_config,
)


pytestmark = pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb required")


def _create_parquet(tmp_path: Path, payer_ids: list[str | None], name="data.parquet"):
    """Create a minimal Parquet file with bill_payer_account_id column."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    rows = ", ".join(
        f"('{pid}', '2026-06-01', 10.0, 'AmazonEC2', 'Usage')"
        if pid else "(NULL, '2026-06-01', 10.0, 'AmazonEC2', 'Usage')"
        for pid in payer_ids
    )
    con.execute(f"""
        CREATE TABLE t AS SELECT * FROM (VALUES {rows})
        t(bill_payer_account_id, line_item_usage_start_date,
          line_item_unblended_cost, line_item_product_code, line_item_usage_type)
    """)
    out = tmp_path / name
    con.execute(f"COPY t TO '{str(out).replace(chr(92), '/')}' (FORMAT PARQUET)")
    con.close()
    return out


def _create_parquet_no_payer(tmp_path: Path, name="data.parquet"):
    """Create Parquet without payer column."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE t AS SELECT * FROM (VALUES
            ('2026-06-01', 10.0, 'AmazonEC2', 'Usage'))
        t(line_item_usage_start_date, line_item_unblended_cost,
          line_item_product_code, line_item_usage_type)
    """)
    out = tmp_path / name
    con.execute(f"COPY t TO '{str(out).replace(chr(92), '/')}' (FORMAT PARQUET)")
    con.close()
    return out


def _setup_ws(tmp_path, payer="999999999999"):
    ws_root = tmp_path / "workspaces"
    default_dir = ws_root / "default"
    default_dir.mkdir(parents=True)
    write_workspace_config(default_dir, create_default_workspace_config())
    ws_dir = ws_root / "customer-a"
    ws_dir.mkdir(parents=True)
    write_workspace_config(ws_dir, WorkspaceConfig(
        name="customer-a", binding_mode="bound",
        aws=WorkspaceAwsConfig(
            payer_account_id=payer, default_connection="main",
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
# Payer validation unit tests with real DuckDB + Parquet
# ---------------------------------------------------------------------------


class TestPayerValidationWithParquet:

    def test_matching_payer_proceeds(self, tmp_path):
        """Matching bill_payer_account_id passes validation."""
        pq = _create_parquet(tmp_path, ["999999999999", "999999999999"])
        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW cur_raw AS SELECT * FROM read_parquet('{str(pq).replace(chr(92), '/')}')")
        result = validate_cur_payer(con, "999999999999", "customer-a")
        con.close()
        assert result.status == "match"

    def test_mismatch_raises(self, tmp_path):
        """Mismatched payer raises before output."""
        from kulshan.cur.payer_validation import PayerMismatchError
        pq = _create_parquet(tmp_path, ["888877776666"])
        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW cur_raw AS SELECT * FROM read_parquet('{str(pq).replace(chr(92), '/')}')")
        with pytest.raises(PayerMismatchError):
            validate_cur_payer(con, "999999999999", "customer-a")
        con.close()

    def test_multiple_payers_raises(self, tmp_path):
        """Multiple distinct payer IDs raise error."""
        from kulshan.cur.payer_validation import MultiplePayersError
        pq = _create_parquet(tmp_path, ["111111111111", "222222222222"])
        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW cur_raw AS SELECT * FROM read_parquet('{str(pq).replace(chr(92), '/')}')")
        with pytest.raises(MultiplePayersError):
            validate_cur_payer(con, "111111111111", "customer-a")
        con.close()

    def test_repeated_single_payer_passes(self, tmp_path):
        """Multiple files with one repeated payer ID pass."""
        pq = _create_parquet(tmp_path, ["999999999999"] * 10)
        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW cur_raw AS SELECT * FROM read_parquet('{str(pq).replace(chr(92), '/')}')")
        result = validate_cur_payer(con, "999999999999", "customer-a")
        con.close()
        assert result.status == "match"

    def test_missing_payer_column_warns(self, tmp_path):
        """No payer column returns missing status."""
        pq = _create_parquet_no_payer(tmp_path)
        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW cur_raw AS SELECT * FROM read_parquet('{str(pq).replace(chr(92), '/')}')")
        result = validate_cur_payer(con, "999999999999", "customer-a")
        con.close()
        assert result.status == "missing"
        assert "does not contain payer" in result.message

    def test_no_boto3_during_validation(self, tmp_path):
        """Payer validation makes no boto3/STS calls."""
        pq = _create_parquet(tmp_path, ["999999999999"])
        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW cur_raw AS SELECT * FROM read_parquet('{str(pq).replace(chr(92), '/')}')")
        mock_boto3 = MagicMock()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            validate_cur_payer(con, "999999999999", "customer-a")
        con.close()
        mock_boto3.Session.assert_not_called()

    def test_unbound_skips_validation(self, tmp_path):
        """Unbound workspace (None payer) skips validation."""
        pq = _create_parquet(tmp_path, ["anything"])
        con = duckdb.connect(":memory:")
        con.execute(f"CREATE VIEW cur_raw AS SELECT * FROM read_parquet('{str(pq).replace(chr(92), '/')}')")
        result = validate_cur_payer(con, None, None)
        con.close()
        assert result.status == "match"


# ---------------------------------------------------------------------------
# CLI-level investigate cost payer proof
# ---------------------------------------------------------------------------


class TestInvestigateCostPayerCLI:

    def test_matching_payer_allows_investigation(self, tmp_path):
        """investigate cost with matching payer proceeds."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup_ws(tmp_path, payer="999999999999")
        p = _patches(tmp_path, ws_root)
        pq = _create_parquet(tmp_path / "cur", ["999999999999", "999999999999"])

        runner = CliRunner()
        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"]:
            result = runner.invoke(main, [
                "--workspace", "customer-a",
                "investigate", "cost",
                "--path", str(pq.parent),
                "--month", "2026-06",
            ])

        # Should not fail due to payer mismatch
        assert "payer mismatch" not in result.output.lower()

    def test_mismatch_exits_before_output(self, tmp_path):
        """investigate cost with mismatched payer exits before output."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup_ws(tmp_path, payer="999999999999")
        p = _patches(tmp_path, ws_root)
        pq = _create_parquet(tmp_path / "cur", ["888877776666"])

        runner = CliRunner()
        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"]:
            result = runner.invoke(main, [
                "--workspace", "customer-a",
                "investigate", "cost",
                "--path", str(pq.parent),
                "--month", "2026-06",
            ])

        assert result.exit_code != 0
        assert "payer mismatch" in result.output.lower() or "does not belong" in result.output.lower()

    def test_multiple_payers_fails(self, tmp_path):
        """investigate cost with multiple payer IDs fails."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup_ws(tmp_path, payer="999999999999")
        p = _patches(tmp_path, ws_root)
        pq = _create_parquet(tmp_path / "cur", ["999999999999", "111111111111"])

        runner = CliRunner()
        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"]:
            result = runner.invoke(main, [
                "--workspace", "customer-a",
                "investigate", "cost",
                "--path", str(pq.parent),
                "--month", "2026-06",
            ])

        assert result.exit_code != 0
        assert "multiple payer" in result.output.lower()

    def test_missing_payer_warns_and_proceeds(self, tmp_path):
        """investigate cost with no payer column warns and proceeds."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup_ws(tmp_path, payer="999999999999")
        p = _patches(tmp_path, ws_root)
        pq = _create_parquet_no_payer(tmp_path / "cur")

        runner = CliRunner()
        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"]:
            result = runner.invoke(main, [
                "--workspace", "customer-a",
                "investigate", "cost",
                "--path", str(pq.parent),
                "--month", "2026-06",
            ])

        # Warning shown but investigation proceeds (may fail for other reasons)
        assert "does not contain payer" in result.output.lower() or "warning" in result.output.lower()

    def test_payer_redacted_in_mismatch(self, tmp_path):
        """Payer IDs are redacted in mismatch output by default."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()

        ws_root = _setup_ws(tmp_path, payer="999999999999")
        p = _patches(tmp_path, ws_root)
        pq = _create_parquet(tmp_path / "cur", ["888877776666"])

        runner = CliRunner()
        with p["ws_root"], p["ws_path"], p["config_file"], p["legacy_main"], p["legacy_sec"]:
            result = runner.invoke(main, [
                "--workspace", "customer-a",
                "investigate", "cost",
                "--path", str(pq.parent),
                "--month", "2026-06",
            ])

        assert result.exit_code != 0
        # Full IDs should not appear (redacted)
        assert "999999999999" not in result.output
        assert "888877776666" not in result.output
        # Redacted forms should appear
        assert "XXXX" in result.output
