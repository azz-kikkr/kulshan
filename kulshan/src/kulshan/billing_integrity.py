"""Deterministic billing-source integrity assessment."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from statistics import median
from typing import Optional

STATUS = {"trusted", "provisional", "suspect", "unknown"}
FINALITY = {"estimated", "closed", "unknown"}
RELATIVE_THRESHOLD = 2.0
ABSOLUTE_THRESHOLD = 1000.0
RANGE_MULTIPLIER = 3.0

@dataclass(frozen=True)
class BillingDataIntegrity:
    status: str
    period_finality: str
    sources: list[str]
    retrieved_at: str
    historical_comparison: str
    cross_source_agreement: str
    confidence_effect: str
    reasons: list[str]
    current_value: Optional[float] = None
    prior_value: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def warning(self) -> Optional[str]:
        if self.status == "suspect":
            return "Possible upstream AWS billing-data issue. Do not take remediation action based only on this report."
        return None

def _finality(period: str, today: date) -> str:
    try:
        year, month = (int(x) for x in period.split("-"))
        return "estimated" if (year, month) >= (today.year, today.month) else "closed"
    except (TypeError, ValueError):
        return "unknown"

def assess_billing_integrity(
    period: str,
    current_value: Optional[float] = None,
    historical_values: Optional[list[float]] = None,
    history: Optional[list[float]] = None,
    prior_value: Optional[float] = None,
    source_values: Optional[dict[str, float]] = None,
    sources: Optional[list[str]] = None,
    retrieved_at: Optional[str] = None,
    source_agreement: str = "not_available",
    today: Optional[date] = None,
) -> BillingDataIntegrity:
    """Assess evidence integrity without querying AWS or external services."""
    history = [float(v) for v in (historical_values or history or []) if v is not None]
    if prior_value is not None and not history:
        history = [float(prior_value)]
    if source_values and source_agreement == "not_available":
        vals = list(source_values.values())
        source_agreement = "agreement" if vals and max(vals) == min(vals) else ("disagreement" if len(vals) > 1 else "not_available")
    if isinstance(today, str):
        today = date.fromisoformat(today)
    finality = _finality(period, today or date.today())
    retrieved = retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    reasons: list[str] = []
    comparison = "available" if history else "not_available"
    status = "unknown" if finality == "unknown" else ("provisional" if finality == "estimated" else "trusted")
    prior = median(history) if history else None
    if finality == "estimated":
        reasons.append("The selected period includes the current billing month and may still change.")
    if not history:
        reasons.append("No local historical comparison is available.")
    if current_value is not None and prior is not None and prior != 0:
        delta = float(current_value) - prior
        relative = abs(delta) / abs(prior)
        spread = max((abs(v - prior) for v in history), default=0.0)
        outside_range = abs(delta) > max(ABSOLUTE_THRESHOLD, spread * RANGE_MULTIPLIER)
        if relative >= RELATIVE_THRESHOLD and abs(delta) >= ABSOLUTE_THRESHOLD and outside_range:
            status = "suspect"
            reasons.append("Movement is far outside the locally observed historical range.")
    if source_agreement == "agreement":
        reasons.append("Cost Explorer and CUR agreement is corroboration from AWS billing systems, not independent verification.")
    elif source_agreement == "disagreement":
        status = "suspect" if status != "unknown" else "unknown"
        reasons.append("Configured billing sources disagree.")
    effect = "reduced" if status == "suspect" else ("limited" if status in {"unknown", "provisional"} else "normal")
    return BillingDataIntegrity(status, finality, list(sources or []), retrieved, comparison, source_agreement, effect, reasons, current_value, prior)


def build_report_billing_integrity(
    results: dict,
    *,
    today: Optional[date] = None,
    retrieved_at: Optional[str] = None,
) -> Optional[dict]:
    """Build a billing-integrity disclosure from evidence already in a report.

    Cost Explorer totals and CUR totals can cover different windows, so their
    presence is disclosed without claiming that they independently agree.
    """
    cost_result = results.get("cost")
    if not isinstance(cost_result, dict) or cost_result.get("skipped"):
        return None

    scores = cost_result.get("scores") or {}
    metadata = cost_result.get("metadata") or {}
    cur_metadata = metadata.get("cur_investigation")

    sources: list[str] = []
    current_value: Optional[float] = None

    ce_total = scores.get("total_spend")
    if ce_total is not None:
        sources.append("cost_explorer")
        current_value = float(ce_total)

    if isinstance(cur_metadata, dict):
        sources.append("cur")
        if current_value is None and cur_metadata.get("total_spend") is not None:
            current_value = float(cur_metadata["total_spend"])

    if not sources:
        return None

    assessment_day = today or date.today()
    period = (
        str(cur_metadata.get("month"))
        if isinstance(cur_metadata, dict) and cur_metadata.get("month")
        else assessment_day.strftime("%Y-%m")
    )
    return assess_billing_integrity(
        period=period,
        current_value=current_value,
        sources=sources,
        retrieved_at=retrieved_at,
        source_agreement="not_available",
        today=assessment_day,
    ).to_dict()






