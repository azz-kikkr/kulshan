"""Security check pack, 50 checks, attack-path discovery, breach-cost estimate, CIS/NIST/SOC2 mapping."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

__version__ = "0.1.0"
__all__ = ["__version__", "run_scan"]


# ---------------------------------------------------------------------------
# Internal mapping tables for canonical finding conversion
# ---------------------------------------------------------------------------

# Scanner category → canonical kind
_CATEGORY_TO_KIND: Dict[str, str] = {
    "Identity & Access": "iam",
    "Network Exposure": "network",
    "Data Protection": "data",
    "Compute Security": "compute",
    "Logging & Monitoring": "logging",
    "Encryption & Secrets": "encryption",
}

# Severity-based confidence: security checks are deterministic read-only
# assertions. CRITICAL/HIGH findings are high-confidence because they are
# direct observations (e.g. root has no MFA). MEDIUM/LOW are heuristic-
# based (e.g. unused user after 90 days). INFO is informational.
_SEVERITY_CONFIDENCE: Dict[str, float] = {
    "critical": 1.0,
    "high": 0.9,
    "medium": 0.75,
    "low": 0.6,
    "info": 0.5,
}

# Remediation complexity classification.  Maps check_id prefix to effort.
# IAM changes are typically low-effort console/CLI changes; network changes
# may require coordination; encryption and compute changes can require
# maintenance windows.
_EFFORT_BY_CHECK_PREFIX: Dict[str, str] = {
    "IAM-001": "low",        # Enable root MFA (console action)
    "IAM-002": "low",        # Delete root access keys
    "IAM-003": "low",        # Enable user MFA
    "IAM-004": "medium",     # Rotate access keys (app changes needed)
    "IAM-005": "medium",     # Scope down admin policies
    "IAM-006": "high",       # Privilege escalation remediation
    "IAM-007": "medium",     # Add ExternalId to trust policy
    "IAM-008": "medium",     # Scope wildcard resources
    "IAM-009": "low",        # Disable/delete unused users
    "IAM-010": "trivial",    # Set password policy
    "IAM-011": "trivial",    # Update password length
    "IAM-012": "medium",     # Scope down role permissions
    "IAM-013": "trivial",    # Enable Access Analyzer
    "IAM-014": "medium",     # Review Access Analyzer findings
    "NET-001": "medium",     # Restrict open security groups
    "NET-002": "low",        # Restrict management port access
    "NET-003": "low",        # Remove public DB port access
    "NET-004": "medium",     # Network security fix
    "NET-005": "low",        # Enable VPC flow logs
    "NET-006": "medium",     # Disable auto-assign public IP
    "DATA-001": "trivial",   # Enable S3 block public access
    "DATA-002": "low",       # Fix public S3 bucket
    "DATA-003": "trivial",   # Enable S3 default encryption
    "DATA-004": "medium",    # Data protection fix
    "DATA-005": "medium",    # Disable public RDS access (reboot)
    "DATA-006": "high",      # Enable RDS encryption (recreate)
    "DATA-007": "low",       # Fix public snapshots
    "DATA-008": "medium",    # Data protection fix
    "DATA-009": "low",       # Fix public RDS snapshots
    "COMP-001": "low",       # Enforce IMDSv2
    "COMP-002": "medium",    # Remove public IPs from EC2
    "COMP-003": "medium",    # Compute security fix
    "COMP-004": "medium",    # Restrict Lambda access
    "COMP-005": "medium",    # Compute security fix
    "COMP-006": "medium",    # Restrict EKS public API
    "LOG-001": "low",        # Enable CloudTrail
    "LOG-002": "trivial",    # Enable log file validation
    "LOG-003": "low",        # Logging fix
    "LOG-004": "low",        # Logging fix
    "LOG-005": "low",        # Enable GuardDuty
    "LOG-006": "low",        # Logging fix
    "LOG-007": "low",        # Enable AWS Config
    "LOG-008": "low",        # Logging fix
    "LOG-009": "low",        # Enable Access Analyzer
    "ENC-001": "trivial",    # Enable KMS key rotation
    "ENC-002": "medium",     # Encryption fix
    "ENC-003": "medium",     # Encryption fix
}

# Default effort when check_id is not in the map
_DEFAULT_EFFORT = "medium"


def _get_effort(check_id: str) -> str:
    """Derive canonical effort from check_id remediation complexity."""
    return _EFFORT_BY_CHECK_PREFIX.get(check_id, _DEFAULT_EFFORT)


def _convert_to_canonical(internal_finding) -> dict:
    """Convert an internal scanner Finding to a canonical Finding dict.

    Parameters
    ----------
    internal_finding
        Instance of ``scanner.base.Finding`` with fields: check_id, title,
        severity (Severity enum), category, resource_type, resource_id,
        resource_arn, region, description, remediation, details.

    Returns
    -------
    dict
        A canonical Finding dict matching ``Kulshan.models.Finding.to_dict()``
        shape with ``schema_version="2.0"``.
    """
    from kulshan.models import (
        Finding,
        Severity as CanonicalSeverity,
        SEVERITY_SCORE_IMPACT,
        compute_fingerprint,
        make_finding_id,
    )
    from .scoring.compliance import get_compliance_tags

    # --- Severity mapping (uppercase enum → lowercase string) ---------------
    severity_str = internal_finding.severity.value.lower()

    # --- Kind (scanner category → canonical kind) ---------------------------
    kind = _CATEGORY_TO_KIND.get(internal_finding.category, "unknown")

    # --- Confidence (based on severity level) -------------------------------
    confidence = _SEVERITY_CONFIDENCE.get(severity_str, 0.5)

    # --- Effort (from remediation complexity classification) -----------------
    effort = _get_effort(internal_finding.check_id)

    # --- Resource location --------------------------------------------------
    resource_arn = internal_finding.resource_arn or internal_finding.resource_id
    service = internal_finding.resource_type

    # --- Compliance frameworks ----------------------------------------------
    compliance_tags = get_compliance_tags(internal_finding.check_id)
    compliance_frameworks = [
        f"{framework} {control_id}" for framework, control_id, _ in compliance_tags
    ] if compliance_tags else []

    # --- Fingerprint & ID ---------------------------------------------------
    now = datetime.now(timezone.utc)
    fingerprint = compute_fingerprint(
        pack="security",
        kind=kind,
        account=None,
        service=service,
        usage_type=internal_finding.resource_id,
        period=now,
    )
    finding_id = make_finding_id(pack="security", kind=kind, fingerprint=fingerprint)

    # --- score_impact -------------------------------------------------------
    score_impact = SEVERITY_SCORE_IMPACT.get(severity_str, 0)

    # --- Construct canonical Finding and return as dict ----------------------
    canonical = Finding(
        id=finding_id,
        pack="security",
        kind=kind,
        fingerprint=fingerprint,
        title=internal_finding.title,
        severity=CanonicalSeverity(severity_str),
        score_impact=score_impact,
        estimated_monthly_impact=0.0,
        confidence=confidence,
        effort=effort,
        risk="safe",
        account_id=None,
        region=internal_finding.region if internal_finding.region != "global" else None,
        resource_arn=resource_arn,
        resource_type=internal_finding.resource_type,
        service=service,
        description=internal_finding.description,
        evidence=internal_finding.details if isinstance(internal_finding.details, dict) else {},
        recommended_action=internal_finding.remediation,
        compliance_frameworks=compliance_frameworks,
        detected_at=now,
        schema_version="2.0",
    )

    return canonical.to_dict()


def run_scan(session, regions: List[str], *, quick: bool = False, deep: bool = False, **kwargs) -> dict:
    """Run the security posture scan and return a scored result dict."""
    from .scanner.iam import IAMScanner
    from .scanner.network import NetworkScanner
    from .scanner.data import DataScanner
    from .scanner.compute import ComputeScanner
    from .scanner.logging_monitor import LoggingScanner
    from .scanner.encryption import EncryptionScanner
    from .scoring.engine import calculate_scores
    from kulshan.parallel import parallel_map

    if quick:
        regions = regions[:3]

    all_findings: list = []
    all_errors: list[str] = []

    scanner_classes = [
        IAMScanner, NetworkScanner, DataScanner,
        ComputeScanner, LoggingScanner, EncryptionScanner,
    ]

    def run_scanner(cls):
        """Run a single scanner and return (findings, errors)."""
        try:
            scanner = cls(session, regions)
            scanner.deep = deep
            result = scanner.scan()
            return result.findings, result.errors
        except Exception as e:
            return [], [f"{cls.__name__}: {e}"]

    # Run all scanners in parallel
    results, map_errors = parallel_map(run_scanner, scanner_classes, desc="scanner")
    all_errors.extend(map_errors)
    
    for findings, errors in results:
        all_findings.extend(findings)
        all_errors.extend(errors)

    scores = calculate_scores(all_findings) if all_findings else {
        "overall_score": 100, "overall_grade": "A+",
        "total_findings": 0, "severity_counts": {},
    }

    # Convert internal findings to canonical Finding dicts
    canonical_findings = [_convert_to_canonical(f) for f in all_findings]

    return {
        "tool": "security",
        "scores": {
            "overall_score": int(scores.get("overall_score", 0)),
            "grade": scores.get("overall_grade", "N/A"),
            "total_findings": scores.get("total_findings", len(all_findings)),
            "severity_counts": scores.get("severity_counts", {}),
            "breakdown": scores.get("category_scores", {}),
        },
        "findings": canonical_findings,
        "errors": all_errors,
    }
