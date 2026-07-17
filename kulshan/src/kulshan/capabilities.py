"""Pack capability requirements for preflight and coverage.

Defines which AWS services each pack needs and provides lightweight
probes to test accessibility without running full scans.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import botocore.exceptions

logger = logging.getLogger(__name__)

CapabilityStatus = Literal[
    "available", "denied", "unavailable", "not_configured", "not_checked", "error"
]
PackReadiness = Literal["ready", "partial", "unavailable", "not_checked"]


@dataclass(frozen=True)
class ServiceProbe:
    """A lightweight probe to test service accessibility."""

    service: str
    method: str
    region: str = "us-east-1"
    kwargs: dict = field(default_factory=dict)
    description: str = ""


@dataclass
class CapabilityResult:
    """Result of probing a single capability."""

    service: str
    method: str
    status: CapabilityStatus
    description: str = ""
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "service": self.service,
            "method": self.method,
            "status": self.status,
        }
        if self.description:
            d["description"] = self.description
        if self.error_code:
            d["error_code"] = self.error_code
        if self.error_message:
            d["error_message"] = self.error_message
        return d


@dataclass
class PackReadinessResult:
    """Readiness assessment for a single pack."""

    pack: str
    readiness: PackReadiness
    capabilities: list[CapabilityResult] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "readiness": self.readiness,
            "reason": self.reason,
        }
        if self.capabilities:
            d["capabilities"] = [c.to_dict() for c in self.capabilities]
        return d


# ---------------------------------------------------------------------------
# Pack probes: one lightweight call per required service capability.
# All probes use MaxResults/MaxItems to prevent expensive pagination.
# ---------------------------------------------------------------------------

PACK_PROBES: dict[str, list[ServiceProbe]] = {
    "cost": [
        ServiceProbe(
            "ce", "get_cost_and_usage",
            description="Cost Explorer API",
            kwargs={
                "TimePeriod": {"Start": "2020-01-01", "End": "2020-01-02"},
                "Granularity": "DAILY",
                "Metrics": ["BlendedCost"],
            },
        ),
    ],
    "security": [
        ServiceProbe(
            "ec2", "describe_instances",
            description="EC2 read access",
            kwargs={"MaxResults": 5},
        ),
        ServiceProbe(
            "iam", "get_account_summary",
            description="IAM read access",
            region="us-east-1",
        ),
    ],
    "sweep": [
        ServiceProbe(
            "ec2", "describe_instances",
            description="EC2 read access",
            kwargs={"MaxResults": 5},
        ),
    ],
    "dr": [
        ServiceProbe(
            "backup", "list_backup_plans",
            description="AWS Backup",
            kwargs={"MaxResults": 1},
        ),
        ServiceProbe(
            "rds", "describe_db_instances",
            description="RDS read access",
            kwargs={"MaxRecords": 20},
        ),
    ],
    "age": [
        ServiceProbe(
            "lambda", "list_functions",
            description="Lambda read access",
            kwargs={"MaxItems": 1},
        ),
    ],
    "drift": [
        ServiceProbe(
            "cloudformation", "list_stacks",
            description="CloudFormation read access",
        ),
    ],
    "tag": [
        ServiceProbe(
            "resourcegroupstaggingapi", "get_tag_keys",
            description="Tagging API",
        ),
    ],
    "pulse": [
        ServiceProbe(
            "cloudwatch", "describe_alarms",
            description="CloudWatch read access",
            kwargs={"MaxRecords": 1},
        ),
    ],
    "limit": [
        ServiceProbe(
            "service-quotas", "list_services",
            description="Service Quotas",
            kwargs={"MaxResults": 1},
        ),
    ],
    "topo": [
        ServiceProbe(
            "ec2", "describe_vpcs",
            description="VPC read access",
        ),
    ],
}


# ---------------------------------------------------------------------------
# Probing functions
# ---------------------------------------------------------------------------

_DENIED_CODES = frozenset({
    "AccessDeniedException",
    "AccessDenied",
    "UnauthorizedOperation",
    "UnauthorizedAccess",
})

_UNAVAILABLE_CODES = frozenset({
    "InvalidRegionException",
    "NoSuchResourceException",
    "UnrecognizedClientException",
    "ServiceException",
})


def probe_capability(session: Any, probe: ServiceProbe) -> CapabilityResult:
    """Make one lightweight AWS call to test service accessibility.

    Returns a CapabilityResult with status:
    - "available": call succeeded
    - "denied": AccessDenied or UnauthorizedOperation
    - "unavailable": service not available in region or endpoint error
    - "error": unexpected failure
    """
    try:
        client = session.client(probe.service, region_name=probe.region)
        getattr(client, probe.method)(**probe.kwargs)
        return CapabilityResult(
            service=probe.service,
            method=probe.method,
            status="available",
            description=probe.description,
        )
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        message = exc.response.get("Error", {}).get("Message", "")
        if code in _DENIED_CODES:
            return CapabilityResult(
                service=probe.service,
                method=probe.method,
                status="denied",
                description=probe.description,
                error_code=code,
                error_message=message[:120],
            )
        if code in _UNAVAILABLE_CODES or "not available" in message.lower():
            return CapabilityResult(
                service=probe.service,
                method=probe.method,
                status="unavailable",
                description=probe.description,
                error_code=code,
                error_message=message[:120],
            )
        # OptInRequired for Cost Explorer
        if code == "OptInRequired" or "not enabled" in message.lower():
            return CapabilityResult(
                service=probe.service,
                method=probe.method,
                status="not_configured",
                description=probe.description,
                error_code=code,
                error_message=message[:120],
            )
        return CapabilityResult(
            service=probe.service,
            method=probe.method,
            status="error",
            description=probe.description,
            error_code=code,
            error_message=message[:120],
        )
    except botocore.exceptions.EndpointConnectionError:
        return CapabilityResult(
            service=probe.service,
            method=probe.method,
            status="unavailable",
            description=probe.description,
            error_message="Endpoint connection failed",
        )
    except botocore.exceptions.NoCredentialsError:
        return CapabilityResult(
            service=probe.service,
            method=probe.method,
            status="denied",
            description=probe.description,
            error_message="No credentials available",
        )
    except Exception as exc:
        return CapabilityResult(
            service=probe.service,
            method=probe.method,
            status="error",
            description=probe.description,
            error_message=str(exc)[:120],
        )


def assess_pack_readiness(session: Any, pack: str, cache: dict[tuple[str, str, str, str], CapabilityResult] | None = None) -> PackReadinessResult:
    """Probe all capabilities for a pack and determine overall readiness.

    Returns:
    - "ready": all probes succeeded
    - "partial": at least one succeeded, at least one denied/unavailable
    - "unavailable": all probes denied or unavailable
    - "not_checked": pack has no defined probes
    """
    probes = PACK_PROBES.get(pack)
    if not probes:
        return PackReadinessResult(pack=pack, readiness="not_checked")

    results: list[CapabilityResult] = []
    for probe in probes:
        key = (probe.service, probe.method, probe.region, repr(sorted(probe.kwargs.items())))
        if cache is not None and key in cache:
            results.append(cache[key])
            continue
        result = probe_capability(session, probe)
        if cache is not None:
            cache[key] = result
        results.append(result)
        results.append(probe_capability(session, probe))

    available_count = sum(1 for r in results if r.status == "available")
    total = len(results)

    if available_count == total:
        readiness: PackReadiness = "ready"
        reason = None
    elif available_count > 0:
        readiness = "partial"
        denied = [r for r in results if r.status in ("denied", "unavailable")]
        reason = "; ".join(
            f"{r.service}:{r.method} {r.status}" for r in denied
        )
    else:
        readiness = "unavailable"
        reason = "; ".join(
            f"{r.service}:{r.method} {r.status}" for r in results
        )

    return PackReadinessResult(
        pack=pack,
        readiness=readiness,
        capabilities=results,
        reason=reason,
    )


def assess_all_packs(session: Any) -> dict[str, PackReadinessResult]:
    """Assess all packs with one probe per unique capability."""
    cache: dict[tuple[str, str, str, str], CapabilityResult] = {}
    return {pack: assess_pack_readiness(session, pack, cache) for pack in PACK_PROBES}

def mask_account_id(account_id: str | None) -> str | None:
    """Mask an AWS account ID for display: show first 5 + *** + last 4."""
    if not account_id or len(account_id) < 10:
        return account_id
    return f"{account_id[:5]}***{account_id[-4:]}"
