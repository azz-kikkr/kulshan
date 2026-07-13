"""Post-processing for findings: deduplication, grouping, cost estimation, severity tuning."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import re


# Remediation cost estimates (USD/month) for common AWS services
# Based on typical configurations, actual costs vary
REMEDIATION_COSTS = {
    # Logging & Monitoring
    "cloudtrail": {
        "description": "CloudTrail trail (multi-region, S3 storage)",
        "monthly_cost": 2.50,  # ~$2-5 for moderate API activity
        "one_time_cost": 0,
    },
    "vpc_flow_logs": {
        "description": "VPC Flow Logs to CloudWatch (1GB/day estimate)",
        "monthly_cost": 1.50,  # ~$0.50/GB ingestion + storage
        "one_time_cost": 0,
    },
    "config_recorder": {
        "description": "AWS Config recorder + rules",
        "monthly_cost": 3.00,  # $0.003/item + rule evaluations
        "one_time_cost": 0,
    },
    "access_analyzer": {
        "description": "IAM Access Analyzer",
        "monthly_cost": 0,  # Free for standard analyzer
        "one_time_cost": 0,
    },
    "guardduty": {
        "description": "Amazon GuardDuty",
        "monthly_cost": 4.00,  # Varies by log volume
        "one_time_cost": 0,
    },
    # Security
    "mfa_device": {
        "description": "Virtual MFA device",
        "monthly_cost": 0,
        "one_time_cost": 0,
    },
    "password_policy": {
        "description": "IAM password policy",
        "monthly_cost": 0,
        "one_time_cost": 0,
    },
    # Encryption
    "kms_key": {
        "description": "KMS customer managed key",
        "monthly_cost": 1.00,  # $1/key/month + $0.03/10K requests
        "one_time_cost": 0,
    },
    "s3_encryption": {
        "description": "S3 default encryption (SSE-S3)",
        "monthly_cost": 0,
        "one_time_cost": 0,
    },
    "s3_versioning": {
        "description": "S3 versioning (storage overhead)",
        "monthly_cost": 0.50,  # Depends on churn rate
        "one_time_cost": 0,
    },
    # Network
    "vpc_endpoint_gateway": {
        "description": "VPC Gateway Endpoint (S3/DynamoDB)",
        "monthly_cost": 0,  # Free for gateway endpoints
        "one_time_cost": 0,
    },
    "vpc_endpoint_interface": {
        "description": "VPC Interface Endpoint",
        "monthly_cost": 7.50,  # ~$0.01/hr + data processing
        "one_time_cost": 0,
    },
    # Backup & DR
    "rds_multi_az": {
        "description": "RDS Multi-AZ deployment",
        "monthly_cost": 50.00,  # ~2x single-AZ cost
        "one_time_cost": 0,
    },
    "backup_plan": {
        "description": "AWS Backup plan",
        "monthly_cost": 0.05,  # Per GB-month warm storage
        "one_time_cost": 0,
    },
}

# Mapping from finding kinds to remediation cost keys
KIND_TO_COST_KEY = {
    # Logging
    "logging": "cloudtrail",
    "no-cloudtrail": "cloudtrail",
    "no-flow-logs": "vpc_flow_logs",
    "flow-logs": "vpc_flow_logs",
    "no-config": "config_recorder",
    "no-access-analyzer": "access_analyzer",
    "no-guardduty": "guardduty",
    # Security
    "iam": "mfa_device",
    "mfa-missing": "mfa_device",
    "password-policy": "password_policy",
    # Encryption
    "encryption": "kms_key",
    "s3-encryption": "s3_encryption",
    "s3-versioning": "s3_versioning",
    # Network
    "network": None,  # Varies
    "no-vpc-endpoints": "vpc_endpoint_gateway",
    # DR
    "single-az": "rds_multi_az",
    "no-backup": "backup_plan",
}


def estimate_remediation_cost(finding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Estimate monthly cost to remediate a finding.
    
    Returns a dict with:
        - monthly_cost: Estimated monthly USD
        - one_time_cost: One-time implementation cost
        - description: What the cost covers
        - confidence: How confident the estimate is (high/medium/low)
    """
    kind = finding.get("kind", "")
    title = finding.get("title", "").lower()
    
    # Try to match by kind first
    cost_key = KIND_TO_COST_KEY.get(kind)
    
    # If no match, try to infer from title
    if not cost_key:
        if "cloudtrail" in title:
            cost_key = "cloudtrail"
        elif "flow log" in title:
            cost_key = "vpc_flow_logs"
        elif "config" in title and "recorder" in title:
            cost_key = "config_recorder"
        elif "access analyzer" in title:
            cost_key = "access_analyzer"
        elif "guardduty" in title:
            cost_key = "guardduty"
        elif "mfa" in title:
            cost_key = "mfa_device"
        elif "multi-az" in title or "single-az" in title:
            cost_key = "rds_multi_az"
        elif "backup" in title:
            cost_key = "backup_plan"
        elif "versioning" in title:
            cost_key = "s3_versioning"
        elif "endpoint" in title:
            cost_key = "vpc_endpoint_gateway"
    
    if cost_key and cost_key in REMEDIATION_COSTS:
        cost_info = REMEDIATION_COSTS[cost_key]
        return {
            "monthly_cost": cost_info["monthly_cost"],
            "one_time_cost": cost_info["one_time_cost"],
            "description": cost_info["description"],
            "confidence": "high" if cost_info["monthly_cost"] == 0 else "medium",
        }
    
    return None


