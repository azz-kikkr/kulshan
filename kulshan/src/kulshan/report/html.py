"""Self-contained HTML report generator for Kulshan."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kulshan.__version__ import __version__
from kulshan.checks.cost.analyzers.advanced import generate_svg_sparkline
from kulshan.orchestrator import TOOL_LABELS, TOOL_ORDER
from kulshan.theme_constants import TOOL_ICONS, GRADE_COLORS_HEX, SEVERITY_COLORS_HEX

# Phase 6C-2: max finding cards rendered per cost subsection.
# Anything beyond this remains in the JSON output.
FINDINGS_PER_SUBSECTION = 25

# Phase 6C-3 follow-up: banner shown above the report header when the caller
# opts in via ``synthetic_sample=True``. Used by the sanitized sample
# generator; never emitted by the production CLI.
_SYNTHETIC_BANNER_HTML = (
    '<div class="synthetic-banner" role="note">'
    '<div class="synthetic-banner-title"><strong>Synthetic sample report</strong></div>'
    '<div class="synthetic-banner-body">'
    'This report uses fixture data only. No customer data, no real AWS '
    'account IDs, and no live AWS environment were used.'
    '</div>'
    '</div>'
)

# Grade color mapping (use shared constants)
_GRADE_COLORS = GRADE_COLORS_HEX

_SEV_COLORS = SEVERITY_COLORS_HEX


def _grade_color(grade: str) -> str:
    """Return hex color for a grade string like A+, B-, etc."""
    if grade in ("N/A", "--"):
        return "#9e9e9e"
    first = grade[0].upper() if grade else "F"
    return _GRADE_COLORS.get(first, "#c62828")


def _svg_dial(score: int, size: int = 120) -> str:
    """Return an inline SVG circle dial for a 0-100 score."""
    score = max(0, min(100, score))
    radius = size * 0.4
    stroke_width = size * 0.08
    cx = size / 2
    cy = size / 2
    circumference = 2 * 3.14159265 * radius
    offset = circumference * (1 - score / 100)
    # Determine color from score
    if score >= 90:
        color = _GRADE_COLORS["A"]
    elif score >= 80:
        color = _GRADE_COLORS["B"]
    elif score >= 70:
        color = _GRADE_COLORS["C"]
    elif score >= 60:
        color = _GRADE_COLORS["D"]
    else:
        color = _GRADE_COLORS["F"]

    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'class="score-dial" aria-label="Score {score} out of 100">'
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
        f'stroke="var(--track-color)" stroke-width="{stroke_width}" />'
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
        f'stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-dasharray="{circumference}" stroke-dashoffset="{offset:.1f}" '
        f'stroke-linecap="round" '
        f'transform="rotate(-90 {cx} {cy})" />'
        f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
        f'class="dial-text" fill="var(--text-primary)" '
        f'font-size="{size * 0.28}px" font-weight="700">{score}</text>'
        f'</svg>'
    )


def _svg_bar(score: int, width: int = 200, height: int = 12) -> str:
    """Return an inline SVG horizontal bar for a 0-100 score."""
    score = max(0, min(100, score))
    fill_width = int(width * score / 100)
    if score >= 90:
        color = _GRADE_COLORS["A"]
    elif score >= 80:
        color = _GRADE_COLORS["B"]
    elif score >= 70:
        color = _GRADE_COLORS["C"]
    elif score >= 60:
        color = _GRADE_COLORS["D"]
    else:
        color = _GRADE_COLORS["F"]

    rx = height // 2
    return (
        f'<svg width="{width}" height="{height}" '
        f'aria-label="Score bar {score} percent">'
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="{rx}" '
        f'fill="var(--track-color)" />'
        f'<rect x="0" y="0" width="{fill_width}" height="{height}" rx="{rx}" '
        f'fill="{color}" />'
        f'</svg>'
    )


def generate_html_report(
    results: dict,
    overall_score: int,
    overall_grade: str,
    account_id: str,
    regions: list,
    duration_secs: float,
    top_actions: Optional[List[dict]] = None,
    synthetic_sample: bool = False,
    coverage: Optional[dict] = None,
) -> str:
    """Return a complete self-contained HTML string for the Kulshan Report.

    When ``synthetic_sample=True`` a banner is rendered above the header
    declaring the report uses fixture data. Used by the sample generator;
    the CLI never sets this.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    esc = html.escape

    # Determine which packs actually ran (not skipped)
    ran_packs = [k for k in TOOL_ORDER if k in results and not results[k].get("skipped")]

    # Executive Summary (prose paragraph)
    executive_summary_html = _build_executive_summary(results, overall_score, overall_grade, top_actions or [])

    # What To Do Next (top actions, renamed)
    what_to_do_html = _build_what_to_do_next(top_actions or [], ran_packs)

    # Addressable Savings (Money on Fire, renamed)
    addressable_savings_html = _build_addressable_savings(results, top_actions or [])

    # Commitment Health (merged: old KPI + purchase mix)
    commitment_health_html = _build_commitment_health(results)

    # Spend Concentration (renamed from HHI)
    spend_concentration_html = _build_spend_concentration(results)

    # Spend Trend (renamed from Cost Velocity)
    spend_trend_html = _build_spend_trend(results)

    # Detailed Breakdown (only packs that ran)
    details_html = _build_detailed_breakdown(results, ran_packs)

    # Synthetic-sample banner: opt-in, default off.
    synthetic_banner_html = _SYNTHETIC_BANNER_HTML if synthetic_sample else ""
    coverage_html = _build_coverage_summary(coverage)

    # Cost Health Score (small, contextual — not a hero dial)
    score_label = "Cost Health Score" if ran_packs == ["cost"] else "Overall Score"

    return _HTML_TEMPLATE.format(
        css=_CSS,
        js=_JS,
        version=esc(__version__),
        account_id=esc(str(account_id)),
        regions_count=len(regions),
        duration=f"{duration_secs:.1f}",
        timestamp=esc(timestamp),
        overall_grade=esc(overall_grade),
        overall_score=overall_score,
        score_label=esc(score_label),
        hero_color=_grade_color(overall_grade),
        executive_summary=executive_summary_html,
        what_to_do_next=what_to_do_html,
        addressable_savings=addressable_savings_html,
        commitment_health=commitment_health_html,
        spend_concentration=spend_concentration_html,
        spend_trend=spend_trend_html,
        detailed_breakdown=details_html,
        synthetic_banner=synthetic_banner_html,
        coverage_summary=coverage_html,
    )


def _build_coverage_summary(coverage: Optional[dict]) -> str:
    """Render the canonical coverage disclosure without exposing identifiers."""
    if not coverage:
        return '<section class="coverage-section"><h2>Coverage</h2><p>Coverage not recorded.</p></section>'
    summary = coverage.get("summary", {})
    status = html.escape(str(summary.get("report_status", "unknown")))
    completed = int(summary.get("packs_completed", 0))
    attempted = int(summary.get("packs_attempted", 0))
    regions = int(summary.get("regions_scanned", 0))
    denied = len(coverage.get("denied_actions", []))
    warnings = coverage.get("warnings", [])
    warning_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in warnings)
    return (
        '<section class="coverage-section"><h2>Coverage</h2>'
        f'<p>Status: <strong>{status}</strong> · {completed}/{attempted} packs · {regions} regions · {denied} denied actions</p>'
        f'{"<ul>" + warning_html + "</ul>" if warning_html else ""}'
        '</section>'
    )

