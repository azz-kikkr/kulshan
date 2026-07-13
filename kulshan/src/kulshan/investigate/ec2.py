"""EC2 investigation brief generation from local CUR/Data Exports evidence."""

from __future__ import annotations

import re
from typing import Any

from kulshan.cur.duckdb_engine import connect_memory, create_ec2_view, register_cur_raw
from kulshan.cur.errors import CurDataError
from kulshan.cur.schema import CurColumnMapping
from kulshan.cur.source import local_parquet_source
from kulshan.investigate.errors import CurInvestigationError
from kulshan.investigate.models import (
    ConfidenceAssessment,
    CostBasis,
    DeltaRow,
    Ec2InvestigationBrief,
    EvidenceItem,
    InvestigationProvenance,
    OwnerCandidate,
    TagCoverage,
    make_evidence_id,
)

_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def investigate_ec2_cur(cur_path: str, month: str | None = None) -> Ec2InvestigationBrief:
    """Investigate EC2 movement in a local Parquet CUR export.
    
    Returns an Ec2InvestigationBrief with full evidence contract including:
    - Provenance (schema version, kulshan version, timestamps)
    - Cost basis (which column, currency, accounting treatment)
    - Confidence assessment (data completeness, ownership confidence)
    - Owner candidate (if detectable from tags or patterns)
    
    Target execution time: < 5 seconds on local Parquet.
    """
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
        
        # Get data through date for provenance
        data_through = _get_data_through(con, mapping)
        
        totals = _period_totals(con, previous_period, current_period)
        top_accounts = (
            _delta_rows(con, "account_id", previous_period, current_period)
            if mapping.account_id is not None
            else []
        )
        top_regions = (
            _delta_rows(con, "region", previous_period, current_period)
            if mapping.region is not None
            else []
        )
        top_resources = _delta_rows(con, "resource_id", previous_period, current_period)
        top_usage_types = _delta_rows(con, "usage_type", previous_period, current_period)
        tag_columns = _available_tag_columns(mapping)
        tag_coverage = _tag_coverage(con, current_period, tag_columns) if tag_columns else None

        previous_cost = totals[previous_period]
        current_cost = totals[current_period]
        delta = current_cost - previous_cost
        delta_percent = None if previous_cost == 0 else (delta / previous_cost) * 100

        # Build evidence lists
        evidence_available = _available_evidence(
            mapping=mapping,
            has_tag_columns=bool(tag_columns),
            previous_period=previous_period,
            current_period=current_period,
        )
        evidence_missing = _missing_evidence(mapping=mapping)
        
        # Assess confidence
        confidence = _assess_confidence(
            mapping=mapping,
            tag_coverage=tag_coverage,
            evidence_available=evidence_available,
            evidence_missing=evidence_missing,
        )
        
        # Infer owner candidate if possible
        owner_candidate = _infer_owner_candidate(
            tag_coverage=tag_coverage,
            top_accounts=top_accounts,
            top_resources=top_resources,
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
            investigation_type="ec2_service_investigation",
            data_through=data_through,
        )

        return Ec2InvestigationBrief(
            provenance=provenance,
            cost_basis=cost_basis,
            service="EC2",
            previous_period=previous_period,
            current_period=current_period,
            previous_cost=previous_cost,
            current_cost=current_cost,
            delta=delta,
            delta_percent=delta_percent,
            top_accounts=top_accounts,
            top_regions=top_regions,
            top_resources=top_resources,
            top_usage_types=top_usage_types,
            tag_coverage=tag_coverage,
            evidence_available=evidence_available,
            evidence_missing=evidence_missing,
            confidence=confidence,
            owner_candidate=owner_candidate,
            review_questions=_review_questions(top_resources, top_usage_types, delta),
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


def _get_data_through(con: Any, mapping: CurColumnMapping) -> str | None:
    """Get the latest date in the CUR data for provenance."""
    try:
        row = con.execute(f"""
            SELECT MAX(CAST({mapping.usage_start} AS DATE)) AS max_date
            FROM cur_raw
        """).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return None


def _available_tag_columns(mapping: CurColumnMapping) -> list[str]:
    columns = []
    if mapping.owner_tag is not None:
        columns.append("owner_tag")
    if mapping.team_tag is not None:
        columns.append("team_tag")
    if mapping.application_tag is not None:
        columns.append("application_tag")
    if mapping.cost_center_tag is not None:
        columns.append("cost_center_tag")
    if mapping.environment_tag is not None:
        columns.append("environment_tag")
    return columns


def _tag_coverage(con: Any, current_period: str, tag_columns: list[str]) -> TagCoverage:
    has_tag_expr = " OR ".join(f"{column} IS NOT NULL" for column in tag_columns)
    totals = con.execute(
        f"""
        SELECT
            SUM(CASE WHEN {has_tag_expr} THEN cost ELSE 0 END) AS tagged_cost,
            SUM(CASE WHEN NOT ({has_tag_expr}) THEN cost ELSE 0 END) AS untagged_cost
        FROM cur_ec2
        WHERE period = ?
        """,
        [current_period],
    ).fetchone()
    return TagCoverage(
        tagged_cost=float((totals[0] if totals else 0.0) or 0.0),
        untagged_cost=float((totals[1] if totals else 0.0) or 0.0),
        owner_values=_tag_values(con, current_period, "owner_tag"),
        team_values=_tag_values(con, current_period, "team_tag"),
        application_values=_tag_values(con, current_period, "application_tag"),
        cost_center_values=_tag_values(con, current_period, "cost_center_tag"),
        environment_values=_tag_values(con, current_period, "environment_tag"),
    )


def _tag_values(con: Any, current_period: str, column: str, limit: int = 5) -> list[str]:
    rows = con.execute(
        f"""
        SELECT {column} AS value, SUM(cost) AS current_cost
        FROM cur_ec2
        WHERE period = ? AND {column} IS NOT NULL
        GROUP BY {column}
        ORDER BY current_cost DESC, value ASC
        LIMIT ?
        """,
        [current_period, limit],
    ).fetchall()
    return [str(row[0]) for row in rows]


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


def _available_evidence(
    mapping: CurColumnMapping,
    has_tag_columns: bool,
    previous_period: str,
    current_period: str,
) -> list[EvidenceItem]:
    """Build the list of available evidence with proper IDs and sources."""
    evidence = [
        EvidenceItem(
            evidence_id=make_evidence_id("cur_parquet", "ec2"),
            label="CUR/Data Exports Parquet",
            detail="Local billing export was readable.",
            source="cur_parquet",
        ),
        EvidenceItem(
            evidence_id=make_evidence_id("ec2_delta", f"{previous_period}-{current_period}"),
            label="EC2 service delta",
            detail=f"Compared {previous_period} vs {current_period} EC2 spend.",
            source="cur_parquet",
        ),
        EvidenceItem(
            evidence_id=make_evidence_id("usage_type_delta"),
            label="Usage type delta",
            detail="Top EC2 usage type contributors were computed.",
            source="cur_parquet",
        ),
    ]
    
    if mapping.account_id is not None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("account_delta"),
            label="Account delta",
            detail="Top EC2 account contributors were computed.",
            source="cur_parquet",
        ))
    
    if mapping.region is not None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("region_delta"),
            label="Region delta",
            detail="Top EC2 region contributors were computed.",
            source="cur_parquet",
        ))
    
    if mapping.resource_id is not None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("resource_delta"),
            label="Resource ID delta",
            detail="Top EC2 resource contributors were computed.",
            source="cur_parquet",
        ))
    
    if has_tag_columns:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("tag_coverage"),
            label="Tag coverage",
            detail="Current-period tagged and untagged EC2 spend were computed.",
            source="cur_parquet",
        ))
    
    if mapping.owner_tag is not None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("owner_tag"),
            label="Owner tag",
            detail="Owner tag values were read from the local CUR export.",
            source="cur_parquet",
        ))
    
    if mapping.team_tag is not None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("team_tag"),
            label="Team tag",
            detail="Team tag values were read from the local CUR export.",
            source="cur_parquet",
        ))
    
    if mapping.application_tag is not None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("application_tag"),
            label="Application tag",
            detail="Application tag values were read from the local CUR export.",
            source="cur_parquet",
        ))
    
    return evidence


