"""Data protection scanner, S3, RDS, EBS."""

from .base import BaseScanner, ScanResult, Severity, Finding
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


class DataScanner(BaseScanner):
    category = "Data Protection"

    def scan(self) -> ScanResult:
        self._scan_s3()
        self._scan_rds()
        self._scan_ebs()
        return ScanResult(findings=self.findings, resources=self.resources, errors=self.errors)

    def _could_not_check(self, check_id: str, resource_id: str, api_operation: str,
                         iam_action: str, error_category: str, err_detail: str):
        """Record a finding indicating a control could not be evaluated.

        A check is marked clean ONLY when the required evidence was successfully
        retrieved and evaluated. This method ensures failed evaluations are never
        silently reported as passing.
        """
        self.add_finding(
            check_id=check_id,
            title=f"Could not evaluate '{resource_id}': {iam_action} denied or unavailable",
            severity=Severity.INFO,
            resource_type="AWS::S3::Bucket",
            resource_id=resource_id,
            description=(
                f"The control could not be evaluated. "
                f"API operation: {api_operation}. "
                f"Required IAM action: {iam_action}. "
                f"Error: {error_category}. "
                f"This resource was NOT confirmed clean. "
                f"Grant the required permission and re-run to obtain a valid result."
            ),
            remediation=f"Grant {iam_action} permission and re-run the scan.",
            details={
                "result_state": "could_not_check",
                "resource": resource_id,
                "api_operation": api_operation,
                "iam_action_required": iam_action,
                "error_category": error_category,
            },
        )

    def _scan_s3(self):
        s3 = self.session.client("s3", region_name="us-east-1")
        buckets_resp, err = safe_api_call(s3, "list_buckets")
        if err:
            self.errors.append(f"S3 list: {err}")
            if _is_access_denied(err):
                self._could_not_check(
                    "DATA-X01", "account-level", "s3:ListBuckets",
                    "s3:ListAllMyBuckets", "access_denied", err)
            return
        buckets = (buckets_resp or {}).get("Buckets", [])
        self.resources["s3_buckets"] = buckets
        self.advance()

        for bucket in buckets:
            name = bucket["Name"]
            # Public access block
            pab, pab_err = safe_api_call(s3, "get_public_access_block", Bucket=name)
            if pab_err and _is_access_denied(pab_err):
                self._could_not_check(
                    "DATA-X02", name, "s3:GetPublicAccessBlock",
                    "s3:GetBucketPublicAccessBlock", "access_denied", pab_err)
            elif not pab and not pab_err:
                self.add_finding(
                    check_id="DATA-001", title=f"S3 bucket '{name}' has no public access block",
                    severity=Severity.HIGH, resource_type="AWS::S3::Bucket",
                    resource_id=name, description="No S3 Block Public Access configuration.",
                    remediation="Enable S3 Block Public Access on this bucket.")
            elif not pab and pab_err:
                # Non-access-denied error (e.g. NoSuchPublicAccessBlockConfiguration)
                self.add_finding(
                    check_id="DATA-001", title=f"S3 bucket '{name}' has no public access block",
                    severity=Severity.HIGH, resource_type="AWS::S3::Bucket",
                    resource_id=name, description="No S3 Block Public Access configuration.",
                    remediation="Enable S3 Block Public Access on this bucket.")
            else:
                config = pab.get("PublicAccessBlockConfiguration", {})
                if not all([config.get("BlockPublicAcls"), config.get("IgnorePublicAcls"),
                           config.get("BlockPublicPolicy"), config.get("RestrictPublicBuckets")]):
                    # Check if actually public
                    status, _ = safe_api_call(s3, "get_bucket_policy_status", Bucket=name)
                    if status and status.get("PolicyStatus", {}).get("IsPublic"):
                        self.add_finding(
                            check_id="DATA-002", title=f"S3 bucket '{name}' is PUBLIC",
                            severity=Severity.CRITICAL, resource_type="AWS::S3::Bucket",
                            resource_id=name, description="This bucket is publicly accessible.",
                            remediation="Enable all S3 Block Public Access settings and review bucket policy.")

            # Encryption
            enc, enc_err = safe_api_call(s3, "get_bucket_encryption", Bucket=name)
            if enc_err:
                if "ServerSideEncryptionConfigurationNotFoundError" in str(enc_err):
                    self.add_finding(
                        check_id="DATA-003", title=f"S3 bucket '{name}' has no default encryption",
                        severity=Severity.HIGH, resource_type="AWS::S3::Bucket",
                        resource_id=name, description="No server-side encryption configured.",
                        remediation="Enable default encryption (SSE-S3 or SSE-KMS).")
                elif _is_access_denied(enc_err):
                    self._could_not_check(
                        "DATA-X03", name, "s3:GetBucketEncryption",
                        "s3:GetEncryptionConfiguration", "access_denied", enc_err)
                else:
                    self._could_not_check(
                        "DATA-X03", name, "s3:GetBucketEncryption",
                        "s3:GetEncryptionConfiguration", "api_error", enc_err)
            # If enc is truthy and no error, encryption IS configured -> clean (no finding needed)

            # Versioning
            ver, ver_err = safe_api_call(s3, "get_bucket_versioning", Bucket=name)
            if ver_err and _is_access_denied(ver_err):
                self._could_not_check(
                    "DATA-X04", name, "s3:GetBucketVersioning",
                    "s3:GetBucketVersioning", "access_denied", ver_err)
            elif not ver or ver.get("Status") != "Enabled":
                self.add_finding(
                    check_id="DATA-004", title=f"S3 bucket '{name}' versioning not enabled",
                    severity=Severity.MEDIUM, resource_type="AWS::S3::Bucket",
                    resource_id=name, description="Without versioning, data loss and ransomware recovery is harder.",
                    remediation="Enable versioning on this bucket.")

    def _scan_rds(self):
        for region in self.regions:
            rds = self.session.client("rds", region_name=region)
            instances, err = safe_api_call(rds, "describe_db_instances")
            if err: continue
            for db in (instances or {}).get("DBInstances", []):
                if db.get("PubliclyAccessible"):
                    self.add_finding(
                        check_id="DATA-005", title=f"RDS '{db['DBInstanceIdentifier']}' is publicly accessible",
                        severity=Severity.CRITICAL, resource_type="AWS::RDS::DBInstance",
                        resource_id=db["DBInstanceIdentifier"], region=region,
                        description="Database is accessible from the internet.",
                        remediation="Disable public accessibility and use private subnets.")
                if not db.get("StorageEncrypted"):
                    self.add_finding(
                        check_id="DATA-006", title=f"RDS '{db['DBInstanceIdentifier']}' is not encrypted",
                        severity=Severity.HIGH, resource_type="AWS::RDS::DBInstance",
                        resource_id=db["DBInstanceIdentifier"], region=region,
                        description="Database storage is not encrypted at rest.",
                        remediation="Enable encryption (requires creating a new encrypted instance and migrating).")

            # Public snapshots
            snaps, _ = safe_api_call(rds, "describe_db_snapshots", SnapshotType="manual")
            for snap in (snaps or {}).get("DBSnapshots", []):
                attr, _ = safe_api_call(rds, "describe_db_snapshot_attributes",
                    DBSnapshotIdentifier=snap["DBSnapshotIdentifier"])
                if attr:
                    for a in attr.get("DBSnapshotAttributesResult", {}).get("DBSnapshotAttributes", []):
                        if a.get("AttributeName") == "restore" and "all" in a.get("AttributeValues", []):
                            self.add_finding(
                                check_id="DATA-007", title=f"RDS snapshot '{snap['DBSnapshotIdentifier']}' is public",
                                severity=Severity.CRITICAL, resource_type="AWS::RDS::DBSnapshot",
                                resource_id=snap["DBSnapshotIdentifier"], region=region,
                                description="Anyone can restore this snapshot and access the data.",
                                remediation="Remove public access from this snapshot immediately.")
            self.advance()

    def _scan_ebs(self):
        for region in self.regions:
            ec2 = self.session.client("ec2", region_name=region)
            vols, _ = safe_api_call(ec2, "describe_volumes")
            for vol in (vols or {}).get("Volumes", []):
                if not vol.get("Encrypted"):
                    self.add_finding(
                        check_id="DATA-008", title=f"EBS volume '{vol['VolumeId']}' is not encrypted",
                        severity=Severity.HIGH, resource_type="AWS::EC2::Volume",
                        resource_id=vol["VolumeId"], region=region,
                        description="EBS volume data is not encrypted at rest.",
                        remediation="Create an encrypted copy and replace the unencrypted volume.")

            snaps, _ = safe_api_call(ec2, "describe_snapshots", OwnerIds=["self"])
            for snap in (snaps or {}).get("Snapshots", []):
                attr, _ = safe_api_call(ec2, "describe_snapshot_attribute",
                    SnapshotId=snap["SnapshotId"], Attribute="createVolumePermission")
                if attr:
                    for perm in attr.get("CreateVolumePermissions", []):
                        if perm.get("Group") == "all":
                            self.add_finding(
                                check_id="DATA-009", title=f"EBS snapshot '{snap['SnapshotId']}' is public",
                                severity=Severity.CRITICAL, resource_type="AWS::EC2::Snapshot",
                                resource_id=snap["SnapshotId"], region=region,
                                description="Anyone can create a volume from this snapshot.",
                                remediation="Remove public access from this snapshot.")
