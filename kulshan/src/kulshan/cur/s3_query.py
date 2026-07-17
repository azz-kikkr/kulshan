"""DuckDB httpfs helpers for S3-native CUR/Data Export queries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3

from kulshan.cur.errors import CurDataError
from kulshan.cur.manifest_reader import ManifestIndex

_COST_COLUMNS = (
    "line_item_net_unblended_cost",
    "line_item_unblended_cost",
    "line_item_blended_cost",
    "pricing_public_on_demand_cost",
)


@dataclass(frozen=True)
class ScanEstimate:
    """Estimated bytes scanned for an S3 Parquet query."""

    estimated_bytes: int
    upper_bound_bytes: int
    method: str
    note: str


@dataclass(frozen=True)
class CostColumnSelection:
    """Selected usable cost column and fallback note."""

    column: str
    fallback_note: str | None = None


@dataclass(frozen=True)
class CostInvestigationResult:
    """S3-native cost investigation summary."""

    total_spend: float
    cost_column: str
    fallback_note: str | None
    top_services: tuple[tuple[str, float], ...]
    top_usage_types: tuple[tuple[str, float], ...]
    top_accounts: tuple[tuple[str, float], ...]
    top_regions: tuple[tuple[str, float], ...]
    estimate: ScanEstimate


def connect_s3_duckdb() -> Any:
    """Create a DuckDB connection configured for S3 httpfs reads."""
    try:
        import duckdb
    except ImportError as exc:
        raise CurDataError(
            "DuckDB is required for S3 CUR investigations. Install duckdb."
        ) from exc

    con = duckdb.connect(database=":memory:")
    try:
        con.execute("LOAD httpfs")
    except Exception as exc:
        con.close()
        raise CurDataError(
            "DuckDB httpfs is required for S3 CUR investigations. The platform team may "
            "need to install or package DuckDB httpfs for this environment."
        ) from exc

    session = boto3.session.Session()
    region = session.region_name or "us-east-1"
    try:
        con.execute(
            "CREATE TEMPORARY SECRET kulshan_s3 ("
            "TYPE S3, PROVIDER credential_chain, REGION "
            f"{_sql_string(region)})"
        )
    except Exception:
        _create_boto3_resolved_secret(con, session, region)
    return con


def _create_boto3_resolved_secret(con: Any, session, region: str) -> None:
    credentials = session.get_credentials()
    if credentials is None:
        raise CurDataError("AWS credentials were not found in the AWS credential provider chain.")
    frozen = credentials.get_frozen_credentials()
    token_sql = ""
    if frozen.token:
        token_sql = f", SESSION_TOKEN {_sql_string(frozen.token)}"
    con.execute(
        "CREATE TEMPORARY SECRET kulshan_s3 ("
        "TYPE S3, KEY_ID "
        f"{_sql_string(frozen.access_key)}, SECRET {_sql_string(frozen.secret_key)}"
        f"{token_sql}, REGION {_sql_string(region)})"
    )


def estimate_scan_bytes(
    con: Any, manifest: ManifestIndex, columns: tuple[str, ...]
) -> ScanEstimate:
    """Estimate scan bytes, falling back to manifest total bytes as an upper bound."""
    upper_bound = manifest.total_size_bytes
    try:
        con.execute(
            "SELECT file_name FROM "
            f"parquet_file_metadata({_read_parquet_arg(manifest)}) LIMIT 1"
        )
        column_list = _sql_string_list(_lower_columns(columns))
        metadata_rows = con.execute(
            f"""
            SELECT SUM(total_compressed_size)
            FROM parquet_metadata({_read_parquet_arg(manifest)})
            WHERE lower(path_in_schema) IN ({column_list})
            """
        ).fetchone()
        estimate = int((metadata_rows[0] if metadata_rows else None) or 0)
        if estimate > 0:
            return ScanEstimate(
                estimated_bytes=estimate,
                upper_bound_bytes=upper_bound,
                method="parquet_metadata",
                note="Estimated from Parquet column compressed sizes.",
            )
    except Exception:
        pass
    return ScanEstimate(
        estimated_bytes=upper_bound,
        upper_bound_bytes=upper_bound,
        method="manifest_upper_bound",
        note="Upper bound from manifest total bytes; Parquet metadata estimate unavailable.",
    )


def analyze_cost_s3(
    con: Any, manifest: ManifestIndex, month: str
) -> CostInvestigationResult:
    """Run a generic monthly cost analysis over S3 CUR Parquet with DuckDB httpfs."""
    columns = cur_columns(con, manifest)
    cost_selection = select_cost_column(con, manifest, columns, month)
    service_col = _required_column(columns, "line_item_product_code", "product_servicecode")
    usage_col = _required_column(columns, "line_item_usage_type", "lineitem_usagetype")
    usage_start = _required_column(
        columns, "line_item_usage_start_date", "lineitem_usagestartdate"
    )
    account_col = _optional_column(columns, "line_item_usage_account_id", "usage_account_id")
    region_col = _optional_column(columns, "product_region", "region")
    estimate_cols = tuple(
        column
        for column in (
            cost_selection.column,
            service_col,
            usage_col,
            usage_start,
            account_col,
            region_col,
        )
        if column
    )
    estimate = estimate_scan_bytes(con, manifest, estimate_cols)
    where = f"strftime(CAST({usage_start} AS TIMESTAMP), '%Y-%m') = {_sql_string(month)}"
    source = _source_sql(manifest)
    total = _scalar_float(
        con,
        f"SELECT SUM(CAST({cost_selection.column} AS DOUBLE)) FROM {source} WHERE {where}",
    )
    return CostInvestigationResult(
        total_spend=total,
        cost_column=cost_selection.column,
        fallback_note=cost_selection.fallback_note,
        top_services=_top_costs(con, source, service_col, cost_selection.column, where, 10),
        top_usage_types=_top_costs(con, source, usage_col, cost_selection.column, where, 10),
        top_accounts=_top_costs(con, source, account_col, cost_selection.column, where, 5)
        if account_col
        else (),
        top_regions=_top_costs(con, source, region_col, cost_selection.column, where, 5)
        if region_col
        else (),
        estimate=estimate,
    )


def cur_columns(con: Any, manifest: ManifestIndex) -> set[str]:
    """Read columns exposed by the manifest's Parquet files."""
    rows = con.execute(f"DESCRIBE SELECT * FROM {_source_sql(manifest)} LIMIT 0").fetchall()
    return {str(row[0]).lower() for row in rows}


