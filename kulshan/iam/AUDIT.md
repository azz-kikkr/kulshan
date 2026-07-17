# IAM Action Audit

Last updated: Commit 3 — Align published IAM policy with implemented AWS calls.

## Summary

- **Baseline policy actions:** 159 (read-only Get, List, Describe)
- **Services:** 32
- **Write actions:** 0
- **New actions added this commit:** 12

---

## Newly Added Actions (Commit 3)

| IAM Action | boto3 Method | Access Level | Capability | Source File | Behavior When Denied |
|---|---|---|---|---|---|
| `bcm-data-exports:GetExport` | `bcm-data-exports.get_export` | Read | cost | `src/kulshan/cur/discovery.py` | silent_omission |
| `bcm-data-exports:ListExports` | `bcm-data-exports.list_exports` | List | cost | `src/kulshan/cur/discovery.py` | silent_omission |
| `config:DescribeConfigurationRecorderStatus` | `config.describe_configuration_recorder_status` | List | security | `checks/security/scanner/logging_monitor.py` | silent_omission |
| `ec2:DescribeSnapshotAttribute` | `ec2.describe_snapshot_attribute` | List | security | `checks/security/scanner/data.py` | silent_omission |
| `iam:GenerateCredentialReport` | `iam.generate_credential_report` | Read | security | `checks/security/scanner/iam.py` | silent_omission |
| `iam:GenerateServiceLastAccessedDetails` | `iam.generate_service_last_accessed_details` | Read | security | `checks/security/scanner/iam.py` | silent_omission |
| `iam:GetAccountAuthorizationDetails` | `iam.get_account_authorization_details` | List | security | `checks/security/scanner/iam.py` | silent_omission |
| `iam:GetCredentialReport` | `iam.get_credential_report` | Read | security | `checks/security/scanner/iam.py` | silent_omission |
| `iam:GetServiceLastAccessedDetails` | `iam.get_service_last_accessed_details` | Read | security | `checks/security/scanner/iam.py` | silent_omission |
| `organizations:DescribeOrganization` | `organizations.describe_organization` | Read | cost | `src/kulshan/preflight.py` | silent_omission |
| `rds:DescribeDBSnapshotAttributes` | `rds.describe_db_snapshot_attributes` | List | security | `checks/security/scanner/data.py` | silent_omission |
| `secretsmanager:ListSecrets` | `secretsmanager.list_secrets` | List | security | `checks/security/scanner/encryption.py` | silent_omission |

---

## Excluded from Baseline: sts:AssumeRole

| IAM Action | Access Level | Status | Rationale |
|---|---|---|---|
| `sts:AssumeRole` | Write | cross_account | Required only for multi-account scanning via `--role-arn`. The caller must already have permission to assume the target role; adding it to the baseline policy would be meaningless (the *caller's* policy, not the *target's* policy, needs this). Including a Write-level action in the baseline would violate the read-only guarantee. |

---

## S3 and KMS Disposition

| Action | In Baseline? | Location | Rationale |
|---|---|---|---|
| `s3:GetObject` | No | S3 add-on policy (docs/iam-setup.md) | Required only for CUR Parquet file reading. Not all users have CUR. The action grants read access to object contents which exceeds metadata-only audit scope. |
| `kms:Decrypt` | No | S3 add-on policy (docs/iam-setup.md) | Required only when CUR data is encrypted with customer-managed KMS keys. Scope is narrower than baseline. |
| `s3:ListBucket` | Yes | Baseline (security.json) | Maps to `list_objects_v2`; metadata-only, no object content access. |
| `s3:GetBucketLocation` | Yes | Baseline (security.json) | Required for bucket region discovery. Read metadata only. |

---

## Silent Omission Documentation

All 12 newly-added actions use `behavior_when_denied: "silent_omission"`. This means:

1. The scanner calls the API via `safe_api_call()`.
2. If `AccessDeniedException` / `AccessDenied` is returned, `safe_api_call()` returns `(None, "Access denied: <method>")`.
3. The scanner logs the error internally but does NOT raise an exception.
4. The scan completes with partial results — findings that depend on the denied data are simply absent.
5. The user sees a degraded but valid report. No crash, no traceback, no data loss.

This is the standard Kulshan degradation pattern: **permission denied → partial results, never hard failure**.

---

## Registry

The complete action registry is at `kulshan/iam/registry.json`. It contains structured metadata for all 12 newly-added actions plus the `sts:AssumeRole` exclusion entry.

Fields per entry:
- `iam_action`: The IAM action string (e.g., `iam:GetAccountAuthorizationDetails`)
- `boto3_method`: The boto3 client method (e.g., `iam.get_account_authorization_details`)
- `aws_access_level`: AWS access level classification (Read, List, Write)
- `status`: `required` (in baseline) or `cross_account` (excluded)
- `capability`: Which audit pack uses this action
- `baseline_eligible`: Whether this action belongs in `kulshan-readonly.json`
- `source_file`: Where in the codebase this action is called
- `behavior_when_denied`: What happens if the permission is missing

---

## Verification

- CI test `test_no_write_access_level_in_baseline` ensures no Write-level action is baseline-eligible.
- CI test `test_sts_assume_role_absent_from_baseline` explicitly verifies `sts:AssumeRole` is not in the composed policy.
- CI test `test_registry_actions_present_in_policy` ensures all required baseline actions are in the composed policy.
- CI test `test_policy_hash_matches_recorded` detects unauthorized policy modifications.
- Regression tests in `test_denied_permissions.py` confirm graceful degradation for every new action.
