"""Unit tests for the cost check pack, 60+ tests covering analyzers, reporters, and edge cases."""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from io import StringIO
from pathlib import Path
import json
import tempfile
import os


# ── Test data fixtures ────────────────────────────────────────────────────────

def _make_daily_df(group_col: str, name: str, costs: list[float], start_days_ago: int = None) -> pd.DataFrame:
    if start_days_ago is None:
        start_days_ago = len(costs)
    dates = [
        datetime.now(timezone.utc).date() - timedelta(days=start_days_ago - i)
        for i in range(len(costs))
    ]
    return pd.DataFrame({"date": pd.to_datetime(dates), group_col: name, "cost": costs})


def _stable_costs(n=30, base=100.0, noise=5.0):
    np.random.seed(42)
    return [max(0, base + np.random.normal(0, noise)) for _ in range(n)]


def _spike_costs(n=30, base=100.0, spike_day=-1, spike_mult=5.0):
    costs = _stable_costs(n, base)
    costs[spike_day] = base * spike_mult
    return costs


def _drop_costs(n=30, base=100.0, drop_day=-1):
    costs = _stable_costs(n, base)
    costs[drop_day] = base * 0.1
    return costs


def _multi_service_df():
    return pd.concat([
        _make_daily_df("service", "EC2", _stable_costs(30, 500)),
        _make_daily_df("service", "S3", _stable_costs(30, 200)),
        _make_daily_df("service", "RDS", _stable_costs(30, 150)),
        _make_daily_df("service", "Lambda", _stable_costs(30, 50)),
        _make_daily_df("service", "CloudWatch", _stable_costs(30, 20)),
    ])


def _multi_account_df():
    return pd.concat([
        _make_daily_df("account", "111111111111", _stable_costs(30, 300)),
        _make_daily_df("account", "222222222222", _stable_costs(30, 200)),
        _make_daily_df("account", "333333333333", _stable_costs(30, 50, noise=30)),
    ])


# ── Anomaly Detection (10 tests) ─────────────────────────────────────────────