def _build_tool_cards(results: dict) -> str:
    """Build the grid of tool scorecard HTML."""
    cards: list[str] = []
    for key in TOOL_ORDER:
        result = results.get(key, {})
        scores = result.get("scores", {})
        skipped = result.get("skipped", False)
        label = TOOL_LABELS.get(key, key)
        icon = TOOL_ICONS.get(key, "")
        score = scores.get("overall_score", 0)
        grade = scores.get("grade", "N/A")
        findings = scores.get("total_findings", 0)
        color = _grade_color(grade)

        if skipped:
            dial = _svg_dial(0, size=90)
            cards.append(
                f'<div class="tool-card skipped">'
                f'<div class="tool-icon">{icon}</div>'
                f'<div class="tool-name">{html.escape(label)}</div>'
                f'<div class="tool-dial">{dial}</div>'
                f'<div class="tool-grade" style="color:#9e9e9e">N/A</div>'
                f'<div class="tool-findings">Skipped</div>'
                f'</div>'
            )
        else:
            dial = _svg_dial(score, size=90)
            cards.append(
                f'<div class="tool-card">'
                f'<div class="tool-icon">{icon}</div>'
                f'<div class="tool-name">{html.escape(label)}</div>'
                f'<div class="tool-dial">{dial}</div>'
                f'<div class="tool-grade" style="color:{color}">{html.escape(grade)}</div>'
                f'<div class="tool-findings">{findings} finding{"s" if findings != 1 else ""}</div>'
                f'</div>'
            )
    return "\n".join(cards)


def _build_severity_summary(results: dict) -> str:
    """Build severity badge HTML."""
    totals: Dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for result in results.values():
        if result.get("skipped"):
            continue
        sev = result.get("scores", {}).get("severity_counts", {})
        for k in totals:
            totals[k] += int(sev.get(k, 0))

    badges: list[str] = []
    for level, count in totals.items():
        color = _SEV_COLORS[level]
        badges.append(
            f'<span class="sev-badge" style="background:{color}">'
            f'{level.capitalize()}: {count}</span>'
        )
    return "\n".join(badges)


def _build_tool_details(results: dict) -> str:
    """Build expandable per-tool detail sections."""
    sections: list[str] = []
    for key in TOOL_ORDER:
        result = results.get(key, {})
        scores = result.get("scores", {})
        skipped = result.get("skipped", False)
        label = TOOL_LABELS.get(key, key)
        icon = TOOL_ICONS.get(key, "")
        score = scores.get("overall_score", 0)
        grade = scores.get("grade", "N/A")
        errors = result.get("errors", [])
        color = _grade_color(grade)
        bar = _svg_bar(score, width=300, height=14)

        status = "Skipped" if skipped else f"{score}/100"

        # Build breakdown rows from scores dict (exclude known meta keys)
        meta_keys = {"overall_score", "grade", "total_findings", "severity_counts"}
        breakdown_rows: list[str] = []
        for k, v in scores.items():
            if k in meta_keys:
                continue
            if isinstance(v, (int, float)):
                sub_bar = _svg_bar(int(v), width=200, height=10)
                breakdown_rows.append(
                    f'<tr><td class="detail-label">{html.escape(str(k))}</td>'
                    f'<td class="detail-score">{v}</td>'
                    f'<td>{sub_bar}</td></tr>'
                )

        breakdown_table = ""
        if breakdown_rows:
            breakdown_table = (
                '<table class="breakdown-table">'
                '<thead><tr><th>Category</th><th>Score</th><th></th></tr></thead>'
                '<tbody>' + "\n".join(breakdown_rows) + '</tbody></table>'
            )

        # Phase 6C-2: cost pack gets findings + overlap summary inline.
        pack_extras = ""
        if key == "cost" and not skipped:
            pack_extras = _build_cost_findings_section(result)

        error_html = ""
        if errors:
            error_items = "\n".join(
                f"<li>{html.escape(str(e))}</li>" for e in errors
            )
            error_html = f'<div class="error-list"><strong>Errors / Warnings:</strong><ul>{error_items}</ul></div>'

        sections.append(
            f'<details class="tool-detail">'
            f'<summary>'
            f'<span class="detail-icon">{icon}</span>'
            f'<span class="detail-name">{html.escape(label)}</span>'
            f'<span class="detail-grade" style="color:{color}">{html.escape(grade)}</span>'
            f'<span class="detail-status">{status}</span>'
            f'</summary>'
            f'<div class="detail-body">'
            f'<div class="detail-bar">{bar}</div>'
            f'{breakdown_table}'
            f'{pack_extras}'
            f'{error_html}'
            f'</div>'
            f'</details>'
        )
    return "\n".join(sections)


# ── Commitment KPI + Money on Fire sections ──────────────────────────────────


def _build_commitment_kpi(results: dict) -> str:
    """Build the Commitment Health KPI section from cost pack breakdown data."""
    cost_result = results.get("cost") or {}
    if cost_result.get("skipped"):
        return ""

    scores = cost_result.get("scores", {})
    breakdown = scores.get("breakdown", {})
    if not breakdown:
        return ""

    # Extract commitment metrics from the breakdown
    ri_coverage = breakdown.get("ri_coverage_pct", 0)
    sp_utilization = breakdown.get("sp_utilization_pct", 0)
    sp_coverage = breakdown.get("sp_coverage_pct", 0)
    unused_commitment = breakdown.get("unused_commitment_usd", 0)

    # If no meaningful data, skip
    if ri_coverage == 0 and sp_utilization == 0 and sp_coverage == 0:
        return ""

    # Build progress bars
    def _kpi_bar(value: float, target: float = 80, label: str = "", suffix: str = "%") -> str:
        pct = min(100, max(0, value))
        color = "#2e7d32" if pct >= target else "#f57f17" if pct >= target * 0.7 else "#c62828"
        return (
            f'<div class="kpi-row">'
            f'<span class="kpi-label">{label}</span>'
            f'<div class="kpi-bar-track">'
            f'<div class="kpi-bar-fill" style="width:{pct}%;background:{color}"></div>'
            f'<div class="kpi-target-line" style="left:{target}%"></div>'
            f'</div>'
            f'<span class="kpi-value">{value:.0f}{suffix}</span>'
            f'</div>'
        )

    rows = []
    if ri_coverage > 0:
        rows.append(_kpi_bar(ri_coverage, 80, "RI Coverage"))
    if sp_coverage > 0:
        rows.append(_kpi_bar(sp_coverage, 70, "SP Coverage"))
    if sp_utilization > 0:
        rows.append(_kpi_bar(sp_utilization, 90, "SP Utilization"))

    unused_html = ""
    if unused_commitment > 0:
        unused_html = f'<div class="kpi-unused">Unused commitment: <strong>${unused_commitment:,.0f}/mo</strong></div>'

    if not rows:
        return ""

    return (
        '<h2 class="section-title">Commitment Health</h2>\n'
        '<div class="commitment-kpi">\n'
        + "\n".join(rows)
        + unused_html
        + '\n</div>'
    )


