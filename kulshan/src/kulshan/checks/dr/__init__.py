"""DR check pack, backup coverage, multi-AZ deployment, cross-region replication, SPOFs, RTO/RPO gaps."""
from __future__ import annotations

import hashlib
from typing import List

__version__ = "0.1.0"
__all__ = ["__version__", "run_scan"]


def _fingerprint(pack: str, kind: str, resource_id: str) -> str:
    raw = f"{pack}|{kind}|{resource_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _extract_findings(scan_results: dict) -> list:
    """Extract canonical findings from DR scan results."""
    findings = []

    # Backup findings
    for item in scan_results.get("backup", {}).get("unprotected", []):
        resource_id = item.get("resource_id", "unknown")
        fp = _fingerprint("dr", "no-backup", resource_id)
        findings.append({
            "id": f"dr-no-backup-{fp}",
            "pack": "dr",
            "kind": "no-backup",
            "title": f"{item.get('resource_type', 'Resource')} '{resource_id}' has no backup",
            "severity": "high",
            "confidence": 0.95,
            "effort": "medium",
            "risk": "safe",
            "resource_id": resource_id,
            "resource_arn": item.get("resource_arn", ""),
            "region": item.get("region", "us-east-1"),
            "description": f"No AWS Backup plan protects this {item.get('resource_type', 'resource')}.",
            "recommended_action": "Configure an AWS Backup plan for this resource.",
            "estimated_monthly_impact": 0,
            "fingerprint": fp,
        })

    # Single-AZ database findings
    for item in scan_results.get("database", {}).get("single_az", []):
        resource_id = item.get("resource_id", "unknown")
        fp = _fingerprint("dr", "single-az", resource_id)
        findings.append({
            "id": f"dr-single-az-{fp}",
            "pack": "dr",
            "kind": "single-az",
            "title": f"RDS '{resource_id}' is single-AZ (no failover)",
            "severity": "high",
            "confidence": 0.99,
            "effort": "medium",
            "risk": "medium",
            "resource_id": resource_id,
            "resource_arn": item.get("resource_arn", ""),
            "region": item.get("region", "us-east-1"),
            "description": "This database has no Multi-AZ failover. An AZ outage causes downtime.",
            "recommended_action": f"aws rds modify-db-instance --db-instance-identifier {resource_id} --multi-az --apply-immediately",
            "estimated_monthly_impact": 0,
            "fingerprint": fp,
        })

    # SPOF findings
    for item in scan_results.get("spof", {}).get("issues", []):
        resource_id = item.get("resource_id", "unknown")
        kind = "single-nat" if "NAT" in item.get("resource_type", "") else "spof"
        fp = _fingerprint("dr", kind, resource_id)
        findings.append({
            "id": f"dr-{kind}-{fp}",
            "pack": "dr",
            "kind": kind,
            "title": f"Single point of failure: {item.get('resource_type', '')} '{resource_id}'",
            "severity": "medium",
            "confidence": 0.85,
            "effort": "high",
            "risk": "medium",
            "resource_id": resource_id,
            "resource_arn": item.get("resource_arn", ""),
            "region": item.get("region", "us-east-1"),
            "description": item.get("reason", "This resource has no redundancy."),
            "recommended_action": "Add redundancy or failover capability.",
            "estimated_monthly_impact": 0,
            "fingerprint": fp,
        })

    # DNS / health check findings
    for item in scan_results.get("dns", {}).get("issues", []):
        resource_id = item.get("resource_id", "unknown")
        fp = _fingerprint("dr", "no-health-check", resource_id)
        findings.append({
            "id": f"dr-no-health-check-{fp}",
            "pack": "dr",
            "kind": "no-health-check",
            "title": f"DNS record '{resource_id}' has no health check",
            "severity": "medium",
            "confidence": 0.80,
            "effort": "low",
            "risk": "safe",
            "resource_id": resource_id,
            "resource_arn": "",
            "region": "global",
            "description": "Route53 record without health check cannot failover automatically.",
            "recommended_action": "Add a Route53 health check and failover routing.",
            "estimated_monthly_impact": 0,
            "fingerprint": fp,
        })

    return findings


def run_scan(session, regions: List[str], *, quick: bool = False, **kwargs) -> dict:
    """Run the DR readiness scan and return a scored result dict."""
    from .scanner.backup import scan_backup
    from .scanner.compute import scan_compute
    from .scanner.database import scan_database
    from .scanner.storage import scan_storage
    from .scanner.dns import scan_dns
    from .scanner.spof import scan_spof
    from .scoring.engine import calculate_score
    from kulshan.parallel import parallel_scanners

    if quick:
        regions = regions[:3]

    scan_results: dict = {}
    all_errors: list[str] = []

    # Run all scanners in parallel
    scanners = {
        "backup": scan_backup,
        "compute": scan_compute,
        "database": scan_database,
        "storage": scan_storage,
        "dns": scan_dns,
        "spof": scan_spof,
    }
    
    scan_results, errors = parallel_scanners(scanners, session, regions)
    all_errors.extend(errors)

    scores = calculate_score(scan_results)
    findings = _extract_findings(scan_results)

    sev: dict = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        s = f.get("severity", "info")
        if s in sev:
            sev[s] += 1

    return {
        "tool": "dr",
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
