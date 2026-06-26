"""Kulshan CLI entry point."""
from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
from typing import Optional

import click
from rich.console import Console

from kulshan.__version__ import __version__
from kulshan.constants import ExitCode


def _handle_sigint(sig, frame):
    """Handle Ctrl+C gracefully."""
    from rich.console import Console
    Console(stderr=True).print("\n  [red]⨯[/red] Aborted by user.")
    import os
    os._exit(130)


signal.signal(signal.SIGINT, _handle_sigint)


def _atomic_write(path: str, content: str, encoding: str = "utf-8") -> None:
    """Write content to a file atomically using tempfile + rename.
    
    Prevents partial files from being left behind if the process is killed.
    """
    dir_name = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            os.unlink(tmp_path)
            raise
    except OSError:
        # Fallback to direct write if tempfile fails (e.g., cross-device)
        with open(path, "w", encoding=encoding) as f:
            f.write(content)


def _emit_output(
    fmt: str,
    results: dict,
    overall_score: int,
    overall_grade: str,
    account_id: str,
    regions: list,
    duration: float,
    top_actions: list,
    all_findings: list,
    scan_metadata: dict,
    output: Optional[str],
    show_pii: bool,
    console: Console,
) -> None:
    """Shared output dispatch for report and convert commands."""
    from kulshan.redact import redact_account_id, redact_payload, redact_filename

    if fmt == "csv":
        from kulshan.report.csv_export import findings_to_csv
        csv_str = findings_to_csv(all_findings)
        if output:
            _atomic_write(output, csv_str)
            console.print(f"CSV report written to {output}")
        else:
            click.echo(csv_str)
        return

    if fmt == "sarif":
        from kulshan.report.sarif import to_sarif_json
        # Apply redaction to SARIF unless --show-pii
        export_findings = all_findings if show_pii else redact_payload(all_findings)
        export_account = account_id if show_pii else redact_account_id(account_id)
        sarif_str = to_sarif_json(
            export_findings, account_id=export_account, regions=regions, version=__version__,
        )
        if output:
            _atomic_write(output, sarif_str)
            console.print(f"SARIF report written to {output}")
        else:
            click.echo(sarif_str)

    elif fmt == "json":
        payload = {
            "kulshan_version": __version__,
            "account_id": account_id,
            "regions": regions,
            "duration_seconds": round(duration, 1),
            "overall_score": overall_score,
            "overall_grade": overall_grade,
            "scan_metadata": scan_metadata,
            "tools": results,
            "findings": all_findings,
            "top_actions": top_actions,
        }
        if output and not show_pii:
            payload = redact_payload(payload)
        json_str = json.dumps(payload, indent=2, default=str)
        if output:
            _atomic_write(output, json_str)
            console.print(f"Report written to {output}")
        else:
            click.echo(json_str)

    elif fmt == "html":
        from kulshan.report.html import generate_html_report
        render_account = account_id if show_pii else redact_account_id(account_id)
        render_actions = top_actions if show_pii else redact_payload(top_actions, show_pii=show_pii)
        render_results = results if show_pii else redact_payload(results, show_pii=show_pii)
        html_str = generate_html_report(
            results=render_results,
            overall_score=overall_score,
            overall_grade=overall_grade,
            account_id=render_account,
            regions=regions,
            duration_secs=duration,
            top_actions=render_actions,
        )
        default_name = f"kulshan-report-{account_id}.html"
        out_path = output or (default_name if show_pii else redact_filename(default_name))
        _atomic_write(out_path, html_str)
        console.print(f"HTML report written to {out_path}")

    else:  # terminal
        from kulshan.report.terminal import render_report
        render_report(
            results=results,
            overall_score=overall_score,
            overall_grade=overall_grade,
            account_id=account_id,
            regions_count=len(regions),
            duration_secs=duration,
            console=console,
            top_actions=top_actions,
        )


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="kulshan")
@click.option("--profile", default=None, help="AWS CLI profile name.")
@click.option("--role-arn", default=None, help="IAM role ARN to assume.")
@click.pass_context
def main(ctx: click.Context, profile: Optional[str], role_arn: Optional[str]) -> None:
    """Kulshan: local-first AWS FinOps baseline."""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    ctx.obj["role_arn"] = role_arn

    # Zero-argument landing page
    if ctx.invoked_subcommand is None:
        from rich.console import Console as RichConsole
        from rich.panel import Panel
        c = RichConsole()
        c.print()
        c.print(Panel.fit(
            "[bold]Kulshan[/bold] — local-first AWS FinOps baseline\n"
            "[dim]Read-only. Local reports. No SaaS. No telemetry.[/dim]\n\n"
            "[bold]Get started:[/bold]\n"
            "  [green]kulshan doctor[/green]              Check AWS connectivity\n"
            "  [green]kulshan report[/green]              90-day cost baseline (default)\n"
            "  [green]kulshan report -o report.html[/green]   Save as HTML\n\n"
            "[bold]Add more packs:[/bold]\n"
            "  [green]kulshan report --packs cost,tag[/green]       Add tag allocation\n"
            "  [green]kulshan report --packs cost,sweep[/green]     Add waste detection\n"
            "  [green]kulshan report --packs all --regions us-east-1[/green]\n\n"
            "[bold]Other:[/bold]\n"
            "  [green]kulshan history[/green]             Past scans\n"
            "  [green]kulshan shell[/green]               Interactive REPL\n\n"
            "[dim]Uses the AWS credentials you already have configured.\n"
            "Default: reads Cost Explorer, writes a local report.[/dim]",
            title="[bold blue]kulshan[/bold blue]",
            border_style="blue",
        ))
        c.print()


