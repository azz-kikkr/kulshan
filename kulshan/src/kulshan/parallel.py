"""Parallel execution utilities for Kulshan scan packs.

Provides thread-based parallel region scanning and pack execution.
Uses ThreadPoolExecutor for I/O-bound AWS API calls.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

# Default worker count: min(32, cpu_count + 4) for I/O bound work
DEFAULT_WORKERS = min(32, (os.cpu_count() or 4) + 4)

# Regional parallelism: scan up to N regions concurrently per pack
REGION_WORKERS = min(16, (os.cpu_count() or 4) + 2)

T = TypeVar("T")


@dataclass
class ParallelResult:
    """Result container for parallel execution."""
    results: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


def parallel_regions(
    fn: Callable[[Any, str], Tuple[Any, List[str]]],
    session: Any,
    regions: List[str],
    max_workers: Optional[int] = None,
    desc: str = "region",
) -> Tuple[Dict[str, Any], List[str]]:
    """Execute a function across multiple regions in parallel.

    Args:
        fn: Function that takes (session, region) and returns (result, errors)
        session: boto3 session to use
        regions: List of AWS regions to scan
        max_workers: Max concurrent workers (default: REGION_WORKERS)
        desc: Description for error messages

    Returns:
        Tuple of (results_by_region dict, all_errors list)
    """
    if not regions:
        return {}, []

    workers = max_workers or REGION_WORKERS
    results: Dict[str, Any] = {}
    all_errors: List[str] = []
    lock = threading.Lock()

    def run_region(region: str) -> Tuple[str, Any, List[str]]:
        try:
            result, errors = fn(session, region)
            return region, result, errors
        except Exception as e:
            return region, None, [f"{desc} {region}: {e}"]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_region, r): r for r in regions}
        for future in as_completed(futures):
            region, result, errors = future.result()
            with lock:
                if result is not None:
                    results[region] = result
                all_errors.extend(errors)

    return results, all_errors


def parallel_regions_flat(
    fn: Callable[[Any, str], Tuple[List[Any], List[str]]],
    session: Any,
    regions: List[str],
    max_workers: Optional[int] = None,
    desc: str = "region",
) -> Tuple[List[Any], List[str]]:
    """Execute a function across regions and flatten results into a single list.

    Args:
        fn: Function that takes (session, region) and returns (list_of_items, errors)
        session: boto3 session to use
        regions: List of AWS regions to scan
        max_workers: Max concurrent workers
        desc: Description for error messages

    Returns:
        Tuple of (flattened_results list, all_errors list)
    """
    results_by_region, all_errors = parallel_regions(
        fn, session, regions, max_workers, desc
    )
    
    flattened: List[Any] = []
    for region_results in results_by_region.values():
        if isinstance(region_results, list):
            flattened.extend(region_results)
    
    return flattened, all_errors


def parallel_scanners(
    scanners: Dict[str, Callable[[Any, List[str]], Tuple[Any, List[str]]]],
    session: Any,
    regions: List[str],
    max_workers: Optional[int] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Execute multiple scanner functions in parallel.

    Args:
        scanners: Dict mapping scanner name to function(session, regions) -> (result, errors)
        session: boto3 session to use
        regions: List of AWS regions
        max_workers: Max concurrent workers

    Returns:
        Tuple of (results_by_scanner dict, all_errors list)
    """
    if not scanners:
        return {}, []

    workers = max_workers or DEFAULT_WORKERS
    results: Dict[str, Any] = {}
    all_errors: List[str] = []
    lock = threading.Lock()

    def run_scanner(name: str, fn: Callable) -> Tuple[str, Any, List[str]]:
        try:
            result, errors = fn(session, regions)
            return name, result, errors
        except Exception as e:
            return name, None, [f"{name}: {e}"]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_scanner, name, fn): name
            for name, fn in scanners.items()
        }
        for future in as_completed(futures):
            name, result, errors = future.result()
            with lock:
                if result is not None:
                    results[name] = result
                all_errors.extend(errors)

    return results, all_errors


