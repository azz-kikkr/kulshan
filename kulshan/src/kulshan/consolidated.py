"""Consolidated payer report execution.

Runs packs across multiple approved AWS connections in a payer workspace,
deduplicates findings, and produces one coherent report.

Key rules:
- Payer-scoped packs (cost) run from ONE connection only.
- Account-scoped packs run from each connection.
- Findings are deduplicated by fingerprint.
- Duplicate cost totals are never summed.
- One parent scan is saved to canonical history.
- Connection execution metadata is stored separately.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from kulshan.orchestrator import run_all_scans, compute_overall
from kulshan.workspace.sts import create_verified_session, StsVerificationError

logger = logging.getLogger(__name__)

# Packs that produce payer-wide data (Cost Explorer returns consolidated billing).
# These run from ONE connection only to avoid double-counting.
PAYER_SCOPED_PACKS = frozenset({"cost"})

# All other packs are account-scoped.
ACCOUNT_SCOPED_PACKS = frozenset({"security", "sweep", "dr", "age", "drift", "tag", "pulse", "limit", "topo"})

ReportStatus = Literal["complete", "partial", "failed"]


@dataclass
class ConnectionExecution:
    """Execution metadata for one connection."""

    connection_name: str
    profile: str | None
    session_account_id: str | None
    role_arn: str | None
    status: Literal["success", "failed", "skipped"]
    duration_seconds: float = 0.0
    packs_attempted: list[str] = field(default_factory=list)
    packs_completed: list[str] = field(default_factory=list)
    error_code: str | None = None


@dataclass
class ConsolidatedResult:
    """Result of a consolidated multi-connection report."""

    results: Dict[str, dict]
    all_findings: list[dict]
    overall_score: int
    overall_grade: str
    report_status: ReportStatus
    connections_executed: list[ConnectionExecution]
    accounts_observed: list[str]
    duration_seconds: float
    payer_connection: str | None = None

    @property
    def successful_connections(self) -> list[ConnectionExecution]:
        return [c for c in self.connections_executed if c.status == "success"]

    @property
    def failed_connections(self) -> list[ConnectionExecution]:
        return [c for c in self.connections_executed if c.status == "failed"]


def run_consolidated_report(
    connections: list[dict],
    regions: list[str],
    selected_packs: list[str],
    *,
    quick: bool = False,
    deep: bool = False,
    days: int = 90,
    console: Any = None,
) -> ConsolidatedResult:
    """Run a consolidated report across multiple connections.

    Each connection dict must have:
        name: str
        profile: str
        role_arn: str | None
        expected_session_account_id: str

    Args:
        connections: List of connection configs.
        regions: AWS regions to scan.
        selected_packs: Packs to run.
        quick: Skip confirmations.
        deep: Run expensive checks.
        days: Cost analysis lookback.
        console: Rich console for output.

    Returns:
        ConsolidatedResult with merged, deduplicated findings.
    """
    from rich.console import Console

    if console is None:
        console = Console()

    start_time = time.time()
    connection_executions: list[ConnectionExecution] = []
    all_pack_results: Dict[str, list[dict]] = {}  # pack -> [result_per_connection]
    all_findings_raw: list[dict] = []
    accounts_observed: set[str] = set()
    payer_connection_name: str | None = None

    # Separate packs into payer-scoped and account-scoped
    payer_packs = [p for p in selected_packs if p in PAYER_SCOPED_PACKS]
    account_packs = [p for p in selected_packs if p in ACCOUNT_SCOPED_PACKS]

    # Track whether payer-scoped packs have been run
    payer_packs_done = False
    default_conn = connections[0] if connections else None

    for conn in connections:
        conn_name = conn["name"]
        profile = conn["profile"]
        role_arn = conn.get("role_arn")
        expected_account = conn["expected_session_account_id"]

        conn_start = time.time()
        packs_attempted: list[str] = []
        packs_completed: list[str] = []

        # Verify credentials
        try:
            verified = create_verified_session(
                profile=profile,
                role_arn=role_arn,
            )
        except StsVerificationError as e:
            # Default connection failure = abort
            if conn == default_conn:
                exec_meta = ConnectionExecution(
                    connection_name=conn_name,
                    profile=profile,
                    session_account_id=None,
                    role_arn=role_arn,
                    status="failed",
                    duration_seconds=time.time() - conn_start,
                    packs_attempted=[],
                    packs_completed=[],
                    error_code="sts_verification_failed",
                )
                connection_executions.append(exec_meta)
                raise DefaultConnectionFailedError(conn_name, str(e)) from e

            # Secondary connection failure = continue partial
            exec_meta = ConnectionExecution(
                connection_name=conn_name,
                profile=profile,
                session_account_id=None,
                role_arn=role_arn,
                status="failed",
                duration_seconds=time.time() - conn_start,
                packs_attempted=[],
                packs_completed=[],
                error_code="sts_verification_failed",
            )
            connection_executions.append(exec_meta)
            logger.warning("Connection '%s' unavailable: %s", conn_name, e)
            continue

        # Credential mismatch check
        if verified.account_id != expected_account:
            exec_meta = ConnectionExecution(
                connection_name=conn_name,
                profile=profile,
                session_account_id=verified.account_id,
                role_arn=role_arn,
                status="failed",
                duration_seconds=time.time() - conn_start,
                packs_attempted=[],
                packs_completed=[],
                error_code="credential_mismatch",
            )
            connection_executions.append(exec_meta)

            if conn == default_conn:
                raise DefaultConnectionFailedError(
                    conn_name,
                    f"Credential mismatch: expected {expected_account}, got {verified.account_id}",
                )
            logger.warning(
                "Connection '%s' credential mismatch (expected %s, got %s), skipping",
                conn_name, expected_account, verified.account_id,
            )
            continue

        session = verified.session
        accounts_observed.add(verified.account_id)

        # Determine which packs this connection should run
        conn_packs: list[str] = []

        # Payer-scoped packs: run only from first successful connection
        if not payer_packs_done and payer_packs:
            conn_packs.extend(payer_packs)
            payer_connection_name = conn_name

        # Account-scoped packs: run from every connection
        conn_packs.extend(account_packs)

        if not conn_packs:
            exec_meta = ConnectionExecution(
                connection_name=conn_name,
                profile=profile,
                session_account_id=verified.account_id,
                role_arn=role_arn,
                status="success",
                duration_seconds=time.time() - conn_start,
                packs_attempted=[],
                packs_completed=[],
            )
            connection_executions.append(exec_meta)
            continue

        packs_attempted = list(conn_packs)

        # Run packs for this connection
        try:
            results = run_all_scans(
                session,
                regions,
                profile=profile,
                quick=quick,
                console=console,
                selected_packs=conn_packs,
                deep=deep,
                days=days,
            )
        except Exception as e:
            exec_meta = ConnectionExecution(
                connection_name=conn_name,
                profile=profile,
                session_account_id=verified.account_id,
                role_arn=role_arn,
                status="failed",
                duration_seconds=time.time() - conn_start,
                packs_attempted=packs_attempted,
                packs_completed=[],
                error_code=f"scan_error: {type(e).__name__}",
            )
            connection_executions.append(exec_meta)

            if conn == default_conn:
                raise DefaultConnectionFailedError(conn_name, str(e)) from e
            continue

        # Mark payer packs as done after first success
        if not payer_packs_done and payer_packs:
            payer_packs_done = True

        # Collect results
        for pack_name, pack_result in results.items():
            if pack_name not in all_pack_results:
                all_pack_results[pack_name] = []
            all_pack_results[pack_name].append(pack_result)

            # Tag findings with source connection
            for finding in pack_result.get("findings", []):
                finding["_source_connections"] = [conn_name]
                all_findings_raw.append(finding)

            if not pack_result.get("skipped"):
                packs_completed.append(pack_name)

        exec_meta = ConnectionExecution(
            connection_name=conn_name,
            profile=profile,
            session_account_id=verified.account_id,
            role_arn=role_arn,
            status="success",
            duration_seconds=time.time() - conn_start,
            packs_attempted=packs_attempted,
            packs_completed=packs_completed,
        )
        connection_executions.append(exec_meta)

    # Check if any connections succeeded
    successful = [c for c in connection_executions if c.status == "success"]
    if not successful:
        raise NoSuccessfulConnectionsError(
            [c.connection_name for c in connection_executions]
        )

    # Merge pack results (use first non-skipped result per pack for scores)
    merged_results: Dict[str, dict] = {}
    for pack_name, result_list in all_pack_results.items():
        # For payer-scoped packs: single result
        if pack_name in PAYER_SCOPED_PACKS:
            merged_results[pack_name] = result_list[0] if result_list else {}
        else:
            # Account-scoped: merge findings, take best scores
            merged_results[pack_name] = _merge_pack_results(result_list)

    # Deduplicate findings
    deduped_findings = deduplicate_findings(all_findings_raw)

    # Compute overall score
    overall_score, overall_grade = compute_overall(merged_results)

    # Determine report status
    total_connections = len(connections)
    successful_count = len(successful)
    if successful_count == total_connections:
        report_status: ReportStatus = "complete"
    elif successful_count > 0:
        report_status = "partial"
    else:
        report_status = "failed"

    duration = time.time() - start_time

    return ConsolidatedResult(
        results=merged_results,
        all_findings=deduped_findings,
        overall_score=overall_score,
        overall_grade=overall_grade,
        report_status=report_status,
        connections_executed=connection_executions,
        accounts_observed=sorted(accounts_observed),
        duration_seconds=duration,
        payer_connection=payer_connection_name,
    )


def _merge_pack_results(result_list: list[dict]) -> dict:
    """Merge multiple account-scoped results for the same pack.

    Combines findings lists, takes the best (non-skipped) scores.
    """
    if not result_list:
        return {}

    # Start with first non-skipped result as base
    base = None
    for r in result_list:
        if not r.get("skipped"):
            base = dict(r)
            break
    if base is None:
        base = dict(result_list[0])

    # Merge findings from all results
    all_findings = []
    for r in result_list:
        all_findings.extend(r.get("findings", []))
    base["findings"] = all_findings

    # Update finding count in scores
    if "scores" in base:
        base["scores"]["total_findings"] = len(all_findings)

    return base


def deduplicate_findings(findings: list[dict]) -> list[dict]:
    """Deduplicate findings by fingerprint.

    Rules:
    - Keep one finding per fingerprint.
    - Prefer the higher-confidence record.
    - Retain all contributing connection names in _source_connections.
    - Never sum duplicate cost totals.

    Args:
        findings: Raw findings list (may contain duplicates).

    Returns:
        Deduplicated findings list.
    """
    seen: Dict[str, dict] = {}  # fingerprint -> best finding

    for finding in findings:
        fp = finding.get("fingerprint", "")
        if not fp:
            # No fingerprint — keep as unique
            fp = finding.get("id", id(finding))

        if fp not in seen:
            seen[fp] = finding
        else:
            existing = seen[fp]
            # Merge source connections
            existing_sources = existing.get("_source_connections", [])
            new_sources = finding.get("_source_connections", [])
            merged_sources = list(set(existing_sources + new_sources))
            existing["_source_connections"] = merged_sources

            # Prefer higher confidence
            existing_conf = existing.get("confidence", 0)
            new_conf = finding.get("confidence", 0)
            if new_conf > existing_conf:
                # Replace with higher-confidence finding but keep merged sources
                finding["_source_connections"] = merged_sources
                seen[fp] = finding

    return list(seen.values())


class DefaultConnectionFailedError(Exception):
    """Default connection credential failure — abort before persistence."""

    def __init__(self, connection_name: str, reason: str):
        self.connection_name = connection_name
        self.reason = reason
        super().__init__(
            f"Default connection '{connection_name}' failed: {reason}"
        )


class NoSuccessfulConnectionsError(Exception):
    """No connections succeeded — cannot produce a report."""

    def __init__(self, attempted: list[str]):
        self.attempted = attempted
        super().__init__(
            f"No successful connections. Attempted: {', '.join(attempted)}"
        )
