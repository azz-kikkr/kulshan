"""EC2 investigation brief generation from local CUR/Data Exports evidence."""

from __future__ import annotations

from typing import Any

from kulshan.cur.duckdb_engine import connect_memory, create_ec2_view, register_cur_raw
from kulshan.cur.errors import CurDataError
from kulshan.cur.source import local_parquet_source
from kulshan.investigate.errors import CurInvestigationError
from kulshan.investigate.models import DeltaRow, Ec2InvestigationBrief


def investigate_ec2_cur(cur_path: str) -> Ec2InvestigationBrief:
    """Investigate EC2 movement in a local Parquet CUR export."""
    try:
        source = local_parquet_source(cur_path)
        con = connect_memory()
    except CurDataError as exc:
        raise CurInvestigationError(str(exc)) from exc

    try:
        mapping = register_cur_raw(con, source)
        create_ec2_view(con, mapping)

        current_period, previous_period = _latest_two_periods(con)
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


def _review_questions(resources: list[DeltaRow], usage_types: list[DeltaRow]) -> list[str]:
    resource = resources[0].name if resources else "the top EC2 resource"
    usage_type = usage_types[0].name if usage_types else "the top EC2 usage type"
    return [
        f"What changed for {resource} during the current period?",
        f"Was the increase in {usage_type} expected or tied to a planned workload change?",
        "Should this EC2 spend be tagged, reallocated, or reviewed before the finance meeting?",
    ]