def _build_money_on_fire(results: dict, top_actions: list) -> str:
    """Build the Money on Fire hero section showing total addressable savings."""
    # Sum up all monthly savings from findings across all packs
    total_savings = 0.0
    savings_by_source: dict = {}

    for f in top_actions:
        impact = 0.0
        try:
            impact = float(f.get("estimated_monthly_impact", 0) or 0)
        except (TypeError, ValueError):
            pass
        if impact > 0:
            total_savings += impact
            pack = f.get("pack", "other")
            savings_by_source[pack] = savings_by_source.get(pack, 0) + impact

    # Also include sweep monthly_waste if available
    sweep_result = results.get("sweep") or {}
    sweep_waste = sweep_result.get("scores", {}).get("monthly_waste", 0) or 0
    if sweep_waste > 0 and "sweep" not in savings_by_source:
        total_savings += sweep_waste
        savings_by_source["sweep"] = sweep_waste

    if total_savings < 1:
        return ""

    # Build breakdown rows
    source_labels = {
        "cost": "Cost anomalies & rightsizing",
        "sweep": "Orphaned resources",
        "security": "Security risk reduction",
        "dr": "DR improvements",
        "limit": "Quota optimization",
    }

    breakdown_rows = []
    for pack, amount in sorted(savings_by_source.items(), key=lambda x: -x[1]):
        label = source_labels.get(pack, pack.capitalize())
        breakdown_rows.append(
            f'<div class="mof-row">'
            f'<span class="mof-label">{html.escape(label)}</span>'
            f'<span class="mof-amount">${amount:,.0f}/mo</span>'
            f'</div>'
        )

    breakdown_html = "\n".join(breakdown_rows) if breakdown_rows else ""

    return (
        '<div class="money-on-fire">\n'
        f'<div class="mof-hero">'
        f'<span class="mof-icon">🔥</span>'
        f'<span class="mof-total">${total_savings:,.0f}<span class="mof-per">/month</span></span>'
        f'</div>\n'
        f'<div class="mof-subtitle">Total addressable savings identified</div>\n'
        f'<div class="mof-breakdown">{breakdown_html}</div>\n'
        '</div>'
    )


# ── Phase 6C-2: top actions + cost findings + overlap helpers ────────────────


def _source_label(kind: str) -> str:
    """Map finding kind → display source label. Mirrors terminal renderer."""
    if isinstance(kind, str) and kind.startswith("anomaly_aws_native"):
        return "AWS"
    return "Kulshan"