def _missing_evidence(mapping: CurColumnMapping) -> list[EvidenceItem]:
    """Build the list of missing evidence with proper IDs and sources."""
    evidence = []
    
    if mapping.account_id is None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("missing_account"),
            label="Account IDs",
            detail="Export does not expose account-level contributors.",
            source="cur_parquet",
        ))
    
    if mapping.region is None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("missing_region"),
            label="Regions",
            detail="Export does not expose region-level contributors.",
            source="cur_parquet",
        ))
    
    if mapping.resource_id is None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("missing_resource"),
            label="Resource IDs",
            detail="Export does not expose resource-level contributors.",
            source="cur_parquet",
        ))
    
    if mapping.owner_tag is None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("missing_owner"),
            label="Owner tags",
            detail="Export does not expose owner tag evidence.",
            source="cur_parquet",
        ))
    
    if mapping.team_tag is None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("missing_team"),
            label="Team tags",
            detail="Export does not expose team tag evidence.",
            source="cur_parquet",
        ))
    
    if mapping.application_tag is None:
        evidence.append(EvidenceItem(
            evidence_id=make_evidence_id("missing_application"),
            label="Application tags",
            detail="Export does not expose application tag evidence.",
            source="cur_parquet",
        ))
    
    # Always missing (future features)
    evidence.extend([
        EvidenceItem(
            evidence_id=make_evidence_id("missing_inventory"),
            label="Resource inventory",
            detail="Live EC2 metadata is not joined to billing evidence yet.",
            source="ec2_api",
        ),
        EvidenceItem(
            evidence_id=make_evidence_id("missing_cloudtrail"),
            label="CloudTrail correlation",
            detail="Change events are not correlated yet.",
            source="cloudtrail",
        ),
        EvidenceItem(
            evidence_id=make_evidence_id("missing_deployment"),
            label="Deployment record",
            detail="Ticket, deploy, or release context is not available.",
            source="external",
        ),
    ])
    
    return evidence


