"""Export helpers for deterministic investigation results."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from kulshan.cur.s3_query import CostInvestigationResult
from kulshan.investigate.models import DeltaRow, Ec2InvestigationBrief, EvidenceItem


def ec2_brief_to_json(brief: Ec2InvestigationBrief) -> str:
    """Serialize an EC2 investigation brief as formatted JSON."""
    return json.dumps(to_jsonable(brief), indent=2) + "\n"


def ec2_brief_to_markdown(brief: Ec2InvestigationBrief) -> str:
    """Render an EC2 investigation brief as Markdown."""
    pct = "n/a" if brief.delta_percent is None else f"{brief.delta_percent:+.1f}%"
    lines = [
        f"# EC2 Investigation: {brief.current_period}",
        "",
        (
            f"EC2 spend moved from {_money(brief.previous_cost)} in {brief.previous_period} "
            f"to {_money(brief.current_cost)} in {brief.current_period}, a delta of "
            f"{_money_delta(brief.delta)} ({pct})."
        ),
        "",
    ]
    lines.extend(_delta_table("Top Accounts", brief.top_accounts))
    lines.extend(_delta_table("Top Regions", brief.top_regions))
    lines.extend(_delta_table("Top Resources", brief.top_resources))
    lines.extend(_delta_table("Top Usage Types", brief.top_usage_types))
    lines.extend(_tag_coverage_section(brief))
    lines.extend(_evidence_list("Evidence Available", brief.evidence_available, "[x]"))
    lines.extend(_evidence_list("Evidence Missing", brief.evidence_missing, "[ ]"))
    lines.extend(["## Review Questions", ""])
    lines.extend(f"{index}. {question}" for index, question in enumerate(brief.review_questions, 1))
    lines.append("")
    return "\n".join(lines)


def cost_result_to_json(result: CostInvestigationResult, month: str) -> str:
    """Serialize an S3-native cost investigation result as formatted JSON."""
    payload = {"billing_month": month, **asdict(result)}
    return json.dumps(to_jsonable(payload), indent=2) + "\n"


def cost_result_to_markdown(result: CostInvestigationResult, month: str) -> str:
    """Render an S3-native cost investigation result as Markdown."""
    lines = [
        f"# Cost Investigation: {month}",
        "",
        f"Total spend was {_money(result.total_spend)} using `{result.cost_column}`.",
    ]
    if result.fallback_note:
        lines.append(f"Cost column fallback: {result.fallback_note}")
    lines.append("")
    lines.extend(_cost_table("Top Services", "Service", result.top_services))
    lines.extend(_cost_table("Top Usage Types", "Usage Type", result.top_usage_types))
    lines.extend(_cost_table("Top Accounts", "Account", result.top_accounts))
    lines.extend(_cost_table("Top Regions", "Region", result.top_regions))
    lines.extend(
        [
            "---",
            "",
            (
                f"Scan estimate: `{result.estimate.method}`, "
                f"{result.estimate.estimated_bytes} bytes "
                f"(upper bound {result.estimate.upper_bound_bytes} bytes)."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def investigation_format_from_path(path: str) -> str:
    """Infer an investigation export format from an output path extension."""
    lower = path.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".md"):
        return "markdown"
    raise ValueError("Investigation output must end in .json or .md.")


def to_jsonable(value: Any) -> Any:
    """Convert nested dataclasses, lists, tuples, and dicts into JSON-safe values."""
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def _delta_table(title: str, rows: list[DeltaRow]) -> list[str]:
    if not rows:
        return []
    lines = [
        f"## {title}",
        "",
        "| Name | Previous | Current | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {_escape_md(row.name)} | {_money(row.previous_cost)} | "
        f"{_money(row.current_cost)} | {_money_delta(row.delta)} |"
        for row in rows
    )
    lines.append("")
    return lines


def _cost_table(title: str, label: str, rows: tuple[tuple[str, float], ...]) -> list[str]:
    if not rows:
        return []
    lines = [f"## {title}", "", f"| {label} | Cost |", "| --- | ---: |"]
    lines.extend(f"| {_escape_md(name)} | {_money(cost)} |" for name, cost in rows)
    lines.append("")
    return lines


def _tag_coverage_section(brief: Ec2InvestigationBrief) -> list[str]:
    if brief.tag_coverage is None:
        return []
    tag_rows = (
        ("Owner", brief.tag_coverage.owner_values),
        ("Team", brief.tag_coverage.team_values),
        ("Application", brief.tag_coverage.application_values),
    )
    lines = [
        "## Tag Coverage",
        "",
        f"- Tagged spend: {_money(brief.tag_coverage.tagged_cost)}",
        f"- Untagged spend: {_money(brief.tag_coverage.untagged_cost)}",
    ]
    for label, values in tag_rows:
        if values:
            lines.append(f"- Top {label.lower()} values:")
            lines.extend(f"  - {_escape_md(value)}" for value in values)
    lines.append("")
    return lines


def _evidence_list(title: str, items: list[EvidenceItem], marker: str) -> list[str]:
    lines = [f"## {title}", ""]
    lines.extend(
        f"- {marker} **{_escape_md(item.label)}:** {_escape_md(item.detail)}"
        for item in items
    )
    lines.append("")
    return lines


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _money_delta(value: float) -> str:
    prefix = "+" if value >= 0 else "-"
    return f"{prefix}${abs(value):,.2f}"


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
