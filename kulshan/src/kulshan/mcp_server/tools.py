"""MCP tool registrations for Kulshan."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from kulshan.orchestrator import TOOL_ORDER

VALID_PACKS = set(TOOL_ORDER)
REPORT_TIMEOUT_SECONDS = 240
SECURITY_TIMEOUT_SECONDS = 120
ANALYSIS_TIMEOUT_SECONDS = 180
VALIDATION_TIMEOUT_SECONDS = 60
PREFLIGHT_TIMEOUT_SECONDS = 30


def register_tools(mcp: Any) -> None:
    """Register Kulshan MCP tools."""

    @mcp.tool()
    def kulshan_preflight() -> str:
        """Check AWS caller identity using the default credential chain."""
        return _run_isolated(
            "preflight",
            {},
            PREFLIGHT_TIMEOUT_SECONDS,
            "Run `aws sts get-caller-identity` and refresh AWS credentials.",
        )

    @mcp.tool()
    def kulshan_report(packs: str = "cost", days: int = 90, regions: str | None = None) -> str:
        """Run selected Kulshan report packs and return compact findings."""
        return _run_isolated(
            "report",
            {"packs": _parse_packs(packs), "days": days, "regions": _parse_regions(regions)},
            REPORT_TIMEOUT_SECONDS,
            "Check AWS credentials, reduce packs, or pass a small comma-separated region list.",
        )

    @mcp.tool()
    def kulshan_analyze_ec2(cur_path: str, month: str | None = None) -> str:
        """Analyze EC2 cost movement from local CUR/Data Export Parquet."""
        return _run_isolated(
            "analyze_ec2",
            {"cur_path": cur_path, "month": month},
            ANALYSIS_TIMEOUT_SECONDS,
            "Validate the local export with `kulshan cur validate --path`.",
        )

    @mcp.tool()
    def kulshan_analyze_cost(s3_uri: str, month: str) -> str:
        """Analyze monthly cost from S3 CUR/Data Export Parquet."""
        return _run_isolated(
            "analyze_cost",
            {"s3_uri": s3_uri, "month": month},
            ANALYSIS_TIMEOUT_SECONDS,
            "Run `kulshan cur s3-check --s3` and verify read-only S3/KMS permissions.",
        )

    @mcp.tool()
    def kulshan_cur_validate(cur_path: str) -> str:
        """Validate local CUR/Data Export Parquet readability and semantic fields."""
        return _run_isolated(
            "cur_validate",
            {"cur_path": cur_path},
            VALIDATION_TIMEOUT_SECONDS,
            "Check the path points to readable CUR/Data Export Parquet files.",
        )

    @mcp.tool()
    def kulshan_quick_security(region: str = "us-east-1") -> str:
        """Fast security scan of a single region. Returns critical/high findings only."""
        return _run_isolated(
            "quick_security",
            {"region": region},
            SECURITY_TIMEOUT_SECONDS,
            "Check AWS credentials and try again.",
        )

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
        return _json(
            {"packs": pack_info, "usage": "kulshan_report(packs='security,dr') or packs='all'"}
        )


def _run_isolated(
    operation: str,
    arguments: dict[str, Any],
    timeout_seconds: int,
    suggestion: str,
) -> str:
    """Run a potentially blocking tool in a killable child process."""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [sys.executable, "-m", "kulshan.mcp_server.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=creationflags,
    )
    request = json.dumps({"operation": operation, "arguments": arguments})
    try:
        stdout, stderr = process.communicate(request, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
        raise ToolError(
            f"Kulshan tool '{operation}' timed out after {timeout_seconds} seconds. {suggestion}"
        ) from None

    if process.returncode != 0:
        detail = stderr.strip() or f"worker exited with code {process.returncode}"
        raise ToolError(f"Kulshan tool '{operation}' failed: {detail}. {suggestion}")
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(
            f"Kulshan tool '{operation}' returned an invalid worker response. {suggestion}"
        ) from exc
    if response.get("status") == "error":
        raise ToolError(f"{response.get('payload', 'Unknown tool error')} {suggestion}")
    return str(response.get("payload", ""))


def _execute_operation(operation: str, arguments: dict[str, Any]) -> str:
    """Dispatch one operation inside the isolated worker process."""
    handlers = {
        "preflight": _execute_preflight,
        "report": _execute_report,
        "analyze_ec2": _execute_analyze_ec2,
        "analyze_cost": _execute_analyze_cost,
        "cur_validate": _execute_cur_validate,
        "quick_security": _execute_quick_security,
    }
    return handlers[operation](**arguments)


def _execute_preflight() -> str:
    import boto3

    identity = boto3.client("sts").get_caller_identity()
    return _json({"status": "ok", "account": identity.get("Account"), "arn": identity.get("Arn")})


def _execute_report(packs: list[str], days: int, regions: list[str] | None) -> str:
    from rich.console import Console

    from kulshan.orchestrator import run_all_scans
    from kulshan.session import create_session, get_enabled_regions

    session = create_session()
    selected_regions = regions
    if selected_regions is None:
        selected_regions = get_enabled_regions(session)
        selected_regions = (
            selected_regions[:3]
            if any(pack != "cost" for pack in packs)
            else selected_regions[:1] or ["us-east-1"]
        )
    results = run_all_scans(
        session=session,
        regions=selected_regions,
        quick=True,
        selected_packs=packs,
        console=Console(stderr=True),
        days=days,
    )
    _raise_if_all_packs_unavailable(results)
    return _json(
        {
            "status": "ok",
            "days": days,
            "packs": packs,
            "regions": selected_regions,
            "findings": _compact_findings_by_pack(results),
        }
    )


def _raise_if_all_packs_unavailable(results: dict[str, Any]) -> None:
    """Fail the MCP call when no requested pack produced a usable result."""
    unavailable = {
        pack: result
        for pack, result in results.items()
        if isinstance(result, dict) and result.get("skipped")
    }
    if results and len(unavailable) == len(results):
        reasons = []
        for pack, result in unavailable.items():
            errors = result.get("errors") or []
            reason = str(errors[0]) if errors else "unavailable"
            reasons.append(f"{pack}: {reason}")
        raise RuntimeError("All requested packs were unavailable: " + "; ".join(reasons))


def _execute_analyze_ec2(cur_path: str, month: str | None) -> str:
    from kulshan.analyze import analyze_ec2_cur
    from kulshan.analyze.export import ec2_brief_to_json

    return ec2_brief_to_json(analyze_ec2_cur(cur_path, month=month))


def _execute_analyze_cost(s3_uri: str, month: str) -> str:
    from kulshan.analyze.export import cost_result_to_json
    from kulshan.cur.manifest_reader import read_manifest_uri
    from kulshan.cur.s3_query import analyze_cost_s3, connect_s3_duckdb

    manifest = read_manifest_uri(s3_uri, billing_period=month)
    con = connect_s3_duckdb()
    try:
        result = analyze_cost_s3(con, manifest, month)
    finally:
        con.close()
    return cost_result_to_json(result, month)


def _execute_cur_validate(cur_path: str) -> str:
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


def _execute_quick_security(region: str) -> str:
    from rich.console import Console

    from kulshan.orchestrator import run_all_scans
    from kulshan.session import create_session

    results = run_all_scans(
        session=create_session(),
        regions=[region],
        quick=True,
        selected_packs=["security"],
        console=Console(stderr=True, quiet=True),
    )
    findings = results.get("security", {}).get("findings", [])
    critical_high = [f for f in findings if f.get("severity") in ("critical", "high")]
    return _json(
        {
            "status": "ok",
            "region": region,
            "total_findings": len(findings),
            "critical_high_count": len(critical_high),
            "findings": [_compact_finding(f) for f in critical_high[:10]],
        }
    )


def _parse_packs(packs: str) -> list[str]:
    value = (packs or "cost").strip().lower()
    if value == "all":
        return list(TOOL_ORDER)
    selected = [part.strip() for part in value.split(",") if part.strip()]
    invalid = [pack for pack in selected if pack not in VALID_PACKS]
    if invalid:
        raise ToolError(f"Unknown pack(s): {', '.join(invalid)}")
    return selected or ["cost"]


def _parse_regions(regions: str | None) -> list[str] | None:
    if regions is None or not regions.strip():
        return None
    return [region.strip() for region in regions.split(",") if region.strip()]


def _compact_findings_by_pack(results: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    output = {}
    for pack, result in results.items():
        findings = result.get("findings", []) if isinstance(result, dict) else []
        output[pack] = [_compact_finding(finding) for finding in findings[:15]]
    return output


def _compact_finding(finding: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "severity": finding.get("severity"),
        "title": finding.get("title"),
        "service": finding.get("service") or finding.get("resource_type") or finding.get("pack"),
        "dollar_impact": finding.get("estimated_monthly_impact"),
        "recommendation": finding.get("recommended_action") or finding.get("remediation"),
    }
    remediation_cost = finding.get("estimated_remediation_cost")
    if remediation_cost:
        compact["remediation_cost_monthly"] = remediation_cost.get("monthly_cost")
        compact["remediation_cost_desc"] = remediation_cost.get("description")
    grouped_count = finding.get("grouped_count")
    if grouped_count and grouped_count > 1:
        compact["grouped_count"] = grouped_count
        compact["grouped_resources"] = finding.get("grouped_resources", [])[:5]
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
