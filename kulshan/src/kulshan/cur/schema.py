"""Schema normalization for CUR/Data Exports inputs."""

from __future__ import annotations

from dataclasses import dataclass

from kulshan.cur.errors import CurDataError


@dataclass(frozen=True)
class CurColumnMapping:
    """Raw CUR column names mapped to Kulshan's semantic fields."""

    usage_start: str
    cost: str
    service: str
    usage_type: str
    resource_id: str | None = None


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
