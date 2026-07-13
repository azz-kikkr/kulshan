"""Schema normalization for CUR/Data Exports inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kulshan.cur.errors import CurDataError


# Shared cost column candidates in preference order.
# Used by both schema resolution and validation to ensure consistency.
COST_COLUMN_CANDIDATES: tuple[str, ...] = (
    "line_item_net_unblended_cost",
    "line_item_unblended_cost",
    "line_item_blended_cost",
    "lineitem_unblendedcost",
    "pricing_public_on_demand_cost",
    "cost",
)


@dataclass(frozen=True)
class CurColumnMapping:
    """Raw CUR column names mapped to Kulshan's semantic fields."""

    usage_start: str
    cost: str
    service: str
    usage_type: str
    resource_id: str | None = None
    account_id: str | None = None
    region: str | None = None
    owner_tag: str | None = None
    team_tag: str | None = None
    application_tag: str | None = None
    cost_center_tag: str | None = None
    environment_tag: str | None = None
    cost_fallback_note: str | None = None  # Set when fallback cost column used


def resolve_cur_columns(columns: set[str]) -> CurColumnMapping:
    """Resolve known CUR/Data Exports aliases into a semantic column mapping."""
    normalized = {column.lower() for column in columns}
    return CurColumnMapping(
        usage_start=_required(
            normalized,
            "usage_start",
            "line_item_usage_start_date",
            "lineitem_usagestartdate",
            "usage_start_date",
        ),
        cost=_required(
            normalized,
            "cost",
            "line_item_net_unblended_cost",
            "line_item_unblended_cost",
            "line_item_blended_cost",
            "lineitem_unblendedcost",
            "pricing_public_on_demand_cost",
            "cost",
        ),
        service=_required(
            normalized,
            "service",
            "line_item_product_code",
            "product_servicecode",
            "product_product_name",
            "service",
        ),
        usage_type=_required(
            normalized,
            "usage_type",
            "line_item_usage_type",
            "lineitem_usagetype",
            "usage_type",
        ),
        resource_id=_first(
            normalized,
            "line_item_resource_id",
            "lineitem_resourceid",
            "resource_id",
        ),
        account_id=_first(
            normalized,
            "line_item_usage_account_id",
            "bill_payer_account_id",
            "linked_account_id",
            "usage_account_id",
        ),
        region=_first(
            normalized,
            "product_region",
            "line_item_availability_zone",
            "availability_zone",
            "region",
        ),
        owner_tag=_first(
            normalized,
            "resource_tags_user_owner",
            "resource_tags_aws_createdby",
        ),
        team_tag=_first(normalized, "resource_tags_user_team"),
        application_tag=_first(
            normalized,
            "resource_tags_user_application",
            "resource_tags_user_app",
            "resource_tags_user_service",
        ),
        cost_center_tag=_first(normalized, "resource_tags_user_cost_center"),
        environment_tag=_first(normalized, "resource_tags_user_environment"),
    )


def _required(columns: set[str], semantic_name: str, *candidates: str) -> str:
    column = _first(columns, *candidates)
    if column is None:
        raise CurDataError(f"Local CUR export is missing required column: {semantic_name}")
    return column


def _first(columns: set[str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def select_nonnull_cost_column(
    con: Any, columns: set[str]
) -> tuple[str, str | None]:
    """Select the first cost column candidate that has non-null data.

    This is the authoritative cost column selector. Both validation and
    investigation code paths must use this to ensure they pick the same column.

    Args:
        con: DuckDB connection with cur_raw view registered.
        columns: Set of available column names (lowercase).

    Returns:
        Tuple of (selected_column, fallback_note).
        fallback_note is None if the preferred column was used,
        otherwise explains which column was used instead.

    Raises:
        CurDataError: If no supported cost column exists or all are null.
    """
    available = [col for col in COST_COLUMN_CANDIDATES if col in columns]
    if not available:
        raise CurDataError("No supported CUR cost column found.")

    preferred = available[0]
    for column in available:
        count = int(
            con.execute(
                f"SELECT COUNT(*) FROM cur_raw WHERE {column} IS NOT NULL"
            ).fetchone()[0]
            or 0
        )
        if count > 0:
            note = None if column == preferred else f"{preferred} was null; using {column}."
            return column, note

    raise CurDataError("Supported CUR cost columns exist but are all null.")