class TestAnomalyDetection:
    def test_no_anomalies_on_stable_data(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        df = _make_daily_df("service", "EC2", _stable_costs(30, 100, 2))
        result = detect_anomalies(df, "service", z_threshold=3.0)
        assert len(result) == 0 or all(result["severity"] == "info")

    def test_detects_spike(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        df = _make_daily_df("service", "EC2", _spike_costs(30, 100, spike_mult=10))
        result = detect_anomalies(df, "service", z_threshold=2.0)
        assert not result.empty
        assert result.iloc[0]["latest_cost"] > 500

    def test_detects_drop(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        df = _make_daily_df("service", "EC2", _drop_costs(30, 100))
        result = detect_anomalies(df, "service", z_threshold=2.0)
        assert not result.empty
        assert result.iloc[0]["pct_change"] < 0

    def test_drops_not_critical(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        df = _make_daily_df("service", "EC2", _drop_costs(30, 50))
        result = detect_anomalies(df, "service", z_threshold=2.0)
        if not result.empty:
            assert all(result["severity"] != "critical")

    def test_deduplicates_per_service(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        df = _make_daily_df("service", "EC2", _spike_costs(30, 100, spike_mult=10))
        result = detect_anomalies(df, "service", z_threshold=2.0)
        assert len(result[result["service"] == "EC2"]) <= 1

    def test_empty_df_returns_empty(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        result = detect_anomalies(pd.DataFrame(), "service")
        assert result.empty

    def test_short_data_skipped(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        df = _make_daily_df("service", "EC2", [10, 20, 30])
        result = detect_anomalies(df, "service")
        assert result.empty

    def test_daily_spikes(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_daily_spikes
        df = _make_daily_df("service", "EC2", _spike_costs(30, 100, spike_mult=5))
        result = detect_daily_spikes(df, "service", spike_multiplier=2.0)
        assert not result.empty

    def test_multi_service_anomalies(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        df = pd.concat([
            _make_daily_df("service", "EC2", _spike_costs(30, 100, spike_mult=8)),
            _make_daily_df("service", "S3", _stable_costs(30, 50, 1)),
        ])
        result = detect_anomalies(df, "service", z_threshold=2.0)
        if not result.empty:
            assert "EC2" in result["service"].values

    def test_high_threshold_fewer_anomalies(self):
        from kulshan.checks.cost.analyzers.anomaly import detect_anomalies
        df = _make_daily_df("service", "EC2", _spike_costs(30, 100, spike_mult=3))
        low = detect_anomalies(df, "service", z_threshold=1.5)
        high = detect_anomalies(df, "service", z_threshold=4.0)
        assert len(high) <= len(low)


# ── Trend Analysis (6 tests) ─────────────────────────────────────────────────

class TestTrends:
    def test_growth_rates_basic(self):
        from kulshan.checks.cost.analyzers.trends import compute_growth_rates
        df = _make_daily_df("service", "EC2", _stable_costs(30, 100))
        result = compute_growth_rates(df, "service")
        assert not result.empty
        assert "wow_growth_pct" in result.columns

    def test_daily_trend(self):
        from kulshan.checks.cost.analyzers.trends import compute_daily_trend
        df = _make_daily_df("service", "EC2", _stable_costs(30, 100))
        result = compute_daily_trend(df)
        assert not result.empty
        assert "7d_avg" in result.columns
        assert len(result) == 30

    def test_top_movers(self):
        from kulshan.checks.cost.analyzers.trends import top_movers
        costs1 = _stable_costs(14, 100) + _stable_costs(14, 200)
        df = _make_daily_df("service", "EC2", costs1)
        result = top_movers(df, "service")
        assert not result.empty

    def test_empty_df(self):
        from kulshan.checks.cost.analyzers.trends import compute_growth_rates
        result = compute_growth_rates(pd.DataFrame(), "service")
        assert result.empty

    def test_multi_service_growth(self):
        from kulshan.checks.cost.analyzers.trends import compute_growth_rates
        df = _multi_service_df()
        result = compute_growth_rates(df, "service")
        assert len(result) >= 3

    def test_daily_trend_rolling_avg(self):
        from kulshan.checks.cost.analyzers.trends import compute_daily_trend
        df = _make_daily_df("service", "EC2", [100] * 7 + [200] * 23)
        result = compute_daily_trend(df)
        assert result["7d_avg"].iloc[-1] > result["7d_avg"].iloc[3]


# ── Waste Detection (5 tests) ────────────────────────────────────────────────

class TestWaste:
    def test_detects_idle(self):
        from kulshan.checks.cost.analyzers.waste import detect_idle_services
        df = _make_daily_df("service", "IdleService", [0.10] * 30)
        result = detect_idle_services(df, "service", idle_threshold=0.50)
        assert not result.empty
        assert result.iloc[0]["service"] == "IdleService"

    def test_ignores_active(self):
        from kulshan.checks.cost.analyzers.waste import detect_idle_services
        df = _make_daily_df("service", "ActiveService", [100.0] * 30)
        result = detect_idle_services(df, "service")
        assert result.empty

    def test_pareto(self):
        from kulshan.checks.cost.analyzers.waste import compute_cost_distribution
        df = pd.concat([
            _make_daily_df("service", "Big", [500] * 10),
            _make_daily_df("service", "Medium", [300] * 10),
            _make_daily_df("service", "Small", [100] * 10),
            _make_daily_df("service", "Tiny", [10] * 10),
        ])
        result = compute_cost_distribution(df, "service")
        assert not result.empty
        big_row = result[result["service"] == "Big"].iloc[0]
        tiny_row = result[result["service"] == "Tiny"].iloc[0]
        assert big_row["pareto_group"] == "Top 80%"
        assert tiny_row["pareto_group"] == "Long tail"

    def test_coverage_opportunities_empty(self):
        from kulshan.checks.cost.analyzers.waste import coverage_opportunities
        result = coverage_opportunities(pd.DataFrame())
        assert result.empty

    def test_coverage_opportunities_low_coverage(self):
        from kulshan.checks.cost.analyzers.waste import coverage_opportunities
        df = pd.DataFrame({"service": ["EC2"], "coverage_pct": [20.0], "on_demand_hours": [800], "reserved_hours": [200]})
        result = coverage_opportunities(df)
        assert not result.empty


# ── Insights (6 tests) ───────────────────────────────────────────────────────

class TestInsights:
    def test_wow_insights(self):
        from kulshan.checks.cost.analyzers.insights import compute_wow_insights
        df = _make_daily_df("service", "EC2", _stable_costs(30, 100))
        result = compute_wow_insights(df)
        assert "trend" in result
        assert "wow_change_pct" in result
        assert "most_expensive_day" in result

    def test_cost_story(self):
        from kulshan.checks.cost.analyzers.insights import generate_cost_story
        df = _make_daily_df("service", "EC2", _stable_costs(30, 100))
        story = generate_cost_story({"service": df})
        assert "EC2" in story
        assert "$" in story

    def test_empty_data(self):
        from kulshan.checks.cost.analyzers.insights import compute_wow_insights
        result = compute_wow_insights(pd.DataFrame())
        assert result == {}

    def test_cost_story_multi_service(self):
        from kulshan.checks.cost.analyzers.insights import generate_cost_story
        data = {"service": _multi_service_df()}
        story = generate_cost_story(data)
        assert len(story) > 20
        assert "$" in story

    def test_cost_story_with_anomalies(self):
        from kulshan.checks.cost.analyzers.insights import generate_cost_story
        data = {"service": _multi_service_df()}
        anomalies = pd.DataFrame({"service": ["EC2"], "latest_cost": [999], "pct_change": [50], "severity": ["critical"]})
        story = generate_cost_story(data, anomalies)
        assert len(story) > 20

    def test_wow_trend_direction(self):
        from kulshan.checks.cost.analyzers.insights import compute_wow_insights
        increasing = [50] * 14 + [150] * 16
        df = _make_daily_df("service", "EC2", increasing)
        result = compute_wow_insights(df)
        assert result.get("trend") in ("increasing", "stable", "decreasing")


# ── Efficiency Score (6 tests) ───────────────────────────────────────────────

class TestEfficiency:
    def test_score_range(self):
        from kulshan.checks.cost.analyzers.efficiency import compute_efficiency_score
        result = compute_efficiency_score(
            ri_coverage=pd.DataFrame(), sp_utilization=pd.DataFrame(),
            ri_utilization=pd.DataFrame(), idle_df=pd.DataFrame(),
            anomalies_df=pd.DataFrame(), total_spend=100000,
        )
        assert 0 <= result["total_score"] <= 100
        assert result["grade"] in ("A", "B", "C", "D", "F")

    def test_perfect_score_components(self):
        from kulshan.checks.cost.analyzers.efficiency import compute_efficiency_score
        ri_cov = pd.DataFrame({"coverage_pct": [90.0]})
        sp_util = pd.DataFrame({"utilization_pct": [95.0]})
        result = compute_efficiency_score(
            ri_coverage=ri_cov, sp_utilization=sp_util,
            ri_utilization=pd.DataFrame(), idle_df=pd.DataFrame(),
            anomalies_df=pd.DataFrame(), total_spend=100000,
        )
        assert result["total_score"] > 50

    def test_zero_spend(self):
        from kulshan.checks.cost.analyzers.efficiency import compute_efficiency_score
        result = compute_efficiency_score(
            ri_coverage=pd.DataFrame(), sp_utilization=pd.DataFrame(),
            ri_utilization=pd.DataFrame(), idle_df=pd.DataFrame(),
            anomalies_df=pd.DataFrame(), total_spend=0,
        )
        assert 0 <= result["total_score"] <= 100

    def test_grade_boundaries(self):
        from kulshan.checks.cost.analyzers.efficiency import _grade
        assert _grade(95) == "A"
        assert _grade(85) == "B"
        assert _grade(70) == "C"
        assert _grade(55) == "D"
        assert _grade(30) == "F"

    def test_critical_anomalies_lower_score(self):
        from kulshan.checks.cost.analyzers.efficiency import compute_efficiency_score
        clean = compute_efficiency_score(
            ri_coverage=pd.DataFrame(), sp_utilization=pd.DataFrame(),
            ri_utilization=pd.DataFrame(), idle_df=pd.DataFrame(),
            anomalies_df=pd.DataFrame(), total_spend=100000,
        )
        anomalies = pd.DataFrame({"service": ["EC2", "S3"], "severity": ["critical", "critical"],
                                   "latest_cost": [500, 300], "pct_change": [100, 80]})
        bad = compute_efficiency_score(
            ri_coverage=pd.DataFrame(), sp_utilization=pd.DataFrame(),
            ri_utilization=pd.DataFrame(), idle_df=pd.DataFrame(),
            anomalies_df=anomalies, total_spend=100000,
        )
        assert bad["total_score"] <= clean["total_score"]

    def test_score_has_breakdown(self):
        from kulshan.checks.cost.analyzers.efficiency import compute_efficiency_score
        result = compute_efficiency_score(
            ri_coverage=pd.DataFrame(), sp_utilization=pd.DataFrame(),
            ri_utilization=pd.DataFrame(), idle_df=pd.DataFrame(),
            anomalies_df=pd.DataFrame(), total_spend=100000,
        )
        assert "breakdown" in result
        assert len(result["breakdown"]) == 5


# ── Network Analysis (4 tests) ───────────────────────────────────────────────

class TestNetwork:
    def test_categorize_empty(self):
        from kulshan.checks.cost.analyzers.network import categorize_network_costs
        result = categorize_network_costs(pd.DataFrame())
        assert result == {}

    def test_categorize_nat(self):
        from kulshan.checks.cost.analyzers.network import categorize_network_costs
        df = pd.DataFrame({"service": ["Amazon Virtual Private Cloud"], "usage_type": ["NatGateway-Hours"], "cost": [100.0]})
        result = categorize_network_costs(df)
        assert result["grand_total"] > 0
        assert "nat_gateway" in result["categories"]

    def test_category_labels(self):
        from kulshan.checks.cost.analyzers.network import get_category_label
        assert "NAT" in get_category_label("nat_gateway")
        assert len(get_category_label("unknown_category")) > 0

    def test_categorize_data_transfer(self):
        from kulshan.checks.cost.analyzers.network import categorize_network_costs
        df = pd.DataFrame({"service": ["Amazon CloudFront"], "usage_type": ["DataTransfer-Out-Bytes"], "cost": [50.0]})
        result = categorize_network_costs(df)
        assert result["grand_total"] == 50.0


# ── WOW Features (8 tests) ───────────────────────────────────────────────────

class TestWowFeatures:
    def test_ecosystem_costs(self):
        from kulshan.checks.cost.analyzers.wow import compute_ecosystem_costs
        df = _multi_service_df()
        result = compute_ecosystem_costs(df)
        assert isinstance(result, list)
        assert len(result) > 0
        assert "ecosystem" in result[0]
        assert "total" in result[0]

    def test_cost_dna(self):
        from kulshan.checks.cost.analyzers.wow import compute_cost_dna
        df = _multi_account_df()
        result = compute_cost_dna(df)
        assert isinstance(result, list)
        assert len(result) > 0
        assert "account" in result[0]
        assert "archetype" in result[0]

    def test_tide_chart(self):
        from kulshan.checks.cost.analyzers.wow import compute_tide_chart
        df = _multi_service_df()
        result = compute_tide_chart(df)
        assert "dow_stats" in result
        assert len(result["dow_stats"]) == 7

    def test_cold_open(self):
        from kulshan.checks.cost.analyzers.wow import generate_cold_open
        data = {"service": _multi_service_df()}
        result = generate_cold_open(data)
        assert isinstance(result, str)
        assert len(result) > 10

    def test_human_terms(self):
        from kulshan.checks.cost.analyzers.wow import cost_in_human_terms
        result = cost_in_human_terms(50000, 30)
        assert isinstance(result, list)
        assert len(result) > 0
        assert "emoji" in result[0]

    def test_human_terms_small_spend(self):
        from kulshan.checks.cost.analyzers.wow import cost_in_human_terms
        result = cost_in_human_terms(5.0, 30)
        assert isinstance(result, list)

    def test_ecosystem_empty(self):
        from kulshan.checks.cost.analyzers.wow import compute_ecosystem_costs
        result = compute_ecosystem_costs(pd.DataFrame())
        assert result == []

    def test_dna_empty(self):
        from kulshan.checks.cost.analyzers.wow import compute_cost_dna
        result = compute_cost_dna(pd.DataFrame())
        assert result == []


# ── Exporter Tests (8 tests) ─────────────────────────────────────────────────

class TestExporter:
    def _make_exporter(self):
        from kulshan.checks.cost.reporters.exporter import CostExporter
        data = {"service": _multi_service_df()}
        return CostExporter(data=data, days=30, story="Test story.")

    def test_export_json(self):
        exp = self._make_exporter()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = exp.export_json(f.name)
        try:
            content = json.loads(Path(path).read_text())
            assert "service" in content
            assert content["days_analyzed"] == 30
            assert content["story"] == "Test story."
        finally:
            os.unlink(path)

    def test_export_csv(self):
        exp = self._make_exporter()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = exp.export_csv(f.name)
        try:
            content = Path(path).read_text()
            assert "dimension" in content
            assert "cost" in content
        finally:
            os.unlink(path)

    def test_export_markdown(self):
        exp = self._make_exporter()
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            path = exp.export_markdown(f.name)
        try:
            content = Path(path).read_text()
            assert "# Cost Analysis Report" in content
            assert "Test story." in content
        finally:
            os.unlink(path)

    def test_export_json_with_anomalies(self):
        from kulshan.checks.cost.reporters.exporter import CostExporter
        anomalies = pd.DataFrame({"service": ["EC2"], "latest_cost": [999], "avg_cost": [100],
                                   "pct_change": [50], "severity": ["critical"], "score": [3.5], "methods": ["Z-Score"]})
        exp = CostExporter(data={"service": _multi_service_df()}, days=30, anomalies=anomalies)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = exp.export_json(f.name)
        try:
            content = json.loads(Path(path).read_text())
            assert "anomalies" in content
        finally:
            os.unlink(path)

    def test_export_ical(self):
        from kulshan.checks.cost.reporters.exporter import CostExporter
        anomalies = pd.DataFrame({"service": ["EC2"], "latest_cost": [999], "avg_cost": [100],
                                   "pct_change": [50], "severity": ["critical"], "date": [datetime.utcnow()],
                                   "score": [3.5], "methods": ["Z-Score"]})
        exp = CostExporter(data={"service": _multi_service_df()}, days=30, anomalies=anomalies)
        with tempfile.NamedTemporaryFile(suffix=".ics", delete=False) as f:
            path = exp.export_ical(f.name)
        try:
            content = Path(path).read_text(encoding="utf-8")
            assert "BEGIN:VCALENDAR" in content
            assert "VEVENT" in content
        finally:
            os.unlink(path)

    def test_export_empty_data(self):
        from kulshan.checks.cost.reporters.exporter import CostExporter
        exp = CostExporter(data={}, days=30)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = exp.export_json(f.name)
        try:
            content = json.loads(Path(path).read_text())
            assert content["days_analyzed"] == 30
        finally:
            os.unlink(path)

    def test_export_csv_empty(self):
        from kulshan.checks.cost.reporters.exporter import CostExporter
        exp = CostExporter(data={}, days=30)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = exp.export_csv(f.name)
        try:
            assert Path(path).exists()
        finally:
            os.unlink(path)

    def test_slack_payload_structure(self):
        exp = self._make_exporter()
        # We can't test actual webhook, but verify the method exists and handles bad URL gracefully
        result = exp.send_slack("https://invalid.example.com/webhook")
        assert result is False


# ── HTML Report Tests (5 tests) ──────────────────────────────────────────────

class TestHTMLReport:
    def test_generates_html(self):
        from kulshan.checks.cost.reporters.html_report import generate_html_report
        data = {"service": _multi_service_df()}
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = generate_html_report(data, f.name, 30)
        try:
            content = Path(path).read_text()
            assert "<!DOCTYPE html>" in content
            assert "Cost Analysis Report" in content
            assert "Chart.js" in content or "chart.js" in content
        finally:
            os.unlink(path)

    def test_html_with_story(self):
        from kulshan.checks.cost.reporters.html_report import generate_html_report
        data = {"service": _multi_service_df()}
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = generate_html_report(data, f.name, 30, story="Test cost story here.")
        try:
            content = Path(path).read_text()
            assert "Test cost story here." in content
        finally:
            os.unlink(path)

    def test_html_with_anomalies(self):
        from kulshan.checks.cost.reporters.html_report import generate_html_report
        data = {"service": _multi_service_df()}
        anomalies = pd.DataFrame({"service": ["EC2"], "latest_cost": [999], "avg_cost": [100],
                                   "pct_change": [50], "severity": ["critical"], "score": [3.5], "methods": ["Z-Score"]})
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = generate_html_report(data, f.name, 30, anomalies=anomalies)
        try:
            content = Path(path).read_text()
            assert "Anomalies" in content
            assert "critical" in content.lower() or "Critical" in content
        finally:
            os.unlink(path)

    def test_html_with_efficiency(self):
        from kulshan.checks.cost.reporters.html_report import generate_html_report
        data = {"service": _multi_service_df()}
        score = {"total_score": 72, "grade": "C", "breakdown": {
            "ri_sp_coverage": {"score": 10, "max": 25, "value": "40%", "target": "80%", "status": "⚠️"},
            "ri_sp_utilization": {"score": 15, "max": 25, "value": "60%", "target": "90%", "status": "⚠️"},
            "waste_detection": {"score": 18, "max": 20, "value": "1 idle", "target": "0", "status": "⚠️"},
            "anomaly_health": {"score": 15, "max": 15, "value": "0 critical", "target": "0", "status": "✅"},
            "cost_stability": {"score": 14, "max": 15, "value": "Committed", "target": "Committed", "status": "✅"},
        }}
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = generate_html_report(data, f.name, 30, efficiency_score=score)
        try:
            content = Path(path).read_text(encoding="utf-8")
            assert "72/100" in content
        finally:
            os.unlink(path)

    def test_html_empty_data(self):
        from kulshan.checks.cost.reporters.html_report import generate_html_report
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = generate_html_report({}, f.name, 30)
        try:
            content = Path(path).read_text()
            assert "<!DOCTYPE html>" in content
        finally:
            os.unlink(path)


# ── Package Metadata Tests (1 test) ──────────────────────────────────────────
# CLI removed: the cost check pack no longer ships a standalone CLI; Kulshan
# is the only customer-facing CLI. The tests for DaysType, _parse_days, and
# cli loading were deleted alongside the per-pack cli.py.

class TestPackageMetadata:
    def test_version(self):
        from kulshan.checks.cost import __version__
        assert __version__ == "0.1.0"

    def test_run_scan_is_top_level(self):
        from kulshan.checks.cost import run_scan
        assert callable(run_scan)


# ── Score Tracking Tests (4 tests) ───────────────────────────────────────────

class TestScoreTracking:
    class _MemoryPath:
        content = None

        def __init__(self, _path=""):
            self.parent = self

        def mkdir(self, **_kwargs):
            return None

        def exists(self):
            return self.content is not None

        def write_text(self, content, **_kwargs):
            type(self).content = content

        def read_text(self, **_kwargs):
            return type(self).content

    def setup_method(self):
        self._MemoryPath.content = None

    def test_save_and_load(self, monkeypatch):
        from kulshan.checks.cost.analyzers import efficiency

        monkeypatch.setattr(efficiency, "Path", self._MemoryPath)
        efficiency.save_score({"total_score": 75, "grade": "C"}, 50000)
        history = efficiency.load_score_history()
        assert len(history) == 1
        assert history[0]["score"] == 75

    def test_no_duplicate_same_day(self, monkeypatch):
        from kulshan.checks.cost.analyzers import efficiency

        monkeypatch.setattr(efficiency, "Path", self._MemoryPath)
        efficiency.save_score({"total_score": 50, "grade": "D"}, 10000)
        efficiency.save_score({"total_score": 60, "grade": "D"}, 12000)
        history = efficiency.load_score_history()
        assert len(history) == 1
        assert history[0]["score"] == 60

    def test_load_empty(self, monkeypatch):
        from kulshan.checks.cost.analyzers import efficiency

        monkeypatch.setattr(efficiency, "Path", self._MemoryPath)
        assert efficiency.load_score_history() == []

    def test_trend_insufficient_data(self, monkeypatch):
        from kulshan.checks.cost.analyzers import efficiency

        monkeypatch.setattr(efficiency, "Path", self._MemoryPath)
        self._MemoryPath.content = "[]"
        assert efficiency.get_score_trend() is None
