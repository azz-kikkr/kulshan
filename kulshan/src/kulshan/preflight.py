"""Pre-flight health check for Kulshan scans."""
from __future__ import annotations

import sys
from typing import Any, List, Tuple

from rich.console import Console


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
