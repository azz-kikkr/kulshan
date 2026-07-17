# ruff: noqa: E501, I001, UP045, UP015, B904, W293
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
    history_db_path: Optional[str] = None,
    coverage: Optional[dict] = None,
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
            "coverage": coverage,
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
            history_db_path=history_db_path,
        )
        # Coverage summary
        if coverage:
            summary = coverage.get("summary", {})
            status = summary.get("report_status", "complete")
            packs_str = f"{summary.get('packs_completed', 0)}/{summary.get('packs_attempted', 0)} packs"
            regions_str = f"{summary.get('regions_scanned', 0)} regions"
            denied = coverage.get("denied_actions", [])
            if status == "complete":
                console.print(f"  [dim]Coverage: {packs_str}, {regions_str}[/dim]")
            elif status == "partial":
                console.print(f"  [yellow]Coverage: {packs_str}, {regions_str}, {len(denied)} denied[/yellow]")
            else:
                console.print(f"  [red]Coverage: {packs_str}, {regions_str} (incomplete)[/red]")
            console.print()


def _offer_reconciliation_if_needed(ws_ctx, payer_account_id, console) -> None:
    """Check if another workspace has the same payer and offer to reconcile.

    Called after a successful payer binding. Non-blocking — if the user
    declines, investigation continues normally.
    """
    from kulshan.workspace.reconcile import find_workspaces_for_payer, reconcile_workspace, ReconcileError
    from kulshan.redact import redact_account_id

    try:
        matches = find_workspaces_for_payer(payer_account_id)
        # Filter out the current workspace
        others = [w for w in matches if w.name != ws_ctx.name]

        if not others:
            return

        # There's already another workspace with this payer
        target = others[0]
        payer_display = redact_account_id(payer_account_id)

        console.print(
            f"  [yellow]This payer is already known.[/yellow]",
            highlight=False,
        )
        console.print(
            f"  Existing environment: [cyan]{target.display_name}[/cyan]",
            highlight=False,
        )
        console.print()
        console.print(
            f"  Link this identity to [cyan]{target.display_name}[/cyan]? [y/N] ",
            highlight=False,
            end="",
        )

        import click as _click
        confirm = _click.confirm("", default=False, prompt_suffix="")
        if not confirm:
            console.print("  [dim]Skipped. Environments remain separate.[/dim]")
            console.print()
            return

        result = reconcile_workspace(
            source_workspace=ws_ctx.name,
            target_workspace=target.name,
        )
        console.print(f"  [green]✓[/green] {result.message}")
        console.print()
    except Exception as e:
        # Reconciliation is non-critical — log and continue
        import logging
        logging.getLogger("kulshan.reconcile").debug("Reconciliation check failed: %s", e)