def parallel_map(
    fn: Callable[[T], Any],
    items: List[T],
    max_workers: Optional[int] = None,
    desc: str = "item",
) -> Tuple[List[Any], List[str]]:
    """Map a function over items in parallel.

    Args:
        fn: Function to apply to each item
        items: List of items to process
        max_workers: Max concurrent workers
        desc: Description for error messages

    Returns:
        Tuple of (results list, errors list)
    """
    if not items:
        return [], []

    workers = max_workers or DEFAULT_WORKERS
    results: List[Any] = []
    errors: List[str] = []
    lock = threading.Lock()

    def run_item(item: T) -> Tuple[Any, Optional[str]]:
        try:
            return fn(item), None
        except Exception as e:
            return None, f"{desc}: {e}"

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_item, item) for item in items]
        for future in as_completed(futures):
            result, error = future.result()
            with lock:
                if result is not None:
                    results.append(result)
                if error:
                    errors.append(error)

    return results, errors


class BatchedApiCaller:
    """Batches and parallelizes AWS API calls across regions.

    Example usage:
        caller = BatchedApiCaller(session)
        
        # Queue calls
        caller.add("ec2", "describe_instances", "us-east-1")
        caller.add("ec2", "describe_vpcs", "us-east-1")
        caller.add("ec2", "describe_instances", "us-west-2")
        
        # Execute all in parallel
        results = caller.execute()
        # results = {
        #   ("ec2", "describe_instances", "us-east-1"): {...},
        #   ("ec2", "describe_vpcs", "us-east-1"): {...},
        #   ...
        # }
    """

    def __init__(self, session: Any, max_workers: Optional[int] = None):
        self.session = session
        self.max_workers = max_workers or DEFAULT_WORKERS
        self._calls: List[Tuple[str, str, str, dict]] = []
        self._lock = threading.Lock()

    def add(
        self,
        service: str,
        method: str,
        region: str,
        **kwargs: Any,
    ) -> None:
        """Queue an API call for batch execution."""
        with self._lock:
            self._calls.append((service, method, region, kwargs))

    def execute(self) -> Tuple[Dict[Tuple[str, str, str], Any], List[str]]:
        """Execute all queued calls in parallel.

        Returns:
            Tuple of (results dict keyed by (service, method, region), errors list)
        """
        from kulshan.aws_runtime import safe_api_call

        results: Dict[Tuple[str, str, str], Any] = {}
        errors: List[str] = []
        lock = threading.Lock()

        def run_call(call: Tuple[str, str, str, dict]) -> Tuple[Tuple[str, str, str], Any, Optional[str]]:
            service, method, region, kwargs = call
            key = (service, method, region)
            try:
                client = self.session.client(service, region_name=region)
                result, error = safe_api_call(client, method, **kwargs)
                return key, result, error
            except Exception as e:
                return key, None, f"{service}.{method} ({region}): {e}"

        with self._lock:
            calls = list(self._calls)
            self._calls.clear()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(run_call, call) for call in calls]
            for future in as_completed(futures):
                key, result, error = future.result()
                with lock:
                    if result is not None:
                        results[key] = result
                    if error:
                        errors.append(error)

        return results, errors


# Cache for quota results to avoid redundant API calls
class QuotaCache:
    """Thread-safe cache for service quota data."""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value

    def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], Any],
    ) -> Tuple[Any, bool]:
        """Get from cache or fetch and cache.

        Returns:
            Tuple of (value, was_cached)
        """
        with self._lock:
            if key in self._cache:
                return self._cache[key], True

        # Fetch outside the lock to allow concurrent fetches for different keys
        value = fetch_fn()

        with self._lock:
            # Double-check in case another thread populated it
            if key not in self._cache:
                self._cache[key] = value
            return self._cache.get(key, value), False

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# Global quota cache instance
_quota_cache = QuotaCache()


def get_quota_cache() -> QuotaCache:
    """Get the global quota cache instance."""
    return _quota_cache
