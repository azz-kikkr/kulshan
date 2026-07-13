"""Shared AWS call runtime for scan packs.

This module centralizes the two things performance work needs first:
consistent boto client configuration and cheap timing telemetry for AWS calls.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import botocore
from botocore.config import Config

BOTO_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    max_pool_connections=32,
    retries={"max_attempts": 3, "mode": "adaptive"},
)


def client(session: Any, service: str, region_name: str | None = None):
    """Create a boto client with Kulshan's shared fast-scan config."""
    kwargs: dict[str, Any] = {"config": BOTO_CONFIG}
    if region_name:
        kwargs["region_name"] = region_name
    return session.client(service, **kwargs)


@dataclass
class ApiStat:
    service: str
    operation: str
    region: str
    calls: int = 0
    errors: int = 0
    pages: int = 0
    seconds: float = 0.0


@dataclass
class PackStat:
    pack: str
    seconds: float


@dataclass
class ApiProfiler:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stats: dict[tuple[str, str, str], ApiStat] = field(default_factory=dict)
    _packs: list[PackStat] = field(default_factory=list)

    def record_call(
        self,
        service: str,
        operation: str,
        region: str,
        seconds: float,
        *,
        pages: int = 0,
        error: bool = False,
    ) -> None:
        key = (service, operation, region)
        with self._lock:
            stat = self._stats.get(key)
            if stat is None:
                stat = ApiStat(service=service, operation=operation, region=region)
                self._stats[key] = stat
            stat.calls += 1
            stat.pages += pages
            stat.seconds += seconds
            if error:
                stat.errors += 1

    def record_pack(self, pack: str, seconds: float) -> None:
        with self._lock:
            self._packs.append(PackStat(pack=pack, seconds=seconds))

    def summary(self) -> dict[str, Any]:
        with self._lock:
            stats = list(self._stats.values())
            packs = list(self._packs)

        total_calls = sum(s.calls for s in stats)
        total_seconds = sum(s.seconds for s in stats)
        total_errors = sum(s.errors for s in stats)

        by_service: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "errors": 0, "seconds": 0.0, "pages": 0}
        )
        for stat in stats:
            item = by_service[stat.service]
            item["calls"] += stat.calls
            item["errors"] += stat.errors
            item["seconds"] += stat.seconds
            item["pages"] += stat.pages

        slowest_ops = sorted(stats, key=lambda s: s.seconds, reverse=True)[:12]
        slowest_packs = sorted(packs, key=lambda s: s.seconds, reverse=True)

        return {
            "total_calls": total_calls,
            "total_errors": total_errors,
            "total_seconds": total_seconds,
            "by_service": dict(sorted(by_service.items())),
            "slowest_operations": slowest_ops,
            "slowest_packs": slowest_packs,
        }


_active_profiler: ApiProfiler | None = None
_profiler_lock = threading.Lock()


def set_active_profiler(profiler: ApiProfiler | None) -> None:
    global _active_profiler
    with _profiler_lock:
        _active_profiler = profiler


def get_active_profiler() -> ApiProfiler | None:
    with _profiler_lock:
        return _active_profiler


def _client_identity(client_obj: Any) -> tuple[str, str]:
    meta = getattr(client_obj, "meta", None)
    service = "unknown"
    region = "global"
    if meta is not None:
        service_model = getattr(meta, "service_model", None)
        service = getattr(service_model, "service_name", None) or getattr(
            meta, "service_model", service
        )
        region = getattr(meta, "region_name", None) or "global"
    return str(service), str(region)


def safe_api_call(client_obj: Any, method: str, **kwargs):
    retries = 3
    service, region = _client_identity(client_obj)
    last_error = "Max retries exceeded"

    # Known regional service limitations
    REGIONAL_LIMITATIONS = {
        ("iam", "list_service_quotas"): "IAM quotas only available via Service Quotas in us-east-1",
        ("service-quotas", "list_service_quotas"): "Some services not available in all regions",
    }

    for attempt in range(retries):
        start = time.perf_counter()
        try:
            result = getattr(client_obj, method)(**kwargs)
            elapsed = time.perf_counter() - start
            profiler = get_active_profiler()
            if profiler:
                profiler.record_call(service, method, region, elapsed)
            if isinstance(result, dict):
                result.pop("ResponseMetadata", None)
            return result, None
        except botocore.exceptions.ClientError as exc:
            elapsed = time.perf_counter() - start
            code = exc.response["Error"]["Code"]
            message = exc.response["Error"]["Message"]
            profiler = get_active_profiler()
            if profiler:
                profiler.record_call(service, method, region, elapsed, error=True)
            if code in ("Throttling", "TooManyRequestsException", "RequestLimitExceeded"):
                time.sleep(2 ** attempt)
                last_error = code
                continue
            if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedAccess"):
                return None, f"Access denied: {method}"
            
            # Improved error messages for regional limitations
            if "not available in the current Region" in message:
                hint = REGIONAL_LIMITATIONS.get((service, method), "")
                if hint:
                    return None, f"{service} {method} unavailable in {region} ({hint})"
                return None, f"{service} {method} not available in {region}"
            if code == "NoSuchResourceException":
                return None, f"{service} {method}: Service not available in {region}"
            if code == "InvalidRegionException":
                return None, f"{service} not supported in region {region}"
            
            return None, f"{code}: {message}"
        except botocore.exceptions.EndpointConnectionError:
            elapsed = time.perf_counter() - start
            profiler = get_active_profiler()
            if profiler:
                profiler.record_call(service, method, region, elapsed, error=True)
            return None, f"{service} endpoint not available in {region}"
        except Exception as exc:
            elapsed = time.perf_counter() - start
            profiler = get_active_profiler()
            if profiler:
                profiler.record_call(service, method, region, elapsed, error=True)
            return None, str(exc)

    return None, last_error


def paginate_all(client_obj: Any, method: str, key: str, **kwargs):
    results = []
    service, region = _client_identity(client_obj)
    start = time.perf_counter()
    pages = 0
    try:
        paginator = client_obj.get_paginator(method)
        for page in paginator.paginate(**kwargs):
            pages += 1
            results.extend(page.get(key, []))
        elapsed = time.perf_counter() - start
        profiler = get_active_profiler()
        if profiler:
            profiler.record_call(service, method, region, elapsed, pages=pages)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        profiler = get_active_profiler()
        if profiler:
            profiler.record_call(service, method, region, elapsed, pages=pages, error=True)
        return results, str(exc)
    return results, None


def render_perf_summary(console: Any, profiler: ApiProfiler) -> None:
    summary = profiler.summary()
    console.print()
    console.print("[bold]AWS API performance[/bold]")
    console.print(
        f"  Calls: {summary['total_calls']}  "
        f"Errors: {summary['total_errors']}  "
        f"API wait: {summary['total_seconds']:.1f}s"
    )

    if summary["slowest_packs"]:
        console.print("  Slowest packs:")
        for pack in summary["slowest_packs"][:6]:
            console.print(f"    {pack.pack}: {pack.seconds:.1f}s")

    if summary["slowest_operations"]:
        console.print("  Slowest AWS operations:")
        for stat in summary["slowest_operations"][:8]:
            page_suffix = f", {stat.pages} page(s)" if stat.pages else ""
            console.print(
                f"    {stat.service}:{stat.operation} "
                f"[{stat.region}] {stat.seconds:.1f}s, {stat.calls} call(s){page_suffix}"
            )
