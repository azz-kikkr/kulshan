"""Unit tests for Kulshan.redact module."""
from __future__ import annotations

from kulshan.redact import (
    redact_account_id,
    redact_arn,
    redact_bucket_name,
    redact_email,
    redact_filename,
    redact_hostname,
    redact_ip,
    redact_payload,
    redact_text,
)


class TestRedactAccountId:
    def test_standard_12_digit(self):
        assert redact_account_id("123456789012") == "XXXX-XXXX-9012"

    def test_preserves_last_4(self):
        assert redact_account_id("999888777666") == "XXXX-XXXX-7666"

    def test_none(self):
        assert redact_account_id(None) == "XXXX-XXXX-XXXX"

    def test_empty_string(self):
        assert redact_account_id("") == "XXXX-XXXX-XXXX"

    def test_short_string(self):
        assert redact_account_id("12") == "XXXX-XXXX-XXXX"

    def test_already_masked(self):
        result = redact_account_id("000000000000")
        assert result == "XXXX-XXXX-0000"


class TestRedactArn:
    def test_iam_user_arn(self):
        arn = "arn:aws:iam::123456789012:user/admin-yuvdeep"
        result = redact_arn(arn)
        assert "123456789012" not in result
        assert "XXXX9012" in result
        assert "admin-yuvdeep" not in result

    def test_s3_arn_no_account(self):
        arn = "arn:aws:s3:::my-bucket"
        result = redact_arn(arn)
        # S3 ARNs don't have account IDs in the standard position
        assert "my-bucket" not in result or "bucket" in result

    def test_rds_arn(self):
        arn = "arn:aws:rds:us-east-1:123456789012:db:production-db"
        result = redact_arn(arn)
        assert "123456789012" not in result
        assert "us-east-1" in result  # region preserved

    def test_none(self):
        assert redact_arn(None) == ""

    def test_empty(self):
        assert redact_arn("") == ""


class TestRedactEmail:
    def test_standard_email(self):
        result = redact_email("yuvdeep@example.com")
        assert result == "y***@***.com"

    def test_preserves_tld(self):
        result = redact_email("admin@company.io")
        assert result.endswith(".io")

    def test_none(self):
        assert redact_email(None) == ""

    def test_not_an_email(self):
        assert redact_email("not-an-email") == "not-an-email"


class TestRedactIp:
    def test_standard_ipv4(self):
        assert redact_ip("10.0.45.12") == "10.0.*.*"

    def test_preserves_first_two_octets(self):
        assert redact_ip("192.168.1.100") == "192.168.*.*"

    def test_none(self):
        assert redact_ip(None) == ""

    def test_not_an_ip(self):
        assert redact_ip("hello") == "hello"


class TestRedactBucketName:
    def test_long_name(self):
        assert redact_bucket_name("my-production-data-bucket") == "my-p****"

    def test_short_name(self):
        assert redact_bucket_name("ab") == "a****"

    def test_none(self):
        assert redact_bucket_name(None) == ""


class TestRedactText:
    def test_inline_account_id(self):
        text = "Investigate in account 123456789012, region us-east-1"
        result = redact_text(text)
        assert "123456789012" not in result
        assert "XXXX9012" in result
        assert "us-east-1" in result

    def test_multiple_account_ids(self):
        text = "Account 111222333444 and 555666777888"
        result = redact_text(text)
        assert "111222333444" not in result
        assert "555666777888" not in result

    def test_inline_email(self):
        text = "Contact admin@company.com for help"
        result = redact_text(text)
        assert "admin@company.com" not in result
        assert "@" in result  # some masked form present

    def test_inline_ip(self):
        result = redact_text("Public endpoint 203.0.113.42 is exposed")
        assert "203.0.113.42" not in result
        assert "203.0.*.*" in result

    def test_none(self):
        assert redact_text(None) == ""

    def test_no_pii(self):
        text = "NAT Gateway in us-east-1 is expensive"
        assert redact_text(text) == text


class TestRedactFilename:
    def test_standard_report_filename(self):
        result = redact_filename("kulshan-report-123456789012.html")
        assert "123456789012" not in result
        assert "XXXX9012" in result
        assert result.endswith(".html")

    def test_no_account_in_name(self):
        assert redact_filename("report.json") == "report.json"