def _validate_workspace_name_callback(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Validate workspace name from --workspace option."""
    if value is None:
        return None
    from kulshan.workspace.validation import validate_workspace_name
    from kulshan.workspace.errors import WorkspaceValidationError
    try:
        validate_workspace_name(value, allow_default=True)
        return value
    except WorkspaceValidationError as e:
        raise click.BadParameter(str(e))


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="kulshan")
@click.option("--profile", default=None, help="AWS CLI profile name.")
@click.option("--role-arn", default=None, help="IAM role ARN to assume.")
@click.option(
    "--workspace", "-w",
    default=None,
    callback=_validate_workspace_name_callback,
    help="Workspace name for multi-payer isolation. Overrides KULSHAN_WORKSPACE env var.",
)
@click.option(
    "--connection", "-c",
    default=None,
    help="Named AWS connection within the workspace.",
)
@click.pass_context
def main(
    ctx: click.Context,
    profile: Optional[str],
    role_arn: Optional[str],
    workspace: Optional[str],
    connection: Optional[str],
) -> None:
    """Kulshan: local-first AWS FinOps baseline."""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    ctx.obj["role_arn"] = role_arn
    ctx.obj["workspace"] = workspace
    ctx.obj["connection"] = connection

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
            "  [green]kulshan preflight[/green]            Check AWS connectivity\n"
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

    # ── Workspace-aware execution context ────────────────────────────────
    # Pre-resolve regions for consolidated path (which exits before normal region selection)
    regions: list = [r.strip() for r in region_override.split(",")] if region_override else []

    from kulshan.workspace.resolution import (
        resolve_workspace as _resolve_ws,
        resolve_workspace_with_profile,
    )
    from kulshan.workspace.execution import resolve_aws_execution, AwsExecutionContext
    from kulshan.workspace.onboarding import auto_onboard, OnboardingError
    from kulshan.workspace.sts import StsVerificationError
    from kulshan.workspace.errors import WorkspaceError

    workspace_name = ctx.obj.get("workspace")
    connection_name = ctx.obj.get("connection")

    # Determine effective profile for onboarding-aware resolution
    effective_profile = profile or os.environ.get("AWS_PROFILE")

    # Try onboarding-aware resolution first
    ws_ctx = None
    onboarding_result = None

    if workspace_name or connection_name:
        # Explicit workspace/connection — use traditional resolution
        try:
            ws_ctx = _resolve_ws(workspace_name)
        except WorkspaceError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)
    else:
        # Automatic routing: check registry, possibly auto-onboard
        try:
            ws_ctx = resolve_workspace_with_profile(
                workspace_name=None,
                profile=effective_profile,
                role_arn=role_arn,
            )
        except WorkspaceError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)

        if ws_ctx is None and effective_profile:
            # Profile supplied but no registry match — auto-onboard
            try:
                onboarding_result = auto_onboard(
                    profile=effective_profile,
                    role_arn=role_arn,
                )
                ws_ctx = onboarding_result.workspace_context
                if onboarding_result.is_new:
                    console.print(
                        f"  [green]✓[/green] Created environment "
                        f"[cyan]{onboarding_result.display_name}[/cyan]",
                        highlight=False,
                    )
                console.print(
                    f"  Using [cyan]{onboarding_result.display_name}[/cyan] "
                    f"[dim]· account {onboarding_result.account_id[:4]}…{onboarding_result.account_id[-4:]}[/dim]",
                    highlight=False,
                )
                console.print()
            except StsVerificationError as e:
                console.print(f"[red]{e}[/red]")
                sys.exit(ExitCode.CONFIG_ERROR)
            except OnboardingError as e:
                console.print(f"[red]Environment onboarding failed: {e}[/red]")
                sys.exit(ExitCode.CONFIG_ERROR)
        elif ws_ctx is None:
            # No profile, no registered workspaces — auto-onboard using default credentials
            try:
                onboarding_result = auto_onboard(
                    profile=None,
                    role_arn=role_arn,
                )
                ws_ctx = onboarding_result.workspace_context
                if onboarding_result.is_new:
                    console.print(
                        f"  [green]✓[/green] Created environment "
                        f"[cyan]{onboarding_result.display_name}[/cyan]",
                        highlight=False,
                    )
                console.print(
                    f"  Using [cyan]{onboarding_result.display_name}[/cyan] "
                    f"[dim]· account {onboarding_result.account_id[:4]}…{onboarding_result.account_id[-4:]}[/dim]",
                    highlight=False,
                )
                console.print()
            except StsVerificationError as e:
                # No valid credentials at all — fall back to unbound default
                try:
                    ws_ctx = _resolve_ws(None)
                except WorkspaceError as ws_e:
                    console.print(f"[red]{ws_e}[/red]")
                    sys.exit(ExitCode.CONFIG_ERROR)
            except OnboardingError as e:
                console.print(f"[red]Environment onboarding failed: {e}[/red]")
                sys.exit(ExitCode.CONFIG_ERROR)

    if ws_ctx.is_bound and onboarding_result is None:
        # Bound workspace (explicit or found via registry)
        has_multi_connections = (
            ws_ctx.config.aws is not None
            and len(ws_ctx.config.aws.connections) > 1
            and not connection_name
        )

        if has_multi_connections:
            # Multi-connection consolidated report
            from kulshan.consolidated import (
                run_consolidated_report,
                DefaultConnectionFailedError,
                NoSuccessfulConnectionsError,
            )
            from kulshan.redact import redact_account_id

            conn_dicts = [
                {
                    "name": c.name,
                    "profile": c.profile,
                    "role_arn": c.role_arn,
                    "expected_session_account_id": c.expected_session_account_id,
                }
                for c in ws_ctx.config.aws.connections
            ]

            console.print(
                f"  [bold]Consolidated report[/bold] · "
                f"{len(conn_dicts)} connections · {ws_ctx.display_name}",
                highlight=False,
            )
            console.print()

            try:
                consolidated = run_consolidated_report(
                    connections=conn_dicts,
                    regions=regions,
                    selected_packs=selected_packs,
                    payer_account_id=ws_ctx.payer_account_id,
                    cost_connection_name=ws_ctx.config.aws.cost_connection if ws_ctx.config.aws else None,
                    quick=quick,
                    deep=deep,
                    days=days,
                    console=console,
                )
            except DefaultConnectionFailedError as e:
                console.print(f"[red]{e}[/red]")
                console.print()
                console.print(
                    f"  [dim]Authenticate connection '{e.connection_name}' "
                    f"and run the report again.[/dim]"
                )
                sys.exit(ExitCode.CONFIG_ERROR)
            except NoSuccessfulConnectionsError as e:
                console.print(f"[red]{e}[/red]")
                sys.exit(ExitCode.CONFIG_ERROR)

            # Use consolidated results
            results = consolidated.results
            all_findings = consolidated.all_findings
            overall_score = consolidated.overall_score
            overall_grade = consolidated.overall_grade
            session = consolidated.successful_connections[0] if consolidated.successful_connections else None
            # For consolidated scans, use payer_account_id for display; account_id in DB is NULL
            account_id = consolidated.payer_account_id or (
                consolidated.accounts_observed[0] if consolidated.accounts_observed else "unknown"
            )
            duration = consolidated.duration_seconds

            # Coverage summary
            status_label = consolidated.report_status.capitalize()
            cost_coverage_label = {
                "verified_payer_wide": "Verified payer-wide",
                "account_scoped": "Account-scoped (not payer-wide)",
                "unavailable": "Unavailable",
                "cur_authoritative": "CUR authoritative",
            }.get(consolidated.payer_cost_coverage, "Unknown")
            conn_names = ", ".join(c.connection_name for c in consolidated.successful_connections)
            accts = ", ".join(
                redact_account_id(a) if not show_pii else a
                for a in consolidated.accounts_observed
            )
            console.print(f"  Report status: [bold]{status_label}[/bold]")
            console.print(f"  Payer cost coverage: {cost_coverage_label}")
            console.print(f"  Connections: {conn_names}")
            console.print(f"  Accounts verified: {accts}")
            console.print()

            if consolidated.payer_cost_coverage == "unavailable" and "cost" in selected_packs:
                console.print(
                    "  [dim]No payer-wide cost authority available. "
                    "Supply verified CUR for payer-wide cost evidence.[/dim]",
                    highlight=False,
                )
                console.print()

            if consolidated.failed_connections:
                for fc in consolidated.failed_connections:
                    console.print(
                        f"  [yellow]Connection \"{fc.connection_name}\" is unavailable.[/yellow]",
                        highlight=False,
                    )
                console.print(
                    f"  [dim]Authenticate that AWS login and run the report again.[/dim]",
                    highlight=False,
                )
                console.print()

            # Save history atomically
            if not no_history:
                try:
                    from kulshan.history import HistoryStore
                    history = HistoryStore(ws_ctx.history_db_path)
                    history.purge_old(retention_days=365)
                    scan_id = history.save_consolidated_scan(
                        regions=regions,
                        duration_seconds=duration,
                        overall_score=overall_score,
                        overall_grade=overall_grade,
                        results=results,
                        findings=all_findings,
                        report_status=consolidated.report_status,
                        payer_account_id=consolidated.payer_account_id,
                        connections=[
                            {
                                "connection_name": ce.connection_name,
                                "profile": ce.profile,
                                "session_account_id": ce.session_account_id,
                                "role_arn": ce.role_arn,
                                "status": ce.status,
                                "duration_seconds": ce.duration_seconds,
                                "packs_attempted": ce.packs_attempted,
                                "packs_completed": ce.packs_completed,
                                "error_code": ce.error_code,
                            }
                            for ce in consolidated.connections_executed
                        ],
                        version=__version__,
                    )
                    history.close()
                except Exception:
                    pass

            # Compute top actions and metadata for output
            from kulshan.findings_processor import process_findings
            top_actions = process_findings(all_findings)[:10] if all_findings else []
            scan_metadata = {
                "report_status": consolidated.report_status,
                "payer_cost_coverage": consolidated.payer_cost_coverage,
                "connections": [
                    {
                        "name": ce.connection_name,
                        "account_id": ce.session_account_id,
                        "status": ce.status,
                    }
                    for ce in consolidated.connections_executed
                ],
                "total_connections": len(consolidated.connections_executed),
                "successful_connections": len(consolidated.successful_connections),
                "payer_connection": consolidated.payer_connection,
                "payer_account_id": consolidated.payer_account_id,
            }

            _emit_output(
                fmt=fmt, results=results, overall_score=overall_score,
                overall_grade=overall_grade, account_id=account_id,
                regions=regions,
                duration=duration, top_actions=top_actions,
                all_findings=all_findings, output=output, show_pii=show_pii,
                scan_metadata=scan_metadata, console=console,
                history_db_path=ws_ctx.history_db_path,
            )

            has_critical = any(
                r.get("scores", {}).get("severity_counts", {}).get("critical", 0) > 0
                for r in results.values()
            )
            sys.exit(ExitCode.FINDING_FAIL if has_critical else ExitCode.SUCCESS)

        else:
            # Single-connection bound workspace: traditional execution
            try:
                exec_ctx = resolve_aws_execution(
                    workspace=ws_ctx,
                    connection_name=connection_name,
                    profile=profile,
                    role_arn=role_arn,
                    show_pii=show_pii,
                )
                session = exec_ctx.session
                account_id = exec_ctx.session_account_id
            except (WorkspaceError, StsVerificationError) as e:
                from kulshan.redact import redact_account_id, redact_arn
                from kulshan.workspace.errors import WorkspaceCredentialMismatchError
                if isinstance(e, WorkspaceCredentialMismatchError) and not show_pii:
                    console.print(
                        f"[red]Credential mismatch for workspace '{e.workspace_name}': "
                        f"expected account {redact_account_id(e.expected_account)}, "
                        f"got {redact_account_id(e.actual_account)}.[/red]"
                    )
                else:
                    console.print(f"[red]{e}[/red]")
                sys.exit(ExitCode.CONFIG_ERROR)

            # Show routing message for auto-onboarded workspaces found via registry
            if not workspace_name and not connection_name:
                display = ws_ctx.display_name
                acct = exec_ctx.session_account_id
                console.print(
                    f"  Using [cyan]{display}[/cyan] "
                    f"[dim]· account {acct[:4]}…{acct[-4:]}[/dim]",
                    highlight=False,
                )
                console.print()
    elif onboarding_result is not None:
        # Session already verified during onboarding — reuse it
        session = onboarding_result.verified_session.session
        account_id = onboarding_result.verified_session.account_id
    else:
        # Unbound default fallback (STS failed without credentials)
        try:
            exec_ctx = resolve_aws_execution(
                workspace=ws_ctx,
                profile=profile,
                role_arn=role_arn,
            )
            session = exec_ctx.session
            account_id = exec_ctx.session_account_id
        except (WorkspaceError, StsVerificationError) as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)

    # Pre-flight health check (with CUR discovery)
    from kulshan.preflight import run_preflight_with_cur
    preflight_result = run_preflight_with_cur(session, console=console)
    if not preflight_result.passed:
        console.print("[red]Pre-flight checks failed. Fix the issues above and retry.[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    # ── CUR/Data Export offer ────────────────────────────────────────────
    # If CUR is available and cost pack is selected, offer to use it
    use_cur = None  # None = no CUR, "additive" = CE + CUR, "only" = CUR only
    cur_s3_uri = None
    has_cost_pack = "cost" in selected_packs
    if (
        preflight_result.cur_export
        and preflight_result.cur_accessible
        and has_cost_pack
        and not yes
        and not quick
    ):
        console.print()
        console.print(
            f"  [cyan bold]⬢ CUR/Data Export available[/cyan bold]"
        )
        console.print(
            f"    [dim]CUR provides richer line-item detail for cost analysis.[/dim]"
        )
        console.print()
        console.print("    [bold]1.[/bold] Cost Explorer only (default)")
        console.print("    [bold]2.[/bold] Cost Explorer + CUR (adds top-mover detail)")
        console.print("    [bold]3.[/bold] CUR only (skips CE API, saves ~$0.15)")
        console.print()
        choice = click.prompt(
            "  Select mode",
            type=click.Choice(["1", "2", "3"]),
            default="1",
            show_choices=False,
        )
        if choice == "2":
            use_cur = "additive"
            cur_s3_uri = preflight_result.cur_export.s3_uri
            console.print(f"    [green]→ CE + CUR mode[/green]")
        elif choice == "3":
            use_cur = "only"
            cur_s3_uri = preflight_result.cur_export.s3_uri
            console.print(f"    [green]→ CUR only mode (no CE API calls)[/green]")
        console.print()

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
    if use_cur == "only":
        # CUR-only mode skips CE entirely
        console.print("  [green]AWS API cost: ~$0.00[/green] [dim](CUR only — no CE API calls)[/dim]")
        console.print()
    elif not yes and not quick and has_cost_pack:
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

    # ── CUR-only mode: skip CE, run CUR analysis directly ───────────
    if use_cur == "only":
        from kulshan.cur.s3_query import connect_s3_duckdb, analyze_cost_s3
        from kulshan.cur.manifest_reader import read_manifest_uri
        from datetime import date
        
        console.print("  [cyan]Running CUR-only cost analysis...[/cyan]")
        start = time.time()
        
        try:
            # Determine current month for analysis
            current_month = date.today().strftime("%Y-%m")
            
            # Connect to S3 and read manifest
            con = connect_s3_duckdb()
            manifest = read_manifest_uri(cur_s3_uri, billing_period=current_month)
            
            # Run CUR analysis from S3
            brief = analyze_cost_s3(con, manifest, current_month)
            con.close()
            duration = time.time() - start
            
            # Convert CUR brief to report-compatible format
            results = {
                "cost": {
                    "tool": "cost",
                    "mode": "cur_only",
                    "scores": {
                        "overall_score": 50,  # Neutral score for CUR-only
                        "grade": "N/A",
                        "total_findings": 0,
                        "severity_counts": {},
                        "total_spend": brief.total_spend,
                    },
                    "findings": [],
                    "errors": [],
                    "metadata": {
                        "cur_investigation": {
                            "month": current_month,
                            "total_spend": brief.total_spend,
                            "cost_column": brief.cost_column,
                            "fallback_note": brief.fallback_note,
                            "top_services": list(brief.top_services[:5]),
                            "top_accounts": list(brief.top_accounts[:5]),
                            "top_regions": list(brief.top_regions[:5]),
                            "top_usage_types": list(brief.top_usage_types[:5]),
                            "data_source": cur_s3_uri,
                            "scan_estimate_bytes": brief.estimate.estimated_bytes,
                        },
                    },
                }
            }
            
            console.print(f"  [green]✓[/green] CUR analysis complete ({duration:.1f}s)")
            console.print()
            console.print(f"    Month: {current_month}")
            console.print(f"    Total spend: ${brief.total_spend:,.2f}")
            console.print()
            
            if brief.top_services:
                console.print("    [bold]Top Services:[/bold]")
                for name, cost in brief.top_services[:5]:
                    console.print(f"      {name}: ${cost:,.2f}")
                console.print()
                
        except Exception as e:
            duration = time.time() - start
            console.print(f"  [red]✗[/red] CUR analysis failed: {e}")
            results = {
                "cost": {
                    "tool": "cost",
                    "mode": "cur_only",
                    "scores": {"overall_score": 0, "grade": "N/A", "total_findings": 0},
                    "findings": [],
                    "errors": [str(e)],
                    "skipped": True,
                }
            }
        
        # Skip the rest of normal report flow for CUR-only mode
        scan_metadata = {"partial": False, "skipped": [], "errors": []}
        overall_score, overall_grade = 50, "N/A"
        all_findings = []
        top_actions = []
        
    else:
        # ── Normal mode: run CE-based scans ──────────────────────────────
        start = time.time()
        results = run_all_scans(
            session=session, regions=regions, profile=profile, quick=quick, console=console,
            selected_packs=selected_packs, perf=perf, deep=deep, days=days,
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
        
        # ── Additive CUR mode: also run CUR analysis ────────────────
        if use_cur == "additive" and cur_s3_uri:
            try:
                from kulshan.cur.s3_query import connect_s3_duckdb, analyze_cost_s3
                from kulshan.cur.manifest_reader import read_manifest_uri
                from datetime import date
                
                console.print()
                console.print("  [cyan]Running supplementary CUR analysis...[/cyan]")
                
                current_month = date.today().strftime("%Y-%m")
                con = connect_s3_duckdb()
                manifest = read_manifest_uri(cur_s3_uri, billing_period=current_month)
                brief = analyze_cost_s3(con, manifest, current_month)
                con.close()
                
                # Add CUR data to cost pack metadata
                if "cost" in results:
                    results["cost"]["metadata"]["cur_investigation"] = {
                        "month": current_month,
                        "total_spend": brief.total_spend,
                        "cost_column": brief.cost_column,
                        "fallback_note": brief.fallback_note,
                        "top_services": list(brief.top_services[:5]),
                        "top_accounts": list(brief.top_accounts[:5]),
                        "top_regions": list(brief.top_regions[:5]),
                        "data_source": cur_s3_uri,
                    }
                
                console.print(f"  [green]✓[/green] CUR enrichment added")
            except Exception as e:
                console.print(f"  [yellow]⚠[/yellow] CUR enrichment failed: {e}")
                # Non-fatal — CE results still valid

    if not no_history:
        try:
            from kulshan.history import HistoryStore

            history = HistoryStore(ws_ctx.history_db_path)
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

    # Build coverage report
    from kulshan.coverage import build_coverage_from_results
    coverage_report = build_coverage_from_results(
        results=results, regions=regions,
        duration_seconds=duration, account_id=account_id,
    )

    _emit_output(
        fmt=fmt, results=results, overall_score=overall_score,
        overall_grade=overall_grade, account_id=account_id,
        regions=regions, duration=duration, top_actions=top_actions,
        all_findings=all_findings, output=output, show_pii=show_pii,
        scan_metadata=scan_metadata, console=console,
        history_db_path=ws_ctx.history_db_path,
        coverage=coverage_report.to_dict(),
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
    """Inspect CUR/Data Exports evidence."""


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
    required=False,
    type=click.Path(exists=True, file_okay=True, dir_okay=True),
    help="Local CUR/Data Exports Parquet file or directory.",
)
@click.option(
    "--s3",
    "s3_uri",
    required=False,
    help="S3 CUR/Data Export prefix for manifest/schema validation.",
)
@click.option(
    "--month",
    required=False,
    help="Billing month for S3 manifest validation in YYYY-MM format.",
)
def cur_validate(cur_path: str | None, s3_uri: str | None, month: str | None) -> None:
    """Validate local Parquet or S3 manifest/schema evidence."""
    from rich.console import Console as RichConsole
    from rich.table import Table

    from kulshan.cur.errors import CurDataError
    from kulshan.cur.manifest_reader import CurManifestError, read_manifest_uri
    from kulshan.cur.validation import validate_local_cur

    console = RichConsole()
    if bool(cur_path) == bool(s3_uri):
        console.print("[red]Provide exactly one of --path or --s3.[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    if s3_uri:
        if not month:
            console.print("[red]S3 manifest validation requires --month YYYY-MM.[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)
        try:
            manifest = read_manifest_uri(s3_uri, billing_period=month)
        except CurManifestError as exc:
            console.print(f"[red]S3 manifest validation failed: {exc}[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)
        console.print("[green]S3 manifest validation passed.[/green]")
        console.print("[dim]S3 manifest/schema validation only; no Parquet data was downloaded or queried.[/dim]")
        table = Table(title="S3 CUR Manifest", show_lines=False)
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("manifest read", "yes")
        table.add_row("manifest key", manifest.manifest_key)
        table.add_row("manifest size", str(manifest.manifest_size_bytes))
        table.add_row("billing period", manifest.billing_period or month)
        table.add_row("file count", str(len(manifest.files)))
        table.add_row("total export size", str(manifest.total_size_bytes))
        table.add_row("columns", str(len(manifest.columns)))
        console.print(table)
        if manifest.columns:
            console.print("Semantic columns from manifest:")
            console.print(", ".join(manifest.columns[:25]))
        return

    try:
        report = validate_local_cur(cur_path or "")
    except CurDataError as exc:
        console.print(f"[red]CUR validation failed: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)
    except Exception as exc:
        console.print(f"[red]CUR validation failed: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    console.print("[green]CUR validation passed.[/green]")
    table = Table(title="CUR/Data Export Validation", show_lines=False)
    table.add_column("Check")
    table.add_column("Result")
    table.add_row("readable", "yes" if report.readable else "no")
    table.add_row("row count", str(report.row_count))
    table.add_row("column count", str(report.column_count))
    table.add_row("semantic fields found", ", ".join(report.semantic_fields))
    table.add_row("selected cost column", report.selected_cost_column)
    table.add_row("fallback note", report.fallback_note or "none")
    table.add_row("EC2 rows", "yes" if report.ec2_rows else "no")
    table.add_row("network usage patterns", "yes" if report.network_usage_patterns else "no")
    table.add_row("Bedrock rows", "yes" if report.bedrock_rows else "no")
    console.print(table)
    console.print(f"[dim]EC2 rows: {'yes' if report.ec2_rows else 'no'}[/dim]")
    _print_count_table(console, "Top Product Codes", report.top_product_codes)
    _print_count_table(console, "Top Usage Types", report.top_usage_types)


def _print_count_table(console, title: str, rows: tuple[tuple[str, int], ...]) -> None:
    from rich.table import Table

    table = Table(title=title, show_lines=False)
    table.add_column("Value")
    table.add_column("Rows", justify="right")
    for value, count in rows:
        table.add_row(value, str(count))
    console.print(table)

@cur.command("s3-check")
@click.option(
    "--s3",
    "s3_uri",
    required=True,
    help="S3 CUR/Data Export prefix to check, for example s3://bucket/prefix/.",
)
def cur_s3_check(s3_uri: str) -> None:
    """Check S3 CUR/Data Export readiness without downloading data."""
    from pathlib import PurePosixPath

    from rich.console import Console as RichConsole
    from rich.table import Table

    from kulshan.cur.s3_check import S3CheckError, check_s3_cur_layout

    console = RichConsole()
    try:
        report = check_s3_cur_layout(s3_uri)
    except S3CheckError as exc:
        console.print(f"[red]S3 readiness check failed: {exc}[/red]")
        if exc.kind == "invalid_s3_uri":
            console.print("[yellow]Use an s3://bucket/prefix/ path.[/yellow]")
        elif exc.kind == "list_access_denied":
            console.print(
                f"[yellow]Likely missing s3:ListBucket on arn:aws:s3:::{exc.bucket}.[/yellow]"
            )
            console.print(
                f"[dim]If scoped by prefix, allow s3:prefix matching {exc.prefix or '*'}.[/dim]"
            )
        elif exc.kind == "head_access_denied":
            console.print(
                f"[yellow]Likely missing s3:GetObject on arn:aws:s3:::{exc.bucket}/{exc.prefix}*.[/yellow]"
            )
            console.print(
                "[dim]kms:Decrypt may also be required for customer-managed KMS keys.[/dim]"
            )
        sys.exit(ExitCode.CONFIG_ERROR)

    table = Table(title="S3 CUR/Data Export Readiness", show_lines=False)
    table.add_column("Check")
    table.add_column("Result")
    table.add_row("S3 path", report.s3_uri)
    table.add_row("Can list prefix", "yes" if report.can_list_prefix else "no")
    table.add_row("metadata/ found", "yes" if report.metadata_prefix_found else "no")
    table.add_row("data/ found", "yes" if report.data_prefix_found else "no")
    table.add_row("Manifest found", "yes" if report.manifest_found else "no")
    table.add_row("Parquet found", "yes" if report.parquet_found else "no")
    table.add_row(
        "Billing periods found",
        ", ".join(report.billing_periods) if report.billing_periods else "none",
    )
    table.add_row(
        "Manifest readable",
        _object_readable_text(report.manifest.readable, report.manifest.size),
    )
    table.add_row(
        "Manifest access issue",
        _object_access_issue_text(report.manifest),
    )
    table.add_row(
        "Parquet readable",
        _object_readable_text(report.parquet.readable, report.parquet.size),
    )
    table.add_row(
        "Parquet access issue",
        _object_access_issue_text(report.parquet),
    )
    table.add_row("Total listed objects", str(report.total_listed_objects))
    table.add_row("Approximate listed bytes", str(report.approximate_listed_bytes))
    console.print(table)

    if report.has_head_access_denial:
        console.print("[red]S3 prefix is not ready for manual CUR validation.[/red]")
        for label, probe in (("Manifest", report.manifest), ("Parquet", report.parquet)):
            if probe.error_code:
                console.print(
                    f"[yellow]{label} HeadObject denied; likely missing "
                    f"{probe.likely_missing_action} on {probe.object_arn}.[/yellow]"
                )
                click.echo(
                    f"{label} HeadObject denied: likely missing "
                    f"{probe.likely_missing_action} on {probe.object_arn}"
                )
                console.print(f"[dim]{probe.kms_hint}[/dim]")
        sys.exit(ExitCode.CONFIG_ERROR)

    if not report.manifest_found or not report.parquet_found:
        console.print("[red]S3 prefix is not ready for manual CUR validation.[/red]")
        if not report.manifest_found:
            console.print("[yellow]No Manifest.json found in the first 50 listed objects.[/yellow]")
        if not report.parquet_found:
            console.print("[yellow]No .parquet file found in the first 50 listed objects.[/yellow]")
        console.print("[dim]Check the bucket, prefix, export name, and billing period path.[/dim]")
        sys.exit(ExitCode.CONFIG_ERROR)

    if report.ready_for_manual_copy and report.parquet.key:
        filename = PurePosixPath(report.parquet.key).name
        console.print("[green]S3 prefix is ready for manual local CUR validation.[/green]")
        console.print("Next manual copy command:")
        click.echo(
            f"aws s3 cp s3://{report.bucket}/{report.parquet.key} "
            f"./.kulshan-real-cur-test/{filename}"
        )
    else:
        console.print("[red]S3 prefix is not ready for manual CUR validation.[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)


def _object_readable_text(readable: bool, size: int | None) -> str:
    if not readable:
        return "no"
    if size is None:
        return "yes"
    return f"yes ({size} bytes)"


def _object_access_issue_text(probe) -> str:
    if not probe.error_code:
        return "none"
    return (
        f"operation={probe.operation}; likely missing {probe.likely_missing_action}; "
        f"object ARN={probe.object_arn}; {probe.kms_hint}"
    )
@main.group()
def analyze() -> None:
    """Analyze a specific cloud cost movement from local evidence."""



@analyze.command("cost")
@click.option(
    "--s3",
    "s3_uri",
    required=False,
    help="S3 CUR/Data Export prefix to query with DuckDB httpfs.",
)
@click.option(
    "--path",
    "cur_path",
    required=False,
    type=click.Path(exists=True, file_okay=True, dir_okay=True),
    help="Local CUR/Data Export Parquet file or directory.",
)
@click.option(
    "--month",
    required=True,
    help="Billing month to analyze in YYYY-MM format.",
)
@click.option(
    "--confirm-scan",
    is_flag=True,
    default=False,
    help="Confirm S3 Parquet scan when the estimate exceeds the configured threshold.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write analysis output to .json or .md.",
)
@click.pass_context
def analyze_cost(
    ctx: click.Context,
    s3_uri: str | None,
    cur_path: str | None,
    month: str,
    confirm_scan: bool,
    output: str | None,
) -> None:
    """Analyze generic monthly cost movement from CUR/Data Export evidence."""
    from rich.console import Console as RichConsole

    from kulshan.cur.errors import CurDataError
    from kulshan.cur.manifest_reader import CurManifestError, read_manifest_uri
    from kulshan.cur.s3_query import (
        connect_s3_duckdb,
        cur_columns,
        estimate_scan_bytes,
        analyze_cost_s3,
        select_cost_column,
    )
    from kulshan.analyze.export import (
        cost_result_to_json,
        cost_result_to_markdown,
        investigation_format_from_path,
    )

    console = RichConsole()
    export_format = None
    if output:
        try:
            export_format = investigation_format_from_path(output)
        except ValueError as exc:
            click.echo(str(exc), err=True)
            sys.exit(ExitCode.CONFIG_ERROR)

    if bool(s3_uri) == bool(cur_path):
        console.print("[red]Provide exactly one source: --s3 s3://bucket/prefix/ or --path ./cur/.[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    # Local CUR analysis
    if cur_path:
        from kulshan.analyze.cost import analyze_cost_cur
        from kulshan.analyze.export import (
            export_brief,
            investigation_format_from_path,
        )

        export_format = None
        if output:
            try:
                export_format = investigation_format_from_path(output)
            except ValueError as exc:
                click.echo(str(exc), err=True)
                sys.exit(ExitCode.CONFIG_ERROR)

        # Workspace payer validation (no AWS calls)
        from kulshan.workspace.resolution import resolve_workspace as _resolve_ws
        from kulshan.workspace.errors import WorkspaceError
        from kulshan.cur.duckdb_engine import connect_memory, register_cur_raw
        from kulshan.cur.source import local_parquet_source
        from kulshan.cur.payer_validation import (
            validate_cur_payer,
            PayerMismatchError,
            MultiplePayersError,
        )
        from kulshan.redact import redact_account_id

        try:
            ws_ctx = _resolve_ws(ctx.obj.get("workspace"))  # resolve workspace (no AWS)
        except WorkspaceError:
            ws_ctx = None

        expected_payer = None
        ws_name = None
        if ws_ctx and ws_ctx.is_bound and ws_ctx.config.aws:
            expected_payer = ws_ctx.config.aws.payer_account_id
            ws_name = ws_ctx.name

        if ws_ctx and ws_ctx.is_bound:
            # Attempt payer binding or validation from CUR evidence
            from kulshan.workspace.payer_binding import (
                try_bind_payer_from_cur,
                InvalidPayerEvidenceError,
                MultiplePayerEvidenceError,
            )
            from kulshan.workspace.onboarding import PayerBindingConflictError

            try:
                _payer_con = connect_memory()
                _payer_source = local_parquet_source(str(cur_path))
                register_cur_raw(_payer_con, _payer_source)

                if expected_payer:
                    # Already bound — validate
                    payer_result = validate_cur_payer(_payer_con, expected_payer, ws_name)
                    if payer_result.status == "missing":
                        console.print(
                            f"  [yellow]Warning:[/yellow] {payer_result.message}"
                        )
                        console.print(
                            f"  [dim]Continuing with unverified local input.[/dim]"
                        )
                        console.print()
                else:
                    # Unverified payer — attempt binding
                    binding_result = try_bind_payer_from_cur(_payer_con, ws_ctx)
                    if binding_result.status == "bound":
                        console.print(
                            f"  [green]✓[/green] {binding_result.message}",
                            highlight=False,
                        )
                        console.print()
                        # Check for payer conflict after binding
                        _offer_reconciliation_if_needed(
                            ws_ctx, binding_result.payer_account_id, console
                        )
                    elif binding_result.status == "missing":
                        console.print(
                            f"  [yellow]Warning:[/yellow] {binding_result.message}",
                            highlight=False,
                        )
                        console.print()

                _payer_con.close()
            except PayerMismatchError as e:
                console.print(f"[red]CUR payer mismatch[/red]")
                console.print()
                console.print(f"  Workspace:       {e.workspace_name}")
                console.print(f"  Expected payer:  {redact_account_id(e.expected_payer)}")
                console.print(f"  CUR payer:       {redact_account_id(e.found_payer)}")
                console.print()
                console.print("  The selected CUR data does not belong to this workspace.")
                sys.exit(ExitCode.CONFIG_ERROR)
            except MultiplePayersError as e:
                console.print(f"[red]{e}[/red]")
                sys.exit(ExitCode.CONFIG_ERROR)
            except PayerBindingConflictError as e:
                console.print(f"[red]CUR payer conflict: {e}[/red]")
                sys.exit(ExitCode.CONFIG_ERROR)
            except (InvalidPayerEvidenceError, MultiplePayerEvidenceError) as e:
                console.print(f"[red]{e}[/red]")
                sys.exit(ExitCode.CONFIG_ERROR)
            except Exception:
                pass  # Non-fatal: validation failure doesn't block investigation

        try:
            brief = analyze_cost_cur(cur_path, month=month)
        except Exception as exc:
            console.print(f"[red]Cost analysis failed: {exc}[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)

        # Export
        if output:
            content = export_brief(brief, export_format or "json", output)
            console.print(f"Wrote: {output}")
        else:
            # Terminal output
            from kulshan.analyze.export import brief_to_terminal
            console.print(brief_to_terminal(brief))
        return

    try:
        manifest = read_manifest_uri(s3_uri or "", billing_period=month)
        console.print("[green]manifest read: yes[/green]")
        console.print(f"manifest size: {manifest.manifest_size_bytes} bytes")
        console.print(f"file count: {len(manifest.files)}")
        console.print(f"total export size: {manifest.total_size_bytes} bytes")
        con = connect_s3_duckdb()
        try:
            columns = cur_columns(con, manifest)
            cost_selection = select_cost_column(con, manifest, columns, month)
            estimate_columns = tuple(
                column
                for column in (
                    cost_selection.column,
                    "line_item_product_code",
                    "product_servicecode",
                    "line_item_usage_type",
                    "lineitem_usagetype",
                    "line_item_usage_start_date",
                    "lineitem_usagestartdate",
                    "line_item_usage_account_id",
                    "usage_account_id",
                    "product_region",
                    "region",
                )
                if column in columns
            )
            estimate = estimate_scan_bytes(con, manifest, estimate_columns)
            threshold_mb = int(os.environ.get("KULSHAN_MAX_SCAN_MB", "100"))
            threshold_bytes = threshold_mb * 1024 * 1024
            console.print(
                f"scan estimate: {estimate.estimated_bytes} bytes ({estimate.method}; "
                f"upper bound {estimate.upper_bound_bytes} bytes)"
            )
            console.print(f"scan estimate note: {estimate.note}")
            if estimate.estimated_bytes > threshold_bytes and not confirm_scan:
                console.print(
                    f"[red]Estimated scan exceeds {threshold_mb} MB. Re-run with --confirm-scan "
                    "to continue.[/red]"
                )
                sys.exit(ExitCode.CONFIG_ERROR)
            result = analyze_cost_s3(con, manifest, month)
        finally:
            con.close()
    except (CurManifestError, CurDataError) as exc:
        console.print(f"[red]Cost investigation failed: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)
    except Exception as exc:
        console.print(f"[red]Cost investigation failed: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    if output:
        if export_format == "json":
            content = cost_result_to_json(result, month)
        else:
            content = cost_result_to_markdown(result, month)
        _atomic_write(output, content)
        console.print(f"Wrote: {output}")

    console.print("[bold]Cost Investigation[/bold]")
    console.print(f"Billing month: {month}")
    console.print(f"Total spend: ${result.total_spend:,.2f}")
    console.print(f"Cost column used: {result.cost_column}")
    if result.fallback_note:
        console.print(f"Cost column fallback: {result.fallback_note}")
    _print_cost_table(console, "Top Product Codes / Services", result.top_services)
    _print_cost_table(console, "Top Usage Types", result.top_usage_types)
    _print_cost_table(console, "Top Usage Accounts", result.top_accounts)
    if result.top_regions:
        _print_cost_table(console, "Top Regions", result.top_regions)
    console.print("Note: standard S3 request and transfer charges may apply.")


def _print_cost_table(console, title: str, rows: tuple[tuple[str, float], ...]) -> None:
    from rich.table import Table

    table = Table(title=title, show_lines=False)
    table.add_column("Name")
    table.add_column("Cost", justify="right")
    for name, cost in rows:
        table.add_row(name, f"${cost:,.2f}")
    console.print(table)


@analyze.command("ec2")
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
    help="Current billing month to analyze in YYYY-MM format. Defaults to latest month.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write analysis output to .json or .md.",
)
@click.pass_context
def analyze_ec2(ctx: click.Context, cur_path: str, month: str | None, output: str | None) -> None:
    """Produce a local EC2 analysis brief from Parquet CUR data."""
    from rich.console import Console as RichConsole
    from rich.table import Table

    from kulshan.analyze import CurAnalysisError, analyze_ec2_cur
    from kulshan.analyze.export import (
        ec2_brief_to_json,
        ec2_brief_to_markdown,
        investigation_format_from_path,
    )

    console = RichConsole()
    export_format = None
    if output:
        try:
            export_format = investigation_format_from_path(output)
        except ValueError as exc:
            click.echo(str(exc), err=True)
            sys.exit(ExitCode.CONFIG_ERROR)

    # Workspace payer validation (no AWS calls)
    from kulshan.workspace.resolution import resolve_workspace as _resolve_ws
    from kulshan.workspace.errors import WorkspaceError
    from kulshan.cur.duckdb_engine import connect_memory, register_cur_raw
    from kulshan.cur.source import local_parquet_source
    from kulshan.cur.payer_validation import (
        validate_cur_payer,
        PayerMismatchError,
        MultiplePayersError,
    )
    from kulshan.redact import redact_account_id

    try:
        ws_ctx = _resolve_ws(ctx.obj.get("workspace"))
    except WorkspaceError:
        ws_ctx = None

    expected_payer = None
    ws_name = None
    if ws_ctx and ws_ctx.is_bound and ws_ctx.config.aws:
        expected_payer = ws_ctx.config.aws.payer_account_id
        ws_name = ws_ctx.name

    if ws_ctx and ws_ctx.is_bound:
        from kulshan.workspace.payer_binding import (
            try_bind_payer_from_cur,
            InvalidPayerEvidenceError,
            MultiplePayerEvidenceError,
        )
        from kulshan.workspace.onboarding import PayerBindingConflictError

        try:
            _payer_con = connect_memory()
            _payer_source = local_parquet_source(str(cur_path))
            register_cur_raw(_payer_con, _payer_source)

            if expected_payer:
                # Already bound — validate
                payer_result = validate_cur_payer(_payer_con, expected_payer, ws_name)
                if payer_result.status == "missing":
                    console.print(
                        f"  [yellow]Warning:[/yellow] {payer_result.message}"
                    )
                    console.print(
                        f"  [dim]Continuing with unverified local input.[/dim]"
                    )
                    console.print()
            else:
                # Unverified payer — attempt binding
                binding_result = try_bind_payer_from_cur(_payer_con, ws_ctx)
                if binding_result.status == "bound":
                    console.print(
                        f"  [green]✓[/green] {binding_result.message}",
                        highlight=False,
                    )
                    console.print()
                    # Check for payer conflict after binding
                    _offer_reconciliation_if_needed(
                        ws_ctx, binding_result.payer_account_id, console
                    )
                elif binding_result.status == "missing":
                    console.print(
                        f"  [yellow]Warning:[/yellow] {binding_result.message}",
                        highlight=False,
                    )
                    console.print()

            _payer_con.close()
        except PayerMismatchError as e:
            console.print(f"[red]CUR payer mismatch[/red]")
            console.print()
            console.print(f"  Workspace:       {e.workspace_name}")
            console.print(f"  Expected payer:  {redact_account_id(e.expected_payer)}")
            console.print(f"  CUR payer:       {redact_account_id(e.found_payer)}")
            console.print()
            console.print("  The selected CUR data does not belong to this workspace.")
            sys.exit(ExitCode.CONFIG_ERROR)
        except MultiplePayersError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)
        except PayerBindingConflictError as e:
            console.print(f"[red]CUR payer conflict: {e}[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)
        except (InvalidPayerEvidenceError, MultiplePayerEvidenceError) as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(ExitCode.CONFIG_ERROR)
        except Exception:
            pass  # Non-fatal

    try:
        brief = analyze_ec2_cur(cur_path, month=month)
    except CurAnalysisError as exc:
        console.print(f"[red]Cannot analyze EC2 CUR data: {exc}[/red]")
        sys.exit(ExitCode.CONFIG_ERROR)

    if output:
        if export_format == "json":
            content = ec2_brief_to_json(brief)
        else:
            content = ec2_brief_to_markdown(brief)
        _atomic_write(output, content)
        console.print(f"Wrote: {output}")

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


def _validate_account_id(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Validate that account ID is exactly 12 digits."""
    if value is None:
        return None
    if not value.isdigit() or len(value) != 12:
        raise click.BadParameter("Account ID must be exactly 12 digits.")
    return value


@main.command()
@click.option("--limit", "-n", default=20, type=int, help="Number of past scans to show.")
@click.option("--show-pii", is_flag=True, default=False, help="Show full account IDs (redacted by default).")
@click.option(
    "--account",
    default=None,
    callback=_validate_account_id,
    help="Filter by AWS account ID (12 digits). This is the credential account used during the scan.",
)
@click.option("--direct-only", is_flag=True, default=False, help="Show only scans stored directly in this workspace (exclude linked history).")
@click.pass_context
def history(ctx: click.Context, limit: int, show_pii: bool, account: str | None, direct_only: bool) -> None:
    """Show past scan history with scores and trends.

    By default, includes scans from linked (reconciled) workspaces.
    Use --direct-only to show only scans from this workspace.

    The account ID stored in history is the credential account from
    sts:GetCallerIdentity at scan time, not necessarily the payer
    or linked accounts being analyzed.

    No AWS credentials or STS calls required.
    """
    from rich.console import Console as RichConsole
    from rich.table import Table
    from kulshan.redact import redact_account_id

    console = RichConsole()

    # Resolve workspace (no AWS calls)
    from kulshan.workspace.resolution import resolve_workspace as _resolve_ws
    from kulshan.workspace.errors import WorkspaceError
    workspace_name = ctx.obj.get("workspace")
    try:
        ws_ctx = _resolve_ws(workspace_name)
    except WorkspaceError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if direct_only:
        # Direct-only: read from this workspace only
        try:
            from kulshan.history import HistoryStore
            store = HistoryStore(ws_ctx.history_db_path)
            scans_raw = store.list_scans(limit=limit, account_id=account)
            store.close()
        except Exception as e:
            console.print(f"[red]Could not read history: {e}[/red]")
            raise SystemExit(1)

        federated_scans = None
        linked_count = 0
        linked_scan_count = 0
    else:
        # Federated: include linked workspaces
        from kulshan.workspace.federated_history import collect_federated_scans

        try:
            result = collect_federated_scans(ws_ctx, limit=limit, account_id=account)
            federated_scans = result.scans
            linked_count = result.linked_workspace_count
            linked_scan_count = result.linked_scan_count
        except Exception as e:
            console.print(f"[red]Could not read history: {e}[/red]")
            raise SystemExit(1)

        scans_raw = None

    # Determine what to render
    if federated_scans is not None:
        if not federated_scans:
            if account:
                console.print(f"[dim]No scan history found for account {account}.[/dim]")
            else:
                console.print("[dim]No scan history found. Run 'kulshan report' to create your first scan.[/dim]")
            return

        table = Table(title=f"History for {ws_ctx.display_name}", show_lines=False)
        table.add_column("ID", style="dim")
        table.add_column("Date", style="cyan")
        table.add_column("Account")
        table.add_column("Score", justify="right")
        table.add_column("Grade", justify="center")
        table.add_column("Findings", justify="right")
        table.add_column("Crit", justify="right", style="red")
        table.add_column("High", justify="right", style="bright_red")
        table.add_column("Source", style="dim")

        for fs in federated_scans:
            scan = fs.scan
            ts = scan.get("timestamp", "")[:16].replace("T", " ")
            raw_account = scan.get("account_id", "?")
            display_account = raw_account if show_pii else redact_account_id(raw_account)
            source = f"{fs.source_display_name} (linked)" if fs.is_linked else fs.source_display_name
            table.add_row(
                scan.get("id", "?"),
                ts,
                display_account,
                str(scan.get("overall_score", 0)),
                scan.get("overall_grade", "?"),
                str(scan.get("total_findings", 0)),
                str(scan.get("critical_findings", 0)),
                str(scan.get("high_findings", 0)),
                source,
            )

        console.print(table)

        if linked_count > 0 and linked_scan_count > 0:
            console.print(
                f"\n  [dim]Includes {linked_scan_count} earlier scan(s) "
                f"from {linked_count} linked environment(s).[/dim]"
            )
            console.print()
    else:
        # Direct-only mode
        if not scans_raw:
            if account:
                console.print(f"[dim]No scan history found for account {account}.[/dim]")
            else:
                console.print("[dim]No scan history found. Run 'kulshan report' to create your first scan.[/dim]")
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

        for scan in scans_raw:
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
@click.pass_context
def delete_history(ctx: click.Context, yes: bool) -> None:
    """Permanently delete all locally stored scan history for the active workspace."""
    from kulshan.history import HistoryStore
    from kulshan.workspace.resolution import resolve_workspace as _resolve_ws
    from kulshan.workspace.errors import WorkspaceError

    workspace_name = ctx.obj.get("workspace")
    try:
        ws_ctx = _resolve_ws(workspace_name)
    except WorkspaceError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    db_path = ws_ctx.history_db_path

    if not yes and not click.confirm(
        f"Delete all scan history for workspace '{ws_ctx.name}'?"
    ):
        click.echo("History was not deleted.")
        return

    store = HistoryStore(db_path)
    deleted = store.delete_all()
    store.close()
    click.echo(f"Deleted {deleted} scan(s) from {db_path}")


@main.command("mcp-serve")
def mcp_serve() -> None:
    """Serve Kulshan MCP tools over stdio."""
    from kulshan.mcp_server import run_server

    run_server()


# -- Workspace commands for multi-payer isolation --
from kulshan.workspace.cli import workspace as workspace_group  # noqa: E402

main.add_command(workspace_group)


@main.command()
@click.option("--deep", is_flag=True, help="Probe each pack's required permissions.")
@click.option("--json", "json_output", is_flag=True, help="Output JSON to stdout (no terminal decoration).")
@click.pass_context
def preflight(ctx: click.Context, deep: bool, json_output: bool) -> None:
    """Check AWS connectivity and readiness without running a scan.

    \b
    Validates:
      - AWS credentials are configured
      - STS caller identity resolves
      - Cost Explorer API is reachable
      - Required permissions are present

    \b
    Flags:
      --deep   Probe every pack's service requirements.
      --json   Output valid JSON to stdout (no Rich formatting).

    No data is written. No cost is incurred. Safe to run repeatedly.
    """
    from kulshan.session import create_session

    profile = ctx.obj.get("profile")
    role_arn = ctx.obj.get("role_arn")

    # JSON mode: no Rich console output at all
    if json_output:
        import json as json_mod
        from kulshan.preflight import (
            run_preflight_basic_json,
            run_preflight_deep,
            deep_result_to_json,
        )

        try:
            session = create_session(profile=profile, role_arn=role_arn)
        except Exception as e:
            output = {
                "identity": {"arn": "", "account_id": "", "partition": ""},
                "workspace": {"name": None, "payer_account_id": None},
                "connections": [],
                "permissions": {"sts": "error"},
                "data_sources": {"cost_explorer": "not_checked", "cur_local": "not_configured", "cur_s3": "not_configured"},
                "packs": {},
                "warnings": [],
                "errors": [f"Cannot create AWS session: {e}"],
            }
            click.echo(json_mod.dumps(output, indent=2))
            return

        if deep:
            result = run_preflight_deep(session)
            output = deep_result_to_json(result)
        else:
            output = run_preflight_basic_json(session)

        click.echo(json_mod.dumps(output, indent=2))
        return

    # Interactive mode (Rich console)
    from rich.console import Console as RichConsole
    from kulshan.preflight import run_preflight, run_preflight_deep

    console = RichConsole()

    console.print()
    console.print("  [bold]Kulshan Preflight[/bold] -- checking AWS readiness")
    console.print()

    try:
        session = create_session(profile=profile, role_arn=role_arn)
    except Exception as e:
        console.print(f"  [red]✗ Cannot create AWS session: {e}[/red]")
        console.print()
        console.print("  [dim]Check: AWS credentials configured? Try: aws sts get-caller-identity[/dim]")
        sys.exit(ExitCode.CONFIG_ERROR)

    passed, warnings = run_preflight(session, console=console)

    if deep and passed:
        # Deep mode: probe every pack
        console.print("  [bold]Deep probe[/bold] — checking per-pack permissions")
        console.print()

        deep_result = run_preflight_deep(session)

        for pack_name, pack_result in deep_result.packs.items():
            if pack_result.readiness == "ready":
                console.print(f"  [green]✓[/green] {pack_name}: ready")
            elif pack_result.readiness == "partial":
                console.print(f"  [yellow]⚠[/yellow] {pack_name}: partial ({pack_result.reason})")
            else:
                console.print(f"  [red]✗[/red] {pack_name}: {pack_result.readiness}")

        console.print()

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
