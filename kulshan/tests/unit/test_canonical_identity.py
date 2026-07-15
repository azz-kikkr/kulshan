"""Tests for canonical identity and session-name stability.

Proves that:
1. Same role with different session names reuses one workspace.
2. Same account with different roles creates separate workspaces.
3. Role paths are canonicalized correctly.
4. IAM user ARN remains stable.
5. Root ARN remains stable.
6. Different AWS partitions remain distinct.
7. Old raw-ARN registry entry migrates to canonical key.
8. Migration reuses existing ws_<hash> directory and history DB.
9. Generated names use role/user names, not session names.
10. No session names or credentials are written to workspace TOML.
11. Repeated kulshan report with changing session names reuses one env.
12. Full suite remains green (verified by runner).
"""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    read_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.context import WorkspaceContext
from kulshan.workspace.onboarding import (
    auto_onboard,
    generate_display_name,
    _extract_principal_name,
)
from kulshan.workspace.registry import (
    canonicalize_arn,
    compute_identity_key_v2,
    compute_workspace_dir_name_v2,
    lookup_workspace_by_identity,
    register_workspace,
    _read_registry,
    _write_registry,
    _HMAC_KEY_V2,
)
from kulshan.workspace.sts import VerifiedAwsSession


# ---------------------------------------------------------------------------
# Test 1: Same role, different session names → same workspace
# ---------------------------------------------------------------------------


class TestSameRoleDifferentSessions:
    """Same role with two different session names reuses one workspace."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()
        monkeypatch.setattr("kulshan.workspace.registry.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: self.workspaces_root / name,
        )
        monkeypatch.setattr("kulshan.workspace.paths.get_workspaces_root", lambda: self.workspaces_root)

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_different_sessions_same_workspace(self, mock_sts):
        """Two logins with different session names → same workspace."""
        mock_sts.return_value = VerifiedAwsSession(
            session=MagicMock(),
            account_id="111111111111",
            arn="arn:aws:sts::111111111111:assumed-role/ReadOnlyRole/session-abc",
            user_id="AROA:session-abc",
            resolved_profile=None,
            role_arn=None,
        )

        r1 = auto_onboard(profile=None)
        assert r1.is_new is True

        # Second login with different session name
        mock_sts.return_value = VerifiedAwsSession(
            session=MagicMock(),
            account_id="111111111111",
            arn="arn:aws:sts::111111111111:assumed-role/ReadOnlyRole/session-xyz",
            user_id="AROA:session-xyz",
            resolved_profile=None,
            role_arn=None,
        )

        r2 = auto_onboard(profile=None)
        assert r2.is_new is False
        assert r2.workspace_context.path == r1.workspace_context.path


# ---------------------------------------------------------------------------
# Test 2: Same account, different roles → separate workspaces
# ---------------------------------------------------------------------------


class TestDifferentRolesSeparate:
    """Same account with two different roles creates separate workspaces."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()
        monkeypatch.setattr("kulshan.workspace.registry.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: self.workspaces_root / name,
        )
        monkeypatch.setattr("kulshan.workspace.paths.get_workspaces_root", lambda: self.workspaces_root)

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_different_roles_different_workspaces(self, mock_sts):
        mock_sts.return_value = VerifiedAwsSession(
            session=MagicMock(),
            account_id="111111111111",
            arn="arn:aws:sts::111111111111:assumed-role/Admin/sess",
            user_id="AROA:sess",
            resolved_profile=None,
            role_arn=None,
        )
        r1 = auto_onboard(profile=None)

        mock_sts.return_value = VerifiedAwsSession(
            session=MagicMock(),
            account_id="111111111111",
            arn="arn:aws:sts::111111111111:assumed-role/ReadOnly/sess",
            user_id="AROA:sess",
            resolved_profile=None,
            role_arn=None,
        )
        r2 = auto_onboard(profile=None)

        assert r1.workspace_context.path != r2.workspace_context.path


# ---------------------------------------------------------------------------
# Test 3: Role paths canonicalized correctly
# ---------------------------------------------------------------------------


class TestRolePathCanonicalization:
    """Role paths are preserved in canonical ARN."""

    def test_simple_role(self):
        assert canonicalize_arn(
            "arn:aws:sts::123456789012:assumed-role/MyRole/session"
        ) == "arn:aws:iam::123456789012:role/MyRole"

    def test_role_with_path(self):
        assert canonicalize_arn(
            "arn:aws:sts::123456789012:assumed-role/team/finops/MyRole/session-123"
        ) == "arn:aws:iam::123456789012:role/team/finops/MyRole"

    def test_deep_path(self):
        assert canonicalize_arn(
            "arn:aws:sts::123456789012:assumed-role/org/dept/team/AuditRole/sess-abc"
        ) == "arn:aws:iam::123456789012:role/org/dept/team/AuditRole"


# ---------------------------------------------------------------------------
# Test 4: IAM user ARN remains stable
# ---------------------------------------------------------------------------


