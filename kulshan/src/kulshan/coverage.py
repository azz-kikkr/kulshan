"""Canonical coverage model for Kulshan reports.

Every report discloses what was evaluated, what failed, and why.
This model is consumed by terminal, JSON, HTML, SARIF, and history output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from kulshan.__version__ import __version__

ExecutionStatus = Literal["complete", "partial", "failed", "skipped", "denied"]


@dataclass
class CoverageError:
    """A specific error encountered during a pack execution."""

    service: str
    action: str
    code: str
    message: str
    required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self.service,
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "required": self.required,
        }


@dataclass
class ExecutionRecord:
    """Per-pack, per-region execution metadata."""

    pack: str
    region: str
    account_id: str = ""
    connection_id: str = ""
    status: ExecutionStatus = "complete"
    duration_seconds: float = 0.0
    findings_count: int = 0
    data_sources: List[str] = field(default_factory=list)
    errors: List[CoverageError] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "pack": self.pack,
            "region": self.region,
            "status": self.status,
        }
        if self.account_id:
            d["account_id"] = self.account_id
        if self.connection_id:
            d["connection_id"] = self.connection_id
        if self.duration_seconds > 0:
            d["duration_seconds"] = round(self.duration_seconds, 2)
        if self.findings_count > 0:
            d["findings_count"] = self.findings_count
        if self.data_sources:
            d["data_sources"] = self.data_sources
        if self.errors:
            d["errors"] = [e.to_dict() for e in self.errors]
        return d


@dataclass
class CoverageReport:
    """Canonical coverage disclosure for a Kulshan scan.

    Summary fields power terminal and HTML output.
    The executions list provides full detail for JSON and history.
    """

    # Summary (terminal/HTML)
    packs_attempted: int = 0
    packs_completed: int = 0
    packs_partial: int = 0
    packs_failed: int = 0
    packs_skipped: int = 0
    regions_scanned: int = 0
    report_status: Literal["complete", "partial", "failed"] = "complete"
    kulshan_version: str = field(default_factory=lambda: __version__)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    scan_duration_seconds: float = 0.0

    # Data source availability
    data_sources: Dict[str, str] = field(default_factory=dict)

    # Detailed execution records (JSON/history)
    executions: List[ExecutionRecord] = field(default_factory=list)

    # Denied permissions discovered during scan
    denied_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "packs_attempted": self.packs_attempted,
                "packs_completed": self.packs_completed,
                "packs_partial": self.packs_partial,
                "packs_failed": self.packs_failed,
                "packs_skipped": self.packs_skipped,
                "regions_scanned": self.regions_scanned,
                "report_status": self.report_status,
                "kulshan_version": self.kulshan_version,
                "generated_at": self.generated_at,
                "scan_duration_seconds": round(self.scan_duration_seconds, 2),
            },
            "data_sources": dict(self.data_sources),
            "denied_actions": list(self.denied_actions),
            "executions": [e.to_dict() for e in self.executions],
        }

    def to_summary_dict(self) -> Dict[str, Any]:
        """Compact summary for terminal/HTML (no execution detail)."""
        return {
            "packs_attempted": self.packs_attempted,
            "packs_completed": self.packs_completed,
            "packs_partial": self.packs_partial,
            "packs_failed": self.packs_failed,
            "regions_scanned": self.regions_scanned,
            "report_status": self.report_status,
            "scan_duration_seconds": round(self.scan_duration_seconds, 2),
        }

    def terminal_summary(self) -> str:
        """One-line terminal summary for end-of-report display."""
        parts = [f"{self.packs_completed}/{self.packs_attempted} packs complete"]
        if self.packs_partial:
            parts.append(f"{self.packs_partial} partial")
        if self.packs_failed:
            parts.append(f"{self.packs_failed} failed")
        if self.packs_skipped:
            parts.append(f"{self.packs_skipped} skipped")
        parts.append(f"{self.regions_scanned} regions")
        if self.denied_actions:
            parts.append(f"{len(self.denied_actions)} permissions denied")
        return " | ".join(parts)


def build_coverage_from_results(
    results: Dict[str, dict],
    regions: List[str],
    duration_seconds: float,
    account_id: str = "",
    payer_account_id: Optional[str] = None,
) -> CoverageReport:
    """Build a CoverageReport from orchestrator results.

    Examines each pack's result for errors, skipped status, and findings
    to determine execution completeness.
    """
    executions: List[ExecutionRecord] = []
    denied_actions: List[str] = []
    packs_completed = 0
    packs_partial = 0
    packs_failed = 0
    packs_skipped = 0

    for pack_name, result in results.items():
        if result.get("skipped"):
            packs_skipped += 1
            for region in regions:
                executions.append(ExecutionRecord(
                    pack=pack_name, region=region,
                    account_id=account_id, status="skipped",
                ))
            continue

        pack_errors = result.get("errors", [])
        findings_count = len(result.get("findings", []))
        pack_denied = [e for e in pack_errors if "Access denied" in str(e) or "AccessDenied" in str(e)]

        if pack_denied:
            # Extract denied actions
            for err_msg in pack_denied:
                denied_actions.append(f"{pack_name}: {err_msg}")

        if pack_errors and not findings_count:
            packs_failed += 1
            status: ExecutionStatus = "failed"
        elif pack_denied:
            packs_partial += 1
            status = "partial"
        else:
            packs_completed += 1
            status = "complete"

        for region in regions:
            errors = []
            for err_msg in pack_denied:
                errors.append(CoverageError(
                    service=pack_name,
                    action=str(err_msg),
                    code="AccessDenied",
                    message=str(err_msg)[:120],
                ))
            executions.append(ExecutionRecord(
                pack=pack_name, region=region,
                account_id=account_id, status=status,
                findings_count=findings_count,
                errors=errors,
            ))

    packs_attempted = len(results)

    # Determine overall report status
    if packs_failed == packs_attempted:
        report_status: Literal["complete", "partial", "failed"] = "failed"
    elif packs_partial > 0 or packs_failed > 0 or packs_skipped > 0:
        report_status = "partial"
    else:
        report_status = "complete"

    # Data sources
    data_sources: Dict[str, str] = {}
    if "cost" in results and not results["cost"].get("skipped"):
        data_sources["cost_explorer"] = "used"
    else:
        data_sources["cost_explorer"] = "not_used"

    # Check for CUR metadata
    cost_meta = results.get("cost", {}).get("metadata", {})
    if cost_meta.get("cur_investigation") or cost_meta.get("cur_analysis"):
        data_sources["cur_parquet"] = "used"
    else:
        data_sources["cur_parquet"] = "not_used"

    return CoverageReport(
        packs_attempted=packs_attempted,
        packs_completed=packs_completed,
        packs_partial=packs_partial,
        packs_failed=packs_failed,
        packs_skipped=packs_skipped,
        regions_scanned=len(regions),
        report_status=report_status,
        scan_duration_seconds=duration_seconds,
        data_sources=data_sources,
        executions=executions,
        denied_actions=denied_actions,
    )