def _assess_confidence(
    mapping: CurColumnMapping,
    tag_coverage: TagCoverage | None,
    evidence_available: list[EvidenceItem],
    evidence_missing: list[EvidenceItem],
) -> ConfidenceAssessment:
    """Assess confidence based on evidence completeness and ownership data."""
    # Data completeness: based on what columns are available
    completeness_score = 0
    if mapping.account_id is not None:
        completeness_score += 1
    if mapping.region is not None:
        completeness_score += 1
    if mapping.resource_id is not None:
        completeness_score += 1
    if mapping.owner_tag is not None:
        completeness_score += 1
    
    if completeness_score >= 3:
        data_completeness = "high"
    elif completeness_score >= 1:
        data_completeness = "medium"
    else:
        data_completeness = "low"
    
    # Ownership confidence: based on tag availability and coverage
    if tag_coverage and tag_coverage.owner_values:
        if tag_coverage.coverage_percent >= 70:
            ownership_confidence = "high"
        elif tag_coverage.coverage_percent >= 30:
            ownership_confidence = "medium"
        else:
            ownership_confidence = "low"
    elif mapping.owner_tag is not None:
        ownership_confidence = "low"  # Has column but no values
    else:
        ownership_confidence = "low"
    
    # Overall label
    if data_completeness == "high" and ownership_confidence != "low":
        label = "high"
    elif data_completeness == "low":
        label = "low"
    else:
        label = "medium"
    
    # Build reason
    reasons = []
    if data_completeness == "high":
        reasons.append("CUR data includes account, region, and resource details")
    elif data_completeness == "medium":
        reasons.append("CUR data has partial dimension coverage")
    else:
        reasons.append("CUR data is missing key dimensions")
    
    if ownership_confidence == "high":
        reasons.append(f"owner tags cover {tag_coverage.coverage_percent:.0f}% of spend")
    elif ownership_confidence == "medium":
        reasons.append("owner tags have partial coverage")
    else:
        reasons.append("no owner mapping available")
    
    return ConfidenceAssessment(
        label=label,
        source_agreement="n/a",  # Single source mode (CUR only)
        data_completeness=data_completeness,
        ownership_confidence=ownership_confidence,
        reason="; ".join(reasons),
    )


def _infer_owner_candidate(
    tag_coverage: TagCoverage | None,
    top_accounts: list[DeltaRow],
    top_resources: list[DeltaRow],
) -> OwnerCandidate | None:
    """Infer a likely owner candidate from available evidence."""
    # Priority 1: Owner tag
    if tag_coverage and tag_coverage.owner_values:
        return OwnerCandidate(
            team=tag_coverage.owner_values[0],
            account_id=top_accounts[0].name if top_accounts else None,
            basis="tag_value",
            confirmation_required=True,
        )
    
    # Priority 2: Team tag
    if tag_coverage and tag_coverage.team_values:
        return OwnerCandidate(
            team=tag_coverage.team_values[0],
            account_id=top_accounts[0].name if top_accounts else None,
            basis="tag_value",
            confirmation_required=True,
        )
    
    # Priority 3: Resource naming pattern (e.g., i-prod-*, i-dev-*)
    if top_resources:
        top_resource = top_resources[0].name
        if top_resource.startswith("i-prod") or "prod" in top_resource.lower():
            return OwnerCandidate(
                team="Production (inferred from naming)",
                account_id=top_accounts[0].name if top_accounts else None,
                basis="resource_naming_pattern",
                confirmation_required=True,
            )
        elif top_resource.startswith("i-dev") or "dev" in top_resource.lower():
            return OwnerCandidate(
                team="Development (inferred from naming)",
                account_id=top_accounts[0].name if top_accounts else None,
                basis="resource_naming_pattern",
                confirmation_required=True,
            )
    
    # Priority 4: Account-only inference
    if top_accounts:
        return OwnerCandidate(
            team=None,
            account_id=top_accounts[0].name,
            basis="inferred",
            confirmation_required=True,
        )
    
    return None


def _review_questions(
    resources: list[DeltaRow],
    usage_types: list[DeltaRow],
    delta: float,
) -> list[str]:
    """Generate contextual review questions."""
    resource = resources[0].name if resources else "the top EC2 resource"
    usage_type = usage_types[0].name if usage_types else "the top EC2 usage type"
    
    questions = [
        f"What changed for {resource} during the current period?",
        f"Was the increase in {usage_type} expected or tied to a planned workload change?",
    ]
    
    if delta > 0:
        questions.append(
            "Should this EC2 spend be tagged, reallocated, or reviewed before the finance meeting?"
        )
    else:
        questions.append(
            "Was the cost decrease due to optimization, workload reduction, or resource termination?"
        )
    
    return questions
