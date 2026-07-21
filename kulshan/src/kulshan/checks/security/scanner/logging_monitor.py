"""Logging and monitoring scanner, CloudTrail, GuardDuty, Config, Access Analyzer."""

from .base import BaseScanner, ScanResult, Severity
from ..utils.aws import safe_api_call


class LoggingScanner(BaseScanner):
    category = "Logging & Monitoring"

    def scan(self) -> ScanResult:
        self._scan_cloudtrail()
        self._scan_guardduty()
        self._scan_config()
        self._scan_access_analyzer()
        return ScanResult(findings=self.findings, resources=self.resources, errors=self.errors)

    def _scan_cloudtrail(self):
        ct = self.session.client("cloudtrail", region_name="us-east-1")
        trails, err = safe_api_call(ct, "describe_trails")
        if err:
            self.errors.append(f"CloudTrail: {err}")
            return
        trail_list = (trails or {}).get("trailList", [])
        self.resources["cloudtrail"] = trail_list
        self.advance()

        if not trail_list:
            self.add_finding(
                check_id="LOG-001", title="No CloudTrail trails configured",
                severity=Severity.CRITICAL, resource_type="AWS::CloudTrail::Trail",
                resource_id="none", description="No audit trail for API activity.",
                remediation="Create a multi-region CloudTrail trail with log file validation.")
            return

        has_multiregion = False
        for trail in trail_list:
            name = trail.get("Name", "unknown")
            if trail.get("IsMultiRegionTrail"):
                has_multiregion = True
            if not trail.get("LogFileValidationEnabled"):
                self.add_finding(
                    check_id="LOG-002", title=f"CloudTrail '{name}' has no log file validation",
                    severity=Severity.HIGH, resource_type="AWS::CloudTrail::Trail",
                    resource_id=name, description="Logs could be tampered with without detection.",
                    remediation="Enable log file validation on this trail.")

            status, _ = safe_api_call(ct, "get_trail_status", Name=trail.get("TrailARN", name))
            if status and not status.get("IsLogging"):
                self.add_finding(
                    check_id="LOG-003", title=f"CloudTrail '{name}' is not logging",
                    severity=Severity.CRITICAL, resource_type="AWS::CloudTrail::Trail",
                    resource_id=name, description="Trail exists but logging is stopped.",
                    remediation="Start logging on this trail immediately.")

        if not has_multiregion:
            self.add_finding(
                check_id="LOG-004", title="No multi-region CloudTrail trail",
                severity=Severity.HIGH, resource_type="AWS::CloudTrail::Trail",
                resource_id="account", description="Activity in non-trailed regions is invisible.",
                remediation="Enable multi-region on at least one trail.")

    def _scan_guardduty(self):
        regions_without_gd = []
        regions_could_not_check = []
        for region in self.regions:
            gd = self.session.client("guardduty", region_name=region)
            detectors, err = safe_api_call(gd, "list_detectors")
            if err:
                if "access denied" in str(err).lower() or "accessdenied" in str(err).lower():
                    regions_could_not_check.append(region)
                else:
                    self.errors.append(f"GuardDuty ({region}): {err}")
                continue
            detector_ids = (detectors or {}).get("DetectorIds", [])
            if not detector_ids:
                regions_without_gd.append(region)
                continue
            # Check for high-severity findings
            for did in detector_ids:
                findings, _ = safe_api_call(gd, "list_findings", DetectorId=did,
                    FindingCriteria={"Criterion": {"severity": {"Gte": 7}}}, MaxResults=10)
                finding_ids = (findings or {}).get("FindingIds", [])
                if finding_ids:
                    self.add_finding(
                        check_id="LOG-006", title=f"GuardDuty has {len(finding_ids)}+ high-severity findings in {region}",
                        severity=Severity.CRITICAL, resource_type="AWS::GuardDuty::Detector",
                        resource_id=did, region=region,
                        description="Active threats detected. Review immediately.",
                        remediation="Investigate and remediate GuardDuty findings in the console.")
        self.advance()

        if regions_without_gd:
            self.add_finding(
                check_id="LOG-005", title=f"GuardDuty disabled in {len(regions_without_gd)} region(s)",
                severity=Severity.CRITICAL, resource_type="AWS::GuardDuty::Detector",
                resource_id="account", description=f"No threat detection in: {', '.join(regions_without_gd[:5])}",
                remediation="Enable GuardDuty in all regions.")

        if regions_could_not_check:
            self.add_finding(
                check_id="LOG-X01",
                title=f"GuardDuty could not be checked in {len(regions_could_not_check)} region(s)",
                severity=Severity.INFO, resource_type="AWS::GuardDuty::Detector",
                resource_id="account",
                description=(
                    f"Access denied when listing GuardDuty detectors in: "
                    f"{', '.join(regions_could_not_check[:5])}. "
                    f"Required IAM action: guardduty:ListDetectors. "
                    f"These regions were NOT confirmed clean."
                ),
                remediation="Grant guardduty:ListDetectors permission and re-run.",
                details={"result_state": "could_not_check", "regions": regions_could_not_check})

    def _scan_config(self):
        cfg = self.session.client("config", region_name="us-east-1")
        recorders, err = safe_api_call(cfg, "describe_configuration_recorders")
        if err:
            if "access denied" in str(err).lower() or "accessdenied" in str(err).lower():
                self.add_finding(
                    check_id="LOG-X02",
                    title="AWS Config could not be checked: access denied",
                    severity=Severity.INFO,
                    resource_type="AWS::Config::ConfigurationRecorder",
                    resource_id="account",
                    description=(
                        "Access denied when describing Config recorders. "
                        "Required IAM action: config:DescribeConfigurationRecorders. "
                        "Config status was NOT evaluated."
                    ),
                    remediation="Grant config:DescribeConfigurationRecorders permission and re-run.",
                    details={"result_state": "could_not_check"})
            else:
                self.errors.append(f"Config: {err}")
            return
        recorder_list = (recorders or {}).get("ConfigurationRecorders", [])
        self.advance()
        if not recorder_list:
            self.add_finding(
                check_id="LOG-007", title="AWS Config recorder not enabled",
                severity=Severity.HIGH, resource_type="AWS::Config::ConfigurationRecorder",
                resource_id="none", description="No configuration change tracking.",
                remediation="Enable AWS Config to track resource configuration changes.")
            return
        status, _ = safe_api_call(cfg, "describe_configuration_recorder_status")
        for s in (status or {}).get("ConfigurationRecordersStatus", []):
            if not s.get("recording"):
                self.add_finding(
                    check_id="LOG-008", title=f"Config recorder '{s.get('name', '')}' is not recording",
                    severity=Severity.HIGH, resource_type="AWS::Config::ConfigurationRecorder",
                    resource_id=s.get("name", "unknown"),
                    description="Config recorder exists but is stopped.",
                    remediation="Start the Config recorder.")

    def _scan_access_analyzer(self):
        could_not_check_regions = []
        for region in self.regions[:3]:  # Check a few key regions
            aa = self.session.client("accessanalyzer", region_name=region)
            analyzers, err = safe_api_call(aa, "list_analyzers")
            if err:
                if "access denied" in str(err).lower() or "accessdenied" in str(err).lower():
                    could_not_check_regions.append(region)
                else:
                    self.errors.append(f"Access Analyzer ({region}): {err}")
                continue
            if not (analyzers or {}).get("analyzers"):
                self.add_finding(
                    check_id="LOG-009", title=f"IAM Access Analyzer not enabled in {region}",
                    severity=Severity.HIGH, resource_type="AWS::AccessAnalyzer::Analyzer",
                    resource_id="none", region=region,
                    description="No automated detection of external resource access.",
                    remediation="Enable IAM Access Analyzer to detect unintended external access.")
                break  # Only report once

        if could_not_check_regions:
            self.add_finding(
                check_id="LOG-X03",
                title=f"Access Analyzer could not be checked in {len(could_not_check_regions)} region(s)",
                severity=Severity.INFO,
                resource_type="AWS::AccessAnalyzer::Analyzer",
                resource_id="account",
                description=(
                    f"Access denied when listing analyzers in: "
                    f"{', '.join(could_not_check_regions)}. "
                    f"Required IAM action: access-analyzer:ListAnalyzers. "
                    f"Access Analyzer status was NOT confirmed clean."
                ),
                remediation="Grant access-analyzer:ListAnalyzers permission and re-run.",
                details={"result_state": "could_not_check", "regions": could_not_check_regions})
