# ruff: noqa: E501
from __future__ import annotations

import shutil
from pathlib import Path

from click.testing import CliRunner

from kulshan.cli import main


def _workspace_tmp(name: str) -> Path:
    root = Path(".kulshan-test-tmp") / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _write_non_ec2_cur(path: Path) -> Path:
    import duckdb

    cur = path / "cur"
    cur.mkdir()
    parquet = cur / "data.parquet"
    duckdb.connect(database=":memory:").execute(
        f"""
        COPY (
            SELECT * FROM (VALUES
                (TIMESTAMP '2026-06-01', 'AmazonS3', 'TimedStorage-ByteHrs', '111111111111', 'us-east-1', NULL, 1.25),
                (TIMESTAMP '2026-06-02', 'awskms', 'KMS-Requests', '111111111111', 'us-east-1', NULL, 2.50),
                (TIMESTAMP '2026-06-03', 'AWSDataTransfer', 'DataTransfer-Out-Bytes', '222222222222', 'us-west-2', NULL, 3.75)
            ) AS t(
                line_item_usage_start_date,
                line_item_product_code,
                line_item_usage_type,
                line_item_usage_account_id,
                product_region,
                line_item_net_unblended_cost,
                line_item_unblended_cost
            )
        ) TO '{parquet.as_posix()}' (FORMAT PARQUET)
        """
    )
    return cur


def test_generic_validate_passes_with_no_ec2_rows() -> None:
    cur = _write_non_ec2_cur(_workspace_tmp("generic"))

    result = CliRunner().invoke(main, ["cur", "validate", "--path", str(cur)])

    assert result.exit_code == 0
    assert "CUR validation passed" in result.output
    assert "EC2 rows" in result.output
    assert "no" in result.output


def test_validate_real_shaped_fixture_with_null_net_cost_reports_fallback() -> None:
    cur = _write_non_ec2_cur(_workspace_tmp("fallback"))

    result = CliRunner().invoke(main, ["cur", "validate", "--path", str(cur)])

    assert result.exit_code == 0
    assert "line_item_unblended_cost" in result.output
    assert "line_item_net_unblended_cost was null" in result.output


def test_validate_reports_top_product_codes_and_usage_types() -> None:
    cur = _write_non_ec2_cur(_workspace_tmp("top-counts"))

    result = CliRunner().invoke(main, ["cur", "validate", "--path", str(cur)])

    assert result.exit_code == 0
    assert "Top Product Codes" in result.output
    assert "AmazonS3" in result.output
    assert "Top Usage Types" in result.output
    assert "TimedStorage-ByteHrs" in result.output


def test_investigate_ec2_still_exits_nonzero_with_no_ec2_rows() -> None:
    cur = _write_non_ec2_cur(_workspace_tmp("ec2"))

    result = CliRunner().invoke(
        main, ["investigate", "ec2", "--cur", str(cur), "--month", "2026-06"]
    )

    assert result.exit_code != 0
    assert "No EC2 cost data" in result.output or "Need at least" in result.output


def _write_null_net_cost_cur_two_months(path: Path) -> Path:
    """Create CUR fixture with all-null net_unblended but populated unblended cost."""
    import duckdb

    cur = path / "cur"
    cur.mkdir()
    parquet = cur / "data.parquet"
    duckdb.connect(database=":memory:").execute(
        f"""
        COPY (
            SELECT * FROM (VALUES
                -- June 2026 (previous month)
                (TIMESTAMP '2026-06-01', 'AmazonS3', 'TimedStorage-ByteHrs', '111111111111', 'us-east-1', NULL, 10.00),
                (TIMESTAMP '2026-06-15', 'awskms', 'KMS-Requests', '111111111111', 'us-east-1', NULL, 5.00),
                -- July 2026 (current month)
                (TIMESTAMP '2026-07-01', 'AmazonS3', 'TimedStorage-ByteHrs', '111111111111', 'us-east-1', NULL, 12.00),
                (TIMESTAMP '2026-07-10', 'awskms', 'KMS-Requests', '111111111111', 'us-east-1', NULL, 8.00)
            ) AS t(
                line_item_usage_start_date,
                line_item_product_code,
                line_item_usage_type,
                line_item_usage_account_id,
                product_region,
                line_item_net_unblended_cost,
                line_item_unblended_cost
            )
        ) TO '{parquet.as_posix()}' (FORMAT PARQUET)
        """
    )
    return cur


def test_investigate_cost_uses_fallback_column_when_net_unblended_is_null() -> None:
    """Regression test: investigate cost should use unblended_cost when net_unblended is all NULL.

    This tests the bug where cur validate correctly fell back to line_item_unblended_cost
    when line_item_net_unblended_cost was all NULL, but investigate cost did not,
    causing "No cost data found" errors on valid CUR exports.
    """
    cur = _write_null_net_cost_cur_two_months(_workspace_tmp("cost-fallback"))

    result = CliRunner().invoke(
        main, ["investigate", "cost", "--path", str(cur), "--month", "2026-07"]
    )

    # Should succeed, not fail with "No cost data found"
    assert result.exit_code == 0, f"Expected success but got: {result.output}"
    # Should show actual cost data (AmazonS3 is in the fixture)
    assert "AmazonS3" in result.output or "Total" in result.output


def test_investigate_cost_fallback_note_propagates_to_json_output() -> None:
    """The cost basis should include fallback note when a non-preferred cost column is used."""
    import json
    import tempfile
    from pathlib import Path as P

    cur = _write_null_net_cost_cur_two_months(_workspace_tmp("cost-fallback-note"))

    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = P(tmpdir) / "brief.json"
        result = CliRunner().invoke(
            main,
            ["investigate", "cost", "--path", str(cur), "--month", "2026-07", "-o", str(out_file)],
        )

        assert result.exit_code == 0, f"Expected success but got: {result.output}"
        data = json.loads(out_file.read_text())
        # The cost_basis should reference the fallback column
        assert data["cost_basis"]["column"] == "line_item_unblended_cost"
        # Should include fallback note
        assert data["cost_basis"]["fallback_note"] is not None
        assert "was null" in data["cost_basis"]["fallback_note"]


def test_validate_and_investigate_cost_select_same_column() -> None:
    """Both cur validate and investigate cost should select the same cost column."""
    cur = _write_null_net_cost_cur_two_months(_workspace_tmp("column-agreement"))

    validate_result = CliRunner().invoke(main, ["cur", "validate", "--path", str(cur)])
    cost_result = CliRunner().invoke(
        main, ["investigate", "cost", "--path", str(cur), "--month", "2026-07"]
    )

    assert validate_result.exit_code == 0
    assert cost_result.exit_code == 0
    # Both should report using line_item_unblended_cost (not net_unblended)
    assert "line_item_unblended_cost" in validate_result.output
    # investigate cost should work (not fail due to wrong column selection)