def _money(v: Any) -> str:
    """Format a numeric value as ``$1,234.56``. Empty string on bad input."""
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def _pct(v: Any) -> str:
    """Format a 0-1 confidence as ``95%``. Empty string on bad input."""
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return ""


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate long strings with an ellipsis."""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _build_top_actions_section(top_actions: List[dict]) -> str:
    """Ranked Top Actions table. Returns empty string when no actions."""
    if not top_actions:
        return ""

    rows: list[str] = []
    for i, f in enumerate(top_actions, start=1):
        sev = str(f.get("severity") or "info")
        sev_color = _SEV_COLORS.get(sev, "#9e9e9e")
        impact = _money(f.get("estimated_monthly_impact"))
        conf = _pct(f.get("confidence"))
        source = _source_label(str(f.get("kind") or ""))
        title = html.escape(_truncate(str(f.get("title") or ""), 160))
        rec = f.get("recommended_action") or ""
        rec_html = (
            f'<div class="ta-rec">{html.escape(_truncate(str(rec), 220))}</div>'
            if rec
            else ""
        )
        rows.append(
            f'<tr class="ta-row">'
            f'<td class="ta-rank">{i}</td>'
            f'<td><span class="sev-pill" style="background:{sev_color}">'
            f'{html.escape(sev.capitalize())}</span></td>'
            f'<td class="ta-impact">{impact}</td>'
            f'<td class="ta-conf">{conf}</td>'
            f'<td class="ta-source">{html.escape(source)}</td>'
            f'<td><div class="ta-title">{title}</div>{rec_html}</td>'
            f'</tr>'
        )

    return (
        '<h2 class="section-title">Top Actions</h2>\n'
        '<table class="top-actions-table">\n'
        '<thead><tr>'
        '<th>#</th><th>Severity</th><th>Monthly impact</th>'
        '<th>Confidence</th><th>Source</th><th>Action</th>'
        '</tr></thead>\n'
        '<tbody>\n' + "\n".join(rows) + '\n</tbody>\n'
        '</table>'
    )


def _build_cost_findings_section(cost_result: dict) -> str:
    """Overlap summary + grouped finding cards for the cost pack.

    Returns empty string when there are neither findings nor metadata.
    """
    findings = cost_result.get("findings") or []
    metadata = cost_result.get("metadata") or {}
    cad = metadata.get("cost_anomaly_detection")

    if not findings and not cad:
        return ""

    parts: list[str] = []

    if cad:
        parts.append(_build_overlap_summary(cad))

    if findings:
        by_kind: Dict[str, list[dict]] = {}
        for f in findings:
            if not isinstance(f, dict):
                continue
            kind = str(f.get("kind") or "other")
            by_kind.setdefault(kind, []).append(f)

        kind_label = {
            "anomaly_statistical": "Kulshan statistical anomalies",
            "anomaly_aws_native": "AWS-native Cost Anomaly Detection",
        }
        canonical = ["anomaly_statistical", "anomaly_aws_native"]
        rendered: set[str] = set()
        for kind in canonical:
            if kind in by_kind:
                parts.append(
                    _build_findings_subsection(kind_label[kind], by_kind[kind])
                )
                rendered.add(kind)
        for kind, fs in by_kind.items():
            if kind in rendered:
                continue
            parts.append(
                _build_findings_subsection(f"Other cost findings ({kind})", fs)
            )

    return '<div class="cost-findings">\n' + "\n".join(parts) + "\n</div>"


def _build_findings_subsection(label: str, findings: list) -> str:
    """One labeled subsection of finding cards, capped at FINDINGS_PER_SUBSECTION."""
    total = len(findings)
    shown = findings[:FINDINGS_PER_SUBSECTION]
    cards = "\n".join(_build_finding_card(f) for f in shown)
    truncation = ""
    if total > FINDINGS_PER_SUBSECTION:
        truncation = (
            f'<p class="findings-truncation">'
            f'Showing {FINDINGS_PER_SUBSECTION} of {total}. Full list in JSON.'
            f'</p>'
        )
    return (
        f'<h3 class="findings-subsection">{html.escape(label)}</h3>\n'
        f'<div class="finding-cards">\n{cards}\n</div>\n{truncation}'
    )


def _build_finding_card(f: dict) -> str:
    """One finding rendered as a card. Empty fields omitted."""
    sev = str(f.get("severity") or "info")
    sev_color = _SEV_COLORS.get(sev, "#9e9e9e")
    title = html.escape(str(f.get("title") or ""))
    impact = _money(f.get("estimated_monthly_impact"))
    conf = _pct(f.get("confidence"))

    impact_html = (
        f'<span class="finding-impact">{impact}/mo</span>' if impact else ""
    )
    conf_html = (
        f'<span class="finding-conf">{conf} confidence</span>' if conf else ""
    )

    meta_pairs = (
        ("Account", f.get("account")),
        ("Region", f.get("region")),
        ("Service", f.get("service")),
        ("Usage type", f.get("usage_type")),
        ("Operation", f.get("operation")),
        ("Resource", f.get("resource_id")),
    )
    meta_rows: list[str] = []
    for k, v in meta_pairs:
        if v in (None, "", [], {}):
            continue
        meta_rows.append(
            f"<dt>{html.escape(k)}</dt><dd>{html.escape(str(v))}</dd>"
        )
    meta_html = ""
    if meta_rows:
        meta_html = '<dl class="finding-meta">' + "".join(meta_rows) + "</dl>"

    why = f.get("why_it_matters")
    why_html = (
        f'<p class="finding-why">{html.escape(str(why))}</p>' if why else ""
    )

    rec = f.get("recommended_action")
    rec_html = (
        f'<p class="finding-rec"><strong>Recommended:</strong> '
        f"{html.escape(str(rec))}</p>"
        if rec
        else ""
    )

    evidence = f.get("evidence")
    evidence_html = ""
    if isinstance(evidence, dict) and evidence:
        evidence_html = _build_evidence_block(evidence)

    return (
        '<div class="finding-card">'
        '<div class="finding-head">'
        f'<span class="sev-pill" style="background:{sev_color}">'
        f"{html.escape(sev.capitalize())}</span>"
        f"{impact_html}{conf_html}"
        "</div>"
        f'<div class="finding-title">{title}</div>'
        f"{meta_html}{why_html}{rec_html}{evidence_html}"
        "</div>"
    )


def _build_evidence_block(evidence: dict) -> str:
    """Collapsible evidence as a flat key/value table. No JSON dump."""
    rows: list[str] = []
    for k, v in evidence.items():
        if v in (None, "", [], {}):
            continue
        if isinstance(v, list):
            display = ", ".join(html.escape(str(x)) for x in v)
        elif isinstance(v, dict):
            display = ", ".join(
                f"{html.escape(str(kk))}={html.escape(str(vv))}"
                for kk, vv in v.items()
            )
        else:
            display = html.escape(str(v))
        rows.append(
            f"<tr><th>{html.escape(str(k))}</th><td>{display}</td></tr>"
        )
    if not rows:
        return ""
    return (
        '<details class="finding-evidence">'
        "<summary>Evidence</summary>"
        '<table class="evidence-table">'
        + "".join(rows)
        + "</table>"
        "</details>"
    )


def _build_overlap_summary(cad: dict) -> str:
    """AWS Cost Anomaly Detection vs Kulshan overlap summary block."""
    status = cad.get("status", "")
    anomaly_count = cad.get("anomaly_count")
    lookback_days = cad.get("lookback_days")
    overlap = cad.get("overlap") or {}
    both = overlap.get("both_count", 0)
    reck_only = overlap.get("Kulshan_only_count", 0)
    aws_only = overlap.get("aws_only_count", 0)
    details = cad.get("details") or ""

    status_bits: list[str] = [
        f"AWS Cost Anomaly Detection: <strong>{html.escape(str(status))}</strong>"
    ]
    if isinstance(anomaly_count, int):
        plural = "anomaly" if anomaly_count == 1 else "anomalies"
        status_bits.append(f"{anomaly_count} {plural}")
    if isinstance(lookback_days, int):
        status_bits.append(f"{lookback_days}-day lookback")

    details_html = ""
    if details:
        details_html = (
            f'<div class="overlap-details">{html.escape(str(details))}</div>'
        )

    return (
        '<div class="overlap-summary">'
        f'<div class="overlap-status">{" · ".join(status_bits)}</div>'
        '<div class="overlap-counts">'
        f'<span class="ovc-both">{both} confirmed by Kulshan</span>'
        f'<span class="ovc-reck">{reck_only} Kulshan-only</span>'
        f'<span class="ovc-aws">{aws_only} AWS-only</span>'
        "</div>"
        f"{details_html}"
        "</div>"
    )


# ── New Report IA: Executive Summary, What To Do Next, etc. ───────────────────


def _build_executive_summary(results: dict, overall_score: int, overall_grade: str, top_actions: list) -> str:
    """Build a 3-5 sentence executive summary paragraph."""
    cost = results.get("cost") or {}
    scores = cost.get("scores", {})
    total_spend = scores.get("total_spend", 0)
    metadata = cost.get("metadata", {})
    velocity = metadata.get("cost_velocity", {})
    purchase_mix = metadata.get("purchase_mix") or {}

    sentences: list[str] = []

    # Spend
    if total_spend and abs(total_spend) > 1:
        sentences.append(f"This account spent <strong>${total_spend:,.0f}</strong> over the analysis period.")
    else:
        sentences.append("This account has near-zero AWS spend, so cost analysis is limited. Run Kulshan against an account with meaningful spend for richer recommendations.")

    # Trend
    trend = velocity.get("trend", "")
    vel_pct = velocity.get("velocity_pct", 0)
    if trend and abs(vel_pct) > 0.5 and total_spend > 1:
        if vel_pct > 0:
            sentences.append(f"Spend is <strong>increasing</strong> at {vel_pct:.1f}% per day.")
        else:
            sentences.append(f"Spend is <strong>decreasing</strong> at {abs(vel_pct):.1f}% per day.")

    # Commitment
    committed_pct = purchase_mix.get("committed_pct", 0)
    on_demand_pct = purchase_mix.get("on_demand_pct", 0)
    if on_demand_pct > 50 and total_spend > 1:
        sentences.append(f"{on_demand_pct:.0f}% of spend is on-demand — commitment strategy recommended.")
    elif committed_pct > 60:
        sentences.append(f"{committed_pct:.0f}% of spend is committed — good coverage.")

    # Savings
    total_savings = sum(float(a.get("estimated_monthly_impact", 0) or 0) for a in top_actions if float(a.get("estimated_monthly_impact", 0) or 0) > 0)
    if total_savings > 1:
        sentences.append(f"<strong>${total_savings:,.0f}/month</strong> in addressable savings identified.")

    # Top action
    if top_actions:
        top = top_actions[0]
        title = str(top.get("title", ""))[:80]
        sentences.append(f"Top priority: {html.escape(title)}.")

    # Grade
    if overall_grade not in ("N/A", "--"):
        sentences.append(f"Cost Health Score: <strong>{overall_score}/100 ({overall_grade})</strong>.")

    if not sentences:
        return ""

    return (
        '<div class="exec-summary">\n'
        '<h2 class="section-title">Executive Summary</h2>\n'
        f'<p class="exec-text">{"  ".join(sentences)}</p>\n'
        '</div>'
    )


def _build_what_to_do_next(top_actions: list, ran_packs: list) -> str:
    """Build the 'What To Do Next' section — prioritized actions table."""
    if not top_actions:
        return ""

    rows: list[str] = []
    for i, f in enumerate(top_actions[:10], start=1):
        sev = str(f.get("severity") or "info")
        sev_color = _SEV_COLORS.get(sev, "#9e9e9e")
        title = html.escape(_truncate(str(f.get("title") or ""), 120))
        rec = f.get("recommended_action") or ""
        rec_html = f'<div class="action-rec">{html.escape(_truncate(str(rec), 180))}</div>' if rec else ""

        # Impact: show $ if available, otherwise show "Risk" or "Visibility"
        impact_val = float(f.get("estimated_monthly_impact", 0) or 0)
        if impact_val >= 1:
            impact = f"${impact_val:,.0f}/mo"
        else:
            # Non-monetary impact
            pack = f.get("pack", "")
            if pack == "security":
                impact = "Risk reduction"
            elif pack in ("pulse", "drift"):
                impact = "Visibility"
            else:
                impact = "—"

        effort = str(f.get("effort", "medium")).capitalize()

        rows.append(
            f'<tr>'
            f'<td class="action-rank">{i}</td>'
            f'<td><span class="sev-pill" style="background:{sev_color}">{html.escape(sev.capitalize())}</span></td>'
            f'<td><div class="action-title">{title}</div>{rec_html}</td>'
            f'<td class="action-impact">{impact}</td>'
            f'<td class="action-effort">{effort}</td>'
            f'</tr>'
        )

    return (
        '<h2 class="section-title">What To Do Next</h2>\n'
        '<table class="actions-table">\n'
        '<thead><tr><th>#</th><th>Severity</th><th>Action</th><th>Impact</th><th>Effort</th></tr></thead>\n'
        '<tbody>\n' + "\n".join(rows) + '\n</tbody>\n</table>'
    )


def _build_addressable_savings(results: dict, top_actions: list) -> str:
    """Build the Addressable Savings section (renamed Money on Fire)."""
    return _build_money_on_fire(results, top_actions)


def _build_commitment_health(results: dict) -> str:
    """Build merged Commitment Health section (old KPI + purchase mix)."""
    cost_result = results.get("cost") or {}
    if cost_result.get("skipped"):
        return ""

    metadata = cost_result.get("metadata") or {}
    scores = cost_result.get("scores", {})
    breakdown = scores.get("breakdown", {})
    purchase_mix = metadata.get("purchase_mix") or {}

    # Get commitment data from breakdown
    ri_sp_cov = breakdown.get("ri_sp_coverage", {})
    ri_sp_util = breakdown.get("ri_sp_utilization", {})

    # Get purchase mix percentages
    on_demand_pct = purchase_mix.get("on_demand_pct", 0)
    committed_pct = purchase_mix.get("committed_pct", 0)
    spot_pct = purchase_mix.get("spot_pct", 0)

    # If no meaningful data, skip
    has_kpi = ri_sp_cov and ri_sp_cov.get("score", 0) > 0
    has_mix = committed_pct > 0 or on_demand_pct > 0

    if not has_kpi and not has_mix:
        return ""

    parts: list[str] = []

    # KPI bars
    def _bar(label: str, value: float, target: float, max_score: float, actual_score: float) -> str:
        pct = min(100, max(0, value))
        color = "#2e7d32" if pct >= target else "#f57f17" if pct >= target * 0.7 else "#c62828"
        status = "✓" if pct >= target * 0.9 else ""
        return (
            f'<div class="kpi-row">'
            f'<span class="kpi-label">{label}</span>'
            f'<div class="kpi-bar-track"><div class="kpi-bar-fill" style="width:{pct}%;background:{color}"></div></div>'
            f'<span class="kpi-value">{value:.0f}% {status}</span>'
            f'</div>'
        )

    if has_kpi:
        cov_val = float(ri_sp_cov.get("value", "0").replace("%", "")) if isinstance(ri_sp_cov.get("value"), str) else 0
        util_val = float(ri_sp_util.get("value", "0").replace("%", "")) if isinstance(ri_sp_util.get("value"), str) else 0
        if cov_val > 0:
            parts.append(_bar("RI/SP Coverage", cov_val, 80, 25, ri_sp_cov.get("score", 0)))
        if util_val > 0:
            parts.append(_bar("RI/SP Utilization", util_val, 90, 25, ri_sp_util.get("score", 0)))

    # On-demand / committed summary
    if has_mix:
        parts.append(
            f'<div class="commitment-summary">'
            f'<span class="cs-item">Committed: <strong>{committed_pct:.0f}%</strong></span>'
            f'<span class="cs-item">On-Demand: <strong>{on_demand_pct:.0f}%</strong></span>'
            f'<span class="cs-item">Spot: <strong>{spot_pct:.0f}%</strong></span>'
            f'</div>'
        )

    # Assessment
    if on_demand_pct > 70:
        parts.append('<p class="kpi-assessment">Heavy on-demand exposure — consider Savings Plans or Reserved Instances.</p>')
    elif committed_pct > 60:
        parts.append('<p class="kpi-assessment">Good commitment coverage.</p>')

    if not parts:
        return ""

    return (
        '<h2 class="section-title">Commitment Health</h2>\n'
        '<div class="commitment-health">\n'
        + "\n".join(parts) +
        '\n</div>'
    )


def _build_spend_concentration(results: dict) -> str:
    """Build Spend Concentration section (renamed from HHI, no jargon)."""
    cost_result = results.get("cost") or {}
    if cost_result.get("skipped"):
        return ""

    metadata = cost_result.get("metadata") or {}
    hhi = metadata.get("hhi_concentration") or {}
    top_services = hhi.get("top_services", [])

    if not top_services:
        return ""

    # Build service bars
    svc_bars: list[str] = []
    for svc in top_services[:5]:
        name = html.escape(str(svc.get("service", "")))
        pct = svc.get("pct", 0)
        svc_bars.append(
            f'<div class="conc-row">'
            f'<span class="conc-name">{name}</span>'
            f'<div class="conc-bar-track"><div class="conc-bar-fill" style="width:{pct}%"></div></div>'
            f'<span class="conc-pct">{pct:.0f}%</span>'
            f'</div>'
        )

    # Plain-English interpretation
    interpretation = ""
    if top_services:
        top_svc = top_services[0]
        top_name = top_svc.get("service", "")
        top_pct = top_svc.get("pct", 0)
        if top_pct > 60:
            interpretation = f'<p class="conc-note">{html.escape(top_name)} represents {top_pct:.0f}% of spend. Review compute purchasing, rightsizing, and architecture opportunities first.</p>'
        elif top_pct > 40:
            interpretation = f'<p class="conc-note">{html.escape(top_name)} is the largest cost driver at {top_pct:.0f}%. Spend is moderately diversified.</p>'
        else:
            interpretation = '<p class="conc-note">Spend is well-diversified across services.</p>'

    return (
        '<h2 class="section-title">Spend Concentration</h2>\n'
        '<div class="spend-concentration">\n'
        + "\n".join(svc_bars) + "\n"
        + interpretation +
        '\n</div>'
    )


def _build_spend_trend(results: dict) -> str:
    """Build Spend Trend section (renamed from Cost Velocity, no jargon)."""
    cost_result = results.get("cost") or {}
    if cost_result.get("skipped"):
        return ""

    metadata = cost_result.get("metadata") or {}
    velocity = metadata.get("cost_velocity") or {}
    daily_costs = velocity.get("daily_costs", [])

    if not velocity or velocity.get("trend") in ("no data", "insufficient data", None):
        return ""

    daily_avg = velocity.get("daily_avg", 0)
    vel_pct = velocity.get("velocity_pct", 0)

    # Sparkline
    sparkline_svg = ""
    if daily_costs and len(daily_costs) >= 3:
        vel = velocity.get("velocity", 0)
        if vel > 0:
            spark_color = "#ef5350"
        elif vel < 0:
            spark_color = "#66bb6a"
        else:
            spark_color = "#1565c0"
        sparkline_svg = generate_svg_sparkline(daily_costs, width=320, height=60, color=spark_color)

    # Simple trend language
    if abs(vel_pct) < 0.5:
        trend_text = "Stable"
    elif vel_pct > 0:
        trend_text = f"Increasing (+{vel_pct:.1f}%/day)"
    else:
        trend_text = f"Decreasing ({vel_pct:.1f}%/day)"

    return (
        '<h2 class="section-title">Spend Trend</h2>\n'
        '<div class="spend-trend">\n'
        f'<div class="trend-metrics">'
        f'<span class="trend-item">Daily average: <strong>${daily_avg:,.0f}</strong></span>'
        f'<span class="trend-item">Trend: <strong>{trend_text}</strong></span>'
        f'</div>\n'
        f'<div class="trend-sparkline">{sparkline_svg}</div>\n'
        '</div>'
    )


def _build_detailed_breakdown(results: dict, ran_packs: list) -> str:
    """Build Detailed Breakdown — only show packs that actually ran."""
    if not ran_packs:
        return ""

    sections: list[str] = []
    for key in ran_packs:
        result = results.get(key, {})
        scores = result.get("scores", {})
        label = TOOL_LABELS.get(key, key)
        icon = TOOL_ICONS.get(key, "")
        score = scores.get("overall_score", 0)
        grade = scores.get("grade", "N/A")
        errors = result.get("errors", [])
        color = _grade_color(grade)
        bar = _svg_bar(score, width=300, height=14)

        # Build breakdown rows
        meta_keys = {"overall_score", "grade", "total_findings", "severity_counts"}
        breakdown_rows: list[str] = []
        for k, v in scores.items():
            if k in meta_keys:
                continue
            if isinstance(v, (int, float)):
                sub_bar = _svg_bar(int(v), width=200, height=10)
                breakdown_rows.append(
                    f'<tr><td class="detail-label">{html.escape(str(k))}</td>'
                    f'<td class="detail-score">{v}</td><td>{sub_bar}</td></tr>'
                )

        breakdown_table = ""
        if breakdown_rows:
            breakdown_table = (
                '<table class="breakdown-table">'
                '<thead><tr><th>Category</th><th>Score</th><th></th></tr></thead>'
                '<tbody>' + "\n".join(breakdown_rows) + '</tbody></table>'
            )

        # Cost pack gets findings inline
        pack_extras = ""
        if key == "cost":
            pack_extras = _build_cost_findings_section(result)

        error_html = ""
        if errors:
            error_items = "\n".join(f"<li>{html.escape(str(e))}</li>" for e in errors)
            error_html = f'<div class="error-list"><strong>Errors:</strong><ul>{error_items}</ul></div>'

        sections.append(
            f'<details class="tool-detail">'
            f'<summary>'
            f'<span class="detail-icon">{icon}</span>'
            f'<span class="detail-name">{html.escape(label)}</span>'
            f'<span class="detail-grade" style="color:{color}">{html.escape(grade)}</span>'
            f'<span class="detail-status">{score}/100</span>'
            f'</summary>'
            f'<div class="detail-body">'
            f'<div class="detail-bar">{bar}</div>'
            f'{breakdown_table}{pack_extras}{error_html}'
            f'</div></details>'
        )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
_CSS = """\
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f5f5f5;
  --bg-card: #ffffff;
  --text-primary: #212121;
  --text-secondary: #616161;
  --border-color: #e0e0e0;
  --track-color: #e0e0e0;
  --shadow: 0 1px 3px rgba(0,0,0,0.1), 0 1px 2px rgba(0,0,0,0.06);
  --shadow-hover: 0 4px 12px rgba(0,0,0,0.1);
}
@media (prefers-color-scheme: dark) {
  :root:not(.light-mode) {
    --bg-primary: #121212;
    --bg-secondary: #1e1e1e;
    --bg-card: #252525;
    --text-primary: #e0e0e0;
    --text-secondary: #9e9e9e;
    --border-color: #333333;
    --track-color: #333333;
    --shadow: 0 1px 3px rgba(0,0,0,0.4);
    --shadow-hover: 0 4px 12px rgba(0,0,0,0.5);
  }
}
.dark-mode {
  --bg-primary: #121212;
  --bg-secondary: #1e1e1e;
  --bg-card: #252525;
  --text-primary: #e0e0e0;
  --text-secondary: #9e9e9e;
  --border-color: #333333;
  --track-color: #333333;
  --shadow: 0 1px 3px rgba(0,0,0,0.4);
  --shadow-hover: 0 4px 12px rgba(0,0,0,0.5);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.5;
  padding: 0;
  margin: 0;
}
.container { max-width: 1100px; margin: 0 auto; padding: 24px 20px; }

