"""Shared boto3 session for all tool adapters."""
from __future__ import annotations

from typing import List, Optional

import boto3
from botocore.config import Config

from kulshan.errors import SessionError

# Sane defaults: don't let a single slow endpoint stall the scan.
BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=15,
    retries={"max_attempts": 2, "mode": "standard"},
)


def create_session(
    profile: Optional[str] = None,
    role_arn: Optional[str] = None,
    region: Optional[str] = None,
) -> boto3.Session:
    try:
        kwargs: dict = {}
        if profile:
            kwargs["profile_name"] = profile
        if region:
            kwargs["region_name"] = region
        session = boto3.Session(**kwargs)

        if role_arn:
            sts = session.client("sts")
            creds = sts.assume_role(
                RoleArn=role_arn, RoleSessionName="kulshan-scan"
            )["Credentials"]
            session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
        return session
    except Exception as e:
        raise SessionError(f"Failed to create AWS session: {e}") from e


def get_account_id(session: boto3.Session) -> str:
    sts = session.client("sts")
    return sts.get_caller_identity()["Account"]


def get_enabled_regions(session: boto3.Session) -> List[str]:
    try:
        ec2 = session.client("ec2", region_name="us-east-1", config=BOTO_CONFIG)
        resp = ec2.describe_regions(
            Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required", "opted-in"]}]
        )
        return sorted(r["RegionName"] for r in resp["Regions"])
    except Exception:
        import logging
        logging.getLogger("kulshan.session").warning(
            "Region enumeration failed, using fallback: us-east-1, us-west-2, eu-west-1"
        )
        return ["us-east-1", "us-west-2", "eu-west-1"]
