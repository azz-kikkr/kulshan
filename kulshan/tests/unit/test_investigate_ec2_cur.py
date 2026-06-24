"""Tests for local EC2 CUR investigations."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from kulshan.cli import main
from kulshan.cur.schema import CurColumnMapping, resolve_cur_columns
from kulshan.investigate.ec2_cur import investigate_ec2_cur


def _sample_cur_path() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "cur" / "sample-cur"


def test_investigate_ec2_cur_calculates_period_delta() -> None:
    brief = investigate_ec2_cur(str(_sample_cur_path()))

    assert brief.previous_period == "2026-05"
    assert brief.current_period == "2026-06"
    assert brief.previous_cost == 200.0
    assert brief.current_cost == 520.0
    assert brief.delta == 320.0
    assert brief.delta_percent == 160.0
    assert brief.top_resources[0].name == "i-prod-a"
    assert brief.top_resources[0].delta == 190.0
    assert brief.top_usage_types[0].name == "BoxUsage:m6i.4xlarge"
    assert len(brief.review_questions) == 3


def test_cur_schema_resolves_athena_style_aliases() -> None:
    mapping = resolve_cur_columns(
        {
            "lineitem_usagestartdate",
            "lineitem_unblendedcost",
            "product_servicecode",
            "lineitem_usagetype",
            "lineitem_resourceid",
        }
    )

    assert mapping == CurColumnMapping(
        usage_start="lineitem_usagestartdate",
        cost="lineitem_unblendedcost",
        service="product_servicecode",
        usage_type="lineitem_usagetype",
        resource_id="lineitem_resourceid",
    )


def test_investigate_ec2_cli_outputs_readable_brief() -> None:
    result = CliRunner().invoke(main, ["investigate", "ec2", "--cur", str(_sample_cur_path())])

    assert result.exit_code == 0
    assert "EC2 Investigation Brief" in result.output
    assert "Previous period cost: $200.00" in result.output
    assert "Current period cost:  $520.00" in result.output
    assert "Delta: +$320.00 (+160.0%)" in result.output
    assert "i-prod-a" in result.output
    assert "BoxUsage:m6i.4xlarge" in result.output
    assert "Review Questions" in result.output
