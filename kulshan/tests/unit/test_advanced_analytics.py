"""Unit tests for Phase 7 advanced analytics features."""
from __future__ import annotations

import pandas as pd
import pytest

from kulshan.checks.cost.analyzers.advanced import (
    compute_cost_velocity,
    compute_hhi_concentration,
    compute_purchase_type_mix,
    generate_svg_sparkline,
    compute_executive_scorecard,
)


class TestCostVelocity:
    """Tests for the cost velocity calculator."""

    def test_returns_keys(self):
        data = pd.DataFrame({
            "date": pd.date_range("2026-05-01", periods=10, freq="D"),
            "service": ["EC2"] * 10,
            "cost": [100 + i * 2 for i in range(10)],
        })
        result = compute_cost_velocity(data, days=10)
        assert "daily_avg" in result
        assert "velocity" in result
        assert "acceleration" in result
        assert "velocity_pct" in result
        assert "trend" in result
        assert "daily_costs" in result

    def test_increasing_trend(self):
        data = pd.DataFrame({
            "date": pd.date_range("2026-05-01", periods=30, freq="D"),
            "service": ["EC2"] * 30,
            "cost": [100 + i * 5 for i in range(30)],
        })
        result = compute_cost_velocity(data, days=30)
        assert result["velocity"] > 0
        assert result["velocity_pct"] > 0
        assert "growing" in result["trend"] or "accelerating" in result["trend"]

    def test_declining_trend(self):
        data = pd.DataFrame({
            "date": pd.date_range("2026-05-01", periods=30, freq="D"),
            "service": ["EC2"] * 30,
            "cost": [500 - i * 5 for i in range(30)],
        })
        result = compute_cost_velocity(data, days=30)
        assert result["velocity"] < 0
        assert "declining" in result["trend"]

    def test_empty_data(self):
        result = compute_cost_velocity(pd.DataFrame(), days=30)
        assert result["trend"] == "no data"
        assert result["daily_costs"] == []

    def test_daily_costs_length(self):
        data = pd.DataFrame({
            "date": pd.date_range("2026-05-01", periods=15, freq="D"),
            "service": ["EC2"] * 15,
            "cost": [100] * 15,
        })
        result = compute_cost_velocity(data, days=15)
        assert len(result["daily_costs"]) == 15


class TestHHIConcentration:
    """Tests for the HHI service concentration calculator."""

    def test_single_service_max_concentration(self):
        data = pd.DataFrame({
            "date": pd.date_range("2026-05-01", periods=3, freq="D"),
            "service": ["EC2", "EC2", "EC2"],
            "cost": [100, 200, 300],
        })
        result = compute_hhi_concentration(data)
        assert result["hhi"] == 10000  # Perfect monopoly
        assert result["classification"] == "concentrated"

    def test_equal_split_low_concentration(self):
        # 10 services with equal spend
        services = [f"Service{i}" for i in range(10)]
        data = pd.DataFrame({
            "date": pd.date_range("2026-05-01", periods=10, freq="D"),
            "service": services,
            "cost": [100] * 10,
        })
        result = compute_hhi_concentration(data)
        assert result["hhi"] == 1000  # 10 * (10^2) = 1000
        assert result["classification"] == "unconcentrated"

    def test_moderate_concentration(self):
        data = pd.DataFrame({
            "date": pd.date_range("2026-05-01", periods=5, freq="D"),
            "service": ["EC2", "S3", "RDS", "Lambda", "Other"],
            "cost": [5000, 2000, 1500, 1000, 500],
        })
        result = compute_hhi_concentration(data)
        assert 1500 <= result["hhi"] <= 5000
        assert result["classification"] in ("moderate", "concentrated")

    def test_top_services_limited(self):
        services = [f"Svc{i}" for i in range(20)]
        data = pd.DataFrame({
            "date": pd.date_range("2026-05-01", periods=20, freq="D"),
            "service": services,
            "cost": list(range(20, 0, -1)),
        })
        result = compute_hhi_concentration(data)
        assert len(result["top_services"]) <= 5

    def test_empty_data(self):
        result = compute_hhi_concentration(pd.DataFrame())
        assert result["hhi"] == 0
        assert result["classification"] == "no data"


