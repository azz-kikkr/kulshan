"""EC2 investigation brief generation from local CUR/Data Exports evidence."""

from __future__ import annotations

import re
from typing import Any

from kulshan.cur.duckdb_engine import connect_memory, create_ec2_view, register_cur_raw
from kulshan.cur.errors import CurDataError
from kulshan.cur.source import local_parquet_source
from kulshan.investigate.errors import CurInvestigationError
from kulshan.investigate.models import DeltaRow, Ec2InvestigationBrief, EvidenceItem

_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def investigate_ec2_cur(cur_path: str, month: str | None = None) -> Ec2InvestigationBrief:
    """Investigate EC2 movement in a local Parquet CUR export."""
    if month is not None:
        _validate_month(month)

    try:
        source = local_parquet_source(cur_path)
        con = connect_memory()
    except CurDataError as exc:
        raise CurInvestigationError(str(exc)) from exc

    try:
        mapping = register_cur_raw(con, source)
        create_ec2_view(con, mapping)

        current_period, previous_period = _comparison_periods(con, month)
        totals = _period_totals(con, previous_period, current_period)
        top_resources = _delta_rows(con, "resource_id", previous_period, current_period)
        top_usage_types = _delta_rows(con, "usage_type", previous_period, current_period)

        previous_cost = totals[previous_period]
        current_cost = totals[current_period]
        delta = current_cost - previous_cost
        delta_percent = None if previous_cost == 0 else (delta / previous_cost) * 100

        return Ec2InvestigationBrief(
            service="EC2",
            previous_period=previous_period,
            current_period=current_period,
            previous_cost=previous_cost,
            current_cost=current_cost,
            delta=delta,
            delta_percent=delta_percent,
            top_resources=top_resources,
            top_usage_types=top_usage_types,
            evidence_available=_available_evidence(mapping.resource_id is not None),
            evidence_missing=_missing_evidence(mapping.resource_id is not None),
            review_questions=_review_questions(top_resources, top_usage_types),
        )
    except CurInvestigationError:
        raise
    except CurDataError as exc:
        raise CurInvestigationError(str(exc)) from exc
    except Exception as exc:
        raise CurInvestigationError(f"Could not query local CUR Parquet data: {exc}") from exc
    finally:
        con.close()


def _comparison_periods(con: Any, month: str | None) -> tuple[str, str]:
    if month is None:
        return _latest_two_periods(con)

    previous_period = _previous_month(month)
    _require_period_data(con, previous_period, month)
    return month, previous_period


def _latest_two_periods(con: Any) -> tuple[str, str]:
    periods = [
        row[0]
        for row in con.execute(
            """
            SELECT period
            FROM cur_ec2
            GROUP BY period
            ORDER BY period DESC
            LIMIT 2
            """
        ).fetchall()
    ]
    if len(periods) < 2:
        raise CurInvestigationError(
            "Need at least two monthly periods with EC2 cost in the local CUR export."
        )
    return periods[0], periods[1]


def _validate_month(month: str) -> None:
    if not _MONTH_PATTERN.match(month):
        raise CurInvestigationError("Month must use YYYY-MM format, for example 2026-06.")


def _previous_month(month: str) -> str:
    _validate_month(month)
    year, month_number = (int(part) for part in month.split("-"))
    if month_number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month_number - 1:02d}"


def _require_period_data(con: Any, previous_period: str, current_period: str) -> None:
    rows = con.execute(
        """
        SELECT period
        FROM cur_ec2
        WHERE period IN (?, ?)
        GROUP BY period
        """,
        [previous_period, current_period],
    ).fetchall()
    available_periods = {row[0] for row in rows}
    if current_period not in available_periods:
        raise CurInvestigationError(
            f"No EC2 cost data found for selected month {current_period} in the local CUR export."
        )
    if previous_period not in available_periods:
        raise CurInvestigationError(
            f"No EC2 cost data found for previous month {previous_period} in the local CUR export."
        )


def _period_totals(con: Any, previous_period: str, current_period: str) -> dict[str, float]:
    rows = con.execute(
        """
        SELECT period, SUM(cost) AS total_cost
        FROM cur_ec2
        WHERE period IN (?, ?)
        GROUP BY period
        """,
        [previous_period, current_period],
    ).fetchall()
    totals = {previous_period: 0.0, current_period: 0.0}
    totals.update({row[0]: float(row[1] or 0.0) for row in rows})
    return totals


def _delta_rows(
    con: Any,
    dimension: str,
    previous_period: str,
    current_period: str,
    limit: int = 5,
) -> list[DeltaRow]:
    rows = con.execute(
        f"""
        SELECT
            {dimension} AS name,
            SUM(CASE WHEN period = ? THEN cost ELSE 0 END) AS previous_cost,
            SUM(CASE WHEN period = ? THEN cost ELSE 0 END) AS current_cost
        FROM cur_ec2
        WHERE period IN (?, ?)
        GROUP BY {dimension}
        ORDER BY current_cost - previous_cost DESC, current_cost DESC, name ASC
        LIMIT ?
        """,
        [previous_period, current_period, previous_period, current_period, limit],
    ).fetchall()
    return [
        DeltaRow(
            name=str(row[0]),
            previous_cost=float(row[1] or 0.0),
            current_cost=float(row[2] or 0.0),
            delta=float((row[2] or 0.0) - (row[1] or 0.0)),
        )
        for row in rows
    ]


def _available_evidence(has_resource_id: bool) -> list[EvidenceItem]:
    evidence = [
        EvidenceItem("CUR/Data Exports Parquet", "Local billing export was readable."),
        EvidenceItem(
            "EC2 service delta",
            "Previous and current EC2 monthly spend were computed.",
        ),
        EvidenceItem("Usage type delta", "Top EC2 usage type contributors were computed."),
    ]
    if has_resource_id:
        evidence.append(
            EvidenceItem("Resource ID delta", "Top EC2 resource contributors were computed.")
        )
    return evidence


def _missing_evidence(has_resource_id: bool) -> list[EvidenceItem]:
    evidence = []
    if not has_resource_id:
        evidence.append(
            EvidenceItem("Resource IDs", "Export does not expose resource-level contributors.")
        )
    evidence.extend(
        [
            EvidenceItem("Owner tags", "Owner/application/environment tags are not evaluated yet."),
            EvidenceItem(
                "Resource inventory",
                "Live EC2 metadata is not joined to billing evidence yet.",
            ),
            EvidenceItem("CloudTrail correlation", "Change events are not correlated yet."),
            EvidenceItem(
                "Deployment record",
                "Ticket, deploy, or release context is not available.",
            ),
        ]
    )
    return evidence

def _review_questions(resources: list[DeltaRow], usage_types: list[DeltaRow]) -> list[str]:
    resource = resources[0].name if resources else "the top EC2 resource"
    usage_type = usage_types[0].name if usage_types else "the top EC2 usage type"
    return [
        f"What changed for {resource} during the current period?",
        f"Was the increase in {usage_type} expected or tied to a planned workload change?",
        "Should this EC2 spend be tagged, reallocated, or reviewed before the finance meeting?",
    ]