/* Header */
.header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 20px 0; border-bottom: 2px solid var(--border-color); margin-bottom: 32px;
}
.header-brand h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.02em; }
.header-brand h1 span { color: #1565c0; }
.header-meta { font-size: 0.82rem; color: var(--text-secondary); text-align: right; line-height: 1.7; }
.theme-toggle {
  background: var(--bg-secondary); border: 1px solid var(--border-color);
  color: var(--text-primary); border-radius: 6px; padding: 6px 12px;
  cursor: pointer; font-size: 0.82rem; margin-left: 12px;
}
.theme-toggle:hover { opacity: 0.8; }

/* Hero */
.hero { text-align: center; padding: 32px 0; }
.hero-grade { font-size: 2.4rem; font-weight: 800; margin-top: 8px; }
.hero-label { font-size: 0.9rem; color: var(--text-secondary); margin-top: 4px; }

/* Section titles */
.section-title {
  font-size: 1.1rem; font-weight: 600; margin: 32px 0 16px;
  padding-bottom: 8px; border-bottom: 1px solid var(--border-color);
}

/* Tool cards grid */
.tool-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 16px; margin-bottom: 32px;
}
.tool-card {
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: 10px; padding: 16px; text-align: center;
  box-shadow: var(--shadow); transition: box-shadow 0.2s;
}
.tool-card:hover { box-shadow: var(--shadow-hover); }
.tool-card.skipped { opacity: 0.55; }
.tool-icon { font-size: 1.6rem; margin-bottom: 4px; }
.tool-name { font-size: 0.82rem; font-weight: 600; margin-bottom: 8px; color: var(--text-primary); }
.tool-dial { margin: 0 auto 6px; }
.tool-grade { font-size: 1.1rem; font-weight: 700; }
.tool-findings { font-size: 0.75rem; color: var(--text-secondary); margin-top: 2px; }