class TestSVGSparkline:
    """Tests for the SVG sparkline generator."""

    def test_basic_output_is_svg(self):
        svg = generate_svg_sparkline([10, 20, 15, 25, 30])
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert "polyline" in svg

    def test_empty_values(self):
        svg = generate_svg_sparkline([])
        assert svg == ""

    def test_single_value(self):
        svg = generate_svg_sparkline([42])
        assert svg == ""

    def test_flat_line(self):
        svg = generate_svg_sparkline([100, 100, 100, 100])
        assert "polyline" in svg

    def test_custom_dimensions(self):
        svg = generate_svg_sparkline([1, 2, 3], width=400, height=80)
        assert 'width="400"' in svg
        assert 'height="80"' in svg

    def test_end_dot_present(self):
        svg = generate_svg_sparkline([10, 20, 30])
        assert "<circle" in svg

    def test_aria_label(self):
        svg = generate_svg_sparkline([5, 10, 15])
        assert 'aria-label="Cost trend sparkline"' in svg


class TestExecutiveScorecard:
    """Tests for the executive scorecard computation."""

    def test_returns_required_keys(self):
        result = compute_executive_scorecard(
            efficiency_breakdown={},
            purchase_mix={"committed_pct": 50, "on_demand_pct": 50, "spot_pct": 0},
            velocity={"velocity_pct": 1.0, "trend": "stable", "warning": None},
            hhi={"hhi": 1200, "classification": "unconcentrated"},
            total_spend=10000,
            total_findings=2,
        )
        assert "pillars" in result
        assert "composite_score" in result
        assert "composite_grade" in result
        assert "headline" in result
        assert len(result["pillars"]) == 4

    def test_scores_bounded_0_100(self):
        result = compute_executive_scorecard(
            efficiency_breakdown={},
            purchase_mix={"committed_pct": 0, "on_demand_pct": 100, "spot_pct": 0},
            velocity={"velocity_pct": 10, "trend": "accelerating", "warning": "danger"},
            hhi={"hhi": 9000, "classification": "concentrated"},
            total_spend=10000,
            total_findings=20,
        )
        assert 0 <= result["composite_score"] <= 100
        for p in result["pillars"]:
            assert 0 <= p["score"] <= 100

    def test_excellent_posture(self):
        result = compute_executive_scorecard(
            efficiency_breakdown={
                "ri_sp_coverage": {"score": 24, "max": 25},
                "ri_sp_utilization": {"score": 24, "max": 25},
                "waste_detection": {"score": 20, "max": 20},
            },
            purchase_mix={"committed_pct": 80, "on_demand_pct": 15, "spot_pct": 5},
            velocity={"velocity_pct": -0.5, "trend": "declining", "warning": None},
            hhi={"hhi": 800, "classification": "unconcentrated"},
            total_spend=100000,
            total_findings=0,
        )
        assert result["composite_score"] >= 80
        assert result["composite_grade"] in ("A", "B")

    def test_pillar_names(self):
        result = compute_executive_scorecard(
            efficiency_breakdown={},
            purchase_mix={"committed_pct": 50, "on_demand_pct": 50, "spot_pct": 0},
            velocity={"velocity_pct": 0, "trend": "stable", "warning": None},
            hhi={"hhi": 1000, "classification": "unconcentrated"},
            total_spend=10000,
            total_findings=0,
        )
        names = [p["name"] for p in result["pillars"]]
        assert "Cost Health" in names
        assert "Commitment" in names
        assert "Efficiency" in names
        assert "Risk" in names

    def test_pillar_icons_present(self):
        result = compute_executive_scorecard(
            efficiency_breakdown={},
            purchase_mix={"committed_pct": 50, "on_demand_pct": 50, "spot_pct": 0},
            velocity={"velocity_pct": 0, "trend": "stable", "warning": None},
            hhi={"hhi": 1000, "classification": "unconcentrated"},
            total_spend=10000,
            total_findings=0,
        )
        for p in result["pillars"]:
            assert p["icon"] != ""
            assert p["detail"] != ""
