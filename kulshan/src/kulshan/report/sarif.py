"""SARIF 2.1.0 export for Kulshan findings.

Maps Kulshan findings to the Static Analysis Results Interchange Format
for integration with GitHub Code Scanning, VS Code SARIF Viewer, and CI/CD
security dashboards.

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from kulshan.__version__ import __version__


# Kulshan severity â†’ SARIF level mapping
SEVERITY_TO_SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}

# Kulshan pack â†’ SARIF tool component
PACK_DESCRIPTIONS = {
    "cost": "AWS cost analysis and anomaly detection",
    "security": "IAM, network, encryption, and logging posture checks",
    "sweep": "Orphaned and unused resource detection",
    "dr": "Disaster recovery and backup posture",
    "age": "Lifecycle audit: EOL runtimes, expiring certificates",
    "drift": "CloudFormation drift and IaC coverage",
    "tag": "Tag compliance and cost attribution",
    "pulse": "Observability, alarms, and logging coverage",
    "limit": "Service quota headroom and scaling readiness",
    "topo": "VPC topology, CIDR overlaps, and route integrity",
}


def findings_to_sarif(
    findings: List[dict],
    account_id: str = "unknown",
    regions: List[str] | None = None,
    version: str | None = None,
    coverage: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Convert a list of canonical Kulshan findings to a SARIF 2.1.0 document.

    Args:
        findings: List of finding dicts (canonical shape from orchestrator).
        account_id: AWS account ID for context.
        regions: List of scanned regions.
        version: Kulshan version string.

    Returns:
        A dict representing a valid SARIF 2.1.0 document.
    """
    tool_version = version or __version__
    regions = regions or []

    # Collect unique rules from findings
    rules_map: Dict[str, Dict[str, Any]] = {}
    results: List[Dict[str, Any]] = []

    for finding in findings:
        rule_id = f"{finding.get('pack', 'unknown')}/{finding.get('kind', 'unknown')}"
        severity = finding.get("severity", "info")
        sarif_level = SEVERITY_TO_SARIF_LEVEL.get(severity, "note")

        # Build rule if not seen before
        if rule_id not in rules_map:
            pack = finding.get("pack", "unknown")
            rules_map[rule_id] = {
                "id": rule_id,
                "name": finding.get("kind", "unknown"),
                "shortDescription": {"text": finding.get("title", rule_id)},
                "fullDescription": {"text": finding.get("description", "")},
                "defaultConfiguration": {"level": sarif_level},
                "properties": {
                    "pack": pack,
                    "tags": [pack, "aws", "cloud-security"],
                },
            }
            # Add help text from remediation if available
            remediation = finding.get("remediation_snippet") or finding.get("recommended_action", "")
            if remediation:
                rules_map[rule_id]["help"] = {
                    "text": remediation,
                    "markdown": f"```bash\n{remediation}\n```",
                }

        # Build result
        result: Dict[str, Any] = {
            "ruleId": rule_id,
            "level": sarif_level,
            "message": {"text": finding.get("title", "Finding")},
            "fingerprints": {},
            "properties": {
                "severity": severity,
                "confidence": finding.get("confidence", 0.0),
                "effort": finding.get("effort", "medium"),
                "risk": finding.get("risk", "safe"),
                "pack": finding.get("pack", ""),
            },
        }

        # Add fingerprint for deduplication
        fingerprint = finding.get("fingerprint", finding.get("id", ""))
        if fingerprint:
            result["fingerprints"]["Kulshan/v1"] = fingerprint

        # Add location (resource ARN as logical location)
        resource_arn = finding.get("resource_arn", "")
        resource_id = finding.get("resource_id", finding.get("id", ""))
        region = finding.get("region", "")

        if resource_arn or resource_id:
            result["locations"] = [{
                "logicalLocations": [{
                    "name": resource_id or resource_arn,
                    "fullyQualifiedName": resource_arn or resource_id,
                    "kind": "resource",
                    "properties": {
                        "region": region,
                        "account_id": account_id,
                    },
                }],
            }]

        # Add monthly impact as property
        impact = finding.get("estimated_monthly_impact") or finding.get("savings_monthly")
        if impact:
            result["properties"]["estimatedMonthlySavingsUSD"] = float(impact)

        results.append(result)

    # Assemble SARIF document
    sarif_doc = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "Kulshan",
                    "fullName": "kulshan",
                    "version": tool_version,
                    "semanticVersion": tool_version,
                    "informationUri": "https://missionfinops.com",
                    "rules": list(rules_map.values()),
                    "properties": {
                        "account_id": account_id,
                        "regions": regions,
                        "coverage": coverage or {},
                    },
                },
            },
            "results": results,
            "invocations": [{
                "executionSuccessful": True,
                "properties": {
                    "account_id": account_id,
                    "regions": regions,
                        "coverage": coverage or {},
                },
            }],
        }],
    }

    return sarif_doc


def to_sarif_json(findings: List[dict], **kwargs) -> str:
    """Convert findings to a SARIF JSON string."""
    doc = findings_to_sarif(findings, **kwargs)
    return json.dumps(doc, indent=2, default=str)

