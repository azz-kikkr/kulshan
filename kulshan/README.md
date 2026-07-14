# Kulshan

The blood test for your AWS bill.

Generate a local AWS audit report in minutes.

```bash
pip install kulshan
kulshan report
```

No setup. No data uploads. No infrastructure changes.

Just your AWS account and your laptop.

[![PyPI](https://img.shields.io/pypi/v/kulshan)](https://pypi.org/project/kulshan/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE.txt)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)

[Documentation](docs/README.md) | [IAM Policy](iam/kulshan-readonly.json) | [GitHub](https://github.com/azz-kikkr/kulshan)

---

## What is Kulshan?

Kulshan reads your AWS account (Cost Explorer and your own CUR/Data Export files) and generates a business-ready report covering:

- Cost anomalies and trends
- Waste and orphaned resources
- Tag compliance and cost attribution
- Commitment health (RI/SP coverage)
- Spend forecasting and acceleration
- Security posture
- DR readiness

Reads Cost Explorer and your own CUR data in place. No data leaves your environment.

Think of it as a baseline before deeper FinOps work, platform evaluations, or leadership reviews.

---

## What You Get

An HTML report you can open in a browser and hand to your VP, CFO, or platform team. Also available as JSON, SARIF, and CSV.

The report scores your account 0-100 across each dimension, highlights the top findings by dollar impact, and provides an executive summary paragraph.

---

## Install

```bash
pip install kulshan
```

Requires Python 3.9+. macOS, Linux, Windows.

---

## AWS Credentials

Kulshan uses your existing AWS CLI credentials. If `aws sts get-caller-identity` works, Kulshan works.

```bash
# SSO
aws sso login --profile your-profile
kulshan --profile your-profile report

# Environment variables
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
kulshan report

# Assume role
kulshan --role-arn arn:aws:iam::123456789012:role/KulshanAudit report
```

---

## Quick Commands

```bash
kulshan --version                       # Show version
kulshan doctor                          # Verify credentials and permissions
kulshan report                          # Default FinOps baseline (cost pack)
kulshan report --quick                  # Fast scan (skips confirmation)
kulshan report -o report.html           # Save as HTML
kulshan report --packs security,sweep   # Run specific packs
kulshan report --packs all --regions us-east-1  # Full 10-pack diagnostic
kulshan history                         # View past scans
kulshan shell                           # Interactive REPL
```

---

## All 10 Audit Packs

| Pack | What it detects |
|------|-----------------|
| `cost` | Cost trends, anomalies, commitment gaps (default) |
| `security` | IAM misconfigurations, encryption gaps, network exposure |
| `sweep` | Orphaned and idle resources (waste detection) |
| `dr` | Backup coverage gaps, multi-AZ gaps, single points of failure |
| `age` | EOL runtimes, expiring certificates, stale resources |
| `drift` | CloudFormation drift, IaC coverage gaps |
| `tag` | Tag compliance violations, unattributed spend |
| `pulse` | Observability gaps, missing alarms |
| `limit` | Service quota headroom issues |
| `topo` | VPC topology issues, CIDR overlaps, route integrity problems |

```bash
kulshan report --packs cost,security,sweep --regions us-east-1
kulshan report --packs all --regions us-east-1,us-west-2
```

---

## CUR / Data Export Investigations

Kulshan can investigate cost movements directly from AWS Cost and Usage Report (CUR) or Data Export Parquet files, both locally and from S3. No Athena, no Glue, no data warehouse required. DuckDB queries your data in place.

### Validate and Inspect

```bash
# Validate CUR Parquet structure
kulshan cur validate --path ./cur/

# Validate S3 manifest
kulshan cur validate --s3 s3://bucket/prefix/ --month 2024-06

# Inspect schema mapping
kulshan cur schema --path ./cur/

# Check S3 readiness (no data download)
kulshan cur s3-check --s3 s3://bucket/prefix/
```

### Investigate Cost Movements

```bash
# Local investigation
kulshan investigate cost --path ./cur/ --month 2024-06

# S3 investigation (queries in place via DuckDB httpfs)
kulshan investigate cost --s3 s3://bucket/prefix/ --month 2024-06

# EC2-specific investigation
kulshan investigate ec2 --cur ./cur/ --month 2024-06

# Export as JSON or Markdown
kulshan investigate cost --path ./cur/ --month 2024-06 -o report.json
kulshan investigate ec2 --cur ./cur/ --month 2024-06 -o report.md
```

### What Investigations Include

**Cost Investigation:** Top movers by service, account, region, usage type. Period-over-period delta with percentages. Suggested next steps for deeper analysis.

**EC2 Investigation:** Instance family, region, and pricing model breakdowns. Resource-level contributors. Tag coverage analysis.

All investigation outputs include structured evidence with unique IDs, schema version, timestamps, and `human_review_required: true`.

---

## MCP Server (Agent Integration)

Kulshan exposes its findings to MCP-compatible agents (Claude Desktop, Cursor, Kiro, and others). The agent may reason over that evidence; Kulshan itself returns deterministic, inspectable evidence.

```bash
kulshan mcp-serve
```

### MCP Configuration

Add to your MCP client configuration (e.g., `.kiro/settings/mcp.json` or Claude Desktop config):

```json
{
  "mcpServers": {
    "kulshan": {
      "command": "kulshan",
      "args": ["mcp-serve"]
    }
  }
}
```

### Available MCP Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `kulshan_doctor` | (none) | Check AWS caller identity |
| `kulshan_report` | `packs`, `days`, `regions` | Run audit packs and return findings |
| `kulshan_quick_security` | `region` | Fast security scan of a single region |
| `kulshan_list_packs` | (none) | List available audit packs |
| `kulshan_cur_validate` | `cur_path` | Validate local CUR Parquet |
| `kulshan_investigate_ec2` | `cur_path`, `month` | Investigate EC2 costs from local CUR |
| `kulshan_investigate_cost` | `s3_uri`, `month` | Investigate costs from S3 CUR |

Kulshan produces deterministic, inspectable evidence that humans and AI systems can verify.

---

## Output Formats

```bash
kulshan report                          # Terminal
kulshan report -o report.html           # HTML (self-contained, shareable)
kulshan report --format json            # JSON
kulshan report --format sarif           # SARIF (GitHub Security tab compatible)
kulshan report --format csv             # CSV (spreadsheet)
```

Convert a previous scan to a different format without re-running:

```bash
kulshan convert -i previous-scan.json --format html -o report.html
```

---

## Trust and Security

Read-only by design. No write permissions required. Published IAM policy included.

> Kulshan is read-only by construction, not read-only by default. There is no cleanup mode to leave off, no write path to enable, and no telemetry to opt out of. Nothing to disable, because nothing exists. The published IAM policy contains zero actions that create, modify, or delete resources. Read every line, verify everything.

- Every action in the published policy is read-level per the AWS service authorization reference
- 147 read-only actions, zero write actions
- Reports stay on your machine, no uploads
- No telemetry, no phone-home
- Open source: Apache 2.0

[View the IAM policy](iam/kulshan-readonly.json)

---

## AWS API Cost

Typical run cost: approximately $0.15 to $0.25 in AWS Cost Explorer API charges. AWS bills Cost Explorer requests at $0.01 per request. All non-cost packs use free AWS APIs.

The CLI asks for confirmation before running Cost Explorer queries. Use `--yes` for CI/CD.

---

## Performance

Kulshan uses parallel execution for audit packs and region scanning. A full 10-pack scan completes substantially faster than sequential execution.

---

## About the Name

Kulshan is the Lummi name for the mountain known colonially as Mt. Baker, meaning "great white watcher." We acknowledge the Lummi and Nooksack peoples as the original namers of this mountain.

---

## Built by

[Mission FinOps](https://missionfinops.com) | Mission, BC, Canada

For questions about what Kulshan detects, or for help investigating and explaining findings, contact Mission FinOps.

---

## Links

- [Documentation](docs/README.md)
- [IAM Policy](iam/kulshan-readonly.json)
- [GitHub](https://github.com/azz-kikkr/kulshan)
- [PyPI](https://pypi.org/project/kulshan/)

---

## License

Apache 2.0. Free and open source forever.
