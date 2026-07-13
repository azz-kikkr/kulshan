"""Generic cost investigation: top movers across all services from local CUR/Data Exports.

This module provides period-over-period cost movement analysis at the service,
account, region, and usage-type level. It returns a CostInvestigationBrief with
full evidence contract including provenance, confidence assessment, and explicit
human_review_required flags.

Target execution time: < 5 seconds on local Parquet.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kulshan.cur.duckdb_engine import connect_memory, register_cur_raw
from kulshan.cur.errors import CurDataError
from kulshan.cur.schema import CurColumnMapping
from kulshan.cur.source import local_parquet_source
from kulshan.investigate.errors import CurInvestigationError
from kulshan.investigate.models import (
    ConfidenceAssessment,
    CostBasis,
    CostInvestigationBrief,
    DeltaRow,
    EvidenceItem,
    InvestigationProvenance,
    OwnerCandidate,
    make_evidence_id,
)

_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def investigate_cost_cur(
    cur_path: str | Path,
    month: str | None = None,
    top_n: int = 10,
) -> CostInvestigationBrief:
    """Investigate cost movement across all services in a local Parquet CUR export.

    Returns a CostInvestigationBrief with full evidence contract including:
    - Provenance (schema version, kulshan version, timestamps)
    - Cost basis (which column, currency, accounting treatment)
    - Top movers by service, account, region, usage type
    - Confidence assessment (data completeness, ownership confidence)
    - Owner candidate (if detectable from patterns)

    Args:
        cur_path: Path to local CUR/Data Exports Parquet file or directory.
        month: Current billing month in YYYY-MM format. Defaults to latest month.
        top_n: Number of top movers to return per dimension. Default: 10.

    Returns:
        CostInvestigationBrief with full evidence contract.

    Raises:
        CurInvestigationError: If the CUR data cannot be read or queried.
    """
    if month is not None:
        _validate_month(month)

    try:
        source = local_parquet_source(str(cur_path))
        con = connect_memory()
    except CurDataError as exc:
        raise CurInvestigationError(str(exc)) from exc

    try:
        mapping = register_cur_raw(con, source)

        current_period, previous_period = _comparison_periods(con, mapping, month)

        # Get data through date for provenance
        data_through = _get_data_through(con, mapping)

        # Period totals
        totals = _period_totals(con, mapping, previous_period, current_period)
        previous_cost = totals[previous_period]
        current_cost = totals[current_period]
        delta = current_cost - previous_cost
        delta_percent = None if previous_cost == 0 else (delta / previous_cost) * 100

        # Top movers by dimension
        top_services = _delta_rows(
            con, mapping, "service", previous_period, current_period, top_n
        )
        top_accounts = (
            _delta_rows(con, mapping, "account_id", previous_period, current_period, top_n)
            if mapping.account_id is not None
            else []
        )
        top_regions = (
            _delta_rows(con, mapping, "region", previous_period, current_period, top_n)
            if mapping.region is not None
            else []
        )
        top_usage_types = _delta_rows(
            con, mapping, "usage_type", previous_period, current_period, top_n
        )

        # Build evidence lists
        evidence_available = _available_evidence(
            mapping=mapping,
            previous_period=previous_period,
            current_period=current_period,
            cur_path=str(cur_path),
        )
        evidence_missing = _missing_evidence(mapping=mapping)

        # Assess confidence
        confidence = _assess_confidence(
            mapping=mapping,
            evidence_available=evidence_available,
            evidence_missing=evidence_missing,
        )

        # Infer owner candidate if possible
        owner_candidate = _infer_owner_candidate(
            top_services=top_services,
            top_accounts=top_accounts,
        )

        # Build cost basis
        cost_basis = CostBasis(
            column=mapping.cost,
            currency="USD",
            includes_credits=False,
            includes_refunds=False,
            includes_taxes=False,
            amortized="amortized" in mapping.cost.lower(),
            fallback_note=None,
        )

        # Build provenance
        provenance = InvestigationProvenance(
            investigation_type="cost_top_movers",
            data_through=data_through,
        )

        # Suggested deep dives
        suggested_deep_dives = _suggested_deep_dives(top_services, top_accounts, delta)

        # Review questions
        review_questions = _review_questions(top_services, top_accounts, delta)

        return CostInvestigationBrief(
            provenance=provenance,
            cost_basis=cost_basis,
            previous_period=previous_period,
            current_period=current_period,
            previous_cost=previous_cost,
            current_cost=current_cost,
            delta=delta,
            delta_percent=delta_percent,
            top_services=top_services,
            top_accounts=top_accounts,
            top_regions=top_regions,
            top_usage_types=top_usage_types,
            evidence_available=evidence_available,
            evidence_missing=evidence_missing,
            confidence=confidence,
            owner_candidate=owner_candidate,
            suggested_deep_dives=suggested_deep_dives,
            review_questions=review_questions,
        )
    except CurInvestigationError:
        raise
    except CurDataError as exc:
        raise CurInvestigationError(str(exc)) from exc
    except Exception as exc:
        raise CurInvestigationError(f"Could not query local CUR Parquet data: {exc}") from exc
    finally:
        con.close()


def _validate_month(month: str) -> None:
    if not _MONTH_PATTERN.match(month):
        raise CurInvestigationError("Month must use YYYY-MM format, for example 2026-06.")


def _previous_month(month: str) -> str:
    _validate_month(month)
    year, month_number = (int(part) for part in month.split("-"))
    if month_number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month_number - 1:02d}"


def _comparison_periods(
    con: Any, mapping: CurColumnMapping, month: str | None
) -> tuple[str, str]:
    if month is None:
        return _latest_two_periods(con, mapping)

    previous_period = _previous_month(month)
    _require_period_data(con, mapping, previous_period, month)
    return month, previous_period


def _latest_two_periods(con: Any, mapping: CurColumnMapping) -> tuple[str, str]:
    periods = [
        row[0]
        for row in con.execute(
            f"""
            SELECT STRFTIME(CAST({mapping.usage_start} AS DATE), '%Y-%m') AS period
            FROM cur_raw
            WHERE {mapping.cost} IS NOT NULL
            GROUP BY period
            ORDER BY period DESC
            LIMIT 2
            """
        ).fetchall()
    ]
    if len(periods) < 2:
        raise CurInvestigationError(
            "Need at least two monthly periods with cost data in the local CUR export."
        )
    return periods[0], periods[1]


def _require_period_data(
    con: Any, mapping: CurColumnMapping, previous_period: str, current_period: str
) -> None:
    rows = con.execute(
        f"""
        SELECT STRFTIME(CAST({mapping.usage_start} AS DATE), '%Y-%m') AS period
        FROM cur_raw
        WHERE {mapping.cost} IS NOT NULL
          AND STRFTIME(CAST({mapping.usage_start} AS DATE), '%Y-%m') IN (?, ?)
        GROUP BY period
        """,
        [previous_period, current_period],
    ).fetchall()
    available_periods = {row[0] for row in rows}
    if current_period not in available_periods:
        raise CurInvestigationError(
            f"No cost data found for selected month {current_period} in the local CUR export."
        )
    if previous_period not in available_periods:
        raise CurInvestigationError(
            f"No cost data found for previous month {previous_period} in the local CUR export."
        )


def _get_data_through(con: Any, mapping: CurColumnMapping) -> str | None:
    """Get the latest date in the CUR data for provenance."""
    try:
        row = con.execute(
            f"""
            SELECT MAX(CAST({mapping.usage_start} AS DATE)) AS max_date
            FROM cur_raw
            """
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return None


def _period_totals(
    con: Any, mapping: CurColumnMapping, previous_period: str, current_period: str
) -> dict[str, float]:
    rows = con.execute(
        f"""
        SELECT
            STRFTIME(CAST({mapping.usage_start} AS DATE), '%Y-%m') AS period,
            SUM({mapping.cost}) AS total_cost
        FROM cur_raw
        WHERE {mapping.cost} IS NOT NULL
          AND STRFTIME(CAST({mapping.usage_start} AS DATE), '%Y-%m') IN (?, ?)
        GROUP BY period
        """,
        [previous_period, current_period],
    ).fetchall()
    totals = {previous_period: 0.0, current_period: 0.0}
    totals.update({row[0]: float(row[1]) if row[1] is not None else 0.0 for row in rows})
    return totals


def _delta_rows(
    con: Any,
    mapping: CurColumnMapping,
    dimension: str,
    previous_period: str,
    current_period: str,
    limit: int = 10,
) -> list[DeltaRow]:
    """Query top movers by dimension (service, account_id, region, usage_type)."""
    # Map dimension to actual column
    column_map = {
        "service": mapping.service,
        "account_id": mapping.account_id,
        "region": mapping.region,
        "usage_type": mapping.usage_type,
    }
    column = column_map.get(dimension)
    if column is None:
        return []

    rows = con.execute(
        f"""
        SELECT
            {column} AS name,
            SUM(CASE WHEN STRFTIME(CAST({mapping.usage_start} AS DATE), '%Y-%m') = ? 
                THEN {mapping.cost} ELSE 0 END) AS previous_cost,
            SUM(CASE WHEN STRFTIME(CAST({mapping.usage_start} AS DATE), '%Y-%m') = ? 
                THEN {mapping.cost} ELSE 0 END) AS current_cost
        FROM cur_raw
        WHERE {mapping.cost} IS NOT NULL
          AND STRFTIME(CAST({mapping.usage_start} AS DATE), '%Y-%m') IN (?, ?)
        GROUP BY {column}
        ORDER BY ABS(current_cost - previous_cost) DESC, current_cost DESC, name ASC
        LIMIT ?
        """,
        [previous_period, current_period, previous_period, current_period, limit],
    ).fetchall()
    return [
        DeltaRow(
            name=str(row[0]) if row[0] else "(blank)",
            previous_cost=float(row[1]) if row[1] is not None else 0.0,
            current_cost=float(row[2]) if row[2] is not None else 0.0,
            delta=float(row[2] if row[2] is not None else 0.0) - float(row[1] if row[1] is not None else 0.0),
        )
        for row in rows
    ]


def _available_evidence(
    mapping: CurColumnMapping,
    previous_period: str,
    current_period: str,
    cur_path: str,
) -> list[EvidenceItem]:
    """Build the list of available evidence with proper IDs and sources."""
    evidence = [
        EvidenceItem(
            evidence_id=make_evidence_id("cur_parquet", cur_path),
            label="CUR/Data Exports Parquet",
            detail=f"Local billing export at {cur_path} was readable.",
            source="cur_parquet",
        ),
        EvidenceItem(
            evidence_id=make_evidence_id("cost_delta", f"{previous_period}-{current_period}"),
            label="Cost delta",
            detail=f"Compared {previous_period} vs {current_period} total spend.",
            source="cur_parquet",
        ),
        EvidenceItem(
            evidence_id=make_evidence_id("service_delta"),
            label="Service delta",
            detail="Top service contributors were computed.",
            source="cur_parquet",
        ),
        EvidenceItem(
            evidence_id=make_evidence_id("usage_type_delta"),
            label="Usage type delta",
            detail="Top usage type contributors were computed.",
            source="cur_parquet",
        ),
    ]

    if mapping.account_id is not None:
        evidence.append(
            EvidenceItem(
                evidence_id=make_evidence_id("account_delta"),
                label="Account delta",
                detail="Top account contributors were computed.",
                source="cur_parquet",
            )
        )

    if mapping.region is not None:
        evidence.append(
            EvidenceItem(
                evidence_id=make_evidence_id("region_delta"),
                label="Region delta",
                detail="Top region contributors were computed.",
                source="cur_parquet",
            )
        )

    return evidence


def _missing_evidence(mapping: CurColumnMapping) -> list[EvidenceItem]:
    """Build the list of missing evidence with proper IDs and sources."""
    evidence = []

    if mapping.account_id is None:
        evidence.append(
            EvidenceItem(
                evidence_id=make_evidence_id("missing_account"),
                label="Account IDs",
                detail="Export does not expose account-level contributors.",
                source="cur_parquet",
            )
        )

    if mapping.region is None:
        evidence.append(
            EvidenceItem(
                evidence_id=make_evidence_id("missing_region"),
                label="Regions",
                detail="Export does not expose region-level contributors.",
                source="cur_parquet",
            )
        )

    # Always missing (future features)
    evidence.extend(
        [
            EvidenceItem(
                evidence_id=make_evidence_id("missing_tags"),
                label="Cost allocation tags",
                detail="Tag-based ownership attribution not yet analyzed for generic cost.",
                source="cur_parquet",
            ),
            EvidenceItem(
                evidence_id=make_evidence_id("missing_anomaly_detection"),
                label="AWS Cost Anomaly Detection",
                detail="Cross-reference with AWS Cost Anomaly Detection not performed.",
                source="cost_anomaly_detection",
            ),
            EvidenceItem(
                evidence_id=make_evidence_id("missing_cloudtrail"),
                label="CloudTrail correlation",
                detail="Change events are not correlated yet.",
                source="cloudtrail",
            ),
        ]
    )

    return evidence


def _assess_confidence(
    mapping: CurColumnMapping,
    evidence_available: list[EvidenceItem],
    evidence_missing: list[EvidenceItem],
) -> ConfidenceAssessment:
    """Assess confidence based on evidence completeness."""
    # Data completeness: based on what columns are available
    completeness_score = 0
    if mapping.account_id is not None:
        completeness_score += 1
    if mapping.region is not None:
        completeness_score += 1
    if mapping.service is not None:
        completeness_score += 1
    if mapping.usage_type is not None:
        completeness_score += 1

    if completeness_score >= 3:
        data_completeness = "high"
    elif completeness_score >= 2:
        data_completeness = "medium"
    else:
        data_completeness = "low"

    # Ownership confidence: low for generic cost (no tag analysis)
    ownership_confidence = "low"

    # Overall label
    if data_completeness == "high":
        label = "medium"  # Medium because no cross-reference with other sources
    elif data_completeness == "low":
        label = "low"
    else:
        label = "medium"

    # Build reason
    reasons = []
    if data_completeness == "high":
        reasons.append("CUR data includes service, account, region, and usage type details")
    elif data_completeness == "medium":
        reasons.append("CUR data has partial dimension coverage")
    else:
        reasons.append("CUR data is missing key dimensions")

    reasons.append("no cross-reference with AWS Cost Anomaly Detection")
    reasons.append("no owner mapping available")

    return ConfidenceAssessment(
        label=label,
        source_agreement="n/a",  # Single source mode (CUR only)
        data_completeness=data_completeness,
        ownership_confidence=ownership_confidence,
        reason="; ".join(reasons),
    )


def _infer_owner_candidate(
    top_services: list[DeltaRow],
    top_accounts: list[DeltaRow],
) -> OwnerCandidate | None:
    """Infer a likely owner candidate from available evidence."""
    # For generic cost, we can only infer from account if single dominant account
    if top_accounts and len(top_accounts) >= 1:
        # Check if top account is dominant (>50% of delta)
        total_delta = sum(abs(a.delta) for a in top_accounts)
        top_delta = abs(top_accounts[0].delta) if top_accounts else 0
        if total_delta > 0 and (top_delta / total_delta) > 0.5:
            return OwnerCandidate(
                team=None,
                account_id=top_accounts[0].name,
                basis="inferred",
                confirmation_required=True,
            )

    # Multiple services/accounts affected - no single owner
    return OwnerCandidate(
        team=None,
        account_id=None,
        basis="unknown",
        confirmation_required=True,
    )


def _suggested_deep_dives(
    top_services: list[DeltaRow],
    top_accounts: list[DeltaRow],
    delta: float,
) -> list[str]:
    """Generate suggested deep dive commands."""
    suggestions = []

    if top_services:
        top_service = top_services[0].name
        if top_service.upper() in ("AMAZONEC2", "EC2", "AMAZON ELASTIC COMPUTE CLOUD"):
            suggestions.append("kulshan investigate ec2 --cur <path> --month <YYYY-MM>")
        # Future: add other service-specific investigations
        # elif top_service.upper() in ("AMAZONS3", "S3"):
        #     suggestions.append("kulshan investigate s3 --cur <path> --month <YYYY-MM>")

    if top_accounts and len(top_accounts) > 1:
        suggestions.append("Review account-level budgets and alerts")

    if delta > 0:
        suggestions.append("Cross-reference with AWS Cost Anomaly Detection alerts")

    return suggestions


def _review_questions(
    top_services: list[DeltaRow],
    top_accounts: list[DeltaRow],
    delta: float,
) -> list[str]:
    """Generate contextual review questions."""
    questions = []

    if top_services:
        service = top_services[0].name
        service_delta = top_services[0].delta
        direction = "increase" if service_delta > 0 else "decrease"
        questions.append(
            f"What changed in {service} during the current period to cause the ${abs(service_delta):,.2f} {direction}?"
        )

    if top_accounts and top_accounts[0].name != "(blank)":
        account = top_accounts[0].name
        questions.append(
            f"Who owns account {account} and were they aware of the cost movement?"
        )

    if delta > 0:
        questions.append(
            "Was this cost increase expected or tied to a planned workload change?"
        )
        questions.append(
            "Should this spend be reviewed before the next finance meeting?"
        )
    else:
        questions.append(
            "Was this cost decrease due to optimization, workload reduction, or resource termination?"
        )

    return questions