class TestIamUserStable:
    """IAM user ARNs are returned unchanged."""

    def test_user_arn_unchanged(self):
        arn = "arn:aws:iam::123456789012:user/alice"
        assert canonicalize_arn(arn) == arn

    def test_user_with_path(self):
        arn = "arn:aws:iam::123456789012:user/team/alice"
        assert canonicalize_arn(arn) == arn

    def test_identity_key_stable(self):
        arn = "arn:aws:iam::123456789012:user/alice"
        k1 = compute_identity_key_v2("123456789012", arn)
        k2 = compute_identity_key_v2("123456789012", arn)
        assert k1 == k2


# ---------------------------------------------------------------------------
# Test 5: Root ARN remains stable
# ---------------------------------------------------------------------------


class TestRootArnStable:
    """Root ARN is returned unchanged."""

    def test_root_unchanged(self):
        arn = "arn:aws:iam::123456789012:root"
        assert canonicalize_arn(arn) == arn

    def test_root_identity_key_stable(self):
        arn = "arn:aws:iam::123456789012:root"
        k1 = compute_identity_key_v2("123456789012", arn)
        k2 = compute_identity_key_v2("123456789012", arn)
        assert k1 == k2


# ---------------------------------------------------------------------------
# Test 6: Different AWS partitions remain distinct
# ---------------------------------------------------------------------------


class TestPartitionsDistinct:
    """Different AWS partitions produce different identity keys."""

    def test_aws_vs_aws_cn(self):
        k1 = compute_identity_key_v2(
            "111111111111",
            "arn:aws:sts::111111111111:assumed-role/Admin/sess",
        )
        k2 = compute_identity_key_v2(
            "111111111111",
            "arn:aws-cn:sts::111111111111:assumed-role/Admin/sess",
        )
        assert k1 != k2

    def test_aws_vs_aws_gov(self):
        k1 = compute_identity_key_v2(
            "111111111111",
            "arn:aws:sts::111111111111:assumed-role/Admin/sess",
        )
        k2 = compute_identity_key_v2(
            "111111111111",
            "arn:aws-us-gov:sts::111111111111:assumed-role/Admin/sess",
        )
        assert k1 != k2


# ---------------------------------------------------------------------------
# Test 7: Old raw-ARN registry entry migrates to canonical key
# ---------------------------------------------------------------------------


class TestRawArnMigration:
    """An old raw-ARN registry entry migrates to the canonical key."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        monkeypatch.setattr("kulshan.workspace.registry.get_data_dir", lambda: tmp_path)

    def test_migration(self):
        """Raw-ARN v2 entry is migrated to canonical key on lookup."""
        account_id = "444444444444"
        raw_arn = "arn:aws:sts::444444444444:assumed-role/BillingRole/old-session"

        # Simulate an old registry entry written with raw ARN hash
        raw_message = f"{account_id}\n{raw_arn}".encode("utf-8")
        raw_digest = hmac.new(_HMAC_KEY_V2, raw_message, hashlib.sha256).hexdigest()
        raw_key = f"v2_{raw_digest[:13]}"

        data = {
            "entries": {
                raw_key: {
                    "workspace_dir": "ws_old12345",
                    "account_id": account_id,
                    "arn": raw_arn,
                    "display_name": "billingrole-oak",
                    "created_at": "2025-01-01T00:00:00Z",
                }
            }
        }
        _write_registry(data)

        # Now look up with a new session name (same role)
        new_arn = "arn:aws:sts::444444444444:assumed-role/BillingRole/new-session"
        entry = lookup_workspace_by_identity(account_id, new_arn)

        assert entry is not None
        assert entry.workspace_dir == "ws_old12345"

        # Verify migration: canonical key now exists, raw key removed
        after = _read_registry()
        canonical_key = compute_identity_key_v2(account_id, new_arn)
        assert canonical_key in after["entries"]
        assert raw_key not in after["entries"]


# ---------------------------------------------------------------------------
# Test 8: Migration reuses existing directory and history DB
# ---------------------------------------------------------------------------


class TestMigrationReusesDirectory:
    """Migration reuses the existing ws_<hash> directory and history DB."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()
        monkeypatch.setattr("kulshan.workspace.registry.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: self.workspaces_root / name,
        )
        monkeypatch.setattr("kulshan.workspace.paths.get_workspaces_root", lambda: self.workspaces_root)

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_migrated_workspace_reused(self, mock_sts):
        """After migration, the same workspace directory is reused."""
        account_id = "555555555555"
        raw_arn = "arn:aws:sts::555555555555:assumed-role/FinOps/first-session"

        # Simulate old entry
        raw_message = f"{account_id}\n{raw_arn}".encode("utf-8")
        raw_digest = hmac.new(_HMAC_KEY_V2, raw_message, hashlib.sha256).hexdigest()
        raw_key = f"v2_{raw_digest[:13]}"
        ws_dir = f"ws_{raw_digest[:8]}"

        # Create workspace directory with a history.db
        ws_path = self.workspaces_root / ws_dir
        ws_path.mkdir()
        (ws_path / "history.db").write_text("fake-db")
        config = WorkspaceConfig(
            name=ws_dir,
            display_name="finops-oak",
            binding_mode="bound",
            aws=WorkspaceAwsConfig(
                payer_account_id=None,
                default_connection="finops",
                connections=[AwsConnection(
                    name="finops", profile="default",
                    expected_session_account_id=account_id,
                )],
            ),
        )
        write_workspace_config(ws_path, config)

        # Register under raw key
        data = {
            "entries": {
                raw_key: {
                    "workspace_dir": ws_dir,
                    "account_id": account_id,
                    "arn": raw_arn,
                    "display_name": "finops-oak",
                    "created_at": "2025-01-01T00:00:00Z",
                }
            }
        }
        _write_registry(data)

        # Now auto_onboard with a new session name
        new_arn = "arn:aws:sts::555555555555:assumed-role/FinOps/second-session"
        mock_sts.return_value = VerifiedAwsSession(
            session=MagicMock(),
            account_id=account_id,
            arn=new_arn,
            user_id="AROA:second-session",
            resolved_profile=None,
            role_arn=None,
        )

        result = auto_onboard(profile=None)
        assert result.is_new is False
        assert result.workspace_context.path == ws_path
        # History DB preserved
        assert (ws_path / "history.db").exists()


