"""Pre-flight health check for Kulshan scans."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from kulshan.capabilities import (
    PACK_PROBES,
    CapabilityStatus,
    PackReadinessResult,
    assess_pack_readiness,
)


@dataclass
class PreflightResult:
    """Result of pre-flight checks including optional CUR discovery."""
    
    passed: bool
    warnings: List[str]
    cur_export: Optional[Any] = None  # CurExportInfo if discovered
    cur_accessible: bool = False


@dataclass
class PreflightDeepResult:
    """Complete preflight result for --deep mode."""

    passed: bool
    identity: Dict[str, Any]  # arn, account_id, user_id, partition
    workspace: Dict[str, Any]  # name, payer, connections
    permissions: Dict[str, CapabilityStatus]  # service -> status
    data_sources: Dict[str, str]  # cur, cost_explorer, etc.
    packs: Dict[str, PackReadinessResult] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def run_preflight(
    session: Any,
    console: Console | None = None,
    verbose: bool = False,
) -> Tuple[bool, List[str]]:
    """Run pre-flight checks and print results.

    Returns (all_passed, list_of_warnings).
    If a critical check fails, returns (False, warnings).
    """
    if console is None:
        console = Console()

    warnings: List[str] = []
    all_passed = True

    console.print("\n  [bold]Pre-flight checks[/bold]")

    # 1. Python version
    py_version = sys.version_info
    if py_version >= (3, 9):
        console.print(f"  [green]✓[/green] Python {py_version.major}.{py_version.minor}")
    else:
        console.print(f"  [red]✗[/red] Python {py_version.major}.{py_version.minor} (need 3.9+)")
        all_passed = False

    # 2. AWS credentials exist
    try:
        credentials = session.get_credentials()
        if credentials is None:
            console.print("  [red]✗[/red] No AWS credentials found")
            console.print("    [dim]Run 'aws configure' or set AWS_ACCESS_KEY_ID[/dim]")
            all_passed = False
        else:
            console.print("  [green]✓[/green] AWS credentials found")
    except Exception as e:
        error_msg = str(e)
        if "Missing Dependency" in error_msg or "awscrt" in error_msg.lower():
            console.print("  [red]✗[/red] Missing AWS CRT dependency for login provider")
            console.print("    [dim]Fix: pip install awscrt[/dim]")
            console.print("    [dim]This is needed for 'aws login' browser-based credentials.[/dim]")
        else:
            console.print(f"  [red]✗[/red] Credential error: {error_msg[:80]}")
        all_passed = False

    # 3. STS identity (credentials not expired)
    account = None
    try:
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        account = identity.get("Account", "unknown")
        console.print(f"  [green]✓[/green] Authenticated (account {account})")
    except Exception as e:
        error_msg = str(e)
        if "ExpiredToken" in error_msg:
            console.print("  [red]✗[/red] Credentials expired")
            console.print("    [dim]Run 'aws sso login' or refresh your credentials[/dim]")
        elif "InvalidClientTokenId" in error_msg:
            console.print("  [red]✗[/red] Invalid credentials")
            console.print("    [dim]Check your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY[/dim]")
        else:
            console.print(f"  [red]✗[/red] Auth failed: {error_msg[:80]}")
        all_passed = False

    # If auth failed, skip API probes
    if not all_passed:
        console.print()
        return all_passed, warnings

    # 4. Cost Explorer access (warning only, not critical)
    ce_ok = False
    try:
        from datetime import date, timedelta
        probe_end = date.today()
        probe_start = probe_end - timedelta(days=2)
        ce = session.client("ce", region_name="us-east-1")
        ce.get_cost_and_usage(
            TimePeriod={"Start": probe_start.isoformat(), "End": probe_end.isoformat()},
            Granularity="DAILY",
            Metrics=["BlendedCost"],
        )
        console.print("  [green]✓[/green] Cost Explorer API accessible")
        ce_ok = True
    except Exception as e:
        error_msg = str(e)
        if "not enabled" in error_msg.lower() or "OptIn" in error_msg:
            console.print("  [yellow]⚠[/yellow] Cost Explorer not enabled")
            console.print("    [dim]Enable in AWS Console → Billing → Cost Explorer. Cost pack will skip.[/dim]")
            warnings.append("Cost Explorer not enabled")
        elif "AccessDenied" in error_msg:
            console.print("  [yellow]⚠[/yellow] No Cost Explorer permission")
            console.print("    [dim]Need ce:GetCostAndUsage. Cost pack will be limited.[/dim]")
            warnings.append("No ce:GetCostAndUsage permission")
        else:
            console.print("  [yellow]⚠[/yellow] Cost Explorer check inconclusive")
            warnings.append(f"CE check: {error_msg[:60]}")

    # 5. EC2 describe (tests basic resource-level read access)
    ec2_ok = False
    try:
        ec2 = session.client("ec2", region_name="us-east-1")
        ec2.describe_instances(MaxResults=5)
        console.print("  [green]✓[/green] EC2 read access (security, sweep, dr packs)")
        ec2_ok = True
    except Exception as e:
        error_msg = str(e)
        if "UnauthorizedOperation" in error_msg or "AccessDenied" in error_msg:
            console.print("  [yellow]⚠[/yellow] No EC2 read permission (some packs limited)")
            warnings.append("No ec2:DescribeInstances")
        else:
            console.print("  [yellow]⚠[/yellow] EC2 probe inconclusive")
            warnings.append(f"EC2 check: {error_msg[:60]}")

    # 6. Organizations access (for multi-account context)
    try:
        org = session.client("organizations", region_name="us-east-1")
        org.describe_organization()
        console.print("  [green]✓[/green] Organizations API (multi-account context)")
    except Exception:
        console.print("  [dim]  ─[/dim] [dim]Organizations not available (single-account mode)[/dim]")

    # Summary guidance
    console.print()
    if ce_ok and ec2_ok:
        console.print("  [green bold]Ready.[/green bold] Cost baseline will run. Inventory packs available with --packs.")
    elif ce_ok:
        console.print("  [green]Cost baseline ready.[/green] [dim]Inventory packs may be limited by permissions.[/dim]")
    elif ec2_ok:
        console.print("  [yellow]No Cost Explorer access.[/yellow] Use --packs security,sweep,dr for free inventory scans.")
    else:
        console.print("  [yellow]Limited permissions. Run kulshan report to see what's available.[/yellow]")

    console.print()
    return all_passed, warnings


def run_preflight_with_cur(
    session: Any,
    console: Console | None = None,
    verbose: bool = False,
) -> PreflightResult:
    """Run pre-flight checks including CUR/Data Export discovery.
    
    Returns a PreflightResult with:
    - passed: whether critical checks passed
    - warnings: list of warning messages  
    - cur_export: CurExportInfo if a Data Export was discovered
    - cur_accessible: whether we can access the CUR S3 location
    """
    # Run standard preflight first
    passed, warnings = run_preflight(session, console, verbose)
    
    result = PreflightResult(
        passed=passed,
        warnings=warnings,
        cur_export=None,
        cur_accessible=False,
    )
    
    # Skip CUR discovery if auth failed
    if not passed:
        return result
    
    if console is None:
        console = Console()
    
    # 7. CUR/Data Export discovery (best-effort, non-blocking)
    try:
        from kulshan.cur.discovery import find_best_cur_export, check_cur_s3_access
        
        export = find_best_cur_export(session)
        if export:
            # Check if we can actually access the S3 data
            accessible = check_cur_s3_access(session, export)
            result.cur_export = export
            result.cur_accessible = accessible
            
            if accessible:
                console.print(f"  [cyan]⬢[/cyan] CUR/Data Export found: [bold]{export.export_name}[/bold]")
                console.print(f"    [dim]{export.s3_uri}[/dim]")
            else:
                console.print(f"  [dim]  ─[/dim] [dim]CUR found but S3 not accessible: {export.export_name}[/dim]")
    except Exception:
        # CUR discovery is best-effort — never block on errors
        pass
    
    return result


def mask_account_id(account_id: str) -> str:
    """Mask an account ID: show first 5 + '***' + last 4 digits.

    Example: '123456789012' -> '12345***9012'
    """
    if not account_id or len(account_id) < 9:
        return account_id
    return f"{account_id[:5]}***{account_id[-4:]}"


def run_preflight_deep(
    session: Any,
    console: Console | None = None,
    verbose: bool = False,
) -> PreflightDeepResult:
    """Run deep preflight: identity check + per-pack capability probes.

    Probes every pack's required services and returns structured results.
    """
    if console is None:
        console = Console()

    warnings: List[str] = []
    errors: List[str] = []
    identity: Dict[str, Any] = {}
    permissions: Dict[str, CapabilityStatus] = {}
    data_sources: Dict[str, str] = {
        "cost_explorer": "not_checked",
        "cur_local": "not_configured",
        "cur_s3": "not_configured",
    }
    workspace: Dict[str, Any] = {"name": None, "payer_account_id": None, "connections": []}
    packs: Dict[str, PackReadinessResult] = {}
    passed = True

    # 1. STS identity check
    try:
        sts = session.client("sts")
        caller = sts.get_caller_identity()
        identity = {
            "arn": caller.get("Arn", ""),
            "account_id": caller.get("Account", ""),
            "user_id": caller.get("UserId", ""),
            "partition": caller.get("Arn", "").split(":")[1] if ":" in caller.get("Arn", "") else "aws",
        }
        permissions["sts"] = "available"
    except Exception as e:
        passed = False
        errors.append(f"STS authentication failed: {str(e)[:100]}")
        permissions["sts"] = "denied"
        # Cannot continue without identity
        return PreflightDeepResult(
            passed=False,
            identity=identity,
            workspace=workspace,
            permissions=permissions,
            data_sources=data_sources,
            packs=packs,
            warnings=warnings,
            errors=errors,
        )

    # 2. Probe each pack
    for pack_name in PACK_PROBES:
        result = assess_pack_readiness(session, pack_name)
        packs[pack_name] = result

        # Aggregate per-service permissions
        for cap in result.capabilities:
            key = cap.service
            # If we already marked it available, keep that
            if permissions.get(key) == "available":
                continue
            permissions[key] = cap.status

    # 3. Data sources assessment
    if permissions.get("ce") == "available":
        data_sources["cost_explorer"] = "available"
    elif permissions.get("ce") == "denied":
        data_sources["cost_explorer"] = "denied"
    else:
        data_sources["cost_explorer"] = permissions.get("ce", "not_checked")

    # 4. Check if all packs are unavailable
    all_unavailable = all(p.readiness == "unavailable" for p in packs.values())
    if all_unavailable:
        warnings.append("All pack probes returned denied/unavailable")

    return PreflightDeepResult(
        passed=passed,
        identity=identity,
        workspace=workspace,
        permissions=permissions,
        data_sources=data_sources,
        packs=packs,
        warnings=warnings,
        errors=errors,
    )


def deep_result_to_json(result: PreflightDeepResult) -> Dict[str, Any]:
    """Convert a PreflightDeepResult to a JSON-serializable dict.

    Account IDs are masked in the output.
    """
    # Mask account ID
    masked_account = mask_account_id(result.identity.get("account_id", ""))

    # Build packs section
    packs_json: Dict[str, Any] = {}
    for pack_name, pack_result in result.packs.items():
        packs_json[pack_name] = {
            "readiness": pack_result.readiness,
            "reason": pack_result.reason,
        }

    return {
        "identity": {
            "arn": result.identity.get("arn", ""),
            "account_id": masked_account,
            "partition": result.identity.get("partition", "aws"),
        },
        "workspace": {
            "name": result.workspace.get("name"),
            "payer_account_id": mask_account_id(result.workspace.get("payer_account_id") or ""),
        },
        "connections": result.workspace.get("connections", []),
        "permissions": dict(result.permissions),
        "data_sources": dict(result.data_sources),
        "packs": packs_json,
        "warnings": list(result.warnings),
        "errors": list(result.errors),
    }


def basic_result_to_json(
    passed: bool,
    identity: Dict[str, Any],
    permissions: Dict[str, CapabilityStatus],
    warnings: List[str],
) -> Dict[str, Any]:
    """Convert basic preflight results to a JSON-serializable dict."""
    masked_account = mask_account_id(identity.get("account_id", ""))

    return {
        "identity": {
            "arn": identity.get("arn", ""),
            "account_id": masked_account,
            "partition": identity.get("partition", "aws"),
        },
        "workspace": {"name": None, "payer_account_id": None},
        "connections": [],
        "permissions": dict(permissions),
        "data_sources": {
            "cost_explorer": permissions.get("ce", "not_checked"),
            "cur_local": "not_configured",
            "cur_s3": "not_configured",
        },
        "packs": {},
        "warnings": list(warnings),
        "errors": [] if passed else ["Pre-flight checks failed"],
    }


def run_preflight_basic_json(session: Any) -> Dict[str, Any]:
    """Run basic preflight (no deep) and return JSON-ready dict.

    No Rich output is produced.
    """
    identity: Dict[str, Any] = {}
    permissions: Dict[str, CapabilityStatus] = {}
    warnings: List[str] = []
    passed = True

    # STS check
    try:
        sts = session.client("sts")
        caller = sts.get_caller_identity()
        identity = {
            "arn": caller.get("Arn", ""),
            "account_id": caller.get("Account", ""),
            "user_id": caller.get("UserId", ""),
            "partition": caller.get("Arn", "").split(":")[1] if ":" in caller.get("Arn", "") else "aws",
        }
        permissions["sts"] = "available"
    except Exception:
        passed = False
        return basic_result_to_json(False, identity, permissions, ["STS authentication failed"])

    # Cost Explorer
    try:
        from datetime import date, timedelta

        probe_end = date.today()
        probe_start = probe_end - timedelta(days=2)
        ce = session.client("ce", region_name="us-east-1")
        ce.get_cost_and_usage(
            TimePeriod={"Start": probe_start.isoformat(), "End": probe_end.isoformat()},
            Granularity="DAILY",
            Metrics=["BlendedCost"],
        )
        permissions["ce"] = "available"
    except Exception as e:
        error_msg = str(e)
        if "AccessDenied" in error_msg:
            permissions["ce"] = "denied"
            warnings.append("No ce:GetCostAndUsage permission")
        else:
            permissions["ce"] = "error"
            warnings.append(f"CE check: {error_msg[:60]}")

    # EC2
    try:
        ec2 = session.client("ec2", region_name="us-east-1")
        ec2.describe_instances(MaxResults=5)
        permissions["ec2"] = "available"
    except Exception as e:
        error_msg = str(e)
        if "UnauthorizedOperation" in error_msg or "AccessDenied" in error_msg:
            permissions["ec2"] = "denied"
            warnings.append("No ec2:DescribeInstances")
        else:
            permissions["ec2"] = "error"
            warnings.append(f"EC2 check: {error_msg[:60]}")

    # Organizations (optional)
    try:
        org = session.client("organizations", region_name="us-east-1")
        org.describe_organization()
        permissions["organizations"] = "available"
    except Exception:
        permissions["organizations"] = "unavailable"

    return basic_result_to_json(passed, identity, permissions, warnings)