/* Severity badges */
.severity-row { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 32px; }
.sev-badge {
  color: #fff; padding: 5px 14px; border-radius: 20px;
  font-size: 0.82rem; font-weight: 600;
}

/* Tool details */
.tool-detail {
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: 8px; margin-bottom: 10px; box-shadow: var(--shadow);
}
.tool-detail summary {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 16px; cursor: pointer; font-size: 0.92rem;
  list-style: none; user-select: none;
}
.tool-detail summary::-webkit-details-marker { display: none; }
.tool-detail summary::before {
  content: '\\25B6'; font-size: 0.7rem; transition: transform 0.2s;
  color: var(--text-secondary);
}
.tool-detail[open] summary::before { transform: rotate(90deg); }
.detail-icon { font-size: 1.1rem; }
.detail-name { font-weight: 600; flex: 1; }
.detail-grade { font-weight: 700; font-size: 0.95rem; }
.detail-status { font-size: 0.82rem; color: var(--text-secondary); min-width: 60px; text-align: right; }
.detail-body { padding: 0 16px 16px; }
.detail-bar { margin-bottom: 12px; }
.breakdown-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-bottom: 10px; }
.breakdown-table th { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border-color); color: var(--text-secondary); font-weight: 600; }
.breakdown-table td { padding: 5px 8px; border-bottom: 1px solid var(--border-color); }
.detail-label { min-width: 140px; }
.detail-score { min-width: 40px; text-align: right; font-weight: 600; padding-right: 12px !important; }
.error-list { background: var(--bg-secondary); border-radius: 6px; padding: 10px 14px; font-size: 0.82rem; }
.error-list ul { margin: 4px 0 0 18px; }
.error-list li { margin-bottom: 2px; }