# ---------------------------------------------------------------------------
# Test 9: Generated names use role/user names, not session names
# ---------------------------------------------------------------------------


class TestNamesUseRoleNotSession:
    """Display names use role or user names, never session names."""

    def test_role_name_used(self):
        name = generate_display_name(
            "123456789012",
            "arn:aws:sts::123456789012:assumed-role/BillingAudit/my-session-2024",
        )
        assert "billingaudit" in name
        assert "my-session" not in name
        assert "2024" not in name

    def test_user_name_used(self):
        name = generate_display_name(
            "123456789012",
            "arn:aws:iam::123456789012:user/jane",
        )
        assert "jane" in name

    def test_session_name_never_in_output(self):
        """Various session names should not appear in display name."""
        for session in ["session-123", "abc@def.com", "kulshan-exec", "botocore-session-xyz"]:
            name = generate_display_name(
                "123456789012",
                f"arn:aws:sts::123456789012:assumed-role/MyRole/{session}",
            )
            assert session.lower() not in name.lower()


# ---------------------------------------------------------------------------
# Test 10: No session names or credentials written to workspace TOML
# ---------------------------------------------------------------------------


class TestNoSessionInToml:
    """No session names or credentials are written to workspace.toml."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()
        monkeypatch.setattr("kulshan.workspace.registry.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: self.workspaces_root / name,
        )
        monkeypatch.setattr("kulshan.workspace.paths.get_workspaces_root", lambda: self.workspaces_root)

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_toml_has_no_session_name(self, mock_sts):
        """workspace.toml does not contain session names or tokens."""
        mock_sts.return_value = VerifiedAwsSession(
            session=MagicMock(),
            account_id="666666666666",
            arn="arn:aws:sts::666666666666:assumed-role/Auditor/sensitive-session-name",
            user_id="AROA:sensitive-session-name",
            resolved_profile=None,
            role_arn=None,
        )

        result = auto_onboard(profile=None)
        toml_path = result.workspace_context.path / "workspace.toml"
        content = toml_path.read_text()

        assert "sensitive-session-name" not in content
        assert "AROA" not in content
        assert "aws_access_key" not in content.lower()
        assert "aws_secret" not in content.lower()
        assert "session_token" not in content.lower()


# ---------------------------------------------------------------------------
# Test 11: Repeated kulshan report with changing sessions → one env
# ---------------------------------------------------------------------------


class TestRepeatedReportOneEnv:
    """aws login → kulshan report (repeated with session rotation) → one env."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        self.workspaces_root = tmp_path / "workspaces"
        self.workspaces_root.mkdir()
        monkeypatch.setattr("kulshan.workspace.registry.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "kulshan.workspace.onboarding.get_workspace_path",
            lambda name: self.workspaces_root / name,
        )
        monkeypatch.setattr("kulshan.workspace.paths.get_workspaces_root", lambda: self.workspaces_root)

    @patch("kulshan.workspace.onboarding.create_verified_session")
    def test_five_sessions_one_workspace(self, mock_sts):
        """Five invocations with rotating session names → one workspace."""
        account_id = "777777777777"
        role = "ProductionAudit"
        paths = set()

        for i in range(5):
            mock_sts.return_value = VerifiedAwsSession(
                session=MagicMock(),
                account_id=account_id,
                arn=f"arn:aws:sts::{account_id}:assumed-role/{role}/session-{i}",
                user_id=f"AROA:session-{i}",
                resolved_profile=None,
                role_arn=None,
            )
            result = auto_onboard(profile=None)
            paths.add(str(result.workspace_context.path))

        # All five invocations used the same workspace
        assert len(paths) == 1
