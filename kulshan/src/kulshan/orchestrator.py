"""Run all tool scans and compute the overall score."""
from __future__ import annotations

import importlib
import logging
import concurrent.futures
import time
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.tree import Tree
from rich.text import Text

from kulshan.adapter import adapt
from kulshan.aws_runtime import ApiProfiler, render_perf_summary, set_active_profiler
from kulshan.models import VALID_EFFORT, VALID_RISK, VALID_SEVERITY
from kulshan.scoring_utils import grade as _grade

TOOL_ORDER = ["cost", "security", "sweep", "dr", "age", "drift", "tag", "pulse", "limit", "topo"]

TOOL_LABELS = {
    "cost": "Cost Analyzer",
    "security": "Security Scanner",
    "sweep": "Waste Detector",
    "dr": "DR Readiness",
    "age": "Lifecycle Audit",
    "drift": "IaC Drift",
    "tag": "Tag Governance",
    "pulse": "Observability",
    "limit": "Quota Headroom",
    "topo": "Network Topology",
}

TOOL_WEIGHTS = {
    "cost": 0.15,
    "security": 0.15,
    "sweep": 0.10,
    "dr": 0.12,
    "age": 0.08,
    "drift": 0.10,
    "tag": 0.08,
    "pulse": 0.08,
    "limit": 0.06,
    "topo": 0.08,
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = ("id", "pack", "kind", "title", "severity", "confidence", "effort", "risk")
_REQUIRED_STRING_KEYS = ("id", "pack", "kind", "title")
_MAX_STRING_LEN = 512


def validate_finding(finding: dict) -> Tuple[bool, str]:
    """Validate a finding dict against the canonical schema.

    Returns (is_valid, error_message). A valid finding returns (True, "").
    An invalid finding returns (False, reason_string) describing the first
    validation failure encountered.
    """
    # Must be a dict
    if not isinstance(finding, dict):
        return (False, f"finding is not a dict, got {type(finding).__name__}")

    # Check required keys are present
    for key in _REQUIRED_KEYS:
        if key not in finding:
            return (False, f"missing required key: {key!r}")

    # Validate required string fields are non-empty strings ≤512 chars
    for key in _REQUIRED_STRING_KEYS:
        val = finding[key]
        if not isinstance(val, str):
            return (False, f"{key!r} must be a string, got {type(val).__name__}")
        if len(val) == 0:
            return (False, f"{key!r} must be non-empty")
        if len(val) > _MAX_STRING_LEN:
            return (False, f"{key!r} exceeds maximum length of {_MAX_STRING_LEN} characters")

    # Validate severity
    severity = finding["severity"]
    if not isinstance(severity, str) or severity not in VALID_SEVERITY:
        return (False, f"severity must be one of {VALID_SEVERITY}, got {severity!r}")

    # Validate confidence is a float/int in [0.0, 1.0]
    confidence = finding["confidence"]
    if not isinstance(confidence, (int, float)):
        return (False, f"confidence must be a float or int, got {type(confidence).__name__}")
    if not (0.0 <= float(confidence) <= 1.0):
        return (False, f"confidence must be in [0.0, 1.0], got {confidence}")

    # Validate effort
    effort = finding["effort"]
    if not isinstance(effort, str) or effort not in VALID_EFFORT:
        return (False, f"effort must be one of {VALID_EFFORT}, got {effort!r}")

    # Validate risk
    risk = finding["risk"]
    if not isinstance(risk, str) or risk not in VALID_RISK:
        return (False, f"risk must be one of {VALID_RISK}, got {risk!r}")

    return (True, "")


def _is_legacy_finding(finding: Any) -> bool:
    """Return True if the finding dict appears to be in a legacy (non-canonical) shape.

    A finding is considered legacy if it:
    - Has a ``tool`` key and no ``pack`` key (old models.py shape), OR
    - Has a ``check_id`` key and no ``kind`` key (old models.py shape), OR
    - Is missing both ``pack`` and ``kind`` canonical keys
    """
    if not isinstance(finding, dict):
        return False
    has_tool = "tool" in finding
    has_pack = "pack" in finding
    has_check_id = "check_id" in finding
    has_kind = "kind" in finding

    # Legacy indicators: tool without pack, check_id without kind, or missing both canonical keys
    if has_tool and not has_pack:
        return True
    if has_check_id and not has_kind:
        return True
    if not has_pack and not has_kind:
        return True
    return False


def run_all_scans(
    session: Any,
    regions: List[str],
    profile: Optional[str] = None,
    quick: bool = False,
    console: Optional[Console] = None,
    selected_packs: Optional[List[str]] = None,
    perf: bool = False,
    deep: bool = False,
    days: int = 90,
) -> Dict[str, dict]:
    if console is None:
        console = Console()

    results: Dict[str, dict] = {}

    # Determine which packs to run
    packs_to_run = selected_packs if selected_packs else TOOL_ORDER
    # Validate pack names
    if selected_packs:
        invalid = [p for p in selected_packs if p not in TOOL_ORDER]
        if invalid:
            console.print(f"  [yellow]Unknown packs: {', '.join(invalid)}[/yellow]")
            console.print(f"  [dim]Valid packs: {', '.join(TOOL_ORDER)}[/dim]")
            console.print()
        packs_to_run = [p for p in selected_packs if p in TOOL_ORDER]

    # --- Scan Plan Tree Preview ---
    tree = Tree(f"[bold]Kulshan Scan Plan[/bold]  [dim]({len(regions)} region{'s' if len(regions) != 1 else ''}, {len(packs_to_run)} pack{'s' if len(packs_to_run) != 1 else ''})[/dim]")
    for tool_key in packs_to_run:
        tree.add(f"[dim]{tool_key}[/dim]  {TOOL_LABELS[tool_key]}")
    console.print(tree)
    console.print()

    profiler = ApiProfiler() if perf else None
    set_active_profiler(profiler)

    # --- Live severity tally state ---
    severity_tally: Dict[str, int] = {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
    }

    def _tally_description() -> str:
        """Build a live severity tally string for the progress bar."""
        parts = []
        if severity_tally["critical"]:
            parts.append(f"[red]●{severity_tally['critical']} Crit[/red]")
        if severity_tally["high"]:
            parts.append(f"[bright_red]●{severity_tally['high']} High[/bright_red]")
        if severity_tally["medium"]:
            parts.append(f"[yellow]●{severity_tally['medium']} Med[/yellow]")
        if severity_tally["low"]:
            parts.append(f"[blue]●{severity_tally['low']} Low[/blue]")
        if severity_tally["info"]:
            parts.append(f"[dim]●{severity_tally['info']} Info[/dim]")
        return "  ".join(parts) if parts else "[dim]No findings yet[/dim]"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        overall_task = progress.add_task("Kulshan scan", total=len(packs_to_run))

        for idx, tool_key in enumerate(packs_to_run, start=1):
            label = TOOL_LABELS[tool_key]
            progress.update(
                overall_task,
                description=f"[bold]{idx}/{len(packs_to_run)} {label}[/bold]  {_tally_description()}",
            )

            pack_start = time.perf_counter()
            check = _load_check(tool_key)
            if check is None:
                results[tool_key] = _skip(tool_key, "Not installed")
            else:
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            check.run_scan,
                            session,
                            regions,
                            quick=quick,
                            profile=profile,
                            deep=deep,
                            days=days,
                        )
                        results[tool_key] = future.result(timeout=300)
                except concurrent.futures.TimeoutError:
                    results[tool_key] = _skip(tool_key, "Timed out after 300s")
                    logger.warning("Pack %s timed out after 300s", tool_key)
                except Exception as e:
                    results[tool_key] = _skip(tool_key, str(e))

            if profiler:
                profiler.record_pack(tool_key, time.perf_counter() - pack_start)

            # Ensure findings key exists (empty list for packs that don't emit findings)
            if "findings" not in results[tool_key]:
                results[tool_key]["findings"] = []

            # Validate findings and exclude invalid ones
            findings = results[tool_key].get("findings", [])
            valid_findings = []
            invalid_findings = 0
            for idx, finding in enumerate(findings):
                # Adapt legacy-shaped findings before validation
                if _is_legacy_finding(finding):
                    finding = adapt(tool_key, finding)

                is_valid, reason = validate_finding(finding)
                if is_valid:
                    valid_findings.append(finding)
                else:
                    invalid_findings += 1
                    logger.warning(
                        "Pack %s finding %d invalid: %s",
                        tool_key, idx, reason,
                    )
            results[tool_key]["findings"] = valid_findings
            if invalid_findings:
                results[tool_key].setdefault("errors", []).append(
                    f"Excluded {invalid_findings} invalid finding(s)"
                )
            _annotate_completeness(results[tool_key])

            # --- Update live severity tally ---
            for f in valid_findings:
                sev = f.get("severity", "info")
                if sev in severity_tally:
                    severity_tally[sev] += 1

            # --- Contextual dimming: print completed pack status ---
            finding_count = len(valid_findings)
            if results[tool_key].get("skipped"):
                progress.console.print(
                    f"  [dim]⊘ {label}[/dim] [dim italic]skipped[/dim italic]"
                )
            elif finding_count == 0:
                progress.console.print(
                    f"  [green]✓ {label}[/green] [dim]0 findings[/dim]"
                )
            else:
                progress.console.print(
                    f"  [green]✓ {label}[/green] [yellow]{finding_count} findings[/yellow]"
                )

            progress.advance(overall_task)

    set_active_profiler(None)

    # --- Final tally summary ---
    console.print()
    tally_parts = []
    if severity_tally["critical"]:
        tally_parts.append(f"[red bold]{severity_tally['critical']} critical[/red bold]")
    if severity_tally["high"]:
        tally_parts.append(f"[bright_red]{severity_tally['high']} high[/bright_red]")
    if severity_tally["medium"]:
        tally_parts.append(f"[yellow]{severity_tally['medium']} medium[/yellow]")
    if severity_tally["low"]:
        tally_parts.append(f"[blue]{severity_tally['low']} low[/blue]")
    if severity_tally["info"]:
        tally_parts.append(f"[dim]{severity_tally['info']} info[/dim]")
    if tally_parts:
        console.print(f"  Findings: {', '.join(tally_parts)}")
    else:
        console.print("  [green]No findings detected.[/green]")
    console.print()
    if profiler:
        render_perf_summary(console, profiler)

    return results