def select_cost_column(
    con: Any,
    manifest: ManifestIndex,
    columns: set[str],
    month: str | None = None,
) -> CostColumnSelection:
    """Choose the first preferred cost column with at least one non-null value."""
    available = [column for column in _COST_COLUMNS if column in columns]
    if not available:
        raise CurDataError("No supported CUR cost column found.")
    usage_start = _optional_column(columns, "line_item_usage_start_date", "lineitem_usagestartdate")
    where = ""
    if month and usage_start:
        where = f" WHERE strftime(CAST({usage_start} AS TIMESTAMP), '%Y-%m') = {_sql_string(month)}"
    source = _source_sql(manifest)
    for column in available:
        count = con.execute(
            f"SELECT COUNT(*) FROM {source}{where} WHERE {column} IS NOT NULL"
            if where == ""
            else f"SELECT COUNT(*) FROM {source}{where} AND {column} IS NOT NULL"
        ).fetchone()[0]
        if int(count or 0) > 0:
            first = available[0]
            note = None if column == first else f"{first} was null; using {column}."
            return CostColumnSelection(column=column, fallback_note=note)
    raise CurDataError("Supported CUR cost columns exist but are all null for the selected data.")


def _top_costs(
    con: Any,
    source: str,
    dimension: str,
    cost_column: str,
    where: str,
    limit: int,
) -> tuple[tuple[str, float], ...]:
    rows = con.execute(
        f"""
        SELECT COALESCE(NULLIF(CAST({dimension} AS VARCHAR), ''), '(unknown)') AS name,
               SUM(CAST({cost_column} AS DOUBLE)) AS cost
        FROM {source}
        WHERE {where} AND {cost_column} IS NOT NULL
        GROUP BY name
        ORDER BY cost DESC, name ASC
        LIMIT {int(limit)}
        """
    ).fetchall()
    return tuple((str(row[0]), float(row[1] or 0.0)) for row in rows)


def _scalar_float(con: Any, sql: str) -> float:
    row = con.execute(sql).fetchone()
    return float((row[0] if row else 0.0) or 0.0)


def _source_sql(manifest: ManifestIndex) -> str:
    return f"read_parquet({_read_parquet_arg(manifest)}, hive_partitioning=true)"


def _read_parquet_arg(manifest: ManifestIndex) -> str:
    uris = [f"s3://{manifest.bucket}/{file.s3_key}" for file in manifest.files]
    if len(uris) == 1:
        return _sql_string(uris[0])
    return "[" + ", ".join(_sql_string(uri) for uri in uris) + "]"


def _required_column(columns: set[str], *names: str) -> str:
    column = _optional_column(columns, *names)
    if column is None:
        raise CurDataError(f"Required CUR column missing: {' or '.join(names)}")
    return column


def _optional_column(columns: set[str], *names: str) -> str | None:
    for name in names:
        if name in columns:
            return name
    return None


def _lower_columns(columns: tuple[str, ...]) -> list[str]:
    return [column.lower() for column in columns]


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_string_list(values: list[str]) -> str:
    return ", ".join(_sql_string(value) for value in values)