def deduplicate_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group similar findings to reduce noise.
    
    Groups findings by:
    - Same kind + same check pattern (e.g., all subnets with auto-assign public IP)
    - Same kind + same VPC (e.g., all default VPCs)
    
    Returns a list with grouped findings having:
    - Original finding structure
    - grouped_resources: List of all affected resources
    - grouped_count: Number of resources in group
    """
    # Group by (pack, kind, check_id_prefix)
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    
    for finding in findings:
        pack = finding.get("pack", "unknown")
        kind = finding.get("kind", "unknown")
        
        # Extract check pattern from title for grouping
        title = finding.get("title", "")
        
        # Grouping rules
        group_key = None
        
        # Subnets auto-assigning public IPs
        if "auto-assigns public IPs" in title:
            group_key = (pack, kind, "subnet-auto-public-ip")
        # VPCs with no flow logs
        elif "has no flow logs" in title:
            group_key = (pack, kind, "vpc-no-flow-logs")
        # Default VPCs
        elif "Default VPC in use" in title:
            group_key = (pack, kind, "default-vpc")
        # S3 versioning
        elif "versioning not enabled" in title:
            group_key = (pack, kind, "s3-no-versioning")
        # IAM Access Analyzer
        elif "Access Analyzer" in title and "not enabled" in title:
            group_key = (pack, kind, "no-access-analyzer")
        # Untagged resources
        elif "has no tags" in title:
            group_key = (pack, kind, "untagged-resource")
        # Default: no grouping
        else:
            group_key = (pack, kind, finding.get("id", ""))
        
        groups[group_key].append(finding)
    
    # Build deduplicated output
    result: List[Dict[str, Any]] = []
    
    for group_key, group_findings in groups.items():
        if len(group_findings) == 1:
            # Single finding, no grouping needed
            finding = group_findings[0].copy()
            finding["grouped_count"] = 1
            finding["grouped_resources"] = [finding.get("resource_id", "")]
            result.append(finding)
        else:
            # Multiple findings, create grouped finding
            base = group_findings[0].copy()
            
            # Collect all resource IDs and regions
            resource_ids = [f.get("resource_id", "") for f in group_findings]
            regions = list(set(f.get("region", "") for f in group_findings))
            
            # Create grouped title
            pack, kind, pattern = group_key
            count = len(group_findings)
            
            if pattern == "subnet-auto-public-ip":
                base["title"] = f"{count} subnets auto-assign public IPs"
                base["description"] = f"{count} subnets across {len(regions)} region(s) automatically assign public IPs to instances."
            elif pattern == "vpc-no-flow-logs":
                base["title"] = f"{count} VPCs have no flow logs"
                base["description"] = f"{count} VPCs across {len(regions)} region(s) have no VPC flow logs enabled."
            elif pattern == "default-vpc":
                base["title"] = f"Default VPC in use in {count} region(s)"
                base["description"] = f"Default VPCs are in use in {count} region(s): {', '.join(regions)}."
            elif pattern == "s3-no-versioning":
                base["title"] = f"{count} S3 buckets have versioning disabled"
                base["description"] = f"{count} S3 buckets do not have versioning enabled."
            elif pattern == "no-access-analyzer":
                base["title"] = f"IAM Access Analyzer not enabled in {count} region(s)"
                base["description"] = f"IAM Access Analyzer is not enabled in {count} region(s)."
            elif pattern == "untagged-resource":
                base["title"] = f"{count} resources have no tags"
                base["description"] = f"{count} resources have zero tags and cannot be attributed."
            
            # Update region to show multiple
            if len(regions) > 1:
                base["region"] = f"{len(regions)} regions"
            
            # Add grouping metadata
            base["grouped_count"] = count
            base["grouped_resources"] = resource_ids
            base["grouped_regions"] = regions
            
            # Aggregate impact
            total_impact = sum(f.get("estimated_monthly_impact", 0) or 0 for f in group_findings)
            base["estimated_monthly_impact"] = total_impact
            
            result.append(base)
    
    return result


def tune_severity(finding: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Adjust finding severity based on context.
    
    Context can include:
    - last_login_days: Days since user last logged in
    - has_resources: Whether VPC/subnet has any resources
    - resource_count: Number of resources affected
    - is_production: Whether the account/resource is production
    
    Returns adjusted severity string.
    """
    original_severity = finding.get("severity", "medium")
    kind = finding.get("kind", "")
    title = finding.get("title", "").lower()
    
    # MFA-less user who hasn't logged in recently
    if "mfa" in title and "console" in title:
        last_login = context.get("last_login_days")
        if last_login is not None and last_login > 90:
            # User hasn't logged in for 90+ days, lower severity
            if original_severity == "critical":
                return "high"
            elif original_severity == "high":
                return "medium"
    
    # Default VPC with no resources
    if "default vpc" in title.lower():
        has_resources = context.get("has_resources", True)
        if not has_resources:
            # Empty default VPC is less risky
            if original_severity in ("high", "critical"):
                return "medium"
            elif original_severity == "medium":
                return "low"
    
    # Subnet auto-assign public IP in default VPC with no instances
    if "auto-assigns public" in title:
        resource_count = context.get("resource_count", 1)
        if resource_count == 0:
            # No instances in subnet, lower risk
            if original_severity == "high":
                return "medium"
    
    # Flow logs missing but no traffic
    if "flow logs" in title:
        has_traffic = context.get("has_traffic", True)
        if not has_traffic:
            if original_severity == "high":
                return "medium"
    
    return original_severity


