"""Cost check pack, multi-method anomaly detection, RI/SP coverage, idle-resource analysis, forecasts."""
from __future__ import annotations

from typing import Any, Optional

from kulshan.scoring_utils import grade as _grade

__version__ = "0.1.0"
__all__ = ["__version__", "run_scan"]

# Phase 6A hard cap on anomaly attribution (Phase 6 Q1: default 5).
# Each attribution makes up to 4 extra Cost Explorer calls @ $0.01 each, so the
# default ceiling adds ~$0.20 per scan beyond the baseline cost-pack spend.
MAX_ATTRIBUTIONS_PER_SCAN_DEFAULT = 5

# Phase 6B AWS Cost Anomaly Detection lookback. AWS's GetAnomalies API hard limit
# is 90 days; we cap at min(days, 90).
AWS_ANOMALY_LOOKBACK_MAX_DAYS = 90

# Severity vocabulary mapping: analyzers.anomaly emits "critical" / "warning" /
# "info"; Finding requires the canonical 5-level vocabulary.
_ANALYZER_SEVERITY_MAP = {
    "critical": "critical",
    "warning": "high",
    "info": "low",
}



def _empty(reason: str) -> dict:
    return {
        "tool": "cost",
        "scores": {"overall_score": 0, "grade": "N/A", "total_findings": 0, "severity_counts": {}},
        "findings": [],
        "errors": [reason],
        "skipped": True,
        "metadata": {
            "cost_anomaly_detection": {
                "status": "no_data",
                "anomaly_count": 0,
                "lookback_days": 0,
                "details": "skipped: " + reason,
            }
        },
    }


def _anomaly_to_finding(anomaly: dict, attribution: Optional[dict]) -> dict:
    """Convert a detect_anomalies row + optional attribution into a serialized Finding dict."""
    from kulshan.models import (
        Finding,
        Severity,
        SEVERITY_SCORE_IMPACT,
        compute_fingerprint,
        make_finding_id,
    )
    from .attribution import build_attribution_title, recommend_action

    attribution = attribution or {}
    service = anomaly.get("service")

    # Map analyzer severity → Finding severity vocabulary.
    raw_sev = str(anomaly.get("severity", "info")).lower()
    severity = _ANALYZER_SEVERITY_MAP.get(raw_sev, "low")

    # Estimated monthly impact ≈ |daily_delta| × 30 (proxy for run-rate).
    try:
        daily_delta = float(anomaly.get("latest_cost", 0)) - float(anomaly.get("avg_cost", 0))
    except (TypeError, ValueError):
        daily_delta = 0.0
    estimated_monthly_impact = round(abs(daily_delta) * 30.0, 2)

    # Confidence increases with method agreement (1 → 0.65, 4 → 1.00).
    try:
        num_methods = int(anomaly.get("num_methods", 1))
    except (TypeError, ValueError):
        num_methods = 1
    confidence = round(min(1.0, 0.50 + 0.15 * num_methods), 2)

    account = attribution.get("account")
    region = attribution.get("region")
    usage_type = attribution.get("usage_type")
    resource_id = attribution.get("resource_id")

    fingerprint = compute_fingerprint(
        pack="cost",
        kind="anomaly_statistical",
        account=account,
        service=service,
        usage_type=usage_type,
        period=anomaly.get("date"),
    )

    title = build_attribution_title(anomaly, attribution)
    rec = recommend_action(anomaly, attribution)

    try:
        pct = float(anomaly.get("pct_change", 0))
        z = float(anomaly.get("z_score", 0))
    except (TypeError, ValueError):
        pct, z = 0.0, 0.0

    description = (
        f"Cost is {pct:.0f}% above baseline (z-score {z:.1f}, "
        f"methods: {anomaly.get('methods', 'n/a')})."
    )

    # Parse detected_at from anomaly date
    detected_at = None
    anomaly_date = anomaly.get("date")
    if anomaly_date is not None:
        from datetime import datetime as _dt, timezone as _tz
        if isinstance(anomaly_date, _dt):
            detected_at = anomaly_date
        elif isinstance(anomaly_date, str) and anomaly_date:
            try:
                detected_at = _dt.fromisoformat(anomaly_date)
            except ValueError:
                pass

    finding = Finding(
        id=make_finding_id(pack="cost", kind="anomaly_statistical", fingerprint=fingerprint),
        pack="cost",
        kind="anomaly_statistical",
        fingerprint=fingerprint,
        title=title,
        severity=Severity(severity),
        score_impact=SEVERITY_SCORE_IMPACT[severity],
        estimated_monthly_impact=estimated_monthly_impact,
        confidence=confidence,
        effort="medium",
        risk="safe",
        account_id=account,
        region=region,
        service=service,
        resource_arn=resource_id,
        description=description,
        evidence={
            "date": str(anomaly.get("date", "")),
            "latest_cost": float(anomaly.get("latest_cost", 0) or 0),
            "avg_cost": float(anomaly.get("avg_cost", 0) or 0),
            "pct_change": pct,
            "z_score": z,
            "mad_score": float(anomaly.get("mad_score", 0) or 0),
            "methods": str(anomaly.get("methods", "")),
            "num_methods": num_methods,
            "attribution_notes": list(attribution.get("notes", [])),
            "attribution_api_calls": int(attribution.get("api_calls", 0)),
            "usage_type": usage_type,
        },
        recommended_action=rec,
        compliance_frameworks=[],
        detected_at=detected_at,
        schema_version="2.0",
    )
    return finding.to_dict()


