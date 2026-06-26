"""DuckDB helpers for local CUR/Data Exports investigation."""

from __future__ import annotations

from typing import Any

from kulshan.cur.errors import CurDataError
from kulshan.cur.schema import CurColumnMapping, resolve_cur_columns


def connect_memory() -> Any:
    """Create an in-memory DuckDB connection."""
    try:
        import duckdb
    except ImportError as exc:
        raise CurDataError(
            "DuckDB is required for local CUR investigations. Install it with: pip install duckdb"
        ) from exc
    return duckdb.connect(database=":memory:")


def register_cur_raw(con: Any, parquet_source: str) -> CurColumnMapping:
    """Register raw CUR Parquet data and return the resolved column mapping."""
    con.execute(
        "CREATE VIEW cur_raw AS "
        f"SELECT * FROM read_parquet({_sql_string(parquet_source)}, union_by_name = true)"
    )
    return resolve_cur_columns(cur_raw_columns(con))


def cur_raw_columns(con: Any) -> set[str]:
    """Return lower-cased raw CUR column names from the registered view."""
    rows = con.execute("DESCRIBE cur_raw").fetchall()
    return {str(row[0]).lower() for row in rows}


def create_ec2_view(con: Any, mapping: CurColumnMapping) -> None:
    """Create a normalized EC2-only view with stable column names."""
    resource_expr = _optional_text_expr(mapping.resource_id)
    account_expr = _optional_text_expr(mapping.account_id)
    region_expr = _optional_text_expr(mapping.region)
    con.execute(
        f"""
        CREATE VIEW cur_ec2 AS
        SELECT
            strftime(CAST({mapping.usage_start} AS TIMESTAMP), '%Y-%m') AS period,
            COALESCE(NULLIF(CAST({mapping.usage_type} AS VARCHAR), ''), '(unknown)') AS usage_type,
            COALESCE({resource_expr}, '(no resource id)') AS resource_id,
            COALESCE({account_expr}, '(no account id)') AS account_id,
            COALESCE({region_expr}, '(no region)') AS region,
            CAST({mapping.cost} AS DOUBLE) AS cost
        FROM cur_raw
        WHERE {_service_filter(mapping.service)}
          AND CAST({mapping.cost} AS DOUBLE) IS NOT NULL
        """
    )


def _optional_text_expr(column: str | None) -> str:
    if column is None:
        return "NULLIF(CAST(NULL AS VARCHAR), '')"
    return f"NULLIF(CAST({column} AS VARCHAR), '')"


def _service_filter(service_column: str) -> str:
    service_expr = f"LOWER(CAST({service_column} AS VARCHAR))"
    return (
        f"{service_expr} IN ('amazonec2', 'amazon elastic compute cloud', 'ec2') "
        f"OR {service_expr} LIKE '%elastic compute cloud%'"
    )


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
