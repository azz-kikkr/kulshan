# Security, Trust & IAM Policy

Kulshan is read-only by construction. This document covers the security model, IAM policy, data handling, and how to verify all of it.

---

## Read-Only by Construction

There is no write mode to enable, no cleanup command to accidentally run, no configuration that could cause modifications.

- 147 read-only actions (Get, List, Describe). Zero write actions.
- Every action verified against the [AWS Service Authorization Reference](https://docs.aws.amazon.com/service-authorization/latest/reference/)
- No code path in Kulshan calls a write API
- No telemetry, no phone-home, no crash analytics, no update checks
- Reports stay on your local filesystem. Nothing leaves your machine.

---

## IAM Policy

The complete policy: [`iam/kulshan-readonly.json`](../iam/kulshan-readonly.json)

| Property | Value |
|----------|-------|
| Total actions | 147 |
| Write actions | 0 |
| Services covered | 27 |
| Resource scope | `*` |

### Services by pack

| Service | Actions | Packs |
|---------|---------|-------|
| Cost Explorer | 11 | cost, tag |
| EC2 | 24 | security, sweep, dr, age, limit, topo |
| IAM | 16 | security |
| S3 | 12 | security |
| RDS | 6 | sweep, dr, age |
| CloudFormation | 5 | drift |
| KMS | 5 | security |
| Lambda | 5 | age, pulse, limit |
| Access Analyzer | 4 | security |
| AWS Backup | 4 | dr |
| CloudTrail | 4 | security |
| DynamoDB | 4 | dr |
| ECS | 4 | dr, age |
| ELB | 4 | sweep |
| GuardDuty | 4 | security |
| Service Quotas | 4 | limit |
| AWS Config | 3 | security |
| CloudWatch | 3 | sweep, pulse |
| Resource Groups Tagging | 3 | tag |
| Route 53 | 3 | age |
| ACM | 2 | age |
| ECR | 2 | sweep |
| EKS | 2 | age |
| ElastiCache | 2 | dr |
| CloudWatch Logs | 2 | pulse |
| Auto Scaling | 1 | dr |
| Organizations | 1 | multi-account |
| SNS | 1 | pulse |
| STS | 1 | identity |
| X-Ray | 1 | pulse |

### Per-pack policies (least-privilege)

Available at [`iam/per-check/`](../iam/per-check/) — one JSON file per pack for teams that want minimum permissions for specific scans only.

---

## Deploying the IAM Role

### Dedicated role (recommended)

```bash
aws iam create-role \
  --role-name KulshanAudit \
  --assume-role-policy-document file://trust-policy.json

aws iam put-role-policy \
  --role-name KulshanAudit \
  --policy-name KulshanReadOnly \
  --policy-document file://iam/kulshan-readonly.json
```

Trust policy for cross-account access:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::YOUR_ACCOUNT:root" },
    "Action": "sts:AssumeRole",
    "Condition": { "StringEquals": { "sts:ExternalId": "kulshan-audit" } }
  }]
}
```

Then: `kulshan --role-arn arn:aws:iam::TARGET:role/KulshanAudit report`

### Existing user/role

```bash
aws iam put-user-policy \
  --user-name your-user \
  --policy-name KulshanReadOnly \
  --policy-document file://iam/kulshan-readonly.json
```

### Multi-account (Organizations)

Deploy via StackSet across member accounts. Use role assumption per account.

### Verify permissions

```bash
kulshan preflight
```

Missing permissions degrade gracefully (partial results, not hard failures).

---

## Data Residency

| Data | Location | Lifetime |
|------|----------|----------|
| Scan results | Local filesystem | Until you delete |
| History database | User data directory | Pruned after 365 days |
| Workspace configs | User data directory | Until you delete |
| Reports (HTML/JSON/SARIF/CSV) | Where you write them | Your control |
| AWS API responses | In-memory only | Discarded after scan |

No data is written to S3, DynamoDB, CloudWatch, or any other AWS service.

---

## Threat Model

**What Kulshan can read:** Cost Explorer billing summaries, resource metadata (instance types, SG rules), configuration state (IAM policies, bucket settings), CloudFormation templates.

**What Kulshan cannot access:** Object contents, application data, secrets, database records, log events.

| Vector | Mitigation |
|--------|------------|
| Compromised binary | Install from PyPI with hash verification, or audit source |
| Supply chain | Standard packages (boto3, click, rich, duckdb, pandas) pinned in pyproject.toml |
| Credential theft | Uses standard AWS SDK chain; no additional storage |
| Output manipulation | Deterministic fingerprints; re-running produces identical results |

---

## Credential Handling & Redaction

Kulshan does not store credentials on disk, cache tokens beyond the process, or log credential values. Exported reports redact account IDs to `XXXX-XXXX-5678` by default (`--show-pii` for full IDs).

---

## Compliance Mapping

Findings include `compliance_frameworks` metadata:

| Framework | Coverage |
|-----------|----------|
| CIS AWS Foundations | Security pack checks |
| SOC 2 | Security, DR, logging |
| NIST 800-53 | Security, access controls |
| AWS Well-Architected | Cost, security, reliability |

Informational only — not a compliance certification tool.

---

## Responsible Disclosure

Email **security@missionfinops.com** with vulnerability details. Do not open public issues. 72-hour acknowledgment SLA. Reporters credited unless anonymity requested.

---

## Notes

- `cloudformation:DetectStackDrift` triggers async comparison, not modification. AWS documents it as read.
- Cost Explorer API: $0.01/request, ~$0.15/run. Kulshan confirms before calling. Use `--yes` in CI/CD.
- IAM policy file dual-licensed: Apache 2.0 + CC BY 4.0 (standalone use).
