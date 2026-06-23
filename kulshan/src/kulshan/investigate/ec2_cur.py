"""Small local CUR proof for EC2 investigation briefs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CurInvestigationError(RuntimeError):
    """Raised when a local CUR investigation cannot be completed."""


@dataclass(frozen=True)
class DeltaRow:
    """A grouped previous/current cost delta."""

    name: str
    previous_cost: float
    current_cost: float
    delta: float


@dataclass(frozen=True)
class Ec2InvestigationBrief:
    """The deterministic evidence needed for the first EC2 brief."""

    service: str
    previous_period: str
    current_period: str
    previous_cost: float
    current_cost: float
    delta: float
    delta_percent: float | None
    top_resources: list[DeltaRow]
    top_usage_types: list[DeltaRow]
    review_questions: list[str]


def investigate_ec2_cur(cur_path: str) -> Ec2InvestigationBrief:
    """Investigate EC2 movement in a local Parquet CUR export."""

    try:
        import duckdb
    except ImportError as exc:
        raise CurInvestigationError(
            "DuckDB is required for local CUR investigations. "
            "Install it with: pip install duckdb"
        ) from exc

    source = _parquet_glob(cur_path)
    con = duckdb.connect(database=":memory:")
    try:
        con.execute(
            "CREATE VIEW cur_raw AS "
            f"SELECT * FROM read_parquet({_sql_string(source)}, union_by_name = true)"
        )
        columns = _columns(con)
        mapping = _resolve_columns(columns)
        _create_normalized_view(con, mapping)

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

        current_period, previous_period = periods[0], periods[1]
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
    except Exception as exc:
        raise CurInvestigationError(f"Could not query local CUR Parquet data: {exc}") from exc
    finally:
        con.close()


def _parquet_glob(cur_path: str) -> str:
    path = Path(cur_path)
    if not path.exists():
        raise CurInvestigationError(f"Local CUR path does not exist: {cur_path}")
    if path.is_file():
        if path.suffix.lower() != ".parquet":
            raise CurInvestigationError("Local CUR input must be a Parquet file or directory.")
        return path.as_posix()

    parquet_files = sorted(path.rglob("*.parquet"))
    if not parquet_files:
        raise CurInvestigationError(f"No Parquet files found under local CUR path: {cur_path}")
    return (path / "**" / "*.parquet").as_posix()


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _columns(con: Any) -> set[str]:
    rows = con.execute("DESCRIBE cur_raw").fetchall()
    return {str(row[0]).lower() for row in rows}


def _resolve_columns(columns: set[str]) -> dict[str, str]:
    mapping = {
        "usage_start": _first(
            columns,
            "line_item_usage_start_date",
            "lineitem_usagestartdate",
            "usage_start_date",
        ),
        "cost": _first(
            columns,
            "line_item_net_unblended_cost",
            "line_item_unblended_cost",
            "line_item_blended_cost",
            "lineitem_unblendedcost",
            "cost",
        ),
        "service": _first(
            columns,
            "line_item_product_code",
            "product_servicecode",
            "product_product_name",
            "service",
        ),
        "usage_type": _first(
            columns,
            "line_item_usage_type",
            "lineitem_usagetype",
            "usage_type",
        ),
        "resource_id": _first(
            columns,
            "line_item_resource_id",
            "lineitem_resourceid",
            "resource_id",
        ),
    }

    missing = [key for key, value in mapping.items() if value is None and key != "resource_id"]
    if missing:
        raise CurInvestigationError(
            "Local CUR export is missing required column(s): " + ", ".join(missing)
        )
    if mapping["resource_id"] is None:
        mapping["resource_id"] = "NULL"
    return {key: value for key, value in mapping.items() if value is not None}


def _first(columns: set[str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _create_normalized_view(con: Any, mapping: dict[str, str]) -> None:
    resource_expr = (
        "NULLIF(CAST(NULL AS VARCHAR), '')"
        if mapping["resource_id"] == "NULL"
        else f"NULLIF(CAST({mapping['resource_id']} AS VARCHAR), '')"
    )
    con.execute(
        f"""
        CREATE VIEW cur_ec2 AS
        SELECT
            strftime(CAST({mapping['usage_start']} AS TIMESTAMP), '%Y-%m') AS period,
            COALESCE(NULLIF(CAST({mapping['usage_type']} AS VARCHAR), ''), '(unknown)') AS usage_type,
            COALESCE({resource_expr}, '(no resource id)') AS resource_id,
            CAST({mapping['cost']} AS DOUBLE) AS cost
        FROM cur_raw
        WHERE {_service_filter(mapping['service'])}
          AND CAST({mapping['cost']} AS DOUBLE) IS NOT NULL
        """
    )


def _service_filter(service_column: str) -> str:
    service_expr = f"LOWER(CAST({service_column} AS VARCHAR))"
    return (
        f"{service_expr} IN ('amazonec2', 'amazon elastic compute cloud', 'ec2') "
        f"OR {service_expr} LIKE '%elastic compute cloud%'"
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
        ORDER BY current_cost - previous_cost DESC
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
