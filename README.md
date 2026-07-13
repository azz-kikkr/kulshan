# Kulshan 🏔️

**Local-first AWS FinOps baseline. One command, one report.**

```bash
pip install kulshan
kulshan report
```

Kulshan reads your AWS Cost Explorer and produces a local FinOps baseline report. Where is the spend? What changed? What should you investigate next?

No SaaS. No CUR upload. No telemetry. No write access. Apache 2.0.

[![PyPI](https://img.shields.io/pypi/v/kulshan)](https://pypi.org/project/kulshan/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE.txt)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)

---

## What You Get

```
$ kulshan report

  Kulshan FinOps Baseline · 90 days · account 1234****9012

  Cost Drivers:
    Amazon EC2          $14,200/mo  (+12% vs prior period)
    Amazon RDS           $4,800/mo  (stable)
    AWS Lambda           $1,200/mo  (+340% — anomaly detected)

  Savings Opportunities:
    RI/SP coverage: 62% (target: 80%)
    Rightsizing: 4 recommendations, est. $380/mo
    Unused commitments: $120/mo underutilized

  Top Actions:
    1. Investigate Lambda cost spike (+$900/mo, 3 methods agree)
    2. Increase Savings Plans coverage (gap: $2,100/mo on-demand)
    3. Rightsize 2 oversized RDS instances (est. $260/mo)

  Score: 78/100 [B]
  Report written to kulshan-report.html
```

---

## What It Answers

| Question | How |
|----------|-----|
| Where is spend going? | Service, account, region breakdown |
| What changed? | Period-over-period comparison, anomaly detection |
| What should I investigate? | Top actions ranked by estimated impact |
| Am I using commitments well? | RI/SP coverage and utilization |
| Is spend attributed? | Tag compliance, unattributed cost detection |
| What's the forecast? | Cost Explorer projection |

---

## Install

```bash
pip install kulshan       # from PyPI
pipx install kulshan      # isolated (recommended)
```

Requires: Python 3.9+ and AWS credentials.

---

## Quick Commands

```bash
kulshan --version                       # Show version
kulshan doctor                          # Verify credentials and permissions
kulshan report                          # Default FinOps baseline (cost pack)
kulshan report --quick                  # Fast scan (skips confirmation)
kulshan report -o report.html           # Save as HTML
kulshan report --packs all --regions us-east-1  # Full 10-pack diagnostic
kulshan history                         # View past scans
kulshan shell                           # Interactive REPL
```

---

## AWS Credentials

Kulshan uses the same credential chain as the AWS CLI. If `aws sts get-caller-identity` works, Kulshan works.

```bash
# Already configured? Just run it:
kulshan report

# SSO:
aws sso login --profile your-profile
kulshan --profile your-profile report

# Temporary credentials (from Console):
export AWS_ACCESS_KEY_ID=ASIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
kulshan report

# Dedicated role:
kulshan --role-arn arn:aws:iam::123456789012:role/KulshanAudit report
```

---

## AWS API Cost

Kulshan's cost baseline reads AWS Cost Explorer APIs. AWS bills these at $0.01 per request.

| Mode | Expected cost | What happens |
|------|---------------|--------------|
| `kulshan report` (default) | ~$0.15–$0.25 | Cost Explorer queries only |
| `--packs cost,tag` | ~$0.15–$0.25 | Same CE queries + tag APIs (free) |
| `--packs sweep` | $0.00 | Regional EC2/EBS APIs (free tier) |
| `--packs all` | ~$0.15–$0.25 | CE + free regional APIs |

The CLI warns you before running Cost Explorer and asks for confirmation. Use `--yes` for CI/CD.

---

## Default vs. Optional Packs

**Default (runs with `kulshan report`):**

| Pack | What it answers |
|------|----------------|
| `cost` | Where is spend going? What changed? What anomalies exist? |

**FinOps add-ons (opt-in):**

| Pack | What it answers |
|------|----------------|
| `tag` | Is spend attributed? Who owns what? |
| `sweep` | What resources are idle or orphaned? (waste detection) |

**AWS diagnostics (opt-in, not FinOps-specific):**

| Pack | What it checks |
|------|---------------|
| `security` | IAM, encryption, network exposure |
| `dr` | Backup coverage, multi-AZ |
| `age` | EOL runtimes, expiring certs |
| `drift` | CloudFormation drift |
| `pulse` | Alarm coverage, logging gaps |
| `limit` | Service quota headroom |
| `topo` | VPC topology, CIDR overlaps |

```bash
# Default FinOps baseline
kulshan report

# Add tag allocation visibility
kulshan report --packs cost,tag

# Add waste detection
kulshan report --packs cost,tag,sweep --regions us-east-1

# Full diagnostic (all packs, explicit)
kulshan report --packs all --regions us-east-1
```


---

## CUR/Data Export Investigations

Kulshan can investigate cost movements directly from CUR/Data Export Parquet files, both locally and from S3. No Athena, no Glue, no data warehouse required.

### Local Investigation (Recommended)

```bash
# Validate CUR Parquet structure
kulshan cur validate --path ./cur/

# Inspect schema mapping
kulshan cur schema --path ./cur/

# Investigate cost top-movers across all services
kulshan investigate cost --path ./cur/ --month 2024-06

# Investigate EC2-specific movements
kulshan investigate ec2 --cur ./cur/ --month 2024-06

# Export as JSON (for AI agents) or Markdown
kulshan investigate cost --path ./cur/ --month 2024-06 -o report.json
kulshan investigate ec2 --cur ./cur/ --month 2024-06 -o report.md
```

### S3-Native Investigation

```bash
# Check S3 readiness (no data download)
kulshan cur s3-check --s3 s3://bucket/prefix/

# Investigate from S3 via DuckDB httpfs
kulshan investigate cost --s3 s3://bucket/prefix/ --month 2024-06
```

### Evidence Contract

All investigation outputs include a full evidence contract for AI agent trust:

```json
{
  "schema_version": "1.0",
  "kulshan_version": "0.2.2",
  "investigation_type": "cost_top_movers",
  "generated_at": "2024-06-15T10:30:00Z",
  "data_through": "2024-06-14",
  "human_review_required": true,
  "confidence": {
    "label": "medium",
    "data_completeness": "high",
    "ownership_confidence": "low",
    "reason": "CUR data includes service, account, region details; no owner mapping"
  },
  "top_services": [...],
  "evidence": {
    "available": [...],
    "missing": [...]
  }
}
```

Key guarantees:
- `human_review_required: true` — always
- Structured confidence (not a numeric score)
- Evidence items with unique IDs for traceability
- Full provenance on every output

### What Investigations Include

**Cost Investigation (`investigate cost`):**
- Top movers by service, account, region, usage type
- Period-over-period delta with percentages
- Suggested deep dives (e.g., "run investigate ec2" if EC2 is top mover)
- Review questions for finance meetings

**EC2 Investigation (`investigate ec2`):**
- Instance family, region, pricing model breakdowns
- Resource-level contributors
- Tag coverage analysis (owner, team, application tags)
- Owner candidate inference with confirmation required

### Local-Only Onboarding

1. Download CUR Parquet files to `./cur/`
2. Validate: `kulshan cur validate --path ./cur/`
3. Investigate: `kulshan investigate cost --path ./cur/ --month YYYY-MM`

No AWS API calls required for local investigation.
---

## Output Formats

```bash
kulshan report                          # Terminal
kulshan report -o report.html           # HTML (self-contained, shareable)
kulshan report --format json            # JSON (for agents, CI/CD)
kulshan report --format sarif           # SARIF (GitHub Security tab)
kulshan report --format csv             # CSV (spreadsheet)
```

---

## IAM Permissions

| Approach | What to attach |
|----------|---------------|
| **Quickest** | `ViewOnlyAccess` + `AWSBillingReadOnlyAccess` |
| **Minimal** | [`kulshan/iam/kulshan-readonly.json`](kulshan/iam/kulshan-readonly.json) (147 actions) |

The policy contains only `Get*`, `List*`, `Describe*` actions. Zero write actions. Published, auditable, version-controlled.

---

## Trust & Safety

| Guarantee | How to verify |
|-----------|--------------|
| No writes to AWS | [IAM policy](kulshan/iam/kulshan-readonly.json): audit actions only |
| Runs locally | No telemetry, no phone-home code |
| No data upload | Reports write to local files only |
| Open source | Apache 2.0 — read every line |
| Verifiable builds | GitHub Actions → PyPI trusted publishing |

---

## About the Name

Kulshan is the Lummi name for Mt. Baker. Before you climb a mountain, you look at the terrain. Before you change an AWS environment, you should understand it.

---

## Built by

Yuvdeep Singh / [Mission FinOps](https://missionfinops.com) — Mission, BC, Canada.

---

## Links

- **PyPI:** https://pypi.org/project/kulshan/
- **GitHub:** https://github.com/azz-kikkr/kulshan
- **Sample report:** https://missionfinops.com/sample/
- **IAM Policy:** [kulshan/iam/kulshan-readonly.json](kulshan/iam/kulshan-readonly.json)