def _build_findings(
    fetcher: Any,
    anomalies_df: Any,
    *,
    max_attributions: int,
    days: int,
    errors: list,
) -> list:
    """Build a serialized findings list from the anomalies DataFrame.

    Top ``max_attributions`` rows by score get full attribution drill-down; the
    remainder become Findings without drill-down detail. Failures during a single
    attribution are captured as errors but do not abort findings construction.
    """
    from .attribution import attribute_anomaly

    if anomalies_df is None or anomalies_df.empty:
        return []

    findings: list = []
    rows = anomalies_df.to_dict("records")
    attribute_window_days = min(days, 14)

    for idx, row in enumerate(rows):
        attribution: Optional[dict]
        if idx < max_attributions:
            try:
                attribution = attribute_anomaly(
                    fetcher, row, days=attribute_window_days
                )
            except Exception as e:  # noqa: BLE001, graceful degradation
                errors.append(
                    f"Attribution for {row.get('service', '?')}: {e}"
                )
                attribution = None
        else:
            attribution = None

        try:
            findings.append(_anomaly_to_finding(row, attribution))
        except Exception as e:  # noqa: BLE001
            errors.append(
                f"Finding construction for {row.get('service', '?')}: {e}"
            )
    return findings


def _build_aws_native_findings(
    fetcher: Any, *, lookback_days: int, errors: list
) -> tuple[list, dict]:
    """Call AWS Cost Anomaly Detection (``ce:GetAnomalies``) and convert results to Findings.

    Returns ``(findings_list, metadata_dict)``. Degrades gracefully on any error
    or empty response: ``findings_list`` is empty and ``metadata.status`` reflects
    the outcome (``"ok"`` / ``"no_data"`` / ``"error"``).

    The fetcher already wraps the underlying API call in ``try/except`` and
    returns an empty DataFrame on AccessDenied. We cannot today distinguish
    "permission denied" from "no anomalies in window" without widening the
    fetcher's contract; the ``"permission_denied"`` status is reserved for a
    future enhancement.
    """
    from .aws_native import aws_anomaly_to_finding

    metadata: dict = {
        "status": "ok",
        "anomaly_count": 0,
        "lookback_days": lookback_days,
        "details": "",
    }

    try:
        df = fetcher.get_anomalies_from_service(days=lookback_days)
    except Exception as e:  # noqa: BLE001
        errors.append(f"AWS Cost Anomaly Detection: {e}")
        metadata["status"] = "error"
        metadata["details"] = str(e)
        return [], metadata

    if df is None or df.empty:
        metadata["status"] = "no_data"
        metadata["details"] = (
            "no anomalies returned (no monitor configured, no anomalies in window, "
            "or insufficient permissions)"
        )
        return [], metadata

    raw_count = len(df)
    findings: list = []
    suppressed = 0
    for row in df.to_dict("records"):
        try:
            finding = aws_anomaly_to_finding(row)
        except Exception as e:  # noqa: BLE001
            errors.append(f"AWS anomaly conversion: {e}")
            continue
        if finding is None:
            suppressed += 1
            continue
        findings.append(finding)

    metadata["anomaly_count"] = len(findings)
    if findings:
        metadata["status"] = "ok"
        if suppressed:
            metadata["details"] = (
                f"{suppressed} of {raw_count} anomalies suppressed (feedback='NO')"
            )
    else:
        metadata["status"] = "no_data"
        if suppressed == raw_count and raw_count > 0:
            metadata["details"] = (
                f"all {raw_count} returned anomalies suppressed (feedback='NO')"
            )

    return findings, metadata


