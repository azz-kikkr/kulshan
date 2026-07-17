"""Regression tests: scanners degrade gracefully when IAM permissions are denied.

Each test mocks the relevant boto3 client method to simulate AccessDeniedException
and verifies the scanner does NOT crash, returning a valid (possibly empty) result.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from botocore.exceptions import ClientError


def _access_denied_error(operation: str = "Operation") -> ClientError:
    """Create a botocore ClientError simulating AccessDeniedException."""
    return ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
        operation,
    )


def _mock_session(region: str = "us-east-1"):
    """Create a mock boto3 session."""
    session = MagicMock()
    session.region_name = region
    return session


# ─── IAM Scanner: get_account_authorization_details denied ─────────────────────


class TestIAMScannerDenied:
    """IAMScanner degrades gracefully when IAM actions are denied."""

    def test_get_account_authorization_details_denied(self):
        """Scanner handles access denied on get_account_authorization_details."""
        from kulshan.checks.security.scanner.iam import IAMScanner

        session = _mock_session()
        iam_client = MagicMock()
        iam_client.generate_credential_report.side_effect = _access_denied_error()
        iam_client.get_credential_report.side_effect = _access_denied_error()
        iam_client.get_account_password_policy.side_effect = _access_denied_error()
        iam_client.list_virtual_mfa_devices.return_value = {"VirtualMFADevices": []}
        session.client.return_value = iam_client

        scanner = IAMScanner(session=session, regions=["us-east-1"])

        with patch(
            "kulshan.checks.security.scanner.iam.safe_api_call",
            return_value=(None, "Access denied: get_account_authorization_details"),
        ):
            result = scanner.scan()

        assert result is not None
        assert hasattr(result, "findings")
        assert hasattr(result, "errors")

    def test_generate_credential_report_denied(self):
        """Scanner handles access denied on generate_credential_report / get_credential_report."""
        from kulshan.checks.security.scanner.iam import IAMScanner

        session = _mock_session()
        iam_client = MagicMock()
        iam_client.generate_credential_report.side_effect = _access_denied_error()
        session.client.return_value = iam_client

        scanner = IAMScanner(session=session, regions=["us-east-1"])

        # Mock safe_api_call to return empty auth details but succeed partially
        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "get_account_authorization_details":
                return {"UserDetailList": [], "RoleDetailList": [],
                        "GroupDetailList": [], "Policies": []}, None
            if method == "get_account_summary":
                return {"SummaryMap": {}}, None
            if method == "get_credential_report":
                return None, "Access denied: get_credential_report"
            if method == "get_account_password_policy":
                return None, "Access denied: get_account_password_policy"
            if method == "list_virtual_mfa_devices":
                return {"VirtualMFADevices": []}, None
            return None, f"Access denied: {method}"

        with patch(
            "kulshan.checks.security.scanner.iam.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            result = scanner.scan()

        assert result is not None
        assert hasattr(result, "findings")


# ─── Encryption Scanner: list_secrets denied ──────────────────────────────────


class TestEncryptionScannerDenied:
    """EncryptionScanner degrades gracefully when secretsmanager:ListSecrets is denied."""

    def test_list_secrets_denied(self):
        """Scanner handles access denied on list_secrets."""
        from kulshan.checks.security.scanner.encryption import EncryptionScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        scanner = EncryptionScanner(session=session, regions=["us-east-1"])

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "list_secrets":
                return None, "Access denied: list_secrets"
            if method == "list_keys":
                return {"Keys": []}, None
            if method == "list_certificates":
                return {"CertificateSummaryList": []}, None
            return None, f"Access denied: {method}"

        with patch(
            "kulshan.checks.security.scanner.encryption.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            result = scanner.scan()

        assert result is not None
        assert hasattr(result, "findings")


# ─── Data Scanner: describe_snapshot_attribute denied ─────────────────────────


class TestDataScannerDenied:
    """DataScanner degrades gracefully when snapshot attribute actions are denied."""

    def test_describe_snapshot_attribute_denied(self):
        """Scanner handles access denied on ec2:DescribeSnapshotAttribute."""
        from kulshan.checks.security.scanner.data import DataScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "describe_snapshot_attribute":
                return None, "Access denied: describe_snapshot_attribute"
            if method == "list_buckets":
                return {"Buckets": []}, None
            if method == "describe_db_instances":
                return {"DBInstances": []}, None
            if method == "describe_db_snapshots":
                return {"DBSnapshots": []}, None
            if method == "describe_volumes":
                return {"Volumes": []}, None
            if method == "describe_snapshots":
                return {"Snapshots": [{"SnapshotId": "snap-12345"}]}, None
            return None, f"Access denied: {method}"

        with patch(
            "kulshan.checks.security.scanner.data.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            result = scanner = DataScanner(session=session, regions=["us-east-1"])
            result = scanner.scan()

        assert result is not None
        assert hasattr(result, "findings")

    def test_describe_db_snapshot_attributes_denied(self):
        """Scanner handles access denied on rds:DescribeDBSnapshotAttributes."""
        from kulshan.checks.security.scanner.data import DataScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "describe_db_snapshot_attributes":
                return None, "Access denied: describe_db_snapshot_attributes"
            if method == "list_buckets":
                return {"Buckets": []}, None
            if method == "describe_db_instances":
                return {"DBInstances": []}, None
            if method == "describe_db_snapshots":
                return {"DBSnapshots": [{"DBSnapshotIdentifier": "mysnap"}]}, None
            if method == "describe_volumes":
                return {"Volumes": []}, None
            if method == "describe_snapshots":
                return {"Snapshots": []}, None
            return None, f"Access denied: {method}"

        with patch(
            "kulshan.checks.security.scanner.data.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            scanner = DataScanner(session=session, regions=["us-east-1"])
            result = scanner.scan()

        assert result is not None
        assert hasattr(result, "findings")


# ─── Logging Scanner: describe_configuration_recorder_status denied ───────────


class TestLoggingScannerDenied:
    """LoggingScanner degrades gracefully when config actions are denied."""

    def test_describe_configuration_recorder_status_denied(self):
        """Scanner handles access denied on config:DescribeConfigurationRecorderStatus."""
        from kulshan.checks.security.scanner.logging_monitor import LoggingScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "describe_configuration_recorder_status":
                return None, "Access denied: describe_configuration_recorder_status"
            if method == "describe_configuration_recorders":
                return {"ConfigurationRecorders": [{"name": "default"}]}, None
            if method == "describe_trails":
                return {"trailList": []}, None
            if method == "list_detectors":
                return {"DetectorIds": []}, None
            if method == "list_analyzers":
                return {"analyzers": []}, None
            return None, f"Access denied: {method}"

        with patch(
            "kulshan.checks.security.scanner.logging_monitor.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            scanner = LoggingScanner(session=session, regions=["us-east-1"])
            result = scanner.scan()

        assert result is not None
        assert hasattr(result, "findings")


# ─── Preflight: describe_organization denied ──────────────────────────────────


class TestPreflightDenied:
    """Preflight passes even when organizations:DescribeOrganization is denied."""

    def test_describe_organization_denied(self):
        """Preflight completes when describe_organization raises AccessDeniedException."""
        from kulshan.preflight import run_preflight
        from rich.console import Console
        import io

        session = _mock_session()

        # Mock credentials
        creds_mock = MagicMock()
        session.get_credentials.return_value = creds_mock

        # Build client mocks
        def make_client(service, **kwargs):
            client = MagicMock()
            if service == "sts":
                client.get_caller_identity.return_value = {
                    "Account": "123456789012",
                    "Arn": "arn:aws:iam::123456789012:user/test",
                    "UserId": "AIDEXAMPLE",
                }
            elif service == "ce":
                client.get_cost_and_usage.return_value = {"ResultsByTime": []}
            elif service == "ec2":
                client.describe_instances.return_value = {"Reservations": []}
            elif service == "organizations":
                client.describe_organization.side_effect = _access_denied_error(
                    "DescribeOrganization"
                )
            return client

        session.client.side_effect = make_client

        console = Console(file=io.StringIO(), force_terminal=True)
        passed, warnings = run_preflight(session, console=console)

        # Preflight should still pass (orgs is informational, not critical)
        assert passed is True


# ─── CUR Discovery: list_exports denied ───────────────────────────────────────


class TestCURDiscoveryDenied:
    """CUR discovery returns empty when bcm-data-exports:ListExports is denied."""

    def test_list_exports_denied(self):
        """Discovery returns empty list when ListExports is denied."""
        from kulshan.cur.discovery import discover_cur_exports

        session = _mock_session()
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.side_effect = _access_denied_error("ListExports")
        client.get_paginator.return_value = paginator
        session.client.return_value = client

        result = discover_cur_exports(session)

        assert result == []
