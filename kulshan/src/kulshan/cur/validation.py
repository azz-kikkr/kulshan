"""Generic CUR/Data Export validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kulshan.cur.duckdb_engine import (
    connect_memory,
    create_ec2_view,
    cur_raw_columns,
    register_cur_raw,
)
from kulshan.cur.errors import CurDataError
from kulshan.cur.schema import CurColumnMapping, select_nonnull_cost_column
from kulshan.cur.source import local_parquet_source


@dataclass(frozen=True)
class CurValidationReport:
    """Generic readability and schema readiness for CUR/Data Export Parquet."""

    readable: bool
    row_count: int
    column_count: int
    semantic_fields: tuple[str, ...]
    selected_cost_column: str
    fallback_note: str | None
    top_product_codes: tuple[tuple[str, int], ...]
    top_usage_types: tuple[tuple[str, int], ...]
    ec2_rows: bool
    network_usage_patterns: bool
    bedrock_rows: bool


def validate_local_cur(cur_path: str) -> CurValidationReport:
    """Validate generic local CUR/Data Export readability without requiring EC2 rows."""
    source = local_parquet_source(cur_path)
    con = connect_memory()
    try:
        mapping = register_cur_raw(con, source)
        columns = cur_raw_columns(con)
        row_count = int(con.execute("SELECT COUNT(*) FROM cur_raw").fetchone()[0] or 0)
        # Use shared selector - mapping already has cost column from register_cur_raw,
        # but we call again to get the fallback_note for the report
        cost_column, fallback_note = select_nonnull_cost_column(con, columns)
        return CurValidationReport(
            readable=True,
            row_count=row_count,
            column_count=len(columns),
            semantic_fields=_semantic_fields(mapping),
            selected_cost_column=cost_column,
            fallback_note=fallback_note,
            top_product_codes=_top_counts(con, mapping.service),
            top_usage_types=_top_counts(con, mapping.usage_type),
            ec2_rows=_has_ec2_rows(con, mapping),
            network_usage_patterns=_has_network_usage(con, mapping),
            bedrock_rows=_has_bedrock_rows(con, mapping),
        )
    finally:
        con.close()


def validate_ec2_view_possible(cur_path: str) -> int:
    """Return EC2 row count using the existing EC2 view behavior."""
    source = local_parquet_source(cur_path)
    con = connect_memory()
    try:
        mapping = register_cur_raw(con, source)
        create_ec2_view(con, mapping)
        return int(con.execute("SELECT COUNT(*) FROM cur_ec2").fetchone()[0] or 0)
    finally:
        con.close()


def _semantic_fields(mapping: CurColumnMapping) -> tuple[str, ...]:
    fields = [
        "usage_start",
        "cost",
        "service",
        "usage_type",
    ]
    optional = {
        "resource_id": mapping.resource_id,
        "account_id": mapping.account_id,
        "region": mapping.region,
        "owner_tag": mapping.owner_tag,
        "team_tag": mapping.team_tag,
        "application_tag": mapping.application_tag,
    }
    fields.extend(name for name, column in optional.items() if column is not None)
    return tuple(fields)


def _top_counts(con: Any, column: str) -> tuple[tuple[str, int], ...]:
    rows = con.execute(
        f"""
        SELECT COALESCE(NULLIF(CAST({column} AS VARCHAR), ''), '(unknown)') AS value,
               COUNT(*) AS rows
        FROM cur_raw
        GROUP BY value
        ORDER BY rows DESC, value ASC
        LIMIT 5
        """
    ).fetchall()
    return tuple((str(row[0]), int(row[1] or 0)) for row in rows)


def _has_ec2_rows(con: Any, mapping: CurColumnMapping) -> bool:
    row = con.execute(
        f"""
        SELECT COUNT(*)
        FROM cur_raw
        WHERE LOWER(CAST({mapping.service} AS VARCHAR)) IN ('amazonec2', 'ec2')
           OR LOWER(CAST({mapping.service} AS VARCHAR)) LIKE '%elastic compute cloud%'
        """
    ).fetchone()
    return int(row[0] or 0) > 0


def _has_network_usage(con: Any, mapping: CurColumnMapping) -> bool:
    row = con.execute(
        f"""
        SELECT COUNT(*)
        FROM cur_raw
        WHERE LOWER(CAST({mapping.usage_type} AS VARCHAR)) LIKE '%data%transfer%'
           OR LOWER(CAST({mapping.usage_type} AS VARCHAR)) LIKE '%nat%'
           OR LOWER(CAST({mapping.usage_type} AS VARCHAR)) LIKE '%loadbalancer%'
        """
    ).fetchone()
    return int(row[0] or 0) > 0


def _has_bedrock_rows(con: Any, mapping: CurColumnMapping) -> bool:
    row = con.execute(
        f"""
        SELECT COUNT(*)
        FROM cur_raw
        WHERE LOWER(CAST({mapping.service} AS VARCHAR)) LIKE '%bedrock%'
        """
    ).fetchone()
    return int(row[0] or 0) > 0
