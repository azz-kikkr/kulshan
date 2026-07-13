"""Pulse check pack, logging coverage, alarm effectiveness, blind-spot heatmap."""
from __future__ import annotations

import hashlib
from typing import List

__version__ = "0.1.0"
__all__ = ["__version__", "run_scan"]


def _fingerprint(pack: str, kind: str, resource_id: str) -> str:
    raw = f"{pack}|{kind}|{resource_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _extract_findings(scan_results: dict) -> list:
    """Extract canonical findings from observability scan results."""
    findings = []

    # Missing alarms
    for item in scan_results.get("alarms", {}).get("unmonitored", []):
        resource_id = item.get("resource_id", "unknown")
        fp = _fingerprint("pulse", "no-alarm", resource_id)
        findings.append({
            "id": f"pulse-no-alarm-{fp}",
            "pack": "pulse",
            "kind": "no-alarm",
            "title": f"{item.get('resource_type', 'Resource')} '{resource_id}' has no CloudWatch alarm",
            "severity": "medium",
            "confidence": 0.90,
            "effort": "low",
            "risk": "safe",
            "resource_id": resource_id,
            "resource_arn": item.get("resource_arn", ""),
            "region": item.get("region", "us-east-1"),
            "description": f"No CloudWatch alarm monitors this {item.get('resource_type', 'resource')}. Failures will go undetected.",
            "recommended_action": "Create a CloudWatch alarm for key metrics (CPU, errors, latency).",
            "estimated_monthly_impact": 0,
            "fingerprint": fp,
        })

    # Missing logging
    for item in scan_results.get("logging", {}).get("gaps", []):
        resource_id = item.get("resource_id", "unknown")
        fp = _fingerprint("pulse", "no-logging", resource_id)
        findings.append({
            "id": f"pulse-no-logging-{fp}",
            "pack": "pulse",
            "kind": "no-logging",
            "title": f"{item.get('resource_type', 'Resource')} '{resource_id}' has no logging enabled",
            "severity": "medium",
            "confidence": 0.85,
            "effort": "low",
            "risk": "safe",
            "resource_id": resource_id,
            "resource_arn": item.get("resource_arn", ""),
            "region": item.get("region", "us-east-1"),
            "description": "Logging is not enabled. Debugging and forensics will be impossible after an incident.",
            "recommended_action": "Enable access logging or audit logging for this resource.",
            "estimated_monthly_impact": 0,
            "fingerprint": fp,
        })

    # Blind spots (services with zero observability)
    for item in scan_results.get("tracing", {}).get("blind_spots", []):
        resource_id = item.get("service", "unknown")
        fp = _fingerprint("pulse", "blind-spot", resource_id)
        findings.append({
            "id": f"pulse-blind-spot-{fp}",
            "pack": "pulse",
            "kind": "blind-spot",
            "title": f"Service '{resource_id}' has no tracing or metrics",
            "severity": "low",
            "confidence": 0.70,
            "effort": "medium",
            "risk": "safe",
            "resource_id": resource_id,
            "resource_arn": "",
            "region": item.get("region", "us-east-1"),
            "description": "No X-Ray traces or custom metrics detected for this service.",
            "recommended_action": "Enable X-Ray tracing or add custom CloudWatch metrics.",
            "estimated_monthly_impact": 0,
            "fingerprint": fp,
        })

    return findings


def run_scan(session, regions: List[str], *, quick: bool = False, **kwargs) -> dict:
    """Run the observability audit and return a scored result dict."""
    from .scanner.logging import scan_logging
    from .scanner.alarms import scan_alarms
    from .scanner.tracing import scan_tracing
    from .scoring.engine import calculate_score
    from kulshan.parallel import parallel_scanners

    if quick:
        regions = regions[:3]

    scan_results: dict = {}
    all_errors: list[str] = []

    # Run all scanners in parallel
    scanners = {
        "logging": scan_logging,
        "alarms": scan_alarms,
        "tracing": scan_tracing,
    }
    
    scan_results, errors = parallel_scanners(scanners, session, regions)
    all_errors.extend(errors)

    scores = calculate_score(scan_results) if scan_results else {
        "overall_score": 100, "grade": "A+", "total_findings": 0,
        "severity_counts": {}, "breakdown": {},
    }

    findings = _extract_findings(scan_results)

    sev: dict = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        s = f.get("severity", "info")
        if s in sev:
            sev[s] += 1

    return {
        "tool": "pulse",
        "findings": findings,
        "scores": {
            "overall_score": int(scores.get("overall_score", 0)),
            "grade": scores.get("grade", "N/A"),
            "total_findings": len(findings),
            "severity_counts": {k: v for k, v in sev.items() if v > 0},
            "breakdown": scores.get("breakdown", {}),
        },
        "errors": all_errors,
    }
