"""Fetch cost data from AWS Cost Explorer API."""
from __future__ import annotations

import boto3
import pandas as pd
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Tuple


class _RateLimiter:
    """Simple thread-safe rate limiter for AWS API calls (default 4 req/sec)."""
    def __init__(self, calls_per_second: float = 4.0):
        self._min_interval = 1.0 / calls_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


# Dimension keys supported by Cost Explorer
DIMENSIONS = {
    "service": "SERVICE",
    "account": "LINKED_ACCOUNT",
    "region": "REGION",
    "usage_type": "USAGE_TYPE",
    "api": "OPERATION",
    "charge_type": "RECORD_TYPE",
    "instance_type": "INSTANCE_TYPE",
    "platform": "PLATFORM",
    "purchase_option": "PURCHASE_TYPE",
    "tenancy": "TENANCY",
    "database_engine": "DATABASE_ENGINE",
    "legal_entity": "LEGAL_ENTITY_NAME",
    "deployment_option": "DEPLOYMENT_OPTION",
    "billing_entity": "BILLING_ENTITY",
    "az": "AZ",
}


class CostFetcher:
    """Fetches cost data from AWS Cost Explorer API."""

    def __init__(self, profile: Optional[str] = None, region: str = "us-east-1"):
        session_kwargs = {}
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs)
        self.ce = session.client("ce", region_name=region)
        self.org = session.client("organizations", region_name=region)
        self._services_cache: Optional[list[tuple[str, float]]] = None
        self._account_names_cache: Optional[dict[str, str]] = None
        self._rate_limiter = _RateLimiter(calls_per_second=4.0)

    def _ce_call(self, method: str, **kwargs):
        """Rate-limited Cost Explorer API call."""
        self._rate_limiter.wait()
        return getattr(self.ce, method)(**kwargs)

    def _date_range(self, days: int) -> tuple[str, str]:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        # AWS CE requires start date to be 1st of month when going beyond 14 months
        if days > 425:
            start = start.replace(day=1)
        elif days > 365:
            start = start.replace(day=1)
        return start.isoformat(), end.isoformat()

    def get_available_services(self, days: int = 7) -> list[tuple[str, float]]:
        """Fetch services with actual costs, sorted by cost descending. Cached."""
        if self._services_cache is not None:
            return self._services_cache

        df = self.get_cost_by_dimension("service", days=days, granularity="MONTHLY")
        if df.empty:
            return []

        totals = df.groupby("service")["cost"].sum()
        totals = totals[totals > 0.01].sort_values(ascending=False)
        self._services_cache = [(name, cost) for name, cost in totals.items()]
        return self._services_cache

    def get_cost_by_dimension(
        self,
        dimension: str,
        days: int = 30,
        granularity: str = "DAILY",
        metric: str = "UnblendedCost",
        service_filter: Optional[str] = None,
        exclude_credits: bool = False,
    ) -> pd.DataFrame:
        """Fetch costs grouped by a given dimension, optionally filtered by service."""
        start, end = self._date_range(days)
        dim_key = DIMENSIONS.get(dimension, dimension)

        # Auto-select hourly granularity for short ranges (≤14 days)
        if days <= 14 and granularity == "DAILY":
            granularity = "HOURLY"

        results = []
        next_token = None
        while True:
            kwargs = dict(
                TimePeriod={"Start": start, "End": end},
                Granularity=granularity,
                Metrics=[metric],
                GroupBy=[{"Type": "DIMENSION", "Key": dim_key}],
            )

            # Build filter (service + exclude credits can be combined)
            filters = []
            if service_filter:
                # Fuzzy match: use CONTAINS if it looks like a partial name
                if len(service_filter) < 30 and not service_filter.startswith("Amazon") and not service_filter.startswith("AWS"):
                    filters.append({
                        "Dimensions": {"Key": "SERVICE", "Values": [service_filter], "MatchOptions": ["CONTAINS"]}
                    })
                else:
                    filters.append({
                        "Dimensions": {"Key": "SERVICE", "Values": [service_filter]}
                    })
            if exclude_credits:
                filters.append({
                    "Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}
                })

            if len(filters) == 1:
                kwargs["Filter"] = filters[0]
            elif len(filters) > 1:
                kwargs["Filter"] = {"And": filters}

            if next_token:
                kwargs["NextPageToken"] = next_token

            resp = self._ce_call("get_cost_and_usage", **kwargs)
            results.extend(resp["ResultsByTime"])
            next_token = resp.get("NextPageToken")
            if not next_token:
                break

        rows = []
        for period in results:
            date = period["TimePeriod"]["Start"]
            for group in period.get("Groups", []):
                key = group["Keys"][0]
                amount = float(group["Metrics"][metric]["Amount"])
                unit = group["Metrics"][metric]["Unit"]
                rows.append({"date": date, dimension: key, "cost": amount, "unit": unit})

        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def get_cost_multi_dimension(
        self,
        dim1: str,
        dim2: str,
        days: int = 30,
        granularity: str = "MONTHLY",
        metric: str = "UnblendedCost",
    ) -> pd.DataFrame:
        """Fetch costs grouped by two dimensions (e.g. service + account)."""
        start, end = self._date_range(days)
        dk1 = DIMENSIONS.get(dim1, dim1)
        dk2 = DIMENSIONS.get(dim2, dim2)

        results = []
        next_token = None
        while True:
            kwargs = dict(
                TimePeriod={"Start": start, "End": end},
                Granularity=granularity,
                Metrics=[metric],
                GroupBy=[
                    {"Type": "DIMENSION", "Key": dk1},
                    {"Type": "DIMENSION", "Key": dk2},
                ],
            )
            if next_token:
                kwargs["NextPageToken"] = next_token
            resp = self._ce_call("get_cost_and_usage", **kwargs)
            results.extend(resp["ResultsByTime"])
            next_token = resp.get("NextPageToken")
            if not next_token:
                break

        rows = []
        for period in results:
            date = period["TimePeriod"]["Start"]
            for group in period.get("Groups", []):
                keys = group["Keys"]
                amount = float(group["Metrics"][metric]["Amount"])
                rows.append({"date": date, dim1: keys[0], dim2: keys[1], "cost": amount})

        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    # Metric format mapping: GetCostAndUsage uses CamelCase, GetCostForecast uses UPPER_SNAKE
    FORECAST_METRIC_MAP = {
        "UnblendedCost": "UNBLENDED_COST",
        "BlendedCost": "BLENDED_COST",
        "AmortizedCost": "AMORTIZED_COST",
        "NetUnblendedCost": "NET_UNBLENDED_COST",
    }

    def get_cost_forecast(
        self, days_ahead: int = 30, metric: str = "UNBLENDED_COST"
    ) -> pd.DataFrame:
        """Get cost forecast. Use MONTHLY granularity for >90 days, DAILY otherwise."""
        start = datetime.utcnow().date() + timedelta(days=1)
        end = start + timedelta(days=days_ahead)
        forecast_metric = self.FORECAST_METRIC_MAP.get(metric, metric)
        granularity = "MONTHLY" if days_ahead > 90 else "DAILY"
        try:
            resp = self._ce_call("get_cost_forecast",
                TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                Metric=forecast_metric,
                Granularity=granularity,
            )
            rows = []
            for point in resp.get("ForecastResultsByTime", []):
                rows.append({
                    "date": point["TimePeriod"]["Start"],
                    "forecast": float(point["MeanValue"]),
                    "lower_bound": float(point.get("PredictionIntervalLowerBound", point["MeanValue"])),
                    "upper_bound": float(point.get("PredictionIntervalUpperBound", point["MeanValue"])),
                })
            df = pd.DataFrame(rows)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception:
            return pd.DataFrame()

    def get_reservation_coverage(self, days: int = 30) -> pd.DataFrame:
        """Get RI/SP coverage data."""
        start, end = self._date_range(days)
        try:
            resp = self._ce_call("get_reservation_coverage",
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            rows = []
            for period in resp.get("CoveragesByTime", []):
                for group in period.get("Groups", []):
                    svc = group["Attributes"].get("SERVICE", "Unknown")
                    cov = group["Coverage"]["CoverageHours"]
                    rows.append({
                        "service": svc,
                        "on_demand_hours": float(cov.get("OnDemandHours", 0)),
                        "reserved_hours": float(cov.get("ReservedHours", 0)),
                        "coverage_pct": float(cov.get("CoverageHoursPercentage", 0)),
                    })
            return pd.DataFrame(rows)
        except Exception:
            return pd.DataFrame()

    def get_savings_plans_utilization(self, days: int = 30) -> pd.DataFrame:
        """Get Savings Plans utilization."""
        start, end = self._date_range(days)
        try:
            resp = self._ce_call("get_savings_plans_utilization",
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
            )
            rows = []
            for period in resp.get("SavingsPlansUtilizationsByTime", []):
                u = period["Utilization"]
                rows.append({
                    "date": period["TimePeriod"]["Start"],
                    "utilization_pct": float(u.get("UtilizationPercentage", 0)),
                    "total_commitment": float(u.get("TotalCommitment", 0)),
                    "used_commitment": float(u.get("UsedCommitment", 0)),
                    "unused_commitment": float(u.get("UnusedCommitment", 0)),
                })
            return pd.DataFrame(rows)
        except Exception:
            return pd.DataFrame()

    def get_account_names(self) -> dict[str, str]:
        """Resolve account IDs to names via Organizations API. Cached."""
        if self._account_names_cache is not None:
            return self._account_names_cache
        mapping = {}
        try:
            paginator = self.org.get_paginator("list_accounts")
            for page in paginator.paginate():
                for acct in page["Accounts"]:
                    mapping[acct["Id"]] = acct["Name"]
        except Exception:
            pass
        self._account_names_cache = mapping
        return mapping

    def get_reservation_utilization(self, days: int = 30) -> pd.DataFrame:
        """Get RI utilization by service."""
        start, end = self._date_range(days)
        try:
            resp = self._ce_call("get_reservation_utilization",
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            rows = []
            for period in resp.get("UtilizationsByTime", []):
                for group in period.get("Groups", []):
                    svc = group.get("Key", "Unknown")
                    u = group["Utilization"]
                    rows.append({
                        "service": svc,
                        "utilization_pct": float(u.get("UtilizationPercentage", 0)),
                        "purchased_hours": float(u.get("PurchasedHours", 0)),
                        "used_hours": float(u.get("TotalActualHours", 0)),
                        "unused_hours": float(u.get("UnusedHours", 0)),
                        "net_savings": float(u.get("NetRISavings", 0)),
                        "on_demand_equivalent": float(u.get("OnDemandCostOfRIHoursUsed", 0)),
                    })
            return pd.DataFrame(rows)
        except Exception:
            return pd.DataFrame()

    def get_network_costs(self, days: int = 30, metric: str = "UnblendedCost") -> pd.DataFrame:
        """Get network-related costs by usage type for deep-dive analysis."""
        start, end = self._date_range(days)
        network_services = [
            "Amazon Virtual Private Cloud",
            "Amazon CloudFront",
            "AWS Cloud WAN",
            "AWS Network Firewall",
            "Amazon Route 53",
            "AWS Transit Gateway",
        ]
        results = []
        next_token = None
        while True:
            kwargs = dict(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Metrics=[metric],
                Filter={"Dimensions": {"Key": "SERVICE", "Values": network_services}},
                GroupBy=[
                    {"Type": "DIMENSION", "Key": "SERVICE"},
                    {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
                ],
            )
            if next_token:
                kwargs["NextPageToken"] = next_token
            resp = self._ce_call("get_cost_and_usage", **kwargs)
            results.extend(resp["ResultsByTime"])
            next_token = resp.get("NextPageToken")
            if not next_token:
                break

        rows = []
        for period in results:
            for group in period.get("Groups", []):
                keys = group["Keys"]
                amount = float(group["Metrics"][metric]["Amount"])
                if amount > 0.01:
                    rows.append({"service": keys[0], "usage_type": keys[1], "cost": amount})

        return pd.DataFrame(rows)

    def get_cost_by_service_and_account(
        self,
        days: int = 90,
        metric: str = "UnblendedCost",
        service_filter: Optional[str] = None,
        max_pages: int = 5,
    ) -> pd.DataFrame:
        """Fetch daily costs grouped by service AND account for deep anomaly detection.

        Pagination is capped at ``max_pages`` to ensure consistent API cost
        regardless of organization size (e.g. 1000-account orgs).
        """
        start, end = self._date_range(days)

        results = []
        next_token = None
        pages = 0
        while True:
            kwargs = dict(
                TimePeriod={"Start": start, "End": end},
                Granularity="DAILY",
                Metrics=[metric],
                GroupBy=[
                    {"Type": "DIMENSION", "Key": "SERVICE"},
                    {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
                ],
            )
            if service_filter:
                kwargs["Filter"] = {
                    "Dimensions": {"Key": "SERVICE", "Values": [service_filter]}
                }
            if next_token:
                kwargs["NextPageToken"] = next_token
            resp = self._ce_call("get_cost_and_usage", **kwargs)
            results.extend(resp["ResultsByTime"])
            next_token = resp.get("NextPageToken")
            pages += 1
            if not next_token or pages >= max_pages:
                break

        rows = []
        for period in results:
            date = period["TimePeriod"]["Start"]
            for group in period.get("Groups", []):
                keys = group["Keys"]
                amount = float(group["Metrics"][metric]["Amount"])
                rows.append({
                    "date": date,
                    "service": keys[0],
                    "account": keys[1],
                    "cost": amount,
                })

        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def get_rightsizing_recommendations(self, service: str = "AmazonEC2") -> pd.DataFrame:
        """Fetch AWS rightsizing recommendations."""
        try:
            results = []
            next_token = None
            while True:
                kwargs = dict(
                    Service=service,
                    Configuration={
                        "RecommendationTarget": "SAME_INSTANCE_FAMILY",
                        "BenefitsConsidered": True,
                    },
                )
                if next_token:
                    kwargs["NextPageToken"] = next_token
                resp = self._ce_call("get_rightsizing_recommendation", **kwargs)
                results.extend(resp.get("RightsizingRecommendations", []))
                next_token = resp.get("NextPageToken")
                if not next_token:
                    break

            rows = []
            for rec in results:
                current = rec.get("CurrentInstance", {})
                action = rec.get("RightsizingType", "Unknown")
                monthly_cost = float(
                    current.get("MonthlyCost", "0")
                    if isinstance(current.get("MonthlyCost"), str)
                    else current.get("MonthlyCost", 0)
                )

                row = {
                    "account": current.get("ResourceDetails", {})
                        .get("EC2ResourceDetails", {})
                        .get("AccountId", current.get("AccountId", "Unknown")),
                    "instance_id": current.get("ResourceId", "Unknown"),
                    "instance_type": current.get("ResourceDetails", {})
                        .get("EC2ResourceDetails", {})
                        .get("InstanceType", "Unknown"),
                    "region": current.get("ResourceDetails", {})
                        .get("EC2ResourceDetails", {})
                        .get("Region", "Unknown"),
                    "action": action,
                    "monthly_cost": monthly_cost,
                }

                # Get target recommendation
                targets = rec.get("ModifyRecommendationDetail", {}).get("TargetInstances", [])
                if targets:
                    target = targets[0]
                    target_cost = float(
                        target.get("EstimatedMonthlyCost", "0")
                        if isinstance(target.get("EstimatedMonthlyCost"), str)
                        else target.get("EstimatedMonthlyCost", 0)
                    )
                    row["target_type"] = target.get("ResourceDetails", {}).get(
                        "EC2ResourceDetails", {}
                    ).get("InstanceType", "-")
                    row["target_monthly_cost"] = target_cost
                    row["estimated_savings"] = round(monthly_cost - target_cost, 2)
                else:
                    row["target_type"] = "Terminate"
                    row["target_monthly_cost"] = 0
                    row["estimated_savings"] = round(monthly_cost, 2)

                rows.append(row)

            return pd.DataFrame(rows)
        except Exception:
            return pd.DataFrame()

    # ── Sprint 2: New API integrations ────────────────────────────────

    def get_cost_comparison(
        self, days: int = 30, metric: str = "UnblendedCost"
    ) -> pd.DataFrame:
        """Compare current period vs previous period. Tries GetCostAndUsageComparisons first, falls back to manual."""
        end = datetime.utcnow().date()
        start = end - timedelta(days=days)
        prev_end = start
        prev_start = prev_end - timedelta(days=days)

        # Try native API first
        try:
            resp = self._ce_call("get_cost_and_usage_comparisons",
                CurrentTimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                BaselineTimePeriod={"Start": prev_start.isoformat(), "End": prev_end.isoformat()},
                Metric=metric,
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            rows = []
            for item in resp.get("CostAndUsageComparisons", []):
                svc = item.get("Keys", ["Unknown"])[0] if item.get("Keys") else "Unknown"
                current = float(item.get("CurrentTimePeriodAmount", 0))
                baseline = float(item.get("BaselineTimePeriodAmount", 0))
                diff = current - baseline
                pct = (diff / baseline * 100) if baseline > 0 else 0
                rows.append({
                    "service": svc, "current": round(current, 2),
                    "baseline": round(baseline, 2), "difference": round(diff, 2),
                    "pct_change": round(pct, 1),
                })
            df = pd.DataFrame(rows)
            if not df.empty:
                return df.sort_values("difference", key=abs, ascending=False)
        except Exception:
            pass

        # Fallback: manual comparison using GetCostAndUsage
        try:
            current_df = self.get_cost_by_dimension("service", days=days, granularity="MONTHLY", metric=metric)
            # Fetch previous period manually
            prev_results = []
            kwargs = dict(
                TimePeriod={"Start": prev_start.isoformat(), "End": prev_end.isoformat()},
                Granularity="MONTHLY", Metrics=[metric],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            resp = self._ce_call("get_cost_and_usage", **kwargs)
            for period in resp.get("ResultsByTime", []):
                for group in period.get("Groups", []):
                    prev_results.append({"service": group["Keys"][0], "cost": float(group["Metrics"][metric]["Amount"])})

            if not current_df.empty and prev_results:
                curr_totals = current_df.groupby("service")["cost"].sum()
                prev_totals = pd.DataFrame(prev_results).groupby("service")["cost"].sum()
                all_svcs = set(curr_totals.index) | set(prev_totals.index)
                rows = []
                for svc in all_svcs:
                    c = curr_totals.get(svc, 0)
                    b = prev_totals.get(svc, 0)
                    d = c - b
                    pct = (d / b * 100) if b > 0 else 0
                    if abs(d) > 0.01:
                        rows.append({"service": svc, "current": round(c, 2), "baseline": round(b, 2),
                                     "difference": round(d, 2), "pct_change": round(pct, 1)})
                df = pd.DataFrame(rows)
                if not df.empty:
                    return df.sort_values("difference", key=abs, ascending=False)
        except Exception:
            pass

        return pd.DataFrame()

    def get_cost_drivers(
        self, days: int = 30, metric: str = "UnblendedCost"
    ) -> pd.DataFrame:
        """Identify what's driving cost changes using GetCostComparisonDrivers."""
        end = datetime.utcnow().date()
        start = end - timedelta(days=days)
        prev_end = start
        prev_start = prev_end - timedelta(days=days)
        try:
            resp = self._ce_call("get_cost_comparison_drivers",
                CurrentTimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                BaselineTimePeriod={"Start": prev_start.isoformat(), "End": prev_end.isoformat()},
                Metric=metric,
            )
            rows = []
            for driver in resp.get("CostComparisonDrivers", []):
                rows.append({
                    "driver": driver.get("DriverName", "Unknown"),
                    "category": driver.get("DriverCategory", "Unknown"),
                    "impact": round(float(driver.get("Impact", {}).get("TotalImpact", 0)), 2),
                    "description": driver.get("Description", ""),
                })
            df = pd.DataFrame(rows)
            if not df.empty:
                df = df.sort_values("impact", key=abs, ascending=False)
            return df
        except Exception:
            return pd.DataFrame()

    def get_sp_purchase_recommendation(
        self, lookback: str = "SIXTY_DAYS", term: str = "ONE_YEAR",
        payment: str = "NO_UPFRONT", sp_type: str = "COMPUTE_SP",
    ) -> dict:
        """Get Savings Plans purchase recommendations."""
        try:
            resp = self._ce_call("get_savings_plans_purchase_recommendation",
                SavingsPlansType=sp_type,
                TermInYears=term,
                PaymentOption=payment,
                LookbackPeriodInDays=lookback,
            )
            meta = resp.get("Metadata", {})
            summary = resp.get("SavingsPlansPurchaseRecommendationSummary", {})
            details = resp.get("SavingsPlansPurchaseRecommendationDetails", [])

            recs = []
            for d in details[:9]:
                recs.append({
                    "account": d.get("AccountId", "-"),
                    "hourly_commitment": round(float(d.get("HourlyCommitmentToPurchase", 0)), 4),
                    "estimated_savings_pct": round(float(d.get("EstimatedSavingsPercentage", 0)), 1),
                    "estimated_monthly_savings": round(float(d.get("EstimatedMonthlySavingsAmount", 0)), 2),
                    "current_on_demand": round(float(d.get("CurrentAverageHourlyOnDemandSpend", 0)), 4),
                })

            return {
                "estimated_total_savings": round(float(summary.get("EstimatedTotalSavings", 0)), 2),
                "estimated_monthly_savings": round(float(summary.get("EstimatedMonthlySavingsAmount", 0)), 2),
                "estimated_savings_pct": round(float(summary.get("EstimatedSavingsPercentage", 0)), 1),
                "hourly_commitment": round(float(summary.get("HourlyCommitmentToPurchase", 0)), 4),
                "term": term,
                "payment": payment,
                "sp_type": sp_type,
                "details": recs,
            }
        except Exception:
            return {}

    def get_ri_purchase_recommendation(
        self, service: str = "Amazon Elastic Compute Cloud - Compute",
        lookback: str = "SIXTY_DAYS", term: str = "ONE_YEAR",
        payment: str = "NO_UPFRONT",
    ) -> dict:
        """Get Reserved Instance purchase recommendations."""
        try:
            resp = self._ce_call("get_reservation_purchase_recommendation",
                Service=service,
                LookbackPeriodInDays=lookback,
                TermInYears=term,
                PaymentOption=payment,
            )
            summary = resp.get("Metadata", {})
            recs_list = resp.get("Recommendations", [])

            details = []
            for rec in recs_list:
                for d in rec.get("RecommendationDetails", [])[:9]:
                    inst = d.get("InstanceDetails", {}).get("EC2InstanceDetails", {})
                    details.append({
                        "instance_type": inst.get("InstanceType", "-"),
                        "region": inst.get("Region", "-"),
                        "platform": inst.get("Platform", "-"),
                        "recommended_count": int(d.get("RecommendedNumberOfInstancesToPurchase", 0)),
                        "estimated_monthly_savings": round(float(d.get("EstimatedMonthlySavingsAmount", 0)), 2),
                        "upfront_cost": round(float(d.get("UpfrontCost", 0)), 2),
                        "monthly_cost": round(float(d.get("RecurringStandardMonthlyCost", 0)), 2),
                    })

            total_savings = sum(d["estimated_monthly_savings"] for d in details)
            return {
                "service": service,
                "term": term,
                "payment": payment,
                "total_monthly_savings": round(total_savings, 2),
                "details": details,
            }
        except Exception:
            return {}

    def get_anomalies_from_service(
        self, days: int = 90, min_impact: float = 10.0
    ) -> pd.DataFrame:
        """Get anomalies from AWS Cost Anomaly Detection service."""
        end = datetime.utcnow().date()
        start = end - timedelta(days=min(days, 90))  # API max is 90 days
        try:
            resp = self._ce_call("get_anomalies",
                DateInterval={"StartDate": start.isoformat(), "EndDate": end.isoformat()},
                MaxResults=50,
            )
            rows = []
            for a in resp.get("Anomalies", []):
                impact = a.get("Impact", {})
                root = a.get("RootCauses", [{}])[0] if a.get("RootCauses") else {}
                rows.append({
                    "anomaly_id": a.get("AnomalyId", ""),
                    "start_date": a.get("AnomalyStartDate", ""),
                    "end_date": a.get("AnomalyEndDate", ""),
                    "service": root.get("Service", "Unknown"),
                    "region": root.get("Region", ""),
                    "account": root.get("LinkedAccount", ""),
                    "usage_type": root.get("UsageType", ""),
                    "total_impact": round(float(impact.get("TotalImpact", 0)), 2),
                    "max_impact": round(float(impact.get("MaxImpact", 0)), 2),
                    "total_actual": round(float(impact.get("TotalActualSpend", 0)), 2),
                    "total_expected": round(float(impact.get("TotalExpectedSpend", 0)), 2),
                    "feedback": a.get("Feedback", ""),
                })
            return pd.DataFrame(rows)
        except Exception:
            return pd.DataFrame()



    # ── Tag-based cost breakdown ──────────────────────────────────────

    def get_cost_by_tag(
        self, tag_key: str, days: int = 30, metric: str = "UnblendedCost"
    ) -> pd.DataFrame:
        """Fetch costs grouped by a cost allocation tag."""
        start, end = self._date_range(days)
        results = []
        next_token = None
        while True:
            kwargs = dict(
                TimePeriod={"Start": start, "End": end},
                Granularity="DAILY",
                Metrics=[metric],
                GroupBy=[{"Type": "TAG", "Key": tag_key}],
            )
            if next_token:
                kwargs["NextPageToken"] = next_token
            resp = self._ce_call("get_cost_and_usage", **kwargs)
            results.extend(resp["ResultsByTime"])
            next_token = resp.get("NextPageToken")
            if not next_token:
                break

        rows = []
        for period in results:
            date = period["TimePeriod"]["Start"]
            for group in period.get("Groups", []):
                tag_val = group["Keys"][0]
                # CE returns "tag_key$value" format, extract just the value
                if "$" in tag_val:
                    tag_val = tag_val.split("$", 1)[1] or "(untagged)"
                amount = float(group["Metrics"][metric]["Amount"])
                rows.append({"date": date, "tag_value": tag_val, "cost": amount})

        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    # ── EC2-Other breakdown ───────────────────────────────────────────

    def get_ec2_other_breakdown(
        self, days: int = 30, metric: str = "UnblendedCost"
    ) -> pd.DataFrame:
        """Break down 'EC2 - Other' costs by USAGE_TYPE."""
        start, end = self._date_range(days)
        results = []
        next_token = None
        while True:
            kwargs = dict(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Metrics=[metric],
                Filter={"Dimensions": {"Key": "SERVICE", "Values": ["EC2 - Other"]}},
                GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
            )
            if next_token:
                kwargs["NextPageToken"] = next_token
            resp = self._ce_call("get_cost_and_usage", **kwargs)
            results.extend(resp["ResultsByTime"])
            next_token = resp.get("NextPageToken")
            if not next_token:
                break

        rows = []
        for period in results:
            for group in period.get("Groups", []):
                usage_type = group["Keys"][0]
                amount = float(group["Metrics"][metric]["Amount"])
                if amount > 0.01:
                    rows.append({"usage_type": usage_type, "cost": amount})

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.groupby("usage_type")["cost"].sum().reset_index()
            df = df.sort_values("cost", ascending=False)
        return df

    # ── Resource-level costs ──────────────────────────────────────────

    def get_top_resources(
        self, days: int = 30, metric: str = "UnblendedCost"
    ) -> pd.DataFrame:
        """Get costs by individual resource ID (EC2 instances). Requires opt-in."""
        start, end = self._date_range(min(days, 14))  # Resource-level limited to 14 days
        try:
            resp = self._ce_call("get_cost_and_usage_with_resources",
                TimePeriod={"Start": start, "End": end},
                Granularity="DAILY",
                Metrics=[metric],
                Filter={"Dimensions": {"Key": "SERVICE", "Values": [
                    "Amazon Elastic Compute Cloud - Compute"
                ]}},
                GroupBy=[
                    {"Type": "DIMENSION", "Key": "RESOURCE_ID"},
                ],
            )
            rows = []
            for period in resp.get("ResultsByTime", []):
                for group in period.get("Groups", []):
                    rid = group["Keys"][0]
                    amount = float(group["Metrics"][metric]["Amount"])
                    if amount > 0.01 and rid != "NoResourceId":
                        rows.append({"resource_id": rid, "cost": amount})

            df = pd.DataFrame(rows)
            if not df.empty:
                df = df.groupby("resource_id")["cost"].sum().reset_index()
                df = df.sort_values("cost", ascending=False).head(20)
            return df
        except Exception:
            return pd.DataFrame()

    def get_marketplace_costs(
        self, days: int = 365, metric: str = "UnblendedCost"
    ) -> pd.DataFrame:
        """Fetch AWS Marketplace (3rd-party vendor) costs by account and service, monthly."""
        start, end = self._date_range(days)
        results = []
        next_token = None
        while True:
            kwargs = dict(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Metrics=[metric],
                Filter={"Dimensions": {"Key": "BILLING_ENTITY", "Values": ["AWS Marketplace"]}},
                GroupBy=[
                    {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
                    {"Type": "DIMENSION", "Key": "SERVICE"},
                ],
            )
            if next_token:
                kwargs["NextPageToken"] = next_token
            resp = self._ce_call("get_cost_and_usage", **kwargs)
            results.extend(resp["ResultsByTime"])
            next_token = resp.get("NextPageToken")
            if not next_token:
                break

        rows = []
        for period in results:
            month = period["TimePeriod"]["Start"][:7]  # "2026-01"
            for group in period.get("Groups", []):
                keys = group["Keys"]
                amount = float(group["Metrics"][metric]["Amount"])
                if amount > 0.01:
                    rows.append({
                        "account": keys[0],
                        "service": keys[1],
                        "month": month,
                        "cost": amount,
                    })

        return pd.DataFrame(rows)