/* Executive Summary */
.exec-summary { margin-bottom: 32px; }
.exec-text { font-size: 0.95rem; line-height: 1.8; color: var(--text-primary); max-width: 700px; }
.exec-text strong { color: var(--text-primary); font-weight: 700; }

/* What To Do Next (actions table) */
.actions-table {
  width: 100%; border-collapse: collapse; margin-bottom: 32px;
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: 8px; overflow: hidden; box-shadow: var(--shadow); font-size: 0.88rem;
}
.actions-table thead th {
  text-align: left; padding: 10px 12px; background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color); color: var(--text-secondary);
  font-weight: 600; font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.04em;
}
.actions-table tbody td { padding: 10px 12px; border-bottom: 1px solid var(--border-color); vertical-align: top; }
.actions-table tbody tr:last-child td { border-bottom: none; }
.action-rank { font-weight: 700; color: var(--text-secondary); width: 32px; }
.action-title { font-weight: 600; margin-bottom: 4px; line-height: 1.4; }
.action-rec { color: var(--text-secondary); font-size: 0.83rem; line-height: 1.4; }
.action-impact { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 600; }
.action-effort { color: var(--text-secondary); white-space: nowrap; }

/* Commitment Health */
.commitment-health { margin-bottom: 32px; }
.commitment-summary { display: flex; gap: 20px; margin: 12px 0; font-size: 0.88rem; }
.cs-item { color: var(--text-secondary); }
.cs-item strong { color: var(--text-primary); }
.kpi-assessment { font-size: 0.85rem; color: var(--text-secondary); font-style: italic; margin-top: 8px; }

