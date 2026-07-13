"""MCP tool registrations for Kulshan."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import boto3

from kulshan.orchestrator import TOOL_ORDER

VALID_PACKS = set(TOOL_ORDER)


def register_tools(mcp: Any) -> None:
    """Register Kulshan MCP tools on a FastMCP instance."""

    @mcp.tool()
    def kulshan_doctor() -> str:
        """Check AWS caller identity using the default credential chain."""
        try:
            identity = boto3.client("sts").get_caller_identity()
            return _json(
                {
                    "status": "ok",
                    "account": identity.get("Account"),
                    "arn": identity.get("Arn"),
                }
            )
        except Exception as exc:
            return _error(exc, "Run `aws sts get-caller-identity` and refresh AWS credentials.")

    @mcp.tool()
    def kulshan_report(packs: str = "cost", days: int = 90, regions: str | None = None) -> str:
        """Run selected Kulshan report packs and return compact findings."""
        try:
            selected_packs = _parse_packs(packs)
            selected_regions = _parse_regions(regions)

            from rich.console import Console

            from kulshan.orchestrator import run_all_scans
            from kulshan.session import create_session, get_enabled_regions

            session = create_session()
            if selected_regions is None:
                selected_regions = get_enabled_regions(session)
                if any(pack != "cost" for pack in selected_packs):
                    selected_regions = selected_regions[:3]
                else:
                    selected_regions = selected_regions[:1] or ["us-east-1"]

            results = run_all_scans(
                session=session,
                regions=selected_regions,
                quick=True,
                selected_packs=selected_packs,
                console=Console(stderr=True),
                days=days,
            )
            return _json(
                {
                    "status": "ok",
                    "days": days,
                    "packs": selected_packs,
                    "regions": selected_regions,
                    "findings": _compact_findings_by_pack(results),
                }
            )
        except Exception as exc:
            return _error(
                exc,
                "Check AWS credentials, reduce packs, or pass a small comma-separated region list.",
            )

    @mcp.tool()
    def kulshan_investigate_ec2(cur_path: str, month: str | None = None) -> str:
        """Investigate EC2 cost movement from local CUR/Data Export Parquet."""
        try:
            from kulshan.investigate import investigate_ec2_cur
            from kulshan.investigate.export import ec2_brief_to_json

            return ec2_brief_to_json(investigate_ec2_cur(cur_path, month=month))
        except Exception as exc:
            return _error(exc, "Validate the local export with `kulshan cur validate --path`.")

    @mcp.tool()
    def kulshan_investigate_cost(s3_uri: str, month: str) -> str:
        """Investigate monthly cost from S3 CUR/Data Export Parquet."""
        try:
            from kulshan.cur.manifest_reader import read_manifest_uri
            from kulshan.cur.s3_query import connect_s3_duckdb, investigate_cost_s3
            from kulshan.investigate.export import cost_result_to_json

            manifest = read_manifest_uri(s3_uri, billing_period=month)
            con = connect_s3_duckdb()
            try:
                result = investigate_cost_s3(con, manifest, month)
            finally:
                con.close()
            return cost_result_to_json(result, month)
        except Exception as exc:
            return _error(
                exc,
                "Run `kulshan cur s3-check --s3` and verify read-only S3/KMS permissions.",
            )

    @mcp.tool()
    def kulshan_cur_validate(cur_path: str) -> str:
        """Validate local CUR/Data Export Parquet readability and semantic fields."""
        try:
            from kulshan.cur.validation import validate_local_cur

            report = validate_local_cur(cur_path)
            return _json(
                {
                    "readable": report.readable,
                    "row_count": report.row_count,
                    "column_count": report.column_count,
                    "semantic_fields": list(report.semantic_fields),
                    "ec2_rows": report.ec2_rows,
                    "network_usage_patterns": report.network_usage_patterns,
                    "selected_cost_column": report.selected_cost_column,
                }
            )
        except Exception as exc:
            return _error(exc, "Check the path points to readable CUR/Data Export Parquet files.")

    @mcp.tool()
    def kulshan_quick_security(region: str = "us-east-1") -> str:
        """Fast security scan of a single region. Returns critical/high findings only."""
        try:
            from rich.console import Console

            from kulshan.orchestrator import run_all_scans
            from kulshan.session import create_session

            session = create_session()
            results = run_all_scans(
                session=session,
                regions=[region],
                quick=True,
                selected_packs=["security"],
                console=Console(stderr=True, quiet=True),
            )
            
            # Filter to critical and high only
            findings = results.get("security", {}).get("findings", [])
            critical_high = [f for f in findings if f.get("severity") in ("critical", "high")]
            
            return _json({
                "status": "ok",
                "region": region,
                "total_findings": len(findings),
                "critical_high_count": len(critical_high),
                "findings": [_compact_finding(f) for f in critical_high[:10]],
            })
        except Exception as exc:
            return _error(exc, "Check AWS credentials and try again.")

    @mcp.tool()
    def kulshan_list_packs() -> str:
        """List all available Kulshan audit packs with descriptions."""
        pack_info = {
            "cost": "AWS cost analysis, anomaly detection (z-score, IQR, MAD)",
            "security": "Security posture: IAM, network exposure, logging, encryption",
            "sweep": "Orphaned resource detection: compute, storage, network, database",
            "dr": "Disaster recovery: backup coverage, multi-AZ, single points of failure",
            "age": "Lifecycle audit: EOL runtimes, expiring certs, staleness",
            "drift": "CloudFormation drift, IaC coverage, severity classification",
            "tag": "Tag compliance, unattributed spend detection",
            "pulse": "Observability and alarm coverage, blind-spot heatmap",
            "limit": "Service quota headroom, scaling event planner",
            "topo": "VPC topology, CIDR overlaps, route integrity",
        }
        return _json({
            "packs": pack_info,
            "usage": "kulshan_report(packs='security,dr') or packs='all'",
        })


def _parse_packs(packs: str) -> list[str]:
    value = (packs or "cost").strip().lower()
    if value == "all":
        return list(TOOL_ORDER)
    selected = [part.strip() for part in value.split(",") if part.strip()]
    invalid = [pack for pack in selected if pack not in VALID_PACKS]
    if invalid:
        raise ValueError(f"Unknown pack(s): {', '.join(invalid)}")
    return selected or ["cost"]


def _parse_regions(regions: str | None) -> list[str] | None:
    if regions is None or not regions.strip():
        return None
    return [region.strip() for region in regions.split(",") if region.strip()]


def _compact_findings_by_pack(results: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for pack, result in results.items():
        findings = result.get("findings", []) if isinstance(result, dict) else []
        output[pack] = [_compact_finding(finding) for finding in findings[:15]]
    return output


def _compact_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Convert finding to compact format for MCP output.
    
    Includes new features: remediation costs, grouping info, severity tuning.
    """
    compact = {
        "severity": finding.get("severity"),
        "title": finding.get("title"),
        "service": finding.get("service") or finding.get("resource_type") or finding.get("pack"),
        "dollar_impact": finding.get("estimated_monthly_impact"),
        "recommendation": finding.get("recommended_action") or finding.get("remediation"),
    }
    
    # Include remediation cost estimate if available
    remediation_cost = finding.get("estimated_remediation_cost")
    if remediation_cost:
        compact["remediation_cost_monthly"] = remediation_cost.get("monthly_cost")
        compact["remediation_cost_desc"] = remediation_cost.get("description")
    
    # Include grouping info for deduplicated findings
    grouped_count = finding.get("grouped_count")
    if grouped_count and grouped_count > 1:
        compact["grouped_count"] = grouped_count
        compact["grouped_resources"] = finding.get("grouped_resources", [])[:5]  # Limit to 5
    
    # Include severity adjustment info
    if finding.get("severity_adjusted"):
        compact["original_severity"] = finding.get("original_severity")
        compact["severity_adjusted"] = True
    
    return compact


def _json(payload: Any) -> str:
    return json.dumps(_jsonable(payload), indent=2)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _error(exc: Exception, suggestion: str) -> str:
    return _json({"error": str(exc), "suggestion": suggestion})
