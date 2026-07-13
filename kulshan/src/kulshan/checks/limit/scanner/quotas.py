"""Scan service quotas and current usage across key AWS services."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple
import threading

from ..utils.aws import paginate_all, safe_api_call

# High-value quotas to check in the default fast path. These are the quotas most
# likely to block a scaling event or audit conversation.
CRITICAL_QUOTAS = [
    {"service": "ec2", "name": "Running On-Demand Standard", "counter": "_count_ec2_instances"},
    {"service": "vpc", "name": "VPCs per Region", "counter": "_count_vpcs"},
    {"service": "vpc", "name": "Security groups per Region", "counter": "_count_security_groups"},
    {"service": "elasticloadbalancing", "name": "Application Load Balancers", "counter": "_count_albs"},
    {"service": "rds", "name": "DB instances", "counter": "_count_rds_instances"},
    {"service": "cloudformation", "name": "Stack count", "counter": "_count_cfn_stacks"},
    {"service": "ebs", "name": "Snapshots per Region", "counter": "_count_ebs_snapshots"},
    {"service": "iam", "name": "IAM Roles", "counter": "_count_iam_roles", "global": True},
    {"service": "iam", "name": "IAM Users", "counter": "_count_iam_users", "global": True},
    {"service": "iam", "name": "Customer managed policies", "counter": "_count_iam_policies", "global": True},
    {"service": "s3", "name": "Buckets", "counter": "_count_s3_buckets", "global": True},
]

# Services that are known to be unavailable in certain regions
# These will be skipped with a clean message instead of an error
REGION_UNAVAILABLE_SERVICES = {
    "iam": ["ap-northeast-1", "ap-northeast-2", "ap-northeast-3", "eu-west-1", "eu-central-1"],
}


def scan_quotas(session, regions, quick=False, deep=False, progress=None, task_id=None) -> Tuple[List[Dict], List[str]]:
    """Scan service quotas and compute utilization percentages.

    Default mode is intentionally narrow and fast: it checks critical quota
    headroom only. Deep mode preserves the old exhaustive Service Quotas crawl.
    """
    if deep:
        return _scan_quotas_deep(session, regions, quick=quick, progress=progress, task_id=task_id)
    return _scan_critical_quotas_parallel(session, regions, progress=progress, task_id=task_id)


def _scan_critical_quotas_parallel(session, regions, progress=None, task_id=None) -> Tuple[List[Dict], List[str]]:
    """Scan critical quotas with parallel region execution and caching."""
    from kulshan.parallel import get_quota_cache
    
    quotas = []
    errors = []
    quota_cache = get_quota_cache()
    results_lock = threading.Lock()
    
    # Separate global and regional specs
    global_specs = [s for s in CRITICAL_QUOTAS if s.get("global")]
    regional_specs = [s for s in CRITICAL_QUOTAS if not s.get("global")]
    
    def scan_region(region: str) -> Tuple[List[Dict], List[str]]:
        """Scan quotas for a single region."""
        region_quotas = []
        region_errors = []
        sq = session.client("service-quotas", region_name=region)
        local_cache = {}
        
        for spec in regional_specs:
            svc_code = spec["service"]
            
            # Check if service is known to be unavailable in this region
            if svc_code in REGION_UNAVAILABLE_SERVICES:
                if region in REGION_UNAVAILABLE_SERVICES[svc_code]:
                    continue
            
            cache_key = f"{region}:{svc_code}"
            
            # Try global cache first
            cached = quota_cache.get(cache_key)
            if cached is not None:
                svc_quotas = cached
            elif cache_key in local_cache:
                svc_quotas = local_cache[cache_key]
            else:
                svc_quotas, q_err = paginate_all(sq, "list_service_quotas", "Quotas", ServiceCode=svc_code)
                if q_err:
                    # Check for region-unavailable error
                    if "not available in the current Region" in str(q_err):
                        local_cache[cache_key] = []
                        continue
                    region_errors.append(f"Service Quotas {svc_code} ({region}): {q_err}")
                    local_cache[cache_key] = []
                else:
                    local_cache[cache_key] = svc_quotas
                    quota_cache.set(cache_key, svc_quotas)
                svc_quotas = local_cache.get(cache_key, [])
            
            quota = _match_quota(svc_quotas, spec["name"])
            if not quota:
                continue
            
            current_usage = _count_for_spec(session, region, spec, region_errors)
            region_quotas.append(_quota_row(svc_code, quota, region, current_usage))
        
        return region_quotas, region_errors
    
    # Scan global quotas first (only once, in first region)
    first_region = regions[0] if regions else "us-east-1"
    sq = session.client("service-quotas", region_name=first_region)
    
    for spec in global_specs:
        svc_code = spec["service"]
        cache_key = f"global:{svc_code}"
        
        cached = quota_cache.get(cache_key)
        if cached is not None:
            svc_quotas = cached
        else:
            svc_quotas, q_err = paginate_all(sq, "list_service_quotas", "Quotas", ServiceCode=svc_code)
            if q_err:
                if "not available in the current Region" not in str(q_err):
                    errors.append(f"Service Quotas {svc_code} (global): {q_err}")
                svc_quotas = []
            quota_cache.set(cache_key, svc_quotas)
        
        quota = _match_quota(svc_quotas, spec["name"])
        if not quota:
            continue
        
        current_usage = _count_for_spec(session, first_region, spec, errors)
        quotas.append(_quota_row(svc_code, quota, "global", current_usage))
    
    # Scan regional quotas in parallel
    max_workers = min(len(regions), 8)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scan_region, r): r for r in regions}
        for future in as_completed(futures):
            region_quotas, region_errors = future.result()
            with results_lock:
                quotas.extend(region_quotas)
                errors.extend(region_errors)
            
            if progress and task_id:
                progress.advance(task_id)
    
    return quotas, errors


def _scan_critical_quotas(session, regions, progress=None, task_id=None) -> Tuple[List[Dict], List[str]]:
    quotas = []
    errors = []
    quota_cache = {}

    for region in regions:
        sq = session.client("service-quotas", region_name=region)
        for spec in CRITICAL_QUOTAS:
            if spec.get("global") and region != regions[0]:
                continue

            svc_code = spec["service"]
            cache_key = (region, svc_code)
            if cache_key not in quota_cache:
                svc_quotas, q_err = paginate_all(sq, "list_service_quotas", "Quotas", ServiceCode=svc_code)
                if q_err:
                    errors.append(f"Service Quotas {svc_code} ({region}): {q_err}")
                    quota_cache[cache_key] = []
                else:
                    quota_cache[cache_key] = svc_quotas

            quota = _match_quota(quota_cache[cache_key], spec["name"])
            if not quota:
                continue

            current_usage = _count_for_spec(session, region, spec, errors)
            quotas.append(_quota_row(svc_code, quota, region, current_usage))

        if progress and task_id:
            progress.advance(task_id)

    return quotas, errors


def _scan_quotas_deep(session, regions, quick=False, progress=None, task_id=None) -> Tuple[List[Dict], List[str]]:
    """Exhaustive Service Quotas scan. This is intentionally opt-in."""
    quotas = []
    errors = []

    for region in regions:
        sq = session.client("service-quotas", region_name=region)

        services, err = paginate_all(sq, "list_services", "Services")
        if err:
            errors.append(f"Service Quotas ({region}): {err}")
            if progress and task_id:
                progress.advance(task_id)
            continue

        if quick:
            top_codes = {
                "ec2", "vpc", "elasticloadbalancing", "lambda", "rds",
                "cloudformation", "ebs", "s3", "iam", "dynamodb",
                "elasticache", "ecs", "eks", "sns", "sqs",
            }
            services = [s for s in services if s.get("ServiceCode", "") in top_codes]

        for svc in services:
            svc_code = svc.get("ServiceCode", "")
            svc_quotas, q_err = paginate_all(sq, "list_service_quotas", "Quotas", ServiceCode=svc_code)
            if q_err:
                continue

            for quota in svc_quotas:
                quota_value = quota.get("Value")
                if quota_value is None or quota_value == 0:
                    continue

                current_usage = None
                usage_metric = quota.get("UsageMetric")
                if usage_metric:
                    current_usage = _get_usage_from_metric(session, region, usage_metric)

                quotas.append(_quota_row(svc_code, quota, region, current_usage))

        if progress and task_id:
            progress.advance(task_id)

    _enrich_with_direct_counts(session, regions, quotas, errors)
    return quotas, errors


def _match_quota(quotas, quota_name):
    needle = quota_name.lower()
    for quota in quotas:
        candidate = quota.get("QuotaName", "").lower()
        if needle in candidate or candidate in needle:
            return quota
    return None


def _count_for_spec(session, region, spec, errors):
    counter_name = spec.get("counter")
    if not counter_name:
        return None
    counter_fn = globals().get(counter_name)
    if counter_fn is None:
        return None
    try:
        return counter_fn(session, region)
    except Exception as exc:
        errors.append(f"Direct count {spec['service']}/{spec['name']} ({region}): {exc}")
        return None


def _quota_row(svc_code, quota, region, current_usage):
    quota_value = quota.get("Value") or 0
    utilization_pct = None
    if current_usage is not None and quota_value > 0:
        utilization_pct = round((current_usage / quota_value) * 100, 1)

    status = "ok"
    if utilization_pct is not None:
        if utilization_pct >= 80:
            status = "critical"
        elif utilization_pct >= 60:
            status = "warning"

    return {
        "service_code": svc_code,
        "service_name": quota.get("ServiceName", svc_code),
        "quota_name": quota.get("QuotaName", "?"),
        "quota_code": quota.get("QuotaCode", "?"),
        "quota_value": quota_value,
        "limit_value": quota_value,
        "current_usage": current_usage,
        "current_value": current_usage,
        "utilization_pct": utilization_pct,
        "usage_percent": utilization_pct or 0,
        "status": status,
        "region": region,
        "adjustable": quota.get("Adjustable", False),
        "global_quota": quota.get("GlobalQuota", False),
    }


def _get_usage_from_metric(session, region, usage_metric):
    """Get current usage from CloudWatch metric. Used only in deep scans."""
    try:
        namespace = usage_metric.get("MetricNamespace", "")
        metric_name = usage_metric.get("MetricName", "")
        dimensions = usage_metric.get("MetricDimensions", {})

        if not namespace or not metric_name:
            return None

        cw = session.client("cloudwatch", region_name=region)
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=1)

        cw_dims = [{"Name": k, "Value": v} for k, v in dimensions.items()]

        resp = cw.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=cw_dims,
            StartTime=start,
            EndTime=end,
            Period=3600,
            Statistics=["Maximum"],
        )
        datapoints = resp.get("Datapoints", [])
        if datapoints:
            return max(dp.get("Maximum", 0) for dp in datapoints)
    except Exception:
        pass
    return None


def _enrich_with_direct_counts(session, regions, quotas, errors):
    """Fill in usage for critical quotas via direct API calls."""
    for region in regions:
        for spec in CRITICAL_QUOTAS:
            if spec.get("global") and region != regions[0]:
                continue
            _try_direct_count(session, region, quotas, spec["service"], spec["name"], globals().get(spec.get("counter", "")), errors)


def _try_direct_count(session, region, quotas, svc_code, quota_name, counter_fn, errors):
    if counter_fn is None:
        return
    for quota in quotas:
        if quota["service_code"] == svc_code and quota["region"] == region and quota_name.lower() in quota["quota_name"].lower():
            if quota["current_usage"] is None:
                try:
                    count = counter_fn(session, quota["region"])
                    quota["current_usage"] = count
                    quota["current_value"] = count
                    if quota["quota_value"] > 0:
                        pct = round((count / quota["quota_value"]) * 100, 1)
                        quota["utilization_pct"] = pct
                        quota["usage_percent"] = pct
                        quota["status"] = "critical" if pct >= 80 else "warning" if pct >= 60 else "ok"
                except Exception as exc:
                    errors.append(f"Direct count {svc_code}/{quota_name} ({region}): {exc}")


def _count_ec2_instances(session, region):
    ec2 = session.client("ec2", region_name=region)
    resp, _ = safe_api_call(ec2, "describe_instances", Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
    return sum(len(r.get("Instances", [])) for r in (resp or {}).get("Reservations", []))


def _count_vpcs(session, region):
    ec2 = session.client("ec2", region_name=region)
    resp, _ = safe_api_call(ec2, "describe_vpcs")
    return len((resp or {}).get("Vpcs", []))


def _count_security_groups(session, region):
    ec2 = session.client("ec2", region_name=region)
    resp, _ = safe_api_call(ec2, "describe_security_groups")
    return len((resp or {}).get("SecurityGroups", []))


def _count_albs(session, region):
    elbv2 = session.client("elbv2", region_name=region)
    lbs, _ = paginate_all(elbv2, "describe_load_balancers", "LoadBalancers")
    return len([lb for lb in lbs if lb.get("Type") == "application"])


def _count_iam_roles(session, region):
    iam = session.client("iam")
    roles, _ = paginate_all(iam, "list_roles", "Roles")
    return len(roles)


def _count_iam_users(session, region):
    iam = session.client("iam")
    users, _ = paginate_all(iam, "list_users", "Users")
    return len(users)


def _count_iam_policies(session, region):
    iam = session.client("iam")
    policies, _ = paginate_all(iam, "list_policies", "Policies", Scope="Local")
    return len(policies)


def _count_rds_instances(session, region):
    rds = session.client("rds", region_name=region)
    instances, _ = paginate_all(rds, "describe_db_instances", "DBInstances")
    return len(instances)


def _count_cfn_stacks(session, region):
    cfn = session.client("cloudformation", region_name=region)
    stacks, _ = paginate_all(cfn, "list_stacks", "StackSummaries")
    return len([s for s in stacks if s.get("StackStatus") != "DELETE_COMPLETE"])


def _count_ebs_snapshots(session, region):
    ec2 = session.client("ec2", region_name=region)
    snaps, _ = paginate_all(ec2, "describe_snapshots", "Snapshots", OwnerIds=["self"])
    return len(snaps)


def _count_s3_buckets(session, region):
    s3 = session.client("s3", region_name="us-east-1")
    resp, _ = safe_api_call(s3, "list_buckets")
    return len((resp or {}).get("Buckets", []))