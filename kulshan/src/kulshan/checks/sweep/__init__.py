"""Sweep check pack, orphans, zombies, monthly waste, account hygiene scoring."""
from __future__ import annotations

import hashlib
from typing import List

__version__ = "0.1.0"
__all__ = ["__version__", "run_scan"]


def _fingerprint(pack: str, kind: str, resource_id: str) -> str:
    raw = f"{pack}|{kind}|{resource_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _severity_from_cost(monthly_cost: float, confidence: str) -> str:
    if monthly_cost >= 500:
        return "high"
    if monthly_cost >= 50:
        return "medium"
    if confidence == "high":
        return "medium"
    return "low"


def _orphan_to_finding(orphan: dict) -> dict:
    """Convert a sweep orphan dict to a canonical finding."""
    resource_id = orphan.get("resource_id", "unknown")
    resource_type = orphan.get("resource_type", "Unknown")
    monthly_cost = orphan.get("monthly_cost", 0) or 0
    confidence_str = orphan.get("confidence", "medium")
    region = orphan.get("region", "us-east-1")
    reason = orphan.get("reason", "Orphaned resource")

    # Map resource_type to kind
    kind_map = {
        "EBS Volume": "unused-ebs-volume",
        "Elastic IP": "unused-elastic-ip",
        "EBS Snapshot": "orphaned-snapshot",
        "AMI": "stale-ami",
        "Network Interface": "unused-eni",
        "NAT Gateway": "unused-nat-gateway",
        "Load Balancer": "idle-load-balancer",
        "RDS Instance": "stopped-instance",
        "Security Group": "unused-security-group",
        "S3 Bucket": "empty-bucket",
    }
    kind = kind_map.get(resource_type, "orphaned-resource")

    confidence_map = {"high": 0.95, "medium": 0.70, "low": 0.40}
    confidence = confidence_map.get(confidence_str, 0.70)

    fp = _fingerprint("sweep", kind, resource_id)
    return {
        "id": f"sweep-{kind}-{fp}",
        "pack": "sweep",
        "kind": kind,
        "title": f"{resource_type} '{resource_id}' is orphaned (${monthly_cost:.0f}/mo)",
        "severity": _severity_from_cost(monthly_cost, confidence_str),
        "confidence": confidence,
        "effort": "trivial",
        "risk": "low" if confidence >= 0.9 else "medium",
        "resource_id": resource_id,
        "resource_arn": orphan.get("resource_arn", ""),
        "region": region,
        "description": reason,
        "recommended_action": orphan.get("cleanup_action", ""),
        "estimated_monthly_impact": monthly_cost,
        "fingerprint": fp,
        "metadata": {
            "resource_type": resource_type,
            "age_days": orphan.get("age_days"),
            "tags": orphan.get("tags", {}),
        },
    }


def run_scan(session, regions: List[str], *, quick: bool = False, **kwargs) -> dict:
    """Run the orphan/waste scan and return a scored result dict."""
    from .scanner.compute import scan_compute
    from .scanner.network import scan_network
    from .scanner.storage import scan_storage
    from .scanner.database import scan_database
    from .scanner.monitoring import scan_monitoring
    from .scoring.engine import calculate_score
    from kulshan.parallel import parallel_scanners

    if quick:
        regions = regions[:3]

    all_orphans: list = []
    all_errors: list[str] = []

    # Run all scanners in parallel
    scanners = {
        "compute": scan_compute,
        "network": scan_network,
        "storage": scan_storage,
        "database": scan_database,
        "monitoring": scan_monitoring,
    }
    
    results, errors = parallel_scanners(scanners, session, regions)
    all_errors.extend(errors)
    
    for scanner_result in results.values():
        if isinstance(scanner_result, list):
            all_orphans.extend(scanner_result)

    scores = calculate_score(all_orphans) if all_orphans else {
        "overall_score": 100, "grade": "A+", "total_orphans": 0,
        "total_monthly_waste": 0, "breakdown": {},
    }

    # Convert orphans to canonical findings
    findings = [_orphan_to_finding(o) for o in all_orphans]

    sev: dict = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        s = f.get("severity", "info")
        if s in sev:
            sev[s] += 1

    return {
        "tool": "sweep",
        "findings": findings,
        "scores": {
            "overall_score": int(scores.get("overall_score", 100)),
            "grade": scores.get("grade", "A+"),
            "total_findings": len(findings),
            "severity_counts": {k: v for k, v in sev.items() if v > 0},
            "breakdown": scores.get("breakdown", {}),
            "monthly_waste": scores.get("total_monthly_waste", 0),
        },
        "errors": all_errors,
    }
