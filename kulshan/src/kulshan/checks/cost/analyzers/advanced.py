"""Advanced cost analytics: purchase mix, cost velocity, HHI concentration, executive scorecard."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ── 1. Purchase Type Mix ─────────────────────────────────────────────────────


def compute_purchase_type_mix(fetcher: Any, days: int = 30) -> Dict[str, Any]:
    """Analyze spend by purchase type (On-Demand, RI, SP, Spot).

    Returns a dict with:
      - mix: list of {type, cost, pct}
      - on_demand_pct: float (the % that is on-demand)
      - committed_pct: float (RI + SP combined %)
      - spot_pct: float
      - total: float
      - assessment: str (brief qualitative assessment)
    """
    try:
        df = fetcher.get_cost_by_dimension("purchase_option", days=days, granularity="MONTHLY")
    except Exception:
        return {"mix": [], "on_demand_pct": 0, "committed_pct": 0, "spot_pct": 0, "total": 0, "assessment": "unavailable"}

    if df.empty:
        return {"mix": [], "on_demand_pct": 0, "committed_pct": 0, "spot_pct": 0, "total": 0, "assessment": "no data"}

    # purchase_option column contains the dimension values
    col = "purchase_option"
    totals = df.groupby(col)["cost"].sum().sort_values(ascending=False)
    grand_total = totals.sum()

    if grand_total <= 0:
        return {"mix": [], "on_demand_pct": 0, "committed_pct": 0, "spot_pct": 0, "total": 0, "assessment": "no spend"}

    mix: List[Dict[str, Any]] = []
    on_demand = 0.0
    committed = 0.0
    spot = 0.0

    for ptype, cost in totals.items():
        pct = round(cost / grand_total * 100, 1)
        mix.append({"type": str(ptype), "cost": round(float(cost), 2), "pct": pct})

        ptype_lower = str(ptype).lower()
        if "on demand" in ptype_lower or "on-demand" in ptype_lower:
            on_demand += cost
        elif "spot" in ptype_lower:
            spot += cost
        else:
            # RI, Savings Plans, etc.
            committed += cost

    on_demand_pct = round(on_demand / grand_total * 100, 1) if grand_total > 0 else 0
    committed_pct = round(committed / grand_total * 100, 1) if grand_total > 0 else 0
    spot_pct = round(spot / grand_total * 100, 1) if grand_total > 0 else 0

    # Assessment
    if committed_pct >= 70:
        assessment = "Excellent commitment coverage"
    elif committed_pct >= 50:
        assessment = "Good commitment coverage, room for optimization"
    elif committed_pct >= 25:
        assessment = "Moderate - significant on-demand exposure"
    else:
        assessment = "Heavy on-demand exposure - commitment strategy recommended"

    return {
        "mix": mix,
        "on_demand_pct": on_demand_pct,
        "committed_pct": committed_pct,
        "spot_pct": spot_pct,
        "total": round(float(grand_total), 2),
        "assessment": assessment,
    }


# ── 2. Cost Velocity ─────────────────────────────────────────────────────────


def compute_cost_velocity(service_data: pd.DataFrame, days: int = 30) -> Dict[str, Any]:
    """Compute cost velocity (rate of change) and acceleration.

    Returns:
      - daily_avg: float (current period average daily spend)
      - velocity: float ($/day change rate - positive = growing)
      - acceleration: float (rate of velocity change - second derivative)
      - velocity_pct: float (% change per day)
      - trend: str (accelerating, decelerating, stable, declining)
      - warning: Optional[str] (alert if acceleration is dangerous)
      - daily_costs: list[float] (for sparkline rendering)
    """
    if service_data.empty or "date" not in service_data.columns:
        return {
            "daily_avg": 0, "velocity": 0, "acceleration": 0,
            "velocity_pct": 0, "trend": "no data", "warning": None,
            "daily_costs": [],
        }

    # Aggregate to daily totals
    daily = service_data.groupby("date")["cost"].sum().sort_index()

    if len(daily) < 3:
        return {
            "daily_avg": round(float(daily.mean()), 2),
            "velocity": 0, "acceleration": 0,
            "velocity_pct": 0, "trend": "insufficient data", "warning": None,
            "daily_costs": [round(float(v), 2) for v in daily.values],
        }

    daily_values = daily.values.astype(float)
    daily_avg = float(daily_values.mean())

    # Velocity: linear regression slope (simple OLS)
    n = len(daily_values)
    x = list(range(n))
    x_mean = (n - 1) / 2
    y_mean = daily_avg

    numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, daily_values))
    denominator = sum((xi - x_mean) ** 2 for xi in x)

    velocity = numerator / denominator if denominator > 0 else 0.0
    velocity_pct = (velocity / daily_avg * 100) if daily_avg > 0 else 0.0

    # Acceleration: compare velocity of first half vs second half
    mid = n // 2
    if mid >= 2:
        first_half = daily_values[:mid]
        second_half = daily_values[mid:]
        v1 = float(first_half[-1] - first_half[0]) / max(1, len(first_half) - 1)
        v2 = float(second_half[-1] - second_half[0]) / max(1, len(second_half) - 1)
        acceleration = v2 - v1
    else:
        acceleration = 0.0

    # Classify trend
    if velocity > 0 and acceleration > 0:
        trend = "accelerating"
    elif velocity > 0 and acceleration <= 0:
        trend = "growing (decelerating)"
    elif velocity < 0 and acceleration < 0:
        trend = "declining (accelerating)"
    elif velocity < 0 and acceleration >= 0:
        trend = "declining (decelerating)"
    else:
        trend = "stable"

    # Warning threshold: >2% daily growth rate with positive acceleration
    warning: Optional[str] = None
    if velocity_pct > 2.0 and acceleration > 0:
        projected_30d = daily_avg * 30 + velocity * 30 * 15  # linear projection
        warning = (
            f"Costs accelerating at {velocity_pct:.1f}%/day. "
            f"Projected 30-day spend: ${projected_30d:,.0f} "
            f"(vs ${daily_avg * 30:,.0f} at current rate)."
        )
    elif velocity_pct > 5.0:
        warning = f"High cost velocity: {velocity_pct:.1f}%/day growth rate."

    return {
        "daily_avg": round(daily_avg, 2),
        "velocity": round(velocity, 2),
        "acceleration": round(acceleration, 4),
        "velocity_pct": round(velocity_pct, 2),
        "trend": trend,
        "warning": warning,
        "daily_costs": [round(float(v), 2) for v in daily_values],
    }


# ── 3. HHI Concentration Index ──────────────────────────────────────────────


def compute_hhi_concentration(service_data: pd.DataFrame) -> Dict[str, Any]:
    """Compute Herfindahl-Hirschman Index for service concentration risk.

    HHI = sum of squared market shares (as percentages).
    - HHI < 1500: Unconcentrated (diversified)
    - HHI 1500-2500: Moderate concentration
    - HHI > 2500: Highly concentrated (lock-in risk)

    Returns:
      - hhi: int (0-10000 scale)
      - classification: str (unconcentrated/moderate/concentrated)
      - top_services: list of {service, pct, cost}
      - risk_assessment: str
      - effective_services: int (equivalent number of equal-sized services)
    """
    if service_data.empty:
        return {
            "hhi": 0, "classification": "no data",
            "top_services": [], "risk_assessment": "insufficient data",
            "effective_services": 0,
        }

    # Aggregate by service
    if "service" in service_data.columns:
        totals = service_data.groupby("service")["cost"].sum()
    else:
        # Fallback: treat each row as a service
        totals = service_data.groupby(service_data.index)["cost"].sum()

    grand_total = totals.sum()
    if grand_total <= 0:
        return {
            "hhi": 0, "classification": "no data",
            "top_services": [], "risk_assessment": "no spend",
            "effective_services": 0,
        }

    # Calculate market shares (as percentages 0-100)
    shares = (totals / grand_total * 100).sort_values(ascending=False)

    # HHI = sum of squared shares
    hhi = int(round(sum(s ** 2 for s in shares.values)))

    # Effective number of services (reciprocal of HHI normalized)
    hhi_normalized = hhi / 10000.0
    effective_services = int(round(1.0 / hhi_normalized)) if hhi_normalized > 0 else len(shares)

    # Classification
    if hhi < 1500:
        classification = "unconcentrated"
        risk_assessment = "Well-diversified spend across services - low lock-in risk"
    elif hhi < 2500:
        classification = "moderate"
        risk_assessment = "Moderate concentration - review top services for alternatives"
    else:
        classification = "concentrated"
        top_svc = shares.index[0] if len(shares) > 0 else "Unknown"
        top_pct = shares.iloc[0] if len(shares) > 0 else 0
        risk_assessment = (
            f"Highly concentrated: {top_svc} is {top_pct:.0f}% of spend. "
            f"Consider diversification or negotiation leverage."
        )

    # Top services
    top_services = []
    for svc, share in shares.head(5).items():
        cost = totals.get(svc, 0)
        top_services.append({
            "service": str(svc),
            "pct": round(float(share), 1),
            "cost": round(float(cost), 2),
        })

    return {
        "hhi": hhi,
        "classification": classification,
        "top_services": top_services,
        "risk_assessment": risk_assessment,
        "effective_services": effective_services,
    }


# ── 4. SVG Sparkline Generator ───────────────────────────────────────────────


def generate_svg_sparkline(
    values: List[float],
    width: int = 200,
    height: int = 40,
    color: str = "#1565c0",
    fill_color: Optional[str] = None,
) -> str:
    """Generate a minimal inline SVG sparkline from a list of values.

    Returns a self-contained SVG string suitable for embedding in HTML.
    """
    if not values or len(values) < 2:
        return ""

    n = len(values)
    min_val = min(values)
    max_val = max(values)
    val_range = max_val - min_val

    if val_range == 0:
        # Flat line
        val_range = 1.0

    # Padding
    pad_x = 2
    pad_y = 4
    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y

    # Generate points
    points: List[str] = []
    for i, v in enumerate(values):
        x = pad_x + (i / (n - 1)) * plot_w
        y = pad_y + (1 - (v - min_val) / val_range) * plot_h
        points.append(f"{x:.1f},{y:.1f}")

    polyline_points = " ".join(points)

    # Optional gradient fill under the line
    fill_path = ""
    if fill_color is None:
        fill_color = color + "20"  # 12% opacity default

    # Build closed polygon for fill area
    first_x = pad_x
    last_x = pad_x + plot_w
    bottom_y = pad_y + plot_h
    fill_points = f"{first_x:.1f},{bottom_y:.1f} " + polyline_points + f" {last_x:.1f},{bottom_y:.1f}"
    fill_path = (
        f'<polygon points="{fill_points}" fill="{fill_color}" stroke="none" />'
    )

    # End dot
    last_point = points[-1].split(",")
    end_dot = (
        f'<circle cx="{last_point[0]}" cy="{last_point[1]}" r="2.5" '
        f'fill="{color}" />'
    )

    # Min/max labels (tiny)
    min_label = f'<text x="{width - 2}" y="{pad_y + plot_h}" text-anchor="end" font-size="8" fill="var(--text-secondary,#999)" dominant-baseline="auto">${min_val:,.0f}</text>'
    max_label = f'<text x="{width - 2}" y="{pad_y + 8}" text-anchor="end" font-size="8" fill="var(--text-secondary,#999)" dominant-baseline="hanging">${max_val:,.0f}</text>'

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'class="sparkline" aria-label="Cost trend sparkline">'
        f'{fill_path}'
        f'<polyline points="{polyline_points}" fill="none" '
        f'stroke="{color}" stroke-width="1.5" stroke-linejoin="round" '
        f'stroke-linecap="round" />'
        f'{end_dot}'
        f'{min_label}'
        f'{max_label}'
        f'</svg>'
    )


# ── 5. Executive Scorecard ───────────────────────────────────────────────────


def compute_executive_scorecard(
    efficiency_breakdown: Dict[str, Any],
    purchase_mix: Dict[str, Any],
    velocity: Dict[str, Any],
    hhi: Dict[str, Any],
    total_spend: float,
    total_findings: int,
) -> Dict[str, Any]:
    """Compute a 4-pillar executive scorecard.

    Pillars (each 0-100):
      1. Cost Health - spend trend + anomaly count
      2. Commitment - coverage x utilization composite
      3. Efficiency - waste + idle detection
      4. Risk - concentration + velocity warnings

    Returns:
      - pillars: list of {name, score, grade, icon, detail}
      - composite_score: int (weighted average)
      - composite_grade: str
      - headline: str (one-line executive summary)
    """
    # ── Pillar 1: Cost Health ──
    cost_health = 80  # Start optimistic
    # Penalize for high velocity
    vel_pct = abs(velocity.get("velocity_pct", 0))
    if vel_pct > 5:
        cost_health -= 30
    elif vel_pct > 2:
        cost_health -= 15
    elif vel_pct > 1:
        cost_health -= 5

    # Penalize for findings
    if total_findings > 10:
        cost_health -= 20
    elif total_findings > 5:
        cost_health -= 10
    elif total_findings > 0:
        cost_health -= 5

    # Bonus for stable/declining trend
    trend = velocity.get("trend", "")
    if "declining" in trend:
        cost_health = min(100, cost_health + 10)

    cost_health = max(0, min(100, cost_health))

    # ── Pillar 2: Commitment ──
    commitment = 50  # Default: no commitments
    committed_pct = purchase_mix.get("committed_pct", 0)
    if committed_pct > 0:
        commitment = min(100, int(committed_pct * 1.3))  # Scale: 77% committed -> 100

    # Boost from efficiency breakdown if available
    ri_sp_cov = efficiency_breakdown.get("ri_sp_coverage", {})
    ri_sp_util = efficiency_breakdown.get("ri_sp_utilization", {})
    if ri_sp_cov and ri_sp_util:
        cov_score = ri_sp_cov.get("score", 0)
        util_score = ri_sp_util.get("score", 0)
        # Weighted: coverage 60%, utilization 40%
        raw = (cov_score / 25 * 60) + (util_score / 25 * 40)
        commitment = max(commitment, int(raw))

    commitment = max(0, min(100, commitment))

    # ── Pillar 3: Efficiency ──
    efficiency = 70  # Default
    waste = efficiency_breakdown.get("waste_detection", {})
    if waste:
        waste_score = waste.get("score", 10)
        efficiency = int(waste_score / 20 * 100)

    # Factor in on-demand exposure
    on_demand_pct = purchase_mix.get("on_demand_pct", 50)
    if on_demand_pct > 80:
        efficiency = max(0, efficiency - 20)
    elif on_demand_pct > 60:
        efficiency = max(0, efficiency - 10)

    efficiency = max(0, min(100, efficiency))

    # ── Pillar 4: Risk ──
    risk = 90  # Start safe
    # Concentration risk
    hhi_val = hhi.get("hhi", 0)
    if hhi_val > 5000:
        risk -= 40
    elif hhi_val > 2500:
        risk -= 25
    elif hhi_val > 1500:
        risk -= 10

    # Velocity risk
    if velocity.get("warning"):
        risk -= 20
    elif vel_pct > 3:
        risk -= 10

    # Anomaly risk
    if total_findings > 5:
        risk -= 15
    elif total_findings > 0:
        risk -= 5

    risk = max(0, min(100, risk))

    # ── Composite ──
    # Weights: Cost Health 30%, Commitment 25%, Efficiency 25%, Risk 20%
    composite = int(
        cost_health * 0.30
        + commitment * 0.25
        + efficiency * 0.25
        + risk * 0.20
    )
    composite = max(0, min(100, composite))

    def _grade(score: int) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"

    pillars = [
        {
            "name": "Cost Health",
            "score": cost_health,
            "grade": _grade(cost_health),
            "icon": "$",
            "detail": f"Velocity: {velocity.get('velocity_pct', 0):+.1f}%/day, {total_findings} findings",
        },
        {
            "name": "Commitment",
            "score": commitment,
            "grade": _grade(commitment),
            "icon": "commitment",
            "detail": f"{committed_pct:.0f}% committed spend",
        },
        {
            "name": "Efficiency",
            "score": efficiency,
            "grade": _grade(efficiency),
            "icon": "efficiency",
            "detail": f"{on_demand_pct:.0f}% on-demand exposure",
        },
        {
            "name": "Risk",
            "score": risk,
            "grade": _grade(risk),
            "icon": "risk",
            "detail": f"HHI {hhi_val:,} ({hhi.get('classification', 'unknown')})",
        },
    ]

    # Headline
    if composite >= 85:
        headline = "Strong cloud financial posture - well-optimized."
    elif composite >= 70:
        headline = "Good financial health with room for optimization."
    elif composite >= 55:
        headline = "Moderate risk - commitment and efficiency gaps identified."
    else:
        headline = "Significant optimization opportunities across multiple pillars."

    return {
        "pillars": pillars,
        "composite_score": composite,
        "composite_grade": _grade(composite),
        "headline": headline,
    }

