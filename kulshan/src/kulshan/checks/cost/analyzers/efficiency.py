"""Cost Efficiency Score, composite 0-100 score based on optimization best practices."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from platformdirs import user_data_dir


def compute_efficiency_score(
    ri_coverage: pd.DataFrame,
    sp_utilization: pd.DataFrame,
    ri_utilization: pd.DataFrame,
    idle_df: pd.DataFrame,
    anomalies_df: pd.DataFrame,
    total_spend: float,
) -> dict:
    """
    Compute a 0-100 cost efficiency score from existing analysis data.

    Scoring breakdown (100 total):
      - RI/SP Coverage:     0-25 points (target: 80%+ coverage)
      - RI/SP Utilization:  0-25 points (target: 90%+ utilization)
      - Waste Detection:    0-20 points (fewer idle resources = better)
      - Anomaly Health:     0-15 points (fewer critical anomalies = better)
      - Cost Stability:     0-15 points (low variance = predictable spend)
    """
    breakdown = {}

    # ── RI/SP Coverage (0-25) ─────────────────────────────────────────
    coverage_pct = 0.0
    if not ri_coverage.empty and "coverage_pct" in ri_coverage.columns:
        coverage_pct = ri_coverage["coverage_pct"].mean()
    coverage_score = min(25, coverage_pct / 80 * 25)
    breakdown["ri_sp_coverage"] = {
        "score": round(coverage_score, 1),
        "max": 25,
        "value": f"{coverage_pct:.1f}%",
        "target": "80%",
        "status": "✅" if coverage_pct >= 70 else ("⚠️" if coverage_pct >= 40 else "❌"),
    }

    # ── RI/SP Utilization (0-25) ──────────────────────────────────────
    util_pct = 0.0
    if not sp_utilization.empty and "utilization_pct" in sp_utilization.columns:
        util_pct = sp_utilization["utilization_pct"].mean()
    elif not ri_utilization.empty and "utilization_pct" in ri_utilization.columns:
        util_pct = ri_utilization["utilization_pct"].mean()
    util_score = min(25, util_pct / 90 * 25)
    breakdown["ri_sp_utilization"] = {
        "score": round(util_score, 1),
        "max": 25,
        "value": f"{util_pct:.1f}%",
        "target": "90%",
        "status": "✅" if util_pct >= 80 else ("⚠️" if util_pct >= 50 else "❌"),
    }

    # ── Waste Detection (0-20) ────────────────────────────────────────
    idle_count = len(idle_df) if not idle_df.empty else 0
    idle_cost = idle_df["total_cost"].sum() if not idle_df.empty and "total_cost" in idle_df.columns else 0
    waste_pct = (idle_cost / total_spend * 100) if total_spend > 0 else 0
    # Perfect = 0 idle, worst = 10+ idle resources
    waste_score = max(0, 20 - idle_count * 2)
    breakdown["waste_detection"] = {
        "score": round(waste_score, 1),
        "max": 20,
        "value": f"{idle_count} idle ({waste_pct:.1f}% of spend)",
        "target": "0 idle resources",
        "status": "✅" if idle_count == 0 else ("⚠️" if idle_count <= 3 else "❌"),
    }

    # ── Anomaly Health (0-15) ─────────────────────────────────────────
    critical_count = 0
    warning_count = 0
    if not anomalies_df.empty and "severity" in anomalies_df.columns:
        critical_count = len(anomalies_df[anomalies_df["severity"] == "critical"])
        warning_count = len(anomalies_df[anomalies_df["severity"] == "warning"])
    anomaly_penalty = critical_count * 5 + warning_count * 2
    anomaly_score = max(0, 15 - anomaly_penalty)
    breakdown["anomaly_health"] = {
        "score": round(anomaly_score, 1),
        "max": 15,
        "value": f"{critical_count} critical, {warning_count} warning",
        "target": "0 critical anomalies",
        "status": "✅" if critical_count == 0 else ("⚠️" if critical_count <= 2 else "❌"),
    }

    # ── Cost Stability (0-15) ─────────────────────────────────────────
    # No RI/SP = all on-demand = less predictable
    has_commitments = (not ri_coverage.empty and coverage_pct > 10) or (not sp_utilization.empty and util_pct > 10)
    stability_score = 15 if has_commitments else 8
    # Penalize if anomalies suggest instability
    if critical_count > 0:
        stability_score = max(0, stability_score - 5)
    breakdown["cost_stability"] = {
        "score": round(stability_score, 1),
        "max": 15,
        "value": "Committed" if has_commitments else "On-Demand only",
        "target": "Committed pricing",
        "status": "✅" if has_commitments else "⚠️",
    }

    total_score = sum(v["score"] for v in breakdown.values())

    return {
        "total_score": round(total_score),
        "max_score": 100,
        "grade": _grade(total_score),
        "breakdown": breakdown,
    }


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 65:
        return "C"
    elif score >= 50:
        return "D"
    return "F"


# ── Score tracking over time ──────────────────────────────────────────────

SCORE_HISTORY_FILE = str(
    Path(user_data_dir("Kulshan", "missionfinops")) / "cost-analyzer-scores.json"
)


def save_score(score_data: dict, total_spend: float):
    """Append current score to local history file."""
    entry = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "score": score_data["total_score"],
        "grade": score_data["grade"],
        "total_spend": round(total_spend, 2),
    }

    history = load_score_history()

    # Don't duplicate same-day entries
    if history and history[-1]["date"] == entry["date"]:
        history[-1] = entry
    else:
        history.append(entry)

    # Keep last 365 entries
    history = history[-365:]
    path = Path(SCORE_HISTORY_FILE)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def load_score_history() -> list[dict]:
    """Load score history from local file."""
    path = Path(SCORE_HISTORY_FILE)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def get_score_trend() -> dict | None:
    """Get score trend from history. Returns None if insufficient data."""
    history = load_score_history()
    if len(history) < 2:
        return None

    current = history[-1]
    previous = history[-2]
    delta = current["score"] - previous["score"]

    # Find 30-day-ago entry if available
    thirty_days_ago = None
    for entry in reversed(history):
        if entry["date"] < datetime.now(timezone.utc).replace(day=1).strftime("%Y-%m-%d"):
            thirty_days_ago = entry
            break

    return {
        "current": current,
        "previous": previous,
        "delta": delta,
        "direction": "improving" if delta > 0 else ("declining" if delta < 0 else "stable"),
        "thirty_days_ago": thirty_days_ago,
    }
