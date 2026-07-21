"""Scan storage resilience: S3 versioning, replication, lifecycle."""

from typing import Dict, List, Tuple
from ..utils.aws import safe_api_call


def _is_access_denied(err_str: str) -> bool:
    """Return True if the error string indicates an IAM authorization failure."""
    if not err_str:
        return False
    lower = err_str.lower()
    return any(phrase in lower for phrase in (
        "access denied", "accessdenied", "unauthorizedaccess",
        "unauthorizedoperation",
    ))


def scan_storage(session, regions, progress=None, task_id=None) -> Tuple[Dict, List[str]]:
    """Audit S3 storage resilience."""
    findings = []
    errors = []
    could_not_check = []
    stats = {
        "buckets": [],
        "no_versioning": [],
        "no_replication": [],
        "no_lifecycle": [],
        "cross_region_replication": 0,
    }

    s3 = session.client("s3", region_name="us-east-1")
    resp, err = safe_api_call(s3, "list_buckets")
    if err:
        errors.append(f"S3: {err}")
        if _is_access_denied(err):
            could_not_check.append({
                "result_state": "could_not_check",
                "resource": "account-level",
                "api_operation": "s3:ListBuckets",
                "iam_action_required": "s3:ListAllMyBuckets",
                "error_category": "access_denied",
                "explanation": "Cannot list S3 buckets. Storage resilience was not evaluated.",
            })
        return {"stats": stats, "findings": findings, "could_not_check": could_not_check}, errors

    buckets = (resp or {}).get("Buckets", [])

    for bucket in buckets:
        name = bucket["Name"]
        info = {"name": name, "versioning": False, "replication": False, "lifecycle": False}

        # Versioning
        ver_resp, ver_err = safe_api_call(s3, "get_bucket_versioning", Bucket=name)
        if ver_err and _is_access_denied(ver_err):
            could_not_check.append({
                "result_state": "could_not_check",
                "resource": name,
                "api_operation": "s3:GetBucketVersioning",
                "iam_action_required": "s3:GetBucketVersioning",
                "error_category": "access_denied",
                "explanation": f"Could not check versioning for bucket '{name}'.",
            })
            info["versioning"] = None  # Unknown, not False
        elif not ver_err and ver_resp:
            status = ver_resp.get("Status", "")
            info["versioning"] = status == "Enabled"
        if info["versioning"] is False:
            stats["no_versioning"].append(name)

        # Replication
        rep_resp, rep_err = safe_api_call(s3, "get_bucket_replication", Bucket=name)
        if rep_err and _is_access_denied(rep_err):
            could_not_check.append({
                "result_state": "could_not_check",
                "resource": name,
                "api_operation": "s3:GetBucketReplication",
                "iam_action_required": "s3:GetReplicationConfiguration",
                "error_category": "access_denied",
                "explanation": f"Could not check replication for bucket '{name}'.",
            })
            info["replication"] = None  # Unknown
        elif not rep_err and rep_resp:
            rules = rep_resp.get("ReplicationConfiguration", {}).get("Rules", [])
            if rules:
                info["replication"] = True
                stats["cross_region_replication"] += 1
        elif rep_err and "ReplicationConfigurationNotFoundError" in str(rep_err):
            # Genuinely no replication - this is a valid "not configured" result
            info["replication"] = False
        if info["replication"] is False:
            stats["no_replication"].append(name)

        # Lifecycle
        lc_resp, lc_err = safe_api_call(s3, "get_bucket_lifecycle_configuration", Bucket=name)
        if lc_err and _is_access_denied(lc_err):
            could_not_check.append({
                "result_state": "could_not_check",
                "resource": name,
                "api_operation": "s3:GetBucketLifecycleConfiguration",
                "iam_action_required": "s3:GetLifecycleConfiguration",
                "error_category": "access_denied",
                "explanation": f"Could not check lifecycle for bucket '{name}'.",
            })
            info["lifecycle"] = None  # Unknown
        elif not lc_err and lc_resp:
            rules = lc_resp.get("Rules", [])
            if rules:
                info["lifecycle"] = True
        if info["lifecycle"] is False:
            stats["no_lifecycle"].append(name)

        stats["buckets"].append(info)

    # Generate findings
    total = len(buckets)
    if total > 0:
        no_ver_count = len(stats["no_versioning"])
        if no_ver_count > 0:
            pct = no_ver_count / total * 100
            findings.append({
                "category": "storage",
                "severity": "high" if pct > 50 else "medium",
                "title": f"{no_ver_count}/{total} S3 buckets have no versioning ({pct:.0f}%)",
                "detail": "Without versioning, accidental deletes or overwrites are permanent.",
                "recommendation": "Enable versioning on all buckets containing important data.",
            })

        if stats["cross_region_replication"] == 0:
            findings.append({
                "category": "storage",
                "severity": "medium",
                "title": "No S3 cross-region replication configured",
                "detail": "All bucket data exists in a single region. A regional outage could cause data unavailability.",
                "recommendation": "Enable cross-region replication for critical buckets.",
            })

    if could_not_check:
        findings.append({
            "category": "storage",
            "severity": "info",
            "title": f"{len(could_not_check)} S3 storage check(s) could not be evaluated",
            "detail": "One or more S3 resilience checks failed due to insufficient permissions. These resources were NOT confirmed compliant.",
            "recommendation": "Grant the required IAM permissions and re-run the scan.",
            "could_not_check": could_not_check,
        })

    if progress and task_id:
        progress.advance(task_id)

    return {"stats": stats, "findings": findings, "could_not_check": could_not_check}, errors