def compute_overall(results: Dict[str, dict]) -> tuple[int, str]:
    completeness = summarize_completeness(results)
    if completeness["partial"]:
        return 0, "N/A"

    total_weight = 0.0
    weighted_sum = 0.0
    for tool_key, result in results.items():
        if result.get("skipped"):
            continue
        score = result.get("scores", {}).get("overall_score", 0)
        weight = TOOL_WEIGHTS.get(tool_key, 0.05)
        weighted_sum += score * weight
        total_weight += weight

    overall = int(weighted_sum / total_weight) if total_weight > 0 else 0
    return overall, _grade(overall)


def summarize_completeness(results: Dict[str, dict]) -> dict:
    """Summarize whether every requested pack completed without reported errors."""
    completed_checks = []
    partial_checks = []
    skipped_checks = []
    failed_checks = []
    missing_permissions = set()

    for tool_key, result in results.items():
        _annotate_completeness(result)
        status = result["completeness"]
        if status == "complete":
            completed_checks.append(tool_key)
        elif status == "skipped":
            skipped_checks.append(tool_key)
        else:
            partial_checks.append(tool_key)

        if result.get("errors"):
            failed_checks.append(tool_key)
        missing_permissions.update(result.get("missing_permissions", []))

    incomplete = bool(partial_checks or skipped_checks)
    return {
        "partial": incomplete,
        "status": "partial" if incomplete else "complete",
        "completed_checks": completed_checks,
        "partial_checks": partial_checks,
        "skipped_checks": skipped_checks,
        "failed_checks": failed_checks,
        "missing_permissions": sorted(missing_permissions),
    }


def _annotate_completeness(result: dict) -> None:
    errors = [str(error) for error in result.get("errors", []) if error]
    result["errors"] = errors
    result["missing_permissions"] = _extract_missing_permissions(errors)

    if result.get("skipped"):
        status = "skipped"
    elif errors:
        status = "partial"
    else:
        status = "complete"

    result["completeness"] = status
    result["partial"] = status != "complete"


def _extract_missing_permissions(errors: List[str]) -> List[str]:
    permission_errors = []
    for error in errors:
        if re.search(r"access.?denied|unauthori[sz]ed|not authorized|missing permission", error, re.I):
            permission_errors.append(error)
    return permission_errors


def _load_check(tool_key: str):
    try:
        return importlib.import_module(f"kulshan.checks.{tool_key}")
    except ImportError:
        return None


def _skip(tool_key: str, reason: str) -> dict:
    result = {
        "tool": tool_key,
        "scores": {
            "overall_score": 0, "grade": "N/A",
            "total_findings": 0, "severity_counts": {},
        },
        "errors": [reason],
        "skipped": True,
    }
    _annotate_completeness(result)
    return result
