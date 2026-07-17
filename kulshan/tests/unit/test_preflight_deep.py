"""Acceptance tests for kulshan preflight --deep and --json."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from click.testing import CliRunner

from kulshan.capabilities import (
    CapabilityResult,
    PackReadinessResult,
    ServiceProbe,
    assess_pack_readiness,
    probe_capability,
)
from kulshan.cli import main
from kulshan.preflight import (
    PreflightDeepResult,
    deep_result_to_json,
    mask_account_id,
    run_preflight_basic_json,
    run_preflight_deep,
)


def _access_denied(operation: str = "Op") -> ClientError:
    return ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
        operation,
    )


def _unauthorized_operation(operation: str = "Op") -> ClientError:
    return ClientError(
        {"Error": {"Code": "UnauthorizedOperation", "Message": "Not authorized"}},
        operation,
    )


def _mock_session_all_available():
    """Mock session where all API calls succeed."""
    session = MagicMock()

    def make_client(service, **kwargs):
        client = MagicMock()
        if service == "sts":
            client.get_caller_identity.return_value = {
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:user/admin",
                "UserId": "AIDEXAMPLE",
            }
        elif service == "ce":
            client.get_cost_and_usage.return_value = {"ResultsByTime": []}
        elif service == "ec2":
            client.describe_instances.return_value = {"Reservations": []}
            client.describe_vpcs.return_value = {"Vpcs": []}
        elif service == "iam":
            client.get_account_summary.return_value = {"SummaryMap": {}}
        elif service == "backup":
            client.list_backup_plans.return_value = {"BackupPlansList": []}
        elif service == "rds":
            client.describe_db_instances.return_value = {"DBInstances": []}
        elif service == "lambda":
            client.list_functions.return_value = {"Functions": []}
        elif service == "cloudformation":
            client.list_stacks.return_value = {"StackSummaries": []}
        elif service == "resourcegroupstaggingapi":
            client.get_tag_keys.return_value = {"TagKeys": []}
        elif service == "cloudwatch":
            client.describe_alarms.return_value = {"MetricAlarms": []}
        elif service == "service-quotas":
            client.list_services.return_value = {"Services": []}
        elif service == "organizations":
            client.describe_organization.return_value = {"Organization": {}}
        return client

    session.client.side_effect = make_client
    session.get_credentials.return_value = MagicMock()
    return session


def _mock_session_all_denied():
    """Mock session where STS works but all service calls are denied."""
    session = MagicMock()

    def make_client(service, **kwargs):
        client = MagicMock()
        if service == "sts":
            client.get_caller_identity.return_value = {
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:user/admin",
                "UserId": "AIDEXAMPLE",
            }
        else:
            # All other service calls raise AccessDenied
            denied = _access_denied(service)
            client.get_cost_and_usage.side_effect = denied
            client.describe_instances.side_effect = denied
            client.describe_vpcs.side_effect = denied
            client.get_account_summary.side_effect = denied
            client.list_backup_plans.side_effect = denied
            client.describe_db_instances.side_effect = denied
            client.list_functions.side_effect = denied
            client.list_stacks.side_effect = denied
            client.get_tag_keys.side_effect = denied
            client.describe_alarms.side_effect = denied
            client.list_services.side_effect = denied
            client.describe_organization.side_effect = denied
        return client

    session.client.side_effect = make_client
    session.get_credentials.return_value = MagicMock()
    return session


def _mock_session_auth_failure():
    """Mock session where STS get_caller_identity fails."""
    session = MagicMock()

    def make_client(service, **kwargs):
        client = MagicMock()
        if service == "sts":
            client.get_caller_identity.side_effect = ClientError(
                {"Error": {"Code": "ExpiredToken", "Message": "Token expired"}},
                "GetCallerIdentity",
            )
        return client

    session.client.side_effect = make_client
    session.get_credentials.return_value = MagicMock()
    return session


# ---------------------------------------------------------------------------
# Capability probe tests
# ---------------------------------------------------------------------------


class TestProbeCapability:
    def test_available(self):
        session = _mock_session_all_available()
        probe = ServiceProbe("ec2", "describe_instances", kwargs={"MaxResults": 5})
        result = probe_capability(session, probe)
        assert result.status == "available"

    def test_denied(self):
        session = MagicMock()
        client = MagicMock()
        client.describe_instances.side_effect = _access_denied()
        session.client.return_value = client
        probe = ServiceProbe("ec2", "describe_instances", kwargs={"MaxResults": 5})
        result = probe_capability(session, probe)
        assert result.status == "denied"

    def test_unauthorized_operation(self):
        session = MagicMock()
        client = MagicMock()
        client.describe_instances.side_effect = _unauthorized_operation()
        session.client.return_value = client
        probe = ServiceProbe("ec2", "describe_instances", kwargs={"MaxResults": 5})
        result = probe_capability(session, probe)
        assert result.status == "denied"

    def test_unavailable_endpoint(self):
        session = MagicMock()
        client = MagicMock()
        client.describe_instances.side_effect = EndpointConnectionError(endpoint_url="https://ec2.fake.aws")
        session.client.return_value = client
        probe = ServiceProbe("ec2", "describe_instances", kwargs={"MaxResults": 5})
        result = probe_capability(session, probe)
        assert result.status == "unavailable"

    def test_unexpected_error(self):
        session = MagicMock()
        client = MagicMock()
        client.describe_instances.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "Something broke"}},
            "DescribeInstances",
        )
        session.client.return_value = client
        probe = ServiceProbe("ec2", "describe_instances", kwargs={"MaxResults": 5})
        result = probe_capability(session, probe)
        assert result.status == "error"


# ---------------------------------------------------------------------------
# Pack readiness tests
# ---------------------------------------------------------------------------


class TestPackReadiness:
    def test_all_packs_ready(self):
        session = _mock_session_all_available()
        result = assess_pack_readiness(session, "cost")
        assert result.readiness == "ready"

    def test_all_denied(self):
        session = _mock_session_all_denied()
        result = assess_pack_readiness(session, "cost")
        assert result.readiness == "unavailable"

    def test_mixed_permissions(self):
        """security pack: EC2 available, IAM denied -> partial."""
        session = MagicMock()

        def make_client(service, **kwargs):
            client = MagicMock()
            if service == "ec2":
                client.describe_instances.return_value = {"Reservations": []}
            elif service == "iam":
                client.get_account_summary.side_effect = _access_denied()
            return client

        session.client.side_effect = make_client
        result = assess_pack_readiness(session, "security")
        assert result.readiness == "partial"
        assert result.reason is not None
        assert "denied" in result.reason


# ---------------------------------------------------------------------------
# Deep preflight tests
# ---------------------------------------------------------------------------


class TestDeepPreflight:
    def test_all_available(self):
        session = _mock_session_all_available()
        result = run_preflight_deep(session)
        assert result.passed is True
        assert result.identity["account_id"] == "123456789012"
        assert all(p.readiness == "ready" for p in result.packs.values())

    def test_auth_failure(self):
        session = _mock_session_auth_failure()
        result = run_preflight_deep(session)
        assert result.passed is False
        assert len(result.errors) > 0

    def test_all_denied(self):
        session = _mock_session_all_denied()
        result = run_preflight_deep(session)
        # STS passes, but all packs are unavailable
        assert result.passed is True
        assert all(p.readiness == "unavailable" for p in result.packs.values())

    def test_organizations_unavailable_still_passes(self):
        session = _mock_session_all_available()
        # Override organizations to deny
        original_side_effect = session.client.side_effect

        def modified_client(service, **kwargs):
            client = original_side_effect(service, **kwargs)
            if service == "organizations":
                client.describe_organization.side_effect = _access_denied()
            return client

        session.client.side_effect = modified_client
        result = run_preflight_deep(session)
        assert result.passed is True


# ---------------------------------------------------------------------------
# JSON output tests
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def test_json_is_valid(self):
        session = _mock_session_all_available()
        result = run_preflight_deep(session)
        output = deep_result_to_json(result)
        # Should be serializable
        json_str = json.dumps(output)
        parsed = json.loads(json_str)
        assert "identity" in parsed
        assert "packs" in parsed
        assert "permissions" in parsed

    def test_json_masks_account_ids(self):
        session = _mock_session_all_available()
        result = run_preflight_deep(session)
        output = deep_result_to_json(result)
        account = output["identity"]["account_id"]
        assert "***" in account
        assert account == "12345***9012"

    def test_basic_json(self):
        session = _mock_session_all_available()
        output = run_preflight_basic_json(session)
        json_str = json.dumps(output)
        parsed = json.loads(json_str)
        assert parsed["identity"]["account_id"] == "12345***9012"
        assert parsed["permissions"]["sts"] == "available"


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestCLIPreflight:
    def test_json_flag_outputs_valid_json(self):
        runner = CliRunner()
        with patch("kulshan.session.create_session", return_value=_mock_session_all_available()):
            result = runner.invoke(main, ["preflight", "--json"])
        assert result.exit_code == 0, f"Failed: {result.output}"
        parsed = json.loads(result.output)
        assert "identity" in parsed

    def test_json_no_ansi_codes(self):
        runner = CliRunner()
        with patch("kulshan.session.create_session", return_value=_mock_session_all_available()):
            result = runner.invoke(main, ["preflight", "--json"])
        # No ANSI escape codes
        assert "\x1b[" not in result.output
        assert "\x1b" not in result.output

    def test_deep_json_includes_pack_readiness(self):
        runner = CliRunner()
        with patch("kulshan.session.create_session", return_value=_mock_session_all_available()):
            result = runner.invoke(main, ["preflight", "--deep", "--json"])
        assert result.exit_code == 0, f"Failed: {result.output}"
        parsed = json.loads(result.output)
        assert "packs" in parsed
        assert "cost" in parsed["packs"]
        assert parsed["packs"]["cost"]["readiness"] == "ready"

    def test_basic_mode_no_pack_probes(self):
        """Without --deep, no per-pack readiness in JSON output."""
        runner = CliRunner()
        with patch("kulshan.session.create_session", return_value=_mock_session_all_available()):
            result = runner.invoke(main, ["preflight", "--json"])
        parsed = json.loads(result.output)
        # Basic mode has empty packs
        assert parsed["packs"] == {}


# ---------------------------------------------------------------------------
# Masking tests
# ---------------------------------------------------------------------------


class TestMasking:
    def test_mask_12_digit_account(self):
        assert mask_account_id("123456789012") == "12345***9012"

    def test_mask_short_string(self):
        assert mask_account_id("12345") == "12345"

    def test_mask_none(self):
        assert mask_account_id(None) is None

    def test_mask_empty(self):
        assert mask_account_id("") == ""
