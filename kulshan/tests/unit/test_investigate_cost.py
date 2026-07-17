# ruff: noqa: E501
from __future__ import annotations

from click.testing import CliRunner

from kulshan.cli import main
from kulshan.cur.manifest_reader import ManifestFile, ManifestIndex
from kulshan.cur.s3_query import CostColumnSelection, CostInvestigationResult, ScanEstimate


class FakeCon:
    def close(self) -> None:
        pass


def _manifest(total_size: int = 70_000) -> ManifestIndex:
    return ManifestIndex(
        bucket="bucket",
        prefix="export/",
        billing_period="2026-06",
        export_name="export",
        files=(ManifestFile("export/data/BILLING_PERIOD=2026-06/a.parquet", total_size),),
        columns=("line_item_unblended_cost",),
        total_size_bytes=total_size,
        s3_glob="s3://bucket/export/data/BILLING_PERIOD=2026-06/a.parquet",
        manifest_key="export/metadata/BILLING_PERIOD=2026-06/Manifest.json",
        manifest_size_bytes=500,
    )


def _patch_success(monkeypatch, estimate_bytes: int = 70_000) -> None:
    monkeypatch.setattr(
        "kulshan.cur.manifest_reader.read_manifest_uri",
        lambda s3_uri, billing_period: _manifest(estimate_bytes),
        raising=False,
    )


def test_analyze_cost_s3_reads_manifest(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "kulshan.cur.manifest_reader.read_manifest_uri",
        lambda s3_uri, billing_period: calls.append((s3_uri, billing_period)) or _manifest(),
        raising=False,
    )
    _patch_query(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["analyze", "cost", "--s3", "s3://bucket/export/", "--month", "2026-06"],
    )

    assert result.exit_code == 0
    assert calls == [("s3://bucket/export/", "2026-06")]
    assert "manifest read: yes" in result.output


def test_analyze_cost_scan_over_threshold_requires_confirm(monkeypatch) -> None:
    monkeypatch.setattr(
        "kulshan.cur.manifest_reader.read_manifest_uri",
        lambda s3_uri, billing_period: _manifest(200),
        raising=False,
    )
    _patch_query(monkeypatch, estimate=ScanEstimate(200, 200, "manifest_upper_bound", "upper"))
    monkeypatch.setenv("KULSHAN_MAX_SCAN_MB", "0")

    result = CliRunner().invoke(
        main,
        ["analyze", "cost", "--s3", "s3://bucket/export/", "--month", "2026-06"],
    )

    assert result.exit_code != 0
    assert "--confirm-scan" in result.output


def test_analyze_cost_scan_under_threshold_runs(monkeypatch) -> None:
    monkeypatch.setattr(
        "kulshan.cur.manifest_reader.read_manifest_uri",
        lambda s3_uri, billing_period: _manifest(),
        raising=False,
    )
    _patch_query(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["analyze", "cost", "--s3", "s3://bucket/export/", "--month", "2026-06"],
    )

    assert result.exit_code == 0
    assert "Total spend: $42.00" in result.output


def test_analyze_cost_output_includes_dimensions_and_note(monkeypatch) -> None:
    monkeypatch.setattr(
        "kulshan.cur.manifest_reader.read_manifest_uri",
        lambda s3_uri, billing_period: _manifest(),
        raising=False,
    )
    _patch_query(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["analyze", "cost", "--s3", "s3://bucket/export/", "--month", "2026-06"],
    )

    assert result.exit_code == 0
    assert "AmazonS3" in result.output
    assert "TimedStorage" in result.output
    assert "111111111111" in result.output
    assert "standard S3 request and transfer charges may apply" in result.output
    assert "Athena" not in result.output
    assert "Glue" not in result.output


def test_analyze_cost_usage_requires_source() -> None:
    result = CliRunner().invoke(main, ["analyze", "cost", "--month", "2026-06"])

    assert result.exit_code != 0
    assert "--s3 s3://bucket/prefix/ or --path ./cur/" in result.output


def _patch_query(monkeypatch, estimate: ScanEstimate | None = None) -> None:
    estimate = estimate or ScanEstimate(70_000, 70_000, "parquet_metadata", "estimate")
    monkeypatch.setattr("kulshan.cur.s3_query.connect_s3_duckdb", lambda: FakeCon(), raising=False)
    monkeypatch.setattr(
        "kulshan.cur.s3_query.cur_columns",
        lambda con, manifest: {
            "line_item_unblended_cost",
            "line_item_product_code",
            "line_item_usage_type",
            "line_item_usage_start_date",
            "line_item_usage_account_id",
        },
        raising=False,
    )
    monkeypatch.setattr(
        "kulshan.cur.s3_query.select_cost_column",
        lambda con, manifest, columns, month: CostColumnSelection("line_item_unblended_cost"),
        raising=False,
    )
    monkeypatch.setattr(
        "kulshan.cur.s3_query.estimate_scan_bytes",
        lambda con, manifest, columns: estimate,
        raising=False,
    )
    monkeypatch.setattr(
        "kulshan.cur.s3_query.analyze_cost_s3",
        lambda con, manifest, month: CostInvestigationResult(
            total_spend=42.0,
            cost_column="line_item_unblended_cost",
            fallback_note=None,
            top_services=(("AmazonS3", 30.0),),
            top_usage_types=(("TimedStorage", 20.0),),
            top_accounts=(("111111111111", 42.0),),
            top_regions=(),
            estimate=estimate,
        ),
        raising=False,
    )
