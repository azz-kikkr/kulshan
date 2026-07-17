"""Source-to-registry coverage guard.

Discovers AWS SDK calls in src/kulshan/ using AST inspection and verifies
every call has a corresponding entry in iam/registry.json.

Patterns detected:
- safe_api_call(client, "method_name", ...)
- paginate_all(client, "method_name", ...)
- client.get_paginator("method_name")
- client.method_name(...) for known AWS API method patterns

Fails when a developer adds a new AWS API call without updating registry.json.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "kulshan" / "src" / "kulshan"
REGISTRY_PATH = REPO_ROOT / "kulshan" / "iam" / "registry.json"

# AWS API method name patterns (prefixes that indicate an AWS API call)
AWS_METHOD_PREFIXES = (
    "describe_", "list_", "get_", "create_", "delete_", "put_",
    "update_", "detect_", "generate_", "assume_", "start_",
    "stop_", "terminate_",
)

# SDK-internal or no-IAM-permission methods that should be excluded
# These are boto3 client methods that do NOT map to IAM actions
SDK_INTERNAL_METHODS = frozenset({
    "get_paginator",
    "get_waiter",
    "can_paginate",
    "close",
    "exceptions",
    "meta",
    # S3 presigned URLs (client-side, no IAM call)
    "generate_presigned_url",
    "generate_presigned_post",
    # STS decode (no separate IAM action needed beyond the one being decoded)
    "decode_authorization_message",
})

# Methods that are called but require no customer IAM permission
# (they are authorized by the SDK session itself or are metadata)
NO_IAM_METHODS = frozenset({
    "get_paginator",
    "get_waiter",
    "can_paginate",
})


@dataclass(frozen=True)
class DiscoveredCall:
    """A discovered AWS SDK call site."""
    file: str
    line: int
    method: str
    pattern: str  # "safe_api_call", "paginate_all", "get_paginator", "direct"


def _discover_calls_in_file(filepath: Path) -> Iterator[DiscoveredCall]:
    """Parse a Python file and yield discovered AWS SDK call sites."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except SyntaxError:
        return

    relative = str(filepath.relative_to(SRC_DIR)).replace("\\", "/")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Pattern 1: safe_api_call(client, "method_name", ...)
        if (isinstance(node.func, ast.Name) and node.func.id == "safe_api_call"
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            yield DiscoveredCall(
                file=relative, line=node.lineno,
                method=node.args[1].value, pattern="safe_api_call",
            )

        # Pattern 2: paginate_all(client, "method_name", ...)
        elif (isinstance(node.func, ast.Name) and node.func.id == "paginate_all"
              and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
              and isinstance(node.args[1].value, str)):
            yield DiscoveredCall(
                file=relative, line=node.lineno,
                method=node.args[1].value, pattern="paginate_all",
            )

        # Pattern 3: client.get_paginator("method_name")
        elif (isinstance(node.func, ast.Attribute)
              and node.func.attr == "get_paginator"
              and len(node.args) >= 1 and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)):
            yield DiscoveredCall(
                file=relative, line=node.lineno,
                method=node.args[0].value, pattern="get_paginator",
            )

        # Pattern 4: client.method_name(...) where method matches AWS patterns
        elif (isinstance(node.func, ast.Attribute)
              and isinstance(node.func.attr, str)
              and any(node.func.attr.startswith(p) for p in AWS_METHOD_PREFIXES)
              and node.func.attr not in SDK_INTERNAL_METHODS):
            yield DiscoveredCall(
                file=relative, line=node.lineno,
                method=node.func.attr, pattern="direct",
            )


def _discover_all_calls() -> list[DiscoveredCall]:
    """Discover all AWS SDK calls across the entire source tree."""
    calls = []
    for filepath in sorted(SRC_DIR.rglob("*.py")):
        # Skip __pycache__
        if "__pycache__" in str(filepath):
            continue
        calls.extend(_discover_calls_in_file(filepath))
    return calls


def _load_registry_methods() -> set[str]:
    """Load all boto3 method names from the registry."""
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    methods = set()
    for entry in registry["actions"]:
        # boto3_method is like "iam.get_account_authorization_details"
        # Extract just the method name part
        parts = entry["boto3_method"].split(".")
        if len(parts) >= 2:
            methods.add(parts[-1])
    return methods


# Explicit reviewed allowlist for dynamic or unusual call patterns
# that the AST inspector cannot resolve but are known and mapped.
DYNAMIC_CALL_ALLOWLIST = frozenset({
    # DuckDB httpfs calls (not boto3, handled by DuckDB's own S3 auth)
    # These show up as describe_regions in session.py for region discovery
})