def process_findings(
    findings: List[Dict[str, Any]],
    *,
    deduplicate: bool = True,
    add_costs: bool = True,
    tune_severities: bool = True,
    context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Process findings with deduplication, cost estimation, and severity tuning.
    
    Args:
        findings: List of finding dicts
        deduplicate: Whether to group similar findings
        add_costs: Whether to add remediation cost estimates
        tune_severities: Whether to adjust severities based on context
        context: Additional context for severity tuning
    
    Returns:
        Processed list of findings
    """
    context = context or {}
    result = findings
    
    # Add remediation cost estimates
    if add_costs:
        for finding in result:
            cost_info = estimate_remediation_cost(finding)
            if cost_info:
                finding["estimated_remediation_cost"] = cost_info
    
    # Tune severities based on context
    if tune_severities:
        for finding in result:
            resource_id = finding.get("resource_id", "")
            resource_context = context.get(resource_id, {})
            
            # Merge global context with resource-specific context
            full_context = {**context, **resource_context}
            
            adjusted = tune_severity(finding, full_context)
            if adjusted != finding.get("severity"):
                finding["original_severity"] = finding.get("severity")
                finding["severity"] = adjusted
                finding["severity_adjusted"] = True
    
    # Deduplicate/group similar findings
    if deduplicate:
        result = deduplicate_findings(result)
    
    return result