/* Spend Concentration */
.spend-concentration { margin-bottom: 32px; }
.conc-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 0.85rem; }
.conc-name { min-width: 180px; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.conc-bar-track { flex: 1; height: 10px; background: var(--track-color); border-radius: 5px; }
.conc-bar-fill { height: 100%; background: #1565c0; border-radius: 5px; }
.conc-pct { min-width: 40px; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }
.conc-note { font-size: 0.85rem; color: var(--text-secondary); margin-top: 12px; line-height: 1.6; }

/* Spend Trend */
.spend-trend { margin-bottom: 32px; }
.trend-metrics { display: flex; gap: 24px; margin-bottom: 12px; font-size: 0.88rem; }
.trend-item { color: var(--text-secondary); }
.trend-item strong { color: var(--text-primary); }
.trend-sparkline { margin: 8px 0; }

/* Footer */
.footer {
  text-align: center; padding: 24px 0; margin-top: 40px;
  border-top: 1px solid var(--border-color);
  font-size: 0.78rem; color: var(--text-secondary); line-height: 1.8;
}

/* Phase 6C-2: Top Actions table */
.top-actions-table {
  width: 100%; border-collapse: collapse; margin-bottom: 32px;
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: 8px; overflow: hidden; box-shadow: var(--shadow);
  font-size: 0.88rem;
}
.top-actions-table thead th {
  text-align: left; padding: 10px 12px; background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color); color: var(--text-secondary);
  font-weight: 600; font-size: 0.74rem;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.top-actions-table tbody td {
  padding: 10px 12px; border-bottom: 1px solid var(--border-color);
  vertical-align: top;
}
.top-actions-table tbody tr:last-child td { border-bottom: none; }
.ta-rank { font-weight: 700; color: var(--text-secondary); width: 32px; }
.ta-impact { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 600; }
.ta-conf { color: var(--text-secondary); white-space: nowrap; }
.ta-source { color: var(--text-secondary); white-space: nowrap; }
.ta-title { font-weight: 600; margin-bottom: 4px; line-height: 1.4; }
.ta-rec { color: var(--text-secondary); font-size: 0.83rem; line-height: 1.4; }

/* Compact severity pill (distinct from .sev-badge used in Severity Summary) */
.sev-pill {
  display: inline-block; color: #fff; padding: 2px 8px; border-radius: 4px;
  font-size: 0.72rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap;
}

/* Cost findings cards */
.cost-findings { margin-top: 16px; }
.findings-subsection {
  font-size: 0.95rem; font-weight: 600; margin: 18px 0 10px;
  color: var(--text-primary);
}
.finding-cards { display: flex; flex-direction: column; gap: 10px; }
.finding-card {
  background: var(--bg-secondary); border: 1px solid var(--border-color);
  border-radius: 6px; padding: 12px 14px;
}
.finding-head {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-bottom: 6px; font-size: 0.82rem;
}
.finding-impact { font-weight: 700; font-variant-numeric: tabular-nums; }
.finding-conf { color: var(--text-secondary); }
.finding-title {
  font-weight: 600; margin-bottom: 8px; line-height: 1.4; font-size: 0.92rem;
}
.finding-meta {
  display: grid; grid-template-columns: max-content 1fr; gap: 2px 14px;
  font-size: 0.82rem; margin-bottom: 8px;
}
.finding-meta dt { color: var(--text-secondary); }
.finding-meta dd { color: var(--text-primary); font-variant-numeric: tabular-nums; }
.finding-why { font-size: 0.85rem; margin-bottom: 6px; line-height: 1.5; }
.finding-rec {
  font-size: 0.85rem; padding: 8px 10px; background: var(--bg-card);
  border-left: 3px solid #1565c0; border-radius: 3px; line-height: 1.5;
  margin-bottom: 8px;
}
.finding-evidence { margin-top: 6px; font-size: 0.82rem; }
.finding-evidence summary {
  cursor: pointer; color: var(--text-secondary); padding: 4px 0; user-select: none;
}
.finding-evidence[open] summary { margin-bottom: 6px; }
.evidence-table {
  width: 100%; border-collapse: collapse; font-size: 0.8rem;
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: 4px; overflow: hidden;
}
.evidence-table th, .evidence-table td {
  padding: 5px 8px; border-bottom: 1px solid var(--border-color);
  text-align: left; vertical-align: top;
}
.evidence-table th {
  background: var(--bg-secondary); color: var(--text-secondary);
  font-weight: 600; min-width: 110px;
}
.evidence-table tr:last-child th, .evidence-table tr:last-child td {
  border-bottom: none;
}
.findings-truncation {
  font-size: 0.8rem; color: var(--text-secondary);
  font-style: italic; margin-top: 8px;
}

/* AWS Cost Anomaly Detection overlap summary */
.overlap-summary {
  background: var(--bg-secondary); border: 1px solid var(--border-color);
  border-radius: 6px; padding: 10px 14px; margin: 12px 0;
  font-size: 0.85rem;
}
.overlap-status { margin-bottom: 4px; }
.overlap-counts {
  display: flex; gap: 16px; flex-wrap: wrap;
  color: var(--text-secondary); font-size: 0.82rem;
}
.overlap-counts span { font-variant-numeric: tabular-nums; }
.overlap-details { margin-top: 6px; font-size: 0.8rem; color: var(--text-secondary); }

/* Synthetic-sample banner (opt-in via synthetic_sample=True) */
.synthetic-banner {
  background: #fff8e1;
  border-bottom: 2px solid #f57f17;
  padding: 14px 24px;
  text-align: center;
  color: #5d4037;
  line-height: 1.5;
}
.synthetic-banner-title {
  font-size: 0.95rem; margin-bottom: 4px;
  letter-spacing: 0.02em;
}
.synthetic-banner-title strong { color: #e65100; }
.synthetic-banner-body { font-size: 0.85rem; max-width: 760px; margin: 0 auto; }
@media (prefers-color-scheme: dark) {
  :root:not(.light-mode) .synthetic-banner {
    background: #2d2418; border-bottom-color: #f57f17; color: #ffe0b2;
  }
}
.dark-mode .synthetic-banner {
  background: #2d2418; border-bottom-color: #f57f17; color: #ffe0b2;
}

/* Print */
@media print {
  body { background: #fff; color: #000; }
  .theme-toggle { display: none; }
  .tool-card { break-inside: avoid; box-shadow: none; border: 1px solid #ccc; }
  .tool-detail { break-inside: avoid; box-shadow: none; }
  .finding-card { break-inside: avoid; }
  .top-actions-table tr { break-inside: avoid; }
  .synthetic-banner { background: #fff8e1; color: #5d4037; }
  .container { max-width: 100%; padding: 0; }
  .footer { border-top: 1px solid #ccc; }
}

/* Commitment KPI */
.commitment-kpi { margin-bottom: 32px; }
.kpi-row {
  display: flex; align-items: center; gap: 12px; margin-bottom: 12px;
  font-size: 0.88rem;
}
.kpi-label { min-width: 120px; font-weight: 600; color: var(--text-primary); }
.kpi-bar-track {
  flex: 1; height: 16px; background: var(--track-color);
  border-radius: 8px; position: relative; overflow: visible;
}
.kpi-bar-fill {
  height: 100%; border-radius: 8px; transition: width 0.3s;
}
.kpi-target-line {
  position: absolute; top: -3px; width: 2px; height: 22px;
  background: var(--text-secondary); opacity: 0.6;
}
.kpi-value { font-weight: 700; min-width: 50px; text-align: right; font-variant-numeric: tabular-nums; }
.kpi-unused {
  margin-top: 8px; padding: 8px 14px; background: var(--bg-secondary);
  border-radius: 6px; font-size: 0.85rem; color: var(--text-secondary);
}

/* Money on Fire */
.money-on-fire {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  border: 1px solid var(--border-color); border-radius: 12px;
  padding: 28px 32px; margin-bottom: 32px; text-align: center;
}
.mof-hero { display: flex; align-items: center; justify-content: center; gap: 12px; }
.mof-icon { font-size: 2.4rem; }
.mof-total {
  font-size: 2.8rem; font-weight: 800; color: #ff6b35;
  font-variant-numeric: tabular-nums;
}
.mof-per { font-size: 1rem; font-weight: 400; color: var(--text-secondary); margin-left: 4px; }
.mof-subtitle { font-size: 0.9rem; color: var(--text-secondary); margin-top: 8px; }
.mof-breakdown {
  display: flex; flex-direction: column; gap: 6px; margin-top: 16px;
  max-width: 400px; margin-left: auto; margin-right: auto; text-align: left;
}
.mof-row { display: flex; justify-content: space-between; font-size: 0.85rem; }
.mof-label { color: var(--text-secondary); }
.mof-amount { font-weight: 600; color: #ff6b35; font-variant-numeric: tabular-nums; }

/* Responsive */
@media (max-width: 600px) {
  .header { flex-direction: column; text-align: center; gap: 10px; }
  .header-meta { text-align: center; }
  .tool-grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
}
"""

# ---------------------------------------------------------------------------
# JS
# ---------------------------------------------------------------------------
_JS = """\
(function() {
  var btn = document.getElementById('theme-toggle');
  var root = document.documentElement;
  function toggle() {
    if (root.classList.contains('dark-mode')) {
      root.classList.remove('dark-mode');
      root.classList.add('light-mode');
      btn.textContent = 'Dark Mode';
    } else {
      root.classList.remove('light-mode');
      root.classList.add('dark-mode');
      btn.textContent = 'Light Mode';
    }
  }
  btn.addEventListener('click', toggle);
})();
"""

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;">
<title>Kulshan Report</title>
<style>
{css}
</style>
</head>
<body>
{synthetic_banner}
<div class="container">

  <!-- Header -->
  <header class="header">
    <div class="header-brand">
      <h1><span>Kulshan</span> Report</h1>
    </div>
    <div class="header-meta">
      Account: {account_id}<br>
      Regions: {regions_count} | Duration: {duration}s<br>
      {timestamp}
      <button class="theme-toggle" id="theme-toggle">Dark Mode</button>
    </div>
  </header>

  <!-- Executive Summary -->
  {executive_summary}

  <!-- What To Do Next -->
  {what_to_do_next}

  <!-- Addressable Savings -->
  {addressable_savings}

  <!-- Commitment Health -->
  {commitment_health}

  <!-- Spend Concentration -->
  {spend_concentration}

  <!-- Spend Trend -->
  {spend_trend}

  <!-- Detailed Breakdown -->
  <h2 class="section-title">Detailed Breakdown</h2>
  {coverage_summary}

  {detailed_breakdown}

  <!-- Footer -->
  <footer class="footer">
    Generated by Kulshan v{version}<br>
    {timestamp}<br>
    {score_label}: {overall_score}/100 ({overall_grade})<br>
    This report was generated locally. No data was sent to external services.
  </footer>

</div>
<script>
{js}
</script>
</body>
</html>
"""
