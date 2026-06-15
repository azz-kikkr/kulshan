"""Rich terminal renderer for the combined Kulshan Report."""
from __future__ import annotations

from typing import Dict, List, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kulshan.__version__ import __version__
from kulshan.orchestrator import TOOL_LABELS, TOOL_ORDER
from kulshan.theme_constants import TOOL_ICONS, SEVERITY_STYLES

# Severity → color used in the Top Actions panel (Phase 6C-1).
_SEVERITY_COLOR = SEVERITY_STYLES


def grade_color(grade: str) -> str:
    if grade.startswith("A"):
        return "green"
    if grade.startswith("B"):
        return "blue"
    if grade.startswith("C"):
        return "yellow"
    if grade.startswith("D"):
        return "dark_orange"
    return "red"


def score_bar(score: int, width: int = 20) -> str:
    filled = int(score / 100 * width)
    empty = width - filled
    if score >= 80:
        color = "green"
    elif score >= 60:
        color = "yellow"
    elif score >= 40:
        color = "dark_orange"
    else:
        color = "red"
    bar_filled = "█" * filled
    bar_empty = "░" * empty
    return f"[{color}]{bar_filled}[/{color}][dim]{bar_empty}[/dim]"


def _truncate(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _source_label(kind: str) -> str:
    """Map Finding kind to a compact source label for the Top Actions panel."""
    if not kind:
        return "?"
    if "aws" in kind:
        return "AWS"
    if "anomaly" in kind:
        return "Kulshan"
    return kind.split("_", 1)[0].capitalize()


def _render_top_actions(
    console: Console, top_actions: List[dict], max_rows: int = 10
) -> None:
    """Render the compact Top Actions panel.

    Silent when ``top_actions`` is empty, keeps the report clean for accounts
    with no findings.
    """
    if not top_actions:
        return

    table = Table(
        title="Top Actions",
        title_style="bold",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
        title_justify="left",
    )
    table.add_column("#", justify="right", width=3, style="dim")
    table.add_column("Severity", width=10)
    table.add_column("$/mo", justify="right", width=10)
    table.add_column("Conf", justify="right", width=5)
    table.add_column("Source", width=10)
    table.add_column("Title", overflow="fold", min_width=40)

    for i, f in enumerate(top_actions[:max_rows], start=1):
        sev = str(f.get("severity", "info"))
        sev_style = _SEVERITY_COLOR.get(sev, "dim")
        try:
            impact = float(f.get("estimated_monthly_impact") or 0)
        except (TypeError, ValueError):
            impact = 0.0
        impact_str = f"${impact:,.0f}" if impact >= 1 else "~$0"
        try:
            conf = float(f.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        title = _truncate(str(f.get("title", "")), max_len=80)
        source = _source_label(str(f.get("kind", "")))

        table.add_row(
            str(i),
            f"[{sev_style}]{sev}[/{sev_style}]",
            impact_str,
            f"{conf:.2f}",
            source,
            title,
        )

    console.print(table)
    console.print()


def _render_aws_anomaly_status(console: Console, results: Dict[str, dict]) -> None:
    """Render a one-line AWS Cost Anomaly Detection status, when metadata exists.

    Looks for ``results["cost"]["metadata"]["cost_anomaly_detection"]``. Silent
    when absent (older fixtures, packs that don't emit metadata).
    """
    cost_result = results.get("cost") or {}
    metadata = cost_result.get("metadata") or {}
    cad = metadata.get("cost_anomaly_detection")
    if not cad:
        return

    status = cad.get("status", "unknown")
    count = int(cad.get("anomaly_count", 0) or 0)
    overlap = cad.get("overlap") or {}
    both = int(overlap.get("both_count", 0) or 0)
    aws_only = int(overlap.get("aws_only_count", 0) or 0)
    reck_only = int(overlap.get("Kulshan_only_count", 0) or 0)

    if status == "ok" and count:
        parts = []
        if both:
            parts.append(f"{both} confirmed by Kulshan")
        if aws_only:
            parts.append(f"{aws_only} AWS-only")
        if reck_only:
            parts.append(f"{reck_only} Kulshan-only")
        suffix = f" ({', '.join(parts)})" if parts else ""
        line = f"AWS Cost Anomaly Detection: {count} anomalies{suffix}."
    elif status == "no_data":
        details = cad.get("details") or "no anomalies in window"
        line = f"AWS Cost Anomaly Detection: no anomalies, {details}"
    elif status == "error":
        details = cad.get("details") or "error"
        line = f"AWS Cost Anomaly Detection: error, {details}"
    else:
        return

    console.print(f"  [dim]{line}[/dim]")
    console.print()


def render_report(
    results: Dict[str, dict],
    overall_score: int,
    overall_grade: str,
    account_id: str,
    regions_count: int,
    duration_secs: float,
    console: Optional[Console] = None,
    top_actions: Optional[List[dict]] = None,
) -> None:
    if console is None:
        console = Console()

    # -- Header panel --
    title = Text()
    title.append("Kulshan", style="bold cyan")
    title.append(f" v{__version__}", style="dim")

    info = f"Account: {account_id}  │  Regions: {regions_count}  │  Duration: {duration_secs:.0f}s"
    console.print()
    console.print(Panel(info, title=title, border_style="cyan", padding=(0, 2)))

    # -- Overall score (hero) --
    g_clr = grade_color(overall_grade)
    console.print()

    # Total spend from cost pack (if available)
    cost_result = results.get("cost") or {}
    total_spend = cost_result.get("scores", {}).get("total_spend")
    spend_str = f"  │  [dim]Spend:[/dim] [bold]${total_spend:,.0f}[/bold]" if total_spend and total_spend > 0 else ""

    console.print(
        f"  [{g_clr} bold]{overall_grade}[/{g_clr} bold]"
        f"  [{g_clr}]{overall_score}/100[/{g_clr}]"
        f"{spend_str}"
    )

    # Delta from last scan (from history)
    try:
        from kulshan.history import HistoryStore
        store = HistoryStore()
        prev = store.get_previous_scan(account_id)
        store.close()
        if prev and prev.get("overall_score") is not None:
            delta = overall_score - prev["overall_score"]
            prev_grade = prev.get("overall_grade", "?")
            if delta > 0:
                console.print(f"  [green]↑ {delta} pts[/green] [dim]vs last scan ({prev_grade})[/dim]")
            elif delta < 0:
                console.print(f"  [red]↓ {abs(delta)} pts[/red] [dim]vs last scan ({prev_grade})[/dim]")
            else:
                console.print(f"  [dim]→ unchanged vs last scan ({prev_grade})[/dim]")
    except Exception:
        pass  # History unavailable, skip delta display

    console.print()

    # -- Tool score table --
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold", padding=(0, 1))
    table.add_column("Tool", min_width=22)
    table.add_column("Score", justify="right", width=5)
    table.add_column("Grade", justify="center", width=5)
    table.add_column("", min_width=22)
    table.add_column("Findings", justify="right", width=8)

    for tool_key in TOOL_ORDER:
        result = results.get(tool_key, {})
        scores = result.get("scores", {})
        score = scores.get("overall_score", 0)
        grade = scores.get("grade", "N/A")
        findings = scores.get("total_findings", 0)
        icon = TOOL_ICONS.get(tool_key, "  ")
        label = TOOL_LABELS.get(tool_key, tool_key)

        if result.get("skipped"):
            table.add_row(
                f"{icon} {label}",
                "[dim]--[/dim]", "[dim]N/A[/dim]",
                "[dim]skipped[/dim]", "[dim]--[/dim]",
            )
        else:
            gc = grade_color(grade)
            table.add_row(
                f"{icon} {label}",
                f"[{gc}]{score}[/{gc}]",
                f"[{gc}]{grade}[/{gc}]",
                score_bar(score),
                str(findings),
            )

    console.print(table)
    console.print()

    # -- Severity summary --
    total_sev: Dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for result in results.values():
        if result.get("skipped"):
            continue
        sev = result.get("scores", {}).get("severity_counts", {})
        for k in total_sev:
            total_sev[k] += int(sev.get(k, 0))

    sev_parts = []
    if total_sev["critical"]:
        sev_parts.append(f"[red bold]Critical: {total_sev['critical']}[/red bold]")
    if total_sev["high"]:
        sev_parts.append(f"[dark_orange]High: {total_sev['high']}[/dark_orange]")
    if total_sev["medium"]:
        sev_parts.append(f"[yellow]Medium: {total_sev['medium']}[/yellow]")
    if total_sev["low"]:
        sev_parts.append(f"[dim]Low: {total_sev['low']}[/dim]")

    if sev_parts:
        console.print("  " + "    ".join(sev_parts))
        console.print()

    # -- Phase 6C-1: Top Actions panel + AWS Cost Anomaly Detection status line --
    if top_actions is None:
        # Computed locally if the caller didn't supply them.
        from kulshan.findings_ranker import flatten_findings, top_n
        top_actions = top_n(flatten_findings(results), n=10)

    _render_top_actions(console, top_actions)
    _render_aws_anomaly_status(console, results)

    # -- Error count --
    error_count = sum(
        len(r.get("errors", []))
        for r in results.values()
        if not r.get("skipped")
    )
    if error_count:
        console.print(f"  [dim]{error_count} non-fatal warnings during scan (use --format json for details)[/dim]")
        console.print()

    # -- Next steps (contextual) --
    console.print("  [dim]─── Next steps ───[/dim]")
    if total_sev.get("critical", 0) > 0:
        console.print("  [dim]Review critical findings above. Export: [green]kulshan report -o report.html[/green][/dim]")
    elif total_sev.get("high", 0) > 0:
        console.print("  [dim]Review high-severity findings. Share: [green]kulshan report -o report.html[/green][/dim]")
    else:
        console.print("  [dim]Looking good! Share with your team: [green]kulshan report -o report.html[/green][/dim]")
    console.print()
