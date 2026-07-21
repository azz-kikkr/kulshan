"""Regression tests for trust and distribution integrity.

Tests in this file guard against:
1. Invalid IAM action names in policies
2. False-clean behavior when API calls fail
3. Policy consistency between repository and website assets
4. Correct scanner behavior under AccessDenied conditions
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
IAM_DIR = REPO_ROOT / "kulshan" / "iam"
COMPOSED_PATH = IAM_DIR / "kulshan-readonly.json"
PER_CHECK_DIR = IAM_DIR / "per-check"
REGISTRY_PATH = IAM_DIR / "registry.json"
HASH_PATH = IAM_DIR / "policy-hash.txt"

# The workspace root for the production website (if present during CI,
# this test is skipped; during local dev the path should exist)
WEBSITE_ROOT = REPO_ROOT.parent / "missionfinops-clean"
WEBSITE_POLICY_PATH = WEBSITE_ROOT / "kulshan" / "iam" / "kulshan-readonly.json"


def _actions(policy: dict) -> set[str]:
    """Extract all IAM actions from a policy document."""
    actions: set[str] = set()
    for stmt in policy.get("Statement", []):
        a = stmt.get("Action", [])
        if isinstance(a, str):
            actions.add(a)
        else:
            actions.update(a)
    return actions


def _load_composed() -> dict:
    return json.loads(COMPOSED_PATH.read_text(encoding="utf-8"))


def _mock_session(region: str = "us-east-1"):
    session = MagicMock()
    session.region_name = region
    return session


def _access_denied_error(method: str = "Operation"):
    from botocore.exceptions import ClientError
    return ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
        method,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. IAM ACTION NAME CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════════════


CORRECT_S3_ACTIONS = {
    "s3:GetEncryptionConfiguration",
    "s3:GetLifecycleConfiguration",
    "s3:GetReplicationConfiguration",
}

INVALID_S3_ACTIONS = {
    "s3:GetBucketEncryption",
    "s3:GetBucketLifecycleConfiguration",
    "s3:GetBucketReplication",
}


class TestIAMActionCorrectness:
    """Composed and per-check policies use correct IAM action names."""

    def test_composed_policy_contains_correct_s3_actions(self):
        """The composed policy must contain the three correct S3 actions."""
        actions = _actions(_load_composed())
        for correct_action in CORRECT_S3_ACTIONS:
            assert correct_action in actions, (
                f"Missing correct action: {correct_action}"
            )

    def test_composed_policy_contains_no_invalid_s3_actions(self):
        """The composed policy must NOT contain the three invalid S3 action names."""
        actions = _actions(_load_composed())
        for invalid_action in INVALID_S3_ACTIONS:
            assert invalid_action not in actions, (
                f"Invalid action still present: {invalid_action}"
            )

    @pytest.mark.parametrize("policy_file", sorted(PER_CHECK_DIR.glob("*.json")))
    def test_per_check_policies_contain_no_invalid_s3_actions(self, policy_file: Path):
        """No per-check policy should contain invalid S3 action names."""
        policy = json.loads(policy_file.read_text(encoding="utf-8"))
        actions = _actions(policy)
        bad = actions & INVALID_S3_ACTIONS
        assert not bad, (
            f"{policy_file.name} contains invalid actions: {sorted(bad)}"
        )

    def test_registry_contains_correct_s3_actions(self):
        """The IAM registry must reference the correct action names."""
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        registry_actions = {e["iam_action"] for e in registry["actions"]}
        for correct_action in CORRECT_S3_ACTIONS:
            assert correct_action in registry_actions, (
                f"Registry missing correct action: {correct_action}"
            )
        for invalid_action in INVALID_S3_ACTIONS:
            assert invalid_action not in registry_actions, (
                f"Registry still has invalid action: {invalid_action}"
            )

    def test_policy_action_count_consistent(self):
        """Composed policy action count should be 159."""
        actions = _actions(_load_composed())
        assert len(actions) == 159, (
            f"Expected 159 actions, got {len(actions)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FALSE-CLEAN PREVENTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestFalseCleanPrevention:
    """AccessDenied errors must never produce a passing/clean result."""

    def test_s3_encryption_access_denied_produces_could_not_check(self):
        """When get_bucket_encryption returns AccessDenied, a could_not_check
        finding must be emitted, not silence (false clean)."""
        from kulshan.checks.security.scanner.data import DataScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "list_buckets":
                return {"Buckets": [{"Name": "test-bucket"}]}, None
            if method == "get_public_access_block":
                return {"PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True, "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
                }}, None
            if method == "get_bucket_encryption":
                return None, "Access denied: get_bucket_encryption"
            if method == "get_bucket_versioning":
                return {"Status": "Enabled"}, None
            if method == "describe_db_instances":
                return {"DBInstances": []}, None
            if method == "describe_db_snapshots":
                return {"DBSnapshots": []}, None
            if method == "describe_volumes":
                return {"Volumes": []}, None
            if method == "describe_snapshots":
                return {"Snapshots": []}, None
            return None, f"Not mocked: {method}"

        with patch(
            "kulshan.checks.security.scanner.data.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            scanner = DataScanner(session=session, regions=["us-east-1"])
            result = scanner.scan()

        # Must have a finding about could_not_check
        could_not_check_findings = [
            f for f in result.findings
            if f.details.get("result_state") == "could_not_check"
        ]
        assert len(could_not_check_findings) >= 1, (
            "AccessDenied on get_bucket_encryption must produce a could_not_check finding"
        )

        # The denied action must be named
        finding = could_not_check_findings[0]
        assert "s3:GetEncryptionConfiguration" in finding.details.get("iam_action_required", ""), (
            "The denied IAM action must be named in the finding"
        )

    def test_s3_encryption_access_denied_does_not_produce_clean(self):
        """An S3 authorization failure cannot produce a passing result.
        The bucket must NOT appear as if encryption is configured."""
        from kulshan.checks.security.scanner.data import DataScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "list_buckets":
                return {"Buckets": [{"Name": "denied-bucket"}]}, None
            if method == "get_public_access_block":
                return {"PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True, "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
                }}, None
            if method == "get_bucket_encryption":
                return None, "Access denied: get_bucket_encryption"
            if method == "get_bucket_versioning":
                return {"Status": "Enabled"}, None
            if method == "describe_db_instances":
                return {"DBInstances": []}, None
            if method == "describe_db_snapshots":
                return {"DBSnapshots": []}, None
            if method == "describe_volumes":
                return {"Volumes": []}, None
            if method == "describe_snapshots":
                return {"Snapshots": []}, None
            return None, f"Not mocked: {method}"

        with patch(
            "kulshan.checks.security.scanner.data.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            scanner = DataScanner(session=session, regions=["us-east-1"])
            result = scanner.scan()

        # There must be at least one finding about the denied bucket
        # It must NOT be zero findings (which would mean "clean")
        bucket_findings = [
            f for f in result.findings
            if "denied-bucket" in f.resource_id
        ]
        assert len(bucket_findings) >= 1, (
            "A bucket with AccessDenied on encryption check must not appear clean (zero findings)"
        )

    def test_s3_encryption_success_still_reports_no_encryption(self):
        """Existing behavior: bucket with no encryption configured still gets finding."""
        from kulshan.checks.security.scanner.data import DataScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "list_buckets":
                return {"Buckets": [{"Name": "unencrypted-bucket"}]}, None
            if method == "get_public_access_block":
                return {"PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True, "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
                }}, None
            if method == "get_bucket_encryption":
                return None, "ServerSideEncryptionConfigurationNotFoundError: blah"
            if method == "get_bucket_versioning":
                return {"Status": "Enabled"}, None
            if method == "describe_db_instances":
                return {"DBInstances": []}, None
            if method == "describe_db_snapshots":
                return {"DBSnapshots": []}, None
            if method == "describe_volumes":
                return {"Volumes": []}, None
            if method == "describe_snapshots":
                return {"Snapshots": []}, None
            return None, f"Not mocked: {method}"

        with patch(
            "kulshan.checks.security.scanner.data.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            scanner = DataScanner(session=session, regions=["us-east-1"])
            result = scanner.scan()

        # Should have DATA-003 finding for no encryption
        data003 = [f for f in result.findings if f.check_id == "DATA-003"]
        assert len(data003) == 1, "Bucket without encryption must still get DATA-003 finding"

    def test_s3_encryption_success_clean_bucket(self):
        """A bucket with encryption configured should produce NO encryption finding."""
        from kulshan.checks.security.scanner.data import DataScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "list_buckets":
                return {"Buckets": [{"Name": "encrypted-bucket"}]}, None
            if method == "get_public_access_block":
                return {"PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True, "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
                }}, None
            if method == "get_bucket_encryption":
                return {"ServerSideEncryptionConfiguration": {
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                }}, None
            if method == "get_bucket_versioning":
                return {"Status": "Enabled"}, None
            if method == "describe_db_instances":
                return {"DBInstances": []}, None
            if method == "describe_db_snapshots":
                return {"DBSnapshots": []}, None
            if method == "describe_volumes":
                return {"Volumes": []}, None
            if method == "describe_snapshots":
                return {"Snapshots": []}, None
            return None, f"Not mocked: {method}"

        with patch(
            "kulshan.checks.security.scanner.data.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            scanner = DataScanner(session=session, regions=["us-east-1"])
            result = scanner.scan()

        # No encryption-related findings for this bucket
        enc_findings = [
            f for f in result.findings
            if f.check_id in ("DATA-003", "DATA-X03") and "encrypted-bucket" in f.resource_id
        ]
        assert len(enc_findings) == 0, (
            "A bucket with confirmed encryption should have no encryption findings"
        )

    def test_dr_storage_access_denied_produces_could_not_check(self):
        """DR storage scanner: AccessDenied on replication/lifecycle
        must produce could_not_check entries, not false-fail or silence."""
        from kulshan.checks.dr.scanner.storage import scan_storage

        session = _mock_session()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "list_buckets":
                return {"Buckets": [{"Name": "dr-test-bucket"}]}, None
            if method == "get_bucket_versioning":
                return {"Status": "Enabled"}, None
            if method == "get_bucket_replication":
                return None, "Access denied: get_bucket_replication"
            if method == "get_bucket_lifecycle_configuration":
                return None, "Access denied: get_bucket_lifecycle_configuration"
            return None, f"Not mocked: {method}"

        with patch(
            "kulshan.checks.dr.scanner.storage.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            session.client.return_value = MagicMock()
            result, errors = scan_storage(session, ["us-east-1"])

        assert "could_not_check" in result, "Result must contain could_not_check key"
        cnc = result["could_not_check"]
        assert len(cnc) >= 2, "Must have could_not_check for replication AND lifecycle"

        # Verify IAM actions are named
        iam_actions_mentioned = [entry["iam_action_required"] for entry in cnc]
        assert "s3:GetReplicationConfiguration" in iam_actions_mentioned
        assert "s3:GetLifecycleConfiguration" in iam_actions_mentioned


# ═══════════════════════════════════════════════════════════════════════════════
# 3. POLICY CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestPolicyConsistency:
    """Policy hash, action counts, and cross-repository agreement."""

    def test_policy_hash_matches(self):
        """SHA256 of composed policy matches the recorded hash file."""
        content = COMPOSED_PATH.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
        actual = hashlib.sha256(content).hexdigest()
        expected = HASH_PATH.read_text(encoding="utf-8").strip()
        assert actual == expected, (
            f"Policy hash mismatch: expected {expected}, got {actual}"
        )

    @pytest.mark.skipif(
        not WEBSITE_POLICY_PATH.exists(),
        reason="Website repository not present at expected path"
    )
    def test_website_policy_matches_repository(self):
        """The production website's downloadable policy must match the authoritative one."""
        repo_content = COMPOSED_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
        web_content = WEBSITE_POLICY_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert repo_content == web_content, (
            "Website policy file does not match repository policy"
        )

    @pytest.mark.skipif(
        not WEBSITE_POLICY_PATH.exists(),
        reason="Website repository not present at expected path"
    )
    def test_website_policy_sha256_matches_hash_file(self):
        """The production website policy JSON produces the same SHA256 as the hash file."""
        web_content = WEBSITE_POLICY_PATH.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
        web_hash = hashlib.sha256(web_content).hexdigest()
        expected = HASH_PATH.read_text(encoding="utf-8").strip()
        assert web_hash == expected, (
            f"Website policy hash {web_hash} != recorded {expected}"
        )

    def test_registry_required_actions_all_in_composed(self):
        """Every registry action marked required+baseline_eligible is in the composed policy."""
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        composed_actions = _actions(_load_composed())
        missing = [
            e["iam_action"] for e in registry["actions"]
            if e.get("baseline_eligible") and e.get("status") == "required"
            and e["iam_action"] not in composed_actions
        ]
        assert not missing, f"Registry actions missing from composed policy: {missing}"

    def test_no_invalid_actions_anywhere_in_iam_dir(self):
        """Sweep all JSON in iam/ for the three known invalid action names."""
        violations = []
        for f in IAM_DIR.rglob("*.json"):
            content = f.read_text(encoding="utf-8")
            for invalid in INVALID_S3_ACTIONS:
                if invalid in content:
                    violations.append(f"{f.name}: {invalid}")
        assert not violations, f"Invalid actions found: {violations}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. REPORT RESULT STATE DISTINGUISHABILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestResultStateDistinguishability:
    """Reports must distinguish 'no issue found' from 'not evaluated'."""

    def test_could_not_check_finding_has_required_fields(self):
        """A could_not_check finding must carry all required metadata."""
        from kulshan.checks.security.scanner.data import DataScanner

        session = _mock_session()
        session.client.return_value = MagicMock()

        def mock_safe_api_call(client_obj, method, **kwargs):
            if method == "list_buckets":
                return {"Buckets": [{"Name": "meta-test"}]}, None
            if method == "get_public_access_block":
                return None, "Access denied: get_public_access_block"
            if method == "get_bucket_encryption":
                return None, "Access denied: get_bucket_encryption"
            if method == "get_bucket_versioning":
                return None, "Access denied: get_bucket_versioning"
            if method == "describe_db_instances":
                return {"DBInstances": []}, None
            if method == "describe_db_snapshots":
                return {"DBSnapshots": []}, None
            if method == "describe_volumes":
                return {"Volumes": []}, None
            if method == "describe_snapshots":
                return {"Snapshots": []}, None
            return None, f"Not mocked: {method}"

        with patch(
            "kulshan.checks.security.scanner.data.safe_api_call",
            side_effect=mock_safe_api_call,
        ):
            scanner = DataScanner(session=session, regions=["us-east-1"])
            result = scanner.scan()

        cnc_findings = [
            f for f in result.findings
            if f.details.get("result_state") == "could_not_check"
        ]
        assert len(cnc_findings) >= 1

        for finding in cnc_findings:
            details = finding.details
            assert "resource" in details, "Finding must name the resource"
            assert "api_operation" in details, "Finding must name the API operation"
            assert "iam_action_required" in details, "Finding must name the IAM action"
            assert "error_category" in details, "Finding must categorize the error"
            # Description must explain that the control was not evaluated
            assert "NOT" in finding.description or "not" in finding.description, (
                "Description must clarify the resource was not confirmed clean"
            )
