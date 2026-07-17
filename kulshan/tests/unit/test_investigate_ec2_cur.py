"""Tests for local EC2 CUR analysis."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from kulshan.cli import main
from kulshan.cur.schema import CurColumnMapping, resolve_cur_columns
from kulshan.analyze import CurAnalysisError, analyze_ec2_cur


def _sample_cur_path() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "cur" / "sample-cur"


def _account_region_cur_path() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "cur" / "account-region-cur"


def _tagged_cur_path() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "cur" / "tagged-cur"


def test_analyze_ec2_cur_calculates_latest_period_delta_by_default() -> None:
    brief = analyze_ec2_cur(str(_sample_cur_path()))

    assert brief.previous_period == "2026-05"
    assert brief.current_period == "2026-06"
    assert brief.previous_cost == 200.0
    assert brief.current_cost == 520.0
    assert brief.delta == 320.0
    assert brief.delta_percent == 160.0
    assert brief.top_resources[0].name == "i-prod-a"
    assert brief.top_resources[0].delta == 190.0
    assert brief.top_usage_types[0].name == "BoxUsage:m6i.4xlarge"
    assert brief.evidence_available[0].label == "CUR/Data Exports Parquet"
    missing_labels = {item.label for item in brief.evidence_missing}
    assert "Account IDs" in missing_labels
    assert "Regions" in missing_labels
    assert "Owner tags" in missing_labels
    assert "Team tags" in missing_labels
    assert "Application tags" in missing_labels
    assert len(brief.review_questions) == 3


def test_analyze_ec2_cur_uses_selected_month() -> None:
    brief = analyze_ec2_cur(str(_sample_cur_path()), month="2026-06")

    assert brief.previous_period == "2026-05"
    assert brief.current_period == "2026-06"
    assert brief.previous_cost == 200.0
    assert brief.current_cost == 520.0


def test_analyze_ec2_cur_includes_account_deltas() -> None:
    brief = analyze_ec2_cur(str(_account_region_cur_path()), month="2026-06")

    assert brief.top_accounts[0].name == "111111111111"
    assert brief.top_accounts[0].previous_cost == 100.0
    assert brief.top_accounts[0].current_cost == 250.0
    assert brief.top_accounts[0].delta == 150.0
    assert "Account delta" in {item.label for item in brief.evidence_available}
    assert "Account IDs" not in {item.label for item in brief.evidence_missing}


def test_analyze_ec2_cur_includes_region_deltas() -> None:
    brief = analyze_ec2_cur(str(_account_region_cur_path()), month="2026-06")

    assert brief.top_regions[0].name == "us-east-1"
    assert brief.top_regions[0].previous_cost == 100.0
    assert brief.top_regions[0].current_cost == 250.0
    assert brief.top_regions[0].delta == 150.0
    assert "Region delta" in {item.label for item in brief.evidence_available}
    assert "Regions" not in {item.label for item in brief.evidence_missing}


def test_analyze_ec2_cur_reports_missing_account_field() -> None:
    brief = analyze_ec2_cur(str(_sample_cur_path()), month="2026-06")

    assert brief.top_accounts == []
    assert "Account IDs" in {item.label for item in brief.evidence_missing}


def test_analyze_ec2_cur_reports_missing_region_field() -> None:
    brief = analyze_ec2_cur(str(_sample_cur_path()), month="2026-06")

    assert brief.top_regions == []
    assert "Regions" in {item.label for item in brief.evidence_missing}


def test_analyze_ec2_cur_reports_tagged_and_untagged_spend() -> None:
    brief = analyze_ec2_cur(str(_tagged_cur_path()), month="2026-06")

    assert brief.tag_coverage is not None
    assert brief.tag_coverage.tagged_cost == 250.0
    assert brief.tag_coverage.untagged_cost == 90.0
    assert "Tag coverage" in {item.label for item in brief.evidence_available}


def test_analyze_ec2_cur_surfaces_owner_team_application_tag_evidence() -> None:
    brief = analyze_ec2_cur(str(_tagged_cur_path()), month="2026-06")

    assert brief.tag_coverage is not None
    assert brief.tag_coverage.owner_values == ["platform"]
    assert brief.tag_coverage.team_values == ["core"]
    assert brief.tag_coverage.application_values == ["checkout"]
    available_labels = {item.label for item in brief.evidence_available}
    assert "Owner tag" in available_labels
    assert "Team tag" in available_labels
    assert "Application tag" in available_labels


def test_analyze_ec2_cur_reports_missing_owner_team_application_tags() -> None:
    brief = analyze_ec2_cur(str(_sample_cur_path()), month="2026-06")

    assert brief.tag_coverage is None
    missing_labels = {item.label for item in brief.evidence_missing}
    assert "Owner tags" in missing_labels
    assert "Team tags" in missing_labels
    assert "Application tags" in missing_labels


def test_analyze_ec2_cur_rejects_invalid_month() -> None:
    try:
        analyze_ec2_cur(str(_sample_cur_path()), month="2026-6")
    except CurAnalysisError as exc:
        assert "YYYY-MM" in str(exc)
    else:
        raise AssertionError("Expected invalid month to fail")


def test_analyze_ec2_cur_fails_when_selected_month_is_missing() -> None:
    try:
        analyze_ec2_cur(str(_sample_cur_path()), month="2026-07")
    except CurAnalysisError as exc:
        assert "selected month 2026-07" in str(exc)
    else:
        raise AssertionError("Expected missing selected month to fail")


def test_analyze_ec2_cur_fails_when_previous_month_is_missing() -> None:
    try:
        analyze_ec2_cur(str(_sample_cur_path()), month="2026-05")
    except CurAnalysisError as exc:
        assert "previous month 2026-04" in str(exc)
    else:
        raise AssertionError("Expected missing previous month to fail")


def test_analyze_ec2_cur_fails_when_cur_path_is_missing() -> None:
    missing_path = _sample_cur_path().parent / "missing-cur"

    try:
        analyze_ec2_cur(str(missing_path))
    except CurAnalysisError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("Expected missing CUR path to fail")


def test_analyze_ec2_cur_fails_for_non_parquet_file() -> None:
    try:
        analyze_ec2_cur(__file__)
    except CurAnalysisError as exc:
        assert "must be a Parquet file or directory" in str(exc)
    else:
        raise AssertionError("Expected non-Parquet CUR input to fail")


def test_analyze_ec2_cur_fails_for_directory_without_parquet() -> None:
    cur_dir = Path(__file__).parent

    try:
        analyze_ec2_cur(str(cur_dir))
    except CurAnalysisError as exc:
        assert "No Parquet files found" in str(exc)
    else:
        raise AssertionError("Expected CUR directory without Parquet files to fail")

def test_cur_schema_resolves_athena_style_aliases() -> None:
    mapping = resolve_cur_columns(
        {
            "lineitem_usagestartdate",
            "lineitem_unblendedcost",
            "product_servicecode",
            "lineitem_usagetype",
            "lineitem_resourceid",
            "resource_tags_user_owner",
            "resource_tags_user_team",
            "resource_tags_user_app",
            "resource_tags_user_cost_center",
            "resource_tags_user_environment",
        }
    )

    assert mapping == CurColumnMapping(
        usage_start="lineitem_usagestartdate",
        cost="lineitem_unblendedcost",
        service="product_servicecode",
        usage_type="lineitem_usagetype",
        resource_id="lineitem_resourceid",
        owner_tag="resource_tags_user_owner",
        team_tag="resource_tags_user_team",
        application_tag="resource_tags_user_app",
        cost_center_tag="resource_tags_user_cost_center",
        environment_tag="resource_tags_user_environment",
    )


def test_cur_schema_cli_outputs_mapping() -> None:
    result = CliRunner().invoke(main, ["cur", "schema", "--path", str(_sample_cur_path())])

    assert result.exit_code == 0
    assert "CUR Schema Mapping" in result.output
    assert "usage_start" in result.output
    assert "line_item_usage_start_date" in result.output
    assert "resource_id" in result.output


def test_cur_validate_cli_accepts_sample_cur() -> None:
    result = CliRunner().invoke(main, ["cur", "validate", "--path", str(_sample_cur_path())])

    assert result.exit_code == 0
    assert "CUR validation passed" in result.output
    assert "EC2 rows:" in result.output


def test_analyze_ec2_cli_outputs_readable_brief() -> None:
    result = CliRunner().invoke(main, ["analyze", "ec2", "--cur", str(_sample_cur_path())])

    assert result.exit_code == 0
    assert "EC2 Investigation Brief" in result.output
    assert "Previous period cost: $200.00" in result.output
    assert "Current period cost:  $520.00" in result.output
    assert "Delta: +$320.00 (+160.0%)" in result.output
    assert "i-prod-a" in result.output
    assert "BoxUsage:m6i.4xlarge" in result.output
    assert "Evidence Available" in result.output
    assert "Evidence Missing" in result.output
    assert "Review Questions" in result.output


def test_analyze_ec2_cli_accepts_selected_month() -> None:
    result = CliRunner().invoke(
        main, ["analyze", "ec2", "--cur", str(_sample_cur_path()), "--month", "2026-06"]
    )

    assert result.exit_code == 0
    assert "Period: 2026-05 -> 2026-06" in result.output


def test_analyze_ec2_cli_outputs_account_and_region_tables() -> None:
    result = CliRunner().invoke(
        main,
        ["analyze", "ec2", "--cur", str(_account_region_cur_path()), "--month", "2026-06"],
    )

    assert result.exit_code == 0
    assert "Top Contributing Accounts" in result.output
    assert "111111111111" in result.output
    assert "Top Contributing Regions" in result.output
    assert "us-east-1" in result.output


def test_analyze_ec2_cli_outputs_tag_coverage() -> None:
    result = CliRunner().invoke(
        main, ["analyze", "ec2", "--cur", str(_tagged_cur_path()), "--month", "2026-06"]
    )

    assert result.exit_code == 0
    assert "Tagged" in result.output
    assert "Untagged" in result.output
    assert "Observed Tag Values" in result.output
    assert "platform" in result.output


def test_analyze_ec2_cli_reports_invalid_month() -> None:
    result = CliRunner().invoke(
        main, ["analyze", "ec2", "--cur", str(_sample_cur_path()), "--month", "2026-6"]
    )

    assert result.exit_code != 0
    assert "YYYY-MM" in result.output