# Methods that appear as direct calls but are NOT AWS API calls
# (they are on non-boto3 objects that happen to match the prefix pattern)
FALSE_POSITIVE_METHODS = frozenset({
    # Click CLI framework methods
    "get_command",
    "get_short_help_str",
    "list_commands",
    # Kulshan internal methods (workspace, history, cost_fetcher helpers)
    "get_credentials",
    "get_available_regions",
    "get_enabled_regions",
    "get_active_profiler",
    "get_history_db_path",
    "get_connection",
    "get_connection_by_profile",
    "get_previous_scan",
    "get_scan",
    "list_scans",
    "delete_all",
    # Cost fetcher wrapper methods (they call CE APIs internally)
    "get_cost_by_dimension",
    "get_cost_by_service_and_account",
    "get_cost_by_tag",
    "get_cost_by_usage_type_filter",
    "get_top_resources",
    "get_total_spend",
    "get_anomalies_from_service",
    # Openpyxl / report methods
    "create_sheet",
    # boto3 session methods (not API calls)
    "get_frozen_credentials",
    "describe",  # pandas DataFrame.describe()
})


def _get_registry_and_exclusion_methods() -> tuple[set[str], set[str]]:
    """Get all known method names from registry plus exclusions."""
    registry_methods = _load_registry_methods()
    # Combine with SDK internals and false positives
    excluded = SDK_INTERNAL_METHODS | FALSE_POSITIVE_METHODS
    return registry_methods, excluded


class TestSourceToRegistryCoverage:
    """Every AWS SDK call in src/kulshan/ must have a registry mapping."""

    def test_all_safe_api_calls_are_registered(self):
        """safe_api_call and paginate_all method names must be in the registry."""
        registry_methods = _load_registry_methods()
        calls = _discover_all_calls()

        # Filter to safe_api_call and paginate_all patterns only
        # These are definitively AWS API calls
        unregistered = []
        for call in calls:
            if call.pattern in ("safe_api_call", "paginate_all", "get_paginator"):
                if call.method not in registry_methods and call.method not in SDK_INTERNAL_METHODS:
                    unregistered.append(call)

        if unregistered:
            msg_lines = ["AWS SDK calls found without registry mapping:"]
            for c in sorted(set((c.file, c.line, c.method) for c in unregistered)):
                msg_lines.append(f"  {c[0]}:{c[1]} -> {c[2]}")
            pytest.fail("\n".join(msg_lines))

    def test_direct_aws_calls_are_registered(self):
        """Direct client.method() calls with AWS-pattern names must be registered."""
        registry_methods = _load_registry_methods()
        calls = _discover_all_calls()

        # Filter to direct pattern only
        unregistered = []
        for call in calls:
            if call.pattern == "direct":
                if (call.method not in registry_methods
                        and call.method not in SDK_INTERNAL_METHODS
                        and call.method not in FALSE_POSITIVE_METHODS):
                    unregistered.append(call)

        if unregistered:
            # Deduplicate by method name for readable output
            unique_methods = sorted(set(
                (c.method, c.file, c.line) for c in unregistered
            ))
            msg_lines = ["Direct AWS SDK calls found without registry mapping:"]
            for method, file, line in unique_methods:
                msg_lines.append(f"  {file}:{line} -> {method}")
            pytest.fail("\n".join(msg_lines))

    def test_discovery_finds_known_calls(self):
        """Sanity check: discovery finds at least the calls we know exist."""
        calls = _discover_all_calls()
        methods_found = {c.method for c in calls}

        # These must be discoverable
        expected = {
            "describe_instances",
            "list_functions",
            "get_cost_and_usage",
            "describe_alarms",
            "list_stacks",
        }
        missing = expected - methods_found
        assert not missing, f"Expected AWS methods not found by discovery: {missing}"

    def test_no_unresolved_call_sites(self):
        """Report total discovery statistics and confirm no gaps."""
        calls = _discover_all_calls()
        registry_methods = _load_registry_methods()

        total_sites = len(calls)
        distinct_methods = {c.method for c in calls}
        registered = distinct_methods & registry_methods
        sdk_internal = distinct_methods & SDK_INTERNAL_METHODS
        false_pos = distinct_methods & FALSE_POSITIVE_METHODS
        covered = registered | sdk_internal | false_pos
        unresolved = distinct_methods - covered

        # This test documents the state; it fails if there are unresolved methods
        assert not unresolved, (
            f"Unresolved AWS SDK call sites:\n"
            f"  Total call sites: {total_sites}\n"
            f"  Distinct methods: {len(distinct_methods)}\n"
            f"  Registry mappings: {len(registered)}\n"
            f"  SDK internal: {len(sdk_internal)}\n"
            f"  False positives: {len(false_pos)}\n"
            f"  Unresolved: {sorted(unresolved)}"
        )
