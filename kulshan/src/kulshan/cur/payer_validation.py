"""CUR payer validation for bound workspaces.

Validates that local CUR/Data Export files belong to the expected
payer account before producing investigation results.

Uses DuckDB projection queries — does not load full dataset into memory.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Literal

from kulshan.cur.errors import CurDataError


# Standard CUR column names for billing/payer account
PAYER_COLUMN_CANDIDATES = (
    "bill_payer_account_id",
    "bill_payeraccountid",
    "payer_account_id",
)


@dataclass
class PayerValidationResult:
    """Result of CUR payer validation."""

    status: Literal["match", "mismatch", "multiple", "missing"]
    expected_payer: str | None = None
    found_payers: list[str] | None = None
    payer_column: str | None = None
    message: str | None = None


class PayerMismatchError(CurDataError):
    """CUR payer does not match workspace configuration."""

    def __init__(
        self,
        workspace_name: str,
        expected_payer: str,
        found_payer: str,
    ):
        self.workspace_name = workspace_name
        self.expected_payer = expected_payer
        self.found_payer = found_payer
        super().__init__(
            f"CUR payer mismatch for workspace '{workspace_name}': "
            f"expected {expected_payer}, found {found_payer}."
        )


class MultiplePayersError(CurDataError):
    """CUR contains multiple distinct payer accounts."""

    def __init__(self, payer_ids: list[str]):
        self.payer_ids = payer_ids
        super().__init__(
            "The CUR input contains multiple payer accounts and cannot be used "
            "with this single-payer workspace."
        )


def validate_cur_payer(
    con: Any,
    expected_payer: str | None,
    workspace_name: str | None = None,
) -> PayerValidationResult:
    """
    Validate CUR payer account against workspace configuration.

    Inspects the bill_payer_account_id column (or equivalent) using
    a DuckDB DISTINCT query — does not load full dataset.

    Args:
        con: DuckDB connection with cur_raw view registered.
        expected_payer: Expected payer account ID (None for unbound).
        workspace_name: Workspace name for error messages.

    Returns:
        PayerValidationResult describing the outcome.

    Raises:
        PayerMismatchError: Single payer found but doesn't match.
        MultiplePayersError: Multiple distinct payer accounts found.
    """
    # Find which payer column exists
    try:
        columns = {
            str(row[0]).lower()
            for row in con.execute("DESCRIBE cur_raw").fetchall()
        }
    except Exception:
        return PayerValidationResult(status="missing", message="Cannot inspect CUR columns.")

    payer_column = None
    for candidate in PAYER_COLUMN_CANDIDATES:
        if candidate in columns:
            payer_column = candidate
            break

    if payer_column is None:
        # No payer evidence available
        return PayerValidationResult(
            status="missing",
            expected_payer=expected_payer,
            message=(
                "The CUR input does not contain payer account evidence. "
                "Kulshan cannot verify that this data belongs to the selected workspace."
            ),
        )

    # Query distinct payer values (ignoring nulls)
    try:
        rows = con.execute(
            f"SELECT DISTINCT CAST({payer_column} AS VARCHAR) "
            f"FROM cur_raw "
            f"WHERE {payer_column} IS NOT NULL "
            f"AND CAST({payer_column} AS VARCHAR) != ''"
        ).fetchall()
    except Exception as e:
        return PayerValidationResult(
            status="missing",
            expected_payer=expected_payer,
            message=f"Cannot query payer column: {e}",
        )

    payer_ids = sorted(set(str(row[0]) for row in rows))

    if not payer_ids:
        return PayerValidationResult(
            status="missing",
            expected_payer=expected_payer,
            payer_column=payer_column,
            message=(
                "The CUR input does not contain payer account evidence. "
                "Kulshan cannot verify that this data belongs to the selected workspace."
            ),
        )

    # No expected payer (unbound workspace) — skip validation
    if expected_payer is None:
        return PayerValidationResult(
            status="match",
            found_payers=payer_ids,
            payer_column=payer_column,
        )

    # Multiple distinct payers
    if len(payer_ids) > 1:
        raise MultiplePayersError(payer_ids)

    # Single payer — compare
    found_payer = payer_ids[0]
    if found_payer == expected_payer:
        return PayerValidationResult(
            status="match",
            expected_payer=expected_payer,
            found_payers=[found_payer],
            payer_column=payer_column,
        )
    else:
        raise PayerMismatchError(
            workspace_name=workspace_name or "(unknown)",
            expected_payer=expected_payer,
            found_payer=found_payer,
        )