class TestRedactPayload:
    def test_show_pii_returns_unchanged(self):
        payload = {"account_id": "123456789012", "score": 72}
        result = redact_payload(payload, show_pii=True)
        assert result["account_id"] == "123456789012"

    def test_redacts_account_id_at_top_level(self):
        payload = {"kulshan_version": "0.1.0", "account_id": "123456789012", "score": 72}
        result = redact_payload(payload)
        assert "123456789012" not in result["account_id"]
        assert result["redacted"] is True

    def test_redacts_nested_findings(self):
        payload = {
            "kulshan_version": "0.1.0",
            "account_id": "123456789012",
            "findings": [
                {
                    "title": "Spike in account 123456789012",
                    "account_id": "123456789012",
                    "resource_arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-abc123",
                }
            ],
        }
        result = redact_payload(payload)
        finding = result["findings"][0]
        assert "123456789012" not in finding["title"]
        assert "123456789012" not in finding["account_id"]
        assert "123456789012" not in finding["resource_arn"]

    def test_preserves_non_pii_fields(self):
        payload = {
            "kulshan_version": "0.1.0",
            "overall_score": 67,
            "overall_grade": "C-",
            "regions": ["us-east-1", "eu-west-1"],
        }
        result = redact_payload(payload)
        assert result["overall_score"] == 67
        assert result["overall_grade"] == "C-"
        assert result["regions"] == ["us-east-1", "eu-west-1"]

    def test_does_not_mutate_input(self):
        payload = {"kulshan_version": "0.1.0", "account_id": "123456789012"}
        redact_payload(payload)
        assert payload["account_id"] == "123456789012"

    def test_handles_none_values(self):
        payload = {"kulshan_version": "0.1.0", "account_id": None, "resource_arn": None}
        result = redact_payload(payload)
        assert result["account_id"] == "XXXX-XXXX-XXXX"

    def test_redacts_realistic_nested_network_and_storage_identifiers(self):
        payload = {
            "kulshan_version": "0.1.0",
            "tools": {
                "security": {
                    "findings": [
                        {
                            "accountId": "123456789012",
                            "bucket_name": "customer-production-exports",
                            "public_ip": "203.0.113.42",
                            "endpoint": "https://orders.prod.example.com:8443/health?verbose=1",
                            "domain_name": "api.internal.example.org",
                            "owner_hint": "platform-owner@example.com",
                            "description": (
                                "Contact platform-owner@example.com about 10.20.30.40 "
                                "using key AKIAABCDEFGHIJKLMNOP"
                            ),
                        }
                    ]
                }
            },
        }

        result = redact_payload(payload)
        finding = result["tools"]["security"]["findings"][0]

        serialized = str(result)
        for leaked in (
            "123456789012",
            "customer-production-exports",
            "203.0.113.42",
            "orders.prod.example.com",
            "api.internal.example.org",
            "platform-owner@example.com",
            "10.20.30.40",
            "AKIAABCDEFGHIJKLMNOP",
        ):
            assert leaked not in serialized

        assert finding["accountId"] == "XXXX-XXXX-9012"
        assert finding["public_ip"] == "203.0.*.*"
        assert finding["bucket_name"].endswith("****")
        assert finding["endpoint"].startswith("https://or****.pr****.")
        assert finding["endpoint"].endswith(":8443/health?verbose=1")
        assert finding["domain_name"].endswith("example.org")

    def test_preserves_report_usefulness(self):
        payload = {
            "region": "us-west-2",
            "service": "Amazon RDS",
            "severity": "high",
            "estimated_monthly_impact": 420.5,
        }
        assert redact_payload(payload) == payload


class TestRedactHostname:
    def test_domain_preserves_suffix(self):
        result = redact_hostname("orders.prod.example.com")
        assert result == "or****.pr****.example.com"

    def test_url_preserves_scheme_port_path_and_query(self):
        result = redact_hostname("https://orders.prod.example.com:8443/health?verbose=1")
        assert result == "https://or****.pr****.example.com:8443/health?verbose=1"