def run_scan(session=None, regions=None, *, quick: bool = False, **kwargs) -> dict:
    """Run cost analysis and return a scored result dict.

    The Cost Explorer API is global, so ``session`` and ``regions`` are accepted
    but unused. ``profile`` (str), ``days`` (int, default 30), and
    ``attribute_anomalies`` (int, default 5; Phase 6 Q1 cap) are read from
    ``kwargs`` so the orchestrator can call every check pack uniformly.
    """
    profile: Optional[str] = kwargs.get("profile")
    days: int = int(kwargs.get("days", 30))
    max_attributions: int = int(
        kwargs.get("attribute_anomalies", MAX_ATTRIBUTIONS_PER_SCAN_DEFAULT)
    )

    from .cost_fetcher import CostFetcher
    from .analyzers.anomaly import detect_anomalies
    from .analyzers.efficiency import compute_efficiency_score
    from .analyzers.waste import detect_idle_services
    from .analyzers.advanced import (
        compute_purchase_type_mix,
        compute_cost_velocity,
        compute_hhi_concentration,
        compute_executive_scorecard,
    )
    from .aws_native import apply_overlap_boost

    errors: list[str] = []

    try:
        fetcher = CostFetcher(profile=profile)
    except Exception as e:
        return _empty(f"CostFetcher init failed: {e}")

    try:
        service_data = fetcher.get_cost_by_dimension("service", days=days)
    except Exception as e:
        return _empty(f"Cost data fetch failed: {e}")

    if service_data.empty:
        return _empty("No cost data available")

    total_spend = float(service_data["cost"].sum())

    import pandas as pd
    anomalies = pd.DataFrame()
    try:
        anomalies = detect_anomalies(service_data, "service")
    except Exception as e:
        errors.append(f"Anomaly detection: {e}")

    anomaly_count = 0 if anomalies.empty else len(anomalies)

    # Phase 6A: statistical findings with attribution.
    statistical_findings = _build_findings(
        fetcher,
        anomalies,
        max_attributions=max_attributions,
        days=days,
        errors=errors,
    )

    # Phase 6B: AWS-native findings.
    aws_lookback = min(days, AWS_ANOMALY_LOOKBACK_MAX_DAYS)
    aws_findings, aws_metadata = _build_aws_native_findings(
        fetcher, lookback_days=aws_lookback, errors=errors,
    )

    # Phase 6B: classify overlap and boost confidence on matched findings.
    classification = apply_overlap_boost(statistical_findings, aws_findings)
    aws_metadata["overlap"] = {
        "both_count": len(classification["both"]),
        "Kulshan_only_count": len(classification["Kulshan_only"]),
        "aws_only_count": len(classification["aws_only"]),
    }

    findings = statistical_findings + aws_findings

    score_val = 50
    breakdown: dict = {}
    try:
        ri_cov = fetcher.get_reservation_coverage(days=min(days, 90))
        sp_util = fetcher.get_savings_plans_utilization(days=min(days, 90))
        ri_util = fetcher.get_reservation_utilization(days=min(days, 90))
        idle = detect_idle_services(service_data, "service")
        score_data = compute_efficiency_score(
            ri_cov, sp_util, ri_util, idle, anomalies, total_spend
        )
        score_val = int(score_data.get("total_score", 50))
        breakdown = score_data.get("breakdown", {})
    except Exception as e:
        errors.append(f"Efficiency scoring: {e}")

    severity_counts: dict = {}
    if not anomalies.empty and "severity" in anomalies.columns:
        raw_counts = anomalies["severity"].value_counts().to_dict()
        # Map analyzer vocabulary → canonical vocabulary
        for raw_sev, count in raw_counts.items():
            canonical_sev = _ANALYZER_SEVERITY_MAP.get(str(raw_sev).lower(), "low")
            severity_counts[canonical_sev] = severity_counts.get(canonical_sev, 0) + int(count)

    # ── Advanced Analytics (Phase 7) ────────────────────────────────────
    # These are best-effort: failures are captured as errors, not fatal.

    # 1. Purchase Type Mix
    purchase_mix: dict = {}
    try:
        purchase_mix = compute_purchase_type_mix(fetcher, days=days)
    except Exception as e:
        errors.append(f"Purchase type mix: {e}")

    # 2. Cost Velocity
    velocity: dict = {}
    try:
        velocity = compute_cost_velocity(service_data, days=days)
    except Exception as e:
        errors.append(f"Cost velocity: {e}")

    # 3. HHI Concentration
    hhi: dict = {}
    try:
        hhi = compute_hhi_concentration(service_data)
    except Exception as e:
        errors.append(f"HHI concentration: {e}")

    # 5. Executive Scorecard
    scorecard: dict = {}
    try:
        scorecard = compute_executive_scorecard(
            efficiency_breakdown=breakdown,
            purchase_mix=purchase_mix,
            velocity=velocity,
            hhi=hhi,
            total_spend=total_spend,
            total_findings=len(findings),
        )
    except Exception as e:
        errors.append(f"Executive scorecard: {e}")

    return {
        "tool": "cost",
        "scores": {
            "overall_score": score_val,
            "grade": _grade(score_val),
            "total_findings": len(findings),
            "severity_counts": severity_counts,
            "breakdown": breakdown,
            "total_spend": total_spend,
        },
        "findings": findings,
        "errors": errors,
        "metadata": {
            "cost_anomaly_detection": aws_metadata,
            "purchase_mix": purchase_mix,
            "cost_velocity": velocity,
            "hhi_concentration": hhi,
            "executive_scorecard": scorecard,
        },
    }
