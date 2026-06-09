"""PII redaction for exported reports.

All file-based exports (HTML, JSON, CSV, PDF, Excel, PPTX, Markdown) apply
redaction by default. Terminal (stdout) output is never redacted.

The ``--show-pii`` CLI flag disables redaction when explicitly passed.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_AWS_ACCOUNT_RE = re.compile(r"\b(\d{12})\b")
_ARN_RE = re.compile(r"arn:aws[^:]*:[^:]+:[^:]*:(\d{12}):")
_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")
_IP_RE = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
_ACCESS_KEY_RE = re.compile(r"\bAKIA[A-Z0-9]{16}\b")
_SECRET_KEY_RE = re.compile(r"\b[A-Za-z0-9/+=]{40}\b")  # AWS secret keys are 40 chars base64


# ---------------------------------------------------------------------------
# Core redaction functions
# ---------------------------------------------------------------------------


def redact_account_id(account_id: str | None) -> str:
    """Redact AWS account ID to show only last 4 digits.

    '123456789012' -> 'XXXX-XXXX-9012'
    """
    if not account_id:
        return "XXXX-XXXX-XXXX"
    s = str(account_id).strip()
    if len(s) >= 4:
        return f"XXXX-XXXX-{s[-4:]}"
    return "XXXX-XXXX-XXXX"


def redact_arn(arn: str | None) -> str:
    """Redact account ID portion of an ARN.

    'arn:aws:s3:::my-bucket' -> unchanged (no account)
    'arn:aws:iam::123456789012:user/admin' -> 'arn:aws:iam::XXXX9012:user/****'
    """
    if not arn:
        return ""
    s = str(arn)
    # Mask account ID in ARN
    s = _ARN_RE.sub(
        lambda m: (
            f"arn:aws:{_get_arn_service(m.group(0))}:{_get_arn_region(m.group(0))}:"
            f"XXXX{m.group(1)[-4:]}:"
        ),
        s,
    )
    # Mask resource names after user/, role/, instance/
    s = re.sub(
        r"(user|role|instance|function|bucket|table|cluster)/([^/\s]+)",
        _mask_resource_name,
        s,
    )
    return s


def redact_email(email: str | None) -> str:
    """Redact email address preserving first char and domain TLD.

    'yuvdeep@example.com' -> 'y***@***.com'
    """
    if not email:
        return ""
    match = _EMAIL_RE.search(str(email))
    if not match:
        return str(email)
    local = match.group(1)
    domain = match.group(2)
    tld = domain.split(".")[-1]
    masked_local = local[0] + "***" if local else "***"
    return f"{masked_local}@***.{tld}"


def redact_ip(ip: str | None) -> str:
    """Redact last two octets of an IP address.

    '10.0.45.12' -> '10.0.*.*'
    """
    if not ip:
        return ""
    match = _IP_RE.search(str(ip))
    if not match:
        return str(ip)
    return f"{match.group(1)}.{match.group(2)}.*.*"


def redact_bucket_name(name: str | None) -> str:
    """Redact S3 bucket name showing first 4 chars.

    'my-production-data-bucket' -> 'my-p****'
    """
    if not name:
        return ""
    s = str(name)
    visible = min(4, max(1, len(s) // 3))
    return s[:visible] + "****"


def redact_hostname(hostname: str | None) -> str:
    """Mask identifying hostname labels while preserving a useful suffix."""
    if not hostname:
        return ""

    value = str(hostname)
    if _IP_RE.fullmatch(value):
        return redact_ip(value)

    parsed = urlsplit(value)
    if parsed.scheme and parsed.hostname:
        masked_host = _mask_hostname_labels(parsed.hostname)
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit(
            (parsed.scheme, f"{masked_host}{port}", parsed.path, parsed.query, parsed.fragment)
        )

    return _mask_hostname_labels(value)


def redact_text(text: str | None) -> str:
    """Scan free text for inline account IDs, emails, and AWS keys, redact them.

    Handles finding titles, descriptions, and recommended_action fields.
    """
    if not text:
        return ""
    s = str(text)
    # Replace 12-digit account IDs
    s = _AWS_ACCOUNT_RE.sub(lambda m: f"XXXX{m.group(1)[-4:]}", s)
    # Replace AWS access keys
    s = _ACCESS_KEY_RE.sub("AKIA****************", s)
    # Replace potential secret keys (40-char base64)
    s = _SECRET_KEY_RE.sub("[REDACTED_KEY]", s)
    # Replace emails
    s = _EMAIL_RE.sub(lambda m: redact_email(m.group(0)), s)
    # Replace IPv4 addresses while retaining broad network context
    s = _IP_RE.sub(lambda m: redact_ip(m.group(0)), s)
    return s


def redact_filename(filename: str) -> str:
    """Strip account IDs from default report filenames.

    'kulshan-report-123456789012.html' -> 'kulshan-report-XXXX9012.html'
    """
    return _AWS_ACCOUNT_RE.sub(lambda m: f"XXXX{m.group(1)[-4:]}", filename)


# ---------------------------------------------------------------------------
# Deep payload redaction (for JSON export)
# ---------------------------------------------------------------------------

# Fields that contain account IDs directly
_ACCOUNT_FIELDS = {"account_id", "account", "accountid"}

# Fields that contain free text with possible embedded PII
_TEXT_FIELDS = {"title", "description", "recommended_action", "why_it_matters", "remediation_text"}

# Fields that contain ARNs
_ARN_FIELDS = {"resource_arn", "arn"}

# Fields that contain email
_EMAIL_FIELDS = {"email", "owner_hint"}

# Fields containing network or storage identifiers
_IP_FIELDS = {"ip", "ip_address", "public_ip", "private_ip", "source_ip", "destination_ip"}
_BUCKET_FIELDS = {"bucket", "bucket_name", "s3_bucket", "bucketname"}
_HOST_FIELDS = {
    "host",
    "hostname",
    "endpoint",
    "endpoint_url",
    "domain",
    "domain_name",
    "dns_name",
}


def redact_payload(payload: Any, show_pii: bool = False) -> Any:
    """Deep-walk a JSON-serializable structure and redact PII fields.

    Returns a new structure (does not mutate input).
    When show_pii=True, returns the payload unchanged.
    """
    if show_pii:
        return payload
    return _redact_value(deepcopy(payload), "")


def _redact_value(obj: Any, key: str) -> Any:
    """Recursively redact a value based on its key context."""
    if obj is None:
        # For known PII fields, redact None as placeholder
        key_lower = key.lower()
        if key_lower in _ACCOUNT_FIELDS or key_lower == "account_id":
            return redact_account_id(None)
        if key_lower in _ARN_FIELDS:
            return None
        return None
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            result[k] = _redact_value(v, k)
        # Add redaction marker at top level
        if "kulshan_version" in obj and "redacted" not in obj:
            result["redacted"] = True
        return result
    elif isinstance(obj, list):
        return [_redact_value(item, key) for item in obj]
    elif isinstance(obj, str):
        return _redact_string(obj, key)
    return obj


def _redact_string(value: str, key: str) -> str:
    """Redact a string value based on its field name context."""
    key_lower = key.lower()

    if key_lower in _ACCOUNT_FIELDS or key_lower == "account_id":
        return redact_account_id(value)
    if key_lower in _ARN_FIELDS:
        return redact_arn(value)
    if key_lower in _EMAIL_FIELDS:
        return redact_email(value)
    if key_lower in _IP_FIELDS:
        return redact_ip(value)
    if key_lower in _BUCKET_FIELDS:
        return redact_bucket_name(value)
    if key_lower in _HOST_FIELDS:
        return redact_hostname(value)
    if key_lower in _TEXT_FIELDS:
        return redact_text(value)

    # For any other string field, scan common inline sensitive values.
    if _AWS_ACCOUNT_RE.search(value) or _EMAIL_RE.search(value) or _ACCESS_KEY_RE.search(value):
        return redact_text(value)

    return value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_arn_service(arn: str) -> str:
    """Extract service from ARN string."""
    parts = arn.split(":")
    return parts[2] if len(parts) > 2 else ""


def _get_arn_region(arn: str) -> str:
    """Extract region from ARN string."""
    parts = arn.split(":")
    return parts[3] if len(parts) > 3 else ""


def _mask_resource_name(match: re.Match) -> str:
    """Mask resource name portion: 'user/admin-yuvdeep' -> 'user/****'."""
    prefix = match.group(1)
    name = match.group(2)
    if len(name) <= 3:
        return f"{prefix}/****"
    return f"{prefix}/{name[:2]}****"


def _mask_hostname_labels(hostname: str) -> str:
    """Mask hostname labels but retain the final suffix for troubleshooting context."""
    labels = hostname.rstrip(".").split(".")
    if len(labels) == 1:
        return _mask_identifier(labels[0])

    suffix_count = 2 if len(labels) > 2 else 1
    masked = [_mask_identifier(label) for label in labels[:-suffix_count]]
    return ".".join(masked + labels[-suffix_count:])


def _mask_identifier(value: str) -> str:
    if not value:
        return "****"
    visible = min(2, len(value))
    return value[:visible] + "****"