@main.command()
@click.option("--quick", is_flag=True, help="Fast baseline (same as default, skips confirmation).")
@click.option(
    "--format", "fmt",
    type=click.Choice(["terminal", "json", "html", "sarif", "csv"]),
    default="terminal",
    help="Output format.",
)
@click.option("--output", "-o", type=click.Path(), default=None, help="Write output to file.")
@click.option("--days", default=90, type=click.IntRange(1, 365), help="Cost analysis lookback (1-365 days). Default: 90.")
@click.option("--show-pii", is_flag=True, default=False, help="Show full account IDs and PII in exported reports.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmations (for CI/CD).")
@click.option("--packs", default=None, help="Packs: cost,security,sweep,dr,age,drift,tag,pulse,limit,topo or 'all'.")
@click.option("--regions", "region_override", default=None, help="Regions to scan (comma-separated). Default: 3 for inventory packs.")
@click.option("--no-history", is_flag=True, help="Do not retain this scan in local history.")
@click.option("--perf", is_flag=True, help="Show pack and AWS API timing details after the scan.")
@click.option("--deep", is_flag=True, help="Run expensive deep checks instead of the fast default path.")
@click.pass_context
def report(ctx: click.Context, quick: bool, fmt: str, output: Optional[str], days: int, show_pii: bool, yes: bool, packs: Optional[str], region_override: Optional[str], no_history: bool, perf: bool, deep: bool) -> None:
    """Run a FinOps baseline using AWS Cost Explorer.

    \b
    Default: Cost Explorer baseline only (~30s, pennies).
    Other packs available with --packs.

    \b
    Examples:
      kulshan report                    Cost baseline (default, fast)
      kulshan report -o report.html     Cost baseline → HTML
      kulshan report --packs security --regions us-east-1
      kulshan report --packs all --regions us-east-1
    """
    from kulshan.orchestrator import compute_overall, run_all_scans, TOOL_ORDER
    from kulshan.session import create_session, get_account_id, get_enabled_regions

    console = Console(stderr=True) if fmt == "json" and output is None else Console()
    profile = ctx.obj.get("profile")
    role_arn = ctx.obj.get("role_arn")

    # ── Pack selection (no AWS calls) ────────────────────────────────────
    selected_packs = None
    if packs:
        if packs.strip().lower() == "all":
            selected_packs = list(TOOL_ORDER)
        else:
            selected_packs = [p.strip() for p in packs.split(",")]
            invalid = [p for p in selected_packs if p not in TOOL_ORDER]
            if invalid:
                console.print(f"  [red]Unknown pack(s): {', '.join(invalid)}[/red]")
                console.print(f"  [dim]Available: {', '.join(TOOL_ORDER)}[/dim]")
                console.print("  [dim]Or: --packs all[/dim]")
                sys.exit(ExitCode.CONFIG_ERROR)
    else:
        # DEFAULT: cost pack only — fast, focused, predictable
        selected_packs = ["cost"]

    # Smart format detection from output file extension
    if output and fmt == "terminal":
        ext = output.rsplit(".", 1)[-1].lower() if "." in output else ""
        ext_map = {"html": "html", "json": "json", "sarif": "sarif", "csv": "csv"}
        if ext in ext_map:
            fmt = ext_map[ext]

    session = create_session(profile=profile, role_arn=role_arn)

    # Pre-flight health check
    from kulshan.preflight import run_preflight
    passed, _warnings = run_preflight(session, console=console)
    if not passed:
        console.print("[red]Pre-flight checks failed. Fix the issues above and retry.[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    account_id = get_account_id(session)

    # ── Region selection ─────────────────────────────────────────────────
    if region_override:
        regions = [r.strip() for r in region_override.split(",")]
    else:
        regions = get_enabled_regions(session)
        has_regional_packs = any(p != "cost" for p in selected_packs)
        if has_regional_packs:
            max_regions = 3
            if len(regions) > max_regions:
                console.print(f"  [dim]{len(regions)} regions enabled → scanning {max_regions}. Use --regions to override.[/dim]")
                regions = regions[:max_regions]
        else:
            # Cost pack is global (us-east-1 only)
            regions = regions[:1] if regions else ["us-east-1"]

    # ── Warning for --packs all ──────────────────────────────────────────
    if packs and packs.strip().lower() == "all" and not yes:
        console.print(
            f"\n  [yellow bold]⚠ Full scan:[/yellow bold] "
            f"All 10 packs × {len(regions)} region(s). This can take several minutes."
        )
        if not click.confirm("  Proceed?", default=True):
            console.print("  Aborted.")
            sys.exit(0)

    # ── API cost notice ──────────────────────────────────────────────────
    has_cost_pack = "cost" in selected_packs
    if not yes and not quick and has_cost_pack:
        est_cost = "$0.15-0.25"
        console.print(
            f"  [red bold]⚠ AWS Cost:[/red bold] "
            f"The cost pack calls AWS Cost Explorer API (typically {est_cost})."
        )
        console.print(
            "  [dim]AWS bills CE API requests at $0.01 each. This is charged by AWS, not Kulshan.[/dim]"
        )
        console.print()
        if not click.confirm("  Include the cost pack?", default=True):
            selected_packs = [p for p in selected_packs if p != "cost"]
            if not selected_packs:
                console.print()
                console.print("  [dim]No packs to run. Add inventory packs with --packs:[/dim]")
                console.print("  [dim]  kulshan report --packs security --regions us-east-1[/dim]")
                console.print("  [dim]  kulshan report --packs all --regions us-east-1[/dim]")
                sys.exit(0)
            has_cost_pack = False
    elif not yes and not has_cost_pack:
        console.print("  [green]AWS API cost: $0.00[/green] [dim](free read-only APIs only)[/dim]")
        console.print()

    # Pack selection already resolved above

    start = time.time()
    results = run_all_scans(
        session=session, regions=regions, profile=profile, quick=quick, console=console,
        selected_packs=selected_packs, perf=perf, deep=deep,
    )
    duration = time.time() - start

    from kulshan.orchestrator import summarize_completeness

    scan_metadata = summarize_completeness(results)
    overall_score, overall_grade = compute_overall(results)
    if scan_metadata["partial"]:
        console.print(
            "[yellow bold]Partial scan:[/yellow bold] overall score withheld because "
            "one or more requested checks were skipped or reported errors."
        )

    # Phase 6C-1: top-level ranked findings + actions for buyer-grade output.
    from kulshan.findings_ranker import flatten_findings, top_n

    all_findings = flatten_findings(results)
    top_actions = top_n(all_findings, n=10)

    # Enrich findings with remediation snippets
    from kulshan.remediation import enrich_findings
    enrich_findings(all_findings)

    if not no_history:
        try:
            from kulshan.history import HistoryStore

            history = HistoryStore()
            history.purge_old(retention_days=365)
            history.save_scan(
                account_id=account_id,
                regions=regions,
                duration_seconds=duration,
                overall_score=overall_score,
                overall_grade=overall_grade,
                results=results,
                findings=all_findings,
                version=__version__,
                store_full_result=False,
            )
            history.close()
        except Exception:
            pass  # History is best-effort, never block the report

    _emit_output(
        fmt=fmt, results=results, overall_score=overall_score,
        overall_grade=overall_grade, account_id=account_id,
        regions=regions, duration=duration, top_actions=top_actions,
        all_findings=all_findings, output=output, show_pii=show_pii,
        scan_metadata=scan_metadata, console=console,
    )

    # Auto-save HTML report (redacted by default) unless user already exported HTML
    if fmt != "html" and not output:
        from kulshan.report.html import generate_html_report
        from kulshan.redact import redact_account_id, redact_payload, redact_filename
        from datetime import date

        render_account = account_id if show_pii else redact_account_id(account_id)
        render_actions = top_actions if show_pii else redact_payload(top_actions, show_pii=show_pii)
        render_results = results if show_pii else redact_payload(results, show_pii=show_pii)
        html_str = generate_html_report(
            results=render_results,
            overall_score=overall_score,
            overall_grade=overall_grade,
            account_id=render_account,
            regions=regions,
            duration_secs=duration,
            top_actions=render_actions,
        )
        html_filename = f"kulshan-report-{date.today().isoformat()}.html"
        if not show_pii:
            html_filename = redact_filename(html_filename)
        _atomic_write(html_filename, html_str)
        console.print(
            f"\n  [dim]Report saved: [bold]{html_filename}[/bold] "
            "(common identifiers masked; review before sharing)[/dim]"
        )
        console.print("  [dim]Use --show-pii for full account IDs. Open in browser for interactive view.[/dim]")

    has_critical = any(
        r.get("scores", {}).get("severity_counts", {}).get("critical", 0) > 0
        for r in results.values()
    )
    sys.exit(ExitCode.FINDING_FAIL if has_critical else ExitCode.SUCCESS)


# Per-pack subcommands (`Kulshan cost`, `Kulshan security`, ...) were
# previously registered here as passthroughs to each tool's standalone Click
# CLI. Those CLIs were removed when we collapsed to one customer-facing CLI;
# `Kulshan Report` is the unified entry point. A future phase will add scoped
# per-pack invocation as `Kulshan scan <pack>` calling the new run_scan API.


@main.group()
def cur() -> None:
    """Inspect local CUR/Data Exports evidence."""


@cur.command("schema")
@click.option(
    "--path",
    "cur_path",
    required=True,
    type=click.Path(exists=True, file_okay=True, dir_okay=True),
    help="Local CUR/Data Exports Parquet file or directory.",
)
def cur_schema(cur_path: str) -> None:
    """Show the resolved schema mapping for local CUR Parquet data."""
    from rich.console import Console as RichConsole
    from rich.table import Table

    from kulshan.cur.duckdb_engine import (
        connect_memory,
        cur_raw_columns,
        register_cur_raw,
    )
    from kulshan.cur.errors import CurDataError
    from kulshan.cur.source import local_parquet_source

    console = RichConsole()
    try:
        source = local_parquet_source(cur_path)
        con = connect_memory()
        try:
            mapping = register_cur_raw(con, source)
            columns = cur_raw_columns(con)
        finally:
            con.close()
    except CurDataError as exc:
        console.print(f"[red]Cannot inspect CUR schema: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    table = Table(title="CUR Schema Mapping", show_lines=False)
    table.add_column("Semantic field")
    table.add_column("Source column")
    table.add_column("Required")
    rows = [
        ("usage_start", mapping.usage_start, "yes"),
        ("cost", mapping.cost, "yes"),
        ("service", mapping.service, "yes"),
        ("usage_type", mapping.usage_type, "yes"),
        (
            "resource_id",
            mapping.resource_id or "(missing; resource contributors unavailable)",
            "no",
        ),
    ]
    for semantic, source_column, required in rows:
        table.add_row(semantic, source_column, required)

    console.print(table)
    console.print(f"[dim]Detected {len(columns)} column(s).[/dim]")


@cur.command("validate")
@click.option(
    "--path",
    "cur_path",
    required=True,
    type=click.Path(exists=True, file_okay=True, dir_okay=True),
    help="Local CUR/Data Exports Parquet file or directory.",
)
def cur_validate(cur_path: str) -> None:
    """Validate that local CUR Parquet data can support EC2 investigation."""
    from rich.console import Console as RichConsole

    from kulshan.cur.duckdb_engine import (
        connect_memory,
        create_ec2_view,
        register_cur_raw,
    )
    from kulshan.cur.errors import CurDataError
    from kulshan.cur.source import local_parquet_source

    console = RichConsole()
    try:
        source = local_parquet_source(cur_path)
        con = connect_memory()
        try:
            mapping = register_cur_raw(con, source)
            create_ec2_view(con, mapping)
            ec2_rows = con.execute("SELECT COUNT(*) FROM cur_ec2").fetchone()[0]
            periods = con.execute("SELECT COUNT(DISTINCT period) FROM cur_ec2").fetchone()[0]
            if ec2_rows == 0:
                raise CurDataError("No EC2 rows found in the local CUR export.")
            if periods < 2:
                raise CurDataError("Need at least two EC2 periods for investigation.")
        finally:
            con.close()
    except CurDataError as exc:
        console.print(f"[red]CUR validation failed: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)
    except Exception as exc:
        console.print(f"[red]CUR validation failed: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    console.print("[green]CUR validation passed.[/green]")
    console.print(f"[dim]EC2 rows: {ec2_rows}; EC2 periods: {periods}[/dim]")
    if mapping.resource_id is None:
        console.print(
            "[yellow]resource_id column missing; resource-level contributors unavailable.[/yellow]"
        )

@main.group()
def investigate() -> None:
    """Investigate a specific cloud cost movement from local evidence."""


@investigate.command("ec2")
@click.option(
    "--cur",
    "cur_path",
    required=True,
    type=click.Path(exists=True, file_okay=True, dir_okay=True),
    help="Local CUR/Data Exports Parquet file or directory.",
)
@click.option(
    "--month",
    default=None,
    help="Current billing month to investigate in YYYY-MM format. Defaults to latest month.",
)
def investigate_ec2(cur_path: str, month: str | None) -> None:
    """Produce a local EC2 investigation brief from Parquet CUR data."""
    from rich.console import Console as RichConsole
    from rich.table import Table

    from kulshan.investigate.ec2_cur import CurInvestigationError, investigate_ec2_cur

    console = RichConsole()
    try:
        brief = investigate_ec2_cur(cur_path, month=month)
    except CurInvestigationError as exc:
        console.print(f"[red]Cannot investigate EC2 CUR data: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    delta_prefix = "+" if brief.delta >= 0 else "-"
    delta_abs = abs(brief.delta)
    pct = "n/a" if brief.delta_percent is None else f"{brief.delta_percent:+.1f}%"

    console.print()
    console.print("[bold]EC2 Investigation Brief[/bold]")
    console.print(f"Period: {brief.previous_period} -> {brief.current_period}")
    console.print(f"Previous period cost: ${brief.previous_cost:,.2f}")
    console.print(f"Current period cost:  ${brief.current_cost:,.2f}")
    console.print(f"Delta: {delta_prefix}${delta_abs:,.2f} ({pct})")
    console.print()

    accounts = Table(title="Top Contributing Accounts", show_lines=False)
    accounts.add_column("Account")
    accounts.add_column("Previous", justify="right")
    accounts.add_column("Current", justify="right")
    accounts.add_column("Delta", justify="right")
    for row in brief.top_accounts:
        accounts.add_row(
            row.name,
            f"${row.previous_cost:,.2f}",
            f"${row.current_cost:,.2f}",
            _format_money_delta(row.delta),
        )
    if brief.top_accounts:
        console.print(accounts)
        console.print()

    regions = Table(title="Top Contributing Regions", show_lines=False)
    regions.add_column("Region")
    regions.add_column("Previous", justify="right")
    regions.add_column("Current", justify="right")
    regions.add_column("Delta", justify="right")
    for row in brief.top_regions:
        regions.add_row(
            row.name,
            f"${row.previous_cost:,.2f}",
            f"${row.current_cost:,.2f}",
            _format_money_delta(row.delta),
        )
    if brief.top_regions:
        console.print(regions)
        console.print()

    resources = Table(title="Top Contributing Resources", show_lines=False)
    resources.add_column("Resource")
    resources.add_column("Previous", justify="right")
    resources.add_column("Current", justify="right")
    resources.add_column("Delta", justify="right")
    for row in brief.top_resources:
        resources.add_row(
            row.name,
            f"${row.previous_cost:,.2f}",
            f"${row.current_cost:,.2f}",
            _format_money_delta(row.delta),
        )
    console.print(resources)
    console.print()

    usage = Table(title="Top Usage Types", show_lines=False)
    usage.add_column("Usage type")
    usage.add_column("Previous", justify="right")
    usage.add_column("Current", justify="right")
    usage.add_column("Delta", justify="right")
    for row in brief.top_usage_types:
        usage.add_row(
            row.name,
            f"${row.previous_cost:,.2f}",
            f"${row.current_cost:,.2f}",
            _format_money_delta(row.delta),
        )
    console.print(usage)
    console.print()

    if brief.tag_coverage is not None:
        tag_table = Table(title="Current Period Tag Coverage", show_lines=False)
        tag_table.add_column("Status")
        tag_table.add_column("Cost", justify="right")
        tag_table.add_row("Tagged", f"${brief.tag_coverage.tagged_cost:,.2f}")
        tag_table.add_row("Untagged", f"${brief.tag_coverage.untagged_cost:,.2f}")
        console.print(tag_table)
        console.print()

        tag_values = Table(title="Observed Tag Values", show_lines=False)
        tag_values.add_column("Tag")
        tag_values.add_column("Values")
        for label, values in (
            ("Owner", brief.tag_coverage.owner_values),
            ("Team", brief.tag_coverage.team_values),
            ("Application", brief.tag_coverage.application_values),
            ("Cost center", brief.tag_coverage.cost_center_values),
            ("Environment", brief.tag_coverage.environment_values),
        ):
            if values:
                tag_values.add_row(label, ", ".join(values))
        if tag_values.row_count:
            console.print(tag_values)
            console.print()

    console.print("[bold]Evidence Available[/bold]")
    for item in brief.evidence_available:
        console.print(f"[green][available][/green] {item.label}: {item.detail}")
    console.print()

    console.print("[bold]Evidence Missing[/bold]")
    for item in brief.evidence_missing:
        console.print(f"[yellow][missing][/yellow] {item.label}: {item.detail}")
    console.print()

    console.print("[bold]Review Questions[/bold]")
    for index, question in enumerate(brief.review_questions, start=1):
        console.print(f"{index}. {question}")
    console.print()


def _format_money_delta(value: float) -> str:
    prefix = "+" if value >= 0 else "-"
    return f"{prefix}${abs(value):,.2f}"


@main.command()
@click.option("--input", "-i", "input_file", required=True, type=click.Path(exists=True), help="Path to a previous JSON scan result.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["terminal", "html", "json", "sarif", "csv"]),
    default="terminal",
    help="Output format to convert to.",
)
@click.option("--output", "-o", type=click.Path(), default=None, help="Write output to file.")
@click.option("--show-pii", is_flag=True, default=False, help="Show full account IDs and PII.")
def convert(input_file: str, fmt: str, output: Optional[str], show_pii: bool) -> None:
    """Re-render a previous JSON scan into a different format (no re-scan needed)."""
    from rich.console import Console as RichConsole

    console = RichConsole()

    with open(input_file, "r") as f:
        payload = json.load(f)

    # Extract data from the saved payload
    results = payload.get("tools", {})
    overall_score = payload.get("overall_score", 0)
    overall_grade = payload.get("overall_grade", "?")
    account_id = payload.get("account_id", "unknown")
    regions = payload.get("regions", [])
    duration = payload.get("duration_seconds", 0)
    top_actions = payload.get("top_actions", [])

    all_findings = []
    for pack_result in results.values():
        if isinstance(pack_result, dict):
            all_findings.extend(pack_result.get("findings", []))

    from kulshan.orchestrator import summarize_completeness

    scan_metadata = payload.get("scan_metadata") or summarize_completeness(results)
    _emit_output(
        fmt=fmt, results=results, overall_score=overall_score,
        overall_grade=overall_grade, account_id=account_id,
        regions=regions, duration=duration, top_actions=top_actions,
        all_findings=all_findings, output=output, show_pii=show_pii,
        scan_metadata=scan_metadata, console=console,
    )


@main.command()
@click.pass_context
def shell(ctx: click.Context) -> None:
    """Launch interactive REPL shell."""
    try:
        from kulshan.repl import run_repl
    except ImportError:
        click.echo(
            "Error: prompt_toolkit is required for the interactive shell.\n"
            "Install it with: pip install 'prompt_toolkit>=3.0'",
            err=True,
        )
        raise SystemExit(1)

    profile = ctx.obj.get("profile")
    role_arn = ctx.obj.get("role_arn")
    run_repl(cli_group=main, profile=profile, role_arn=role_arn)


@main.command()
@click.option("--force", is_flag=True, help="Overwrite existing config file.")
def init(force: bool) -> None:
    """Generate a starter config.toml in the current directory."""
    import os
    config_path = os.path.join(os.getcwd(), "kulshan.toml")
    if os.path.exists(config_path) and not force:
        click.echo(f"Config already exists at {config_path}. Use --force to overwrite.", err=True)
        raise SystemExit(1)

    config_content = '''# Kulshan Configuration
# Generated by: kulshan init
# Docs: https://missionfinops.com/docs/cli

[aws]
# profile = "default"
# role_arn = "arn:aws:iam::123456789012:role/KulshanAudit"
# regions = ["us-east-1", "us-west-2", "eu-west-1"]

[output]
# default_format = "terminal"   # terminal, html, json
# report_dir = "."

[scan]
# quick_regions = ["us-east-1", "us-west-2", "eu-west-1"]
# days = 30                     # cost lookback period
# show_pii = false              # redact PII in exports by default

[history]
# retention_days = 365
# db_path = "~/.local/share/Kulshan/history.db"
'''

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(config_content)
    click.echo(f"Created {config_path}")


@main.command()
@click.option("--limit", "-n", default=20, type=int, help="Number of past scans to show.")
@click.option("--show-pii", is_flag=True, default=False, help="Show full account IDs (redacted by default).")
def history(limit: int, show_pii: bool) -> None:
    """Show past scan history with scores and trends."""
    from rich.console import Console as RichConsole
    from rich.table import Table
    from kulshan.redact import redact_account_id

    console = RichConsole()

    try:
        from kulshan.history import HistoryStore
        store = HistoryStore()
        scans = store.list_scans(limit=limit)
        store.close()
    except Exception as e:
        console.print(f"[red]Could not read history: {e}[/red]")
        raise SystemExit(1)

    if not scans:
        console.print("[dim]No scan history found. Run 'Kulshan Report' to create your first scan.[/dim]")
        return

    table = Table(title="Scan History", show_lines=False)
    table.add_column("ID", style="dim")
    table.add_column("Date", style="cyan")
    table.add_column("Account")
    table.add_column("Score", justify="right")
    table.add_column("Grade", justify="center")
    table.add_column("Findings", justify="right")
    table.add_column("Crit", justify="right", style="red")
    table.add_column("High", justify="right", style="bright_red")
    table.add_column("Duration", justify="right", style="dim")

    for scan in scans:
        ts = scan.get("timestamp", "")[:16].replace("T", " ")
        raw_account = scan.get("account_id", "?")
        display_account = raw_account if show_pii else redact_account_id(raw_account)
        table.add_row(
            scan.get("id", "?"),
            ts,
            display_account,
            str(scan.get("overall_score", 0)),
            scan.get("overall_grade", "?"),
            str(scan.get("total_findings", 0)),
            str(scan.get("critical_findings", 0)),
            str(scan.get("high_findings", 0)),
            f"{scan.get('duration_seconds', 0):.0f}s",
        )

    console.print(table)


@main.command("delete-history")
@click.option("--yes", is_flag=True, help="Delete without an interactive confirmation.")
def delete_history(yes: bool) -> None:
    """Permanently delete all locally stored scan history."""
    from kulshan.history import HistoryStore, get_history_db_path

    if not yes and not click.confirm("Delete all locally stored Kulshan scan history?"):
        click.echo("History was not deleted.")
        return

    store = HistoryStore()
    deleted = store.delete_all()
    store.close()
    click.echo(f"Deleted {deleted} scan(s) from {get_history_db_path()}")


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check AWS connectivity and readiness without running a scan.

    \b
    Validates:
      - AWS credentials are configured
      - STS caller identity resolves
      - Cost Explorer API is reachable
      - Required permissions are present

    No data is written. No cost is incurred. Safe to run repeatedly.
    """
    from rich.console import Console as RichConsole
    from kulshan.session import create_session
    from kulshan.preflight import run_preflight

    console = RichConsole()
    profile = ctx.obj.get("profile")
    role_arn = ctx.obj.get("role_arn")

    console.print()
    console.print("  [bold]Kulshan Doctor[/bold] — checking AWS readiness")
    console.print()

    try:
        session = create_session(profile=profile, role_arn=role_arn)
    except Exception as e:
        console.print(f"  [red]✗ Cannot create AWS session: {e}[/red]")
        console.print()
        console.print("  [dim]Check: AWS credentials configured? Try: aws sts get-caller-identity[/dim]")
        sys.exit(ExitCode.CONFIG_ERROR)

    passed, warnings = run_preflight(session, console=console)

    if passed:
        console.print()
        console.print("  [green bold]✓ All checks passed.[/green bold] Ready to run [green]kulshan report[/green].")
    else:
        console.print()
        console.print("  [red bold]✗ Pre-flight checks failed.[/red bold] Fix the issues above and retry.")
        sys.exit(ExitCode.CONFIG_ERROR)

    console.print()


# -- Wire up tab completion, ? help, theming, and Rich help formatting --
from kulshan.setup import setup_cli  # noqa: E402

setup_cli(main, "Kulshan", "Local-first AWS FinOps baseline.", __version__)
