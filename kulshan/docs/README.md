# Kulshan CLI Reference

Version 0.2.4

---

## Overview

Kulshan is a local-first, read-only AWS audit CLI. It reads both AWS Cost Explorer and AWS Cost and Usage Reports (CUR / Data Exports) to generate FinOps baseline reports.

- **Local-first**: All data stays on your machine. No uploads, no SaaS account required.
- **Read-only**: The published IAM policy contains zero actions that create, modify, or delete resources.
- **Evidence posture**: Kulshan detects, collects, and reports evidence. It does not modify your AWS account.

Kulshan produces deterministic, inspectable evidence that humans and AI systems can verify.

---

## Installation

```bash
pip install kulshan
```

Requirements:
- Python 3.9 or later
- AWS credentials configured (see below)
- macOS, Linux, or Windows

---

## AWS Credentials

Kulshan uses the standard AWS credential chain. If `aws sts get-caller-identity` works, Kulshan works.

### SSO

```bash
aws sso login --profile your-profile
kulshan --profile your-profile report
```

### Environment Variables

```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...  # if using temporary credentials
kulshan report
```

### Assume Role

```bash
kulshan --role-arn arn:aws:iam::123456789012:role/KulshanAudit report
```

### Verify Credentials

```bash
kulshan doctor
```

---

## Command Reference

### Global Options

These options apply to all commands:

| Option | Description |
|--------|-------------|
| `--version` | Show version and exit |
| `--profile AWS_PROFILE` | AWS CLI profile name |
| `--role-arn TEXT` | IAM role ARN to assume |
| `--help` | Show help and exit |

---

### Core Commands

#### `kulshan report`

Run a FinOps baseline using AWS Cost Explorer.

```bash
kulshan report [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--quick` | Fast baseline, skips confirmation |
| `--format [terminal\|json\|html\|sarif\|csv]` | Output format |
| `-o, --output PATH` | Write output to file |
| `--days INTEGER` | Cost analysis lookback (1-365 days, default: 90) |
| `--show-pii` | Show full account IDs and PII in exported reports |
| `-y, --yes` | Skip confirmations (for CI/CD) |
| `--packs TEXT` | Packs to run: cost,security,sweep,dr,age,drift,tag,pulse,limit,topo or "all" |
| `--regions TEXT` | Regions to scan (comma-separated, default: 3 for inventory packs) |
| `--no-history` | Do not retain this scan in local history |
| `--perf` | Show pack and AWS API timing details after the scan |
| `--deep` | Run expensive deep checks instead of the fast default path |

**Examples:**

```bash
# Default cost baseline
kulshan report

# Save as HTML
kulshan report -o report.html

# Run security and sweep packs in us-east-1
kulshan report --packs security,sweep --regions us-east-1

# Full 10-pack scan, skip confirmation
kulshan report --packs all --regions us-east-1 --yes

# JSON output for CI/CD
kulshan report --format json -o results.json --yes
```

**Output:**

Terminal output shows scores, findings by severity, and a summary. File outputs depend on format:
- HTML: Self-contained report with charts
- JSON: Structured findings with all fields (including `recommended_action`, `severity`, `confidence`, `fingerprint`)
- SARIF: GitHub Security tab compatible
- CSV: Spreadsheet-friendly

---

#### `kulshan doctor`

Check AWS connectivity and readiness without running a scan.

```bash
kulshan doctor
```

No options. Validates:
- AWS credentials are configured
- STS caller identity resolves
- Cost Explorer API is reachable
- Required permissions are present

No data is written. No cost is incurred. Safe to run repeatedly.

**Example output:**

```
Kulshan Doctor: checking AWS readiness
Pre-flight checks
  OK  Python 3.12
  OK  AWS credentials found
  OK  Authenticated (account 123456789012)
  OK  Cost Explorer API accessible
  OK  EC2 read access (security, sweep, dr packs)
      Organizations not available (single-account mode)
Ready. Cost baseline will run. Inventory packs available with --packs.
```

---

#### `kulshan history`

Show past scan history with scores and trends.

```bash
kulshan history [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `-n, --limit INTEGER` | Number of past scans to show |
| `--show-pii` | Show full account IDs (redacted by default) |
| `--account TEXT` | Filter by AWS account ID (must be exactly 12 digits) |

**Note:** The account ID stored in history is the credential account from `sts:GetCallerIdentity` at scan time. This is the account whose credentials were used to run the scan, not necessarily the payer account or linked accounts being analyzed.

**Examples:**

```bash
# Show all history
kulshan history

# Filter by account
kulshan history --account 123456789012

# Show more results with full account IDs
kulshan history --limit 50 --show-pii
```

---

#### `kulshan delete-history`

Permanently delete all locally stored scan history.

```bash
kulshan delete-history [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--yes` | Delete without interactive confirmation |

---

#### `kulshan convert`

Re-render a previous JSON scan into a different format (no re-scan needed).

```bash
kulshan convert [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `-i, --input PATH` | Path to a previous JSON scan result (required) |
| `--format [terminal\|html\|json\|sarif\|csv]` | Output format to convert to |
| `-o, --output PATH` | Write output to file |
| `--show-pii` | Show full account IDs and PII |

**Example:**

```bash
kulshan convert -i previous-scan.json --format html -o report.html
```

---

#### `kulshan init`

Generate a starter config.toml in the current directory.

```bash
kulshan init [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--force` | Overwrite existing config file |

---

#### `kulshan shell`

Launch interactive REPL shell.

```bash
kulshan shell
```

---

#### `kulshan setup-completion`

Print shell completion script for Kulshan.

```bash
kulshan setup-completion [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--shell [bash\|zsh\|fish\|powershell]` | Shell type (auto-detected from $SHELL if not provided) |

---

### CUR Commands

Commands for inspecting AWS Cost and Usage Reports (CUR) and Data Exports.

#### `kulshan cur validate`

Validate local Parquet or S3 manifest/schema evidence.

```bash
kulshan cur validate [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--path PATH` | Local CUR/Data Exports Parquet file or directory |
| `--s3 TEXT` | S3 CUR/Data Export prefix for manifest/schema validation |
| `--month TEXT` | Billing month for S3 manifest validation (YYYY-MM format) |

**Examples:**

```bash
# Validate local Parquet
kulshan cur validate --path ./cur/

# Validate S3 manifest
kulshan cur validate --s3 s3://my-bucket/cur-prefix/ --month 2024-06
```

---

#### `kulshan cur schema`

Show the resolved schema mapping for local CUR Parquet data.

```bash
kulshan cur schema --path PATH
```

**Options:**

| Option | Description |
|--------|-------------|
| `--path PATH` | Local CUR/Data Exports Parquet file or directory (required) |

---

#### `kulshan cur s3-check`

Check S3 CUR/Data Export readiness without downloading data.

```bash
kulshan cur s3-check --s3 TEXT
```

**Options:**

| Option | Description |
|--------|-------------|
| `--s3 TEXT` | S3 CUR/Data Export prefix to check (required) |

**Example:**

```bash
kulshan cur s3-check --s3 s3://my-bucket/cur-prefix/
```

---

### Investigation Commands

Commands for investigating specific cost movements from CUR/Data Export evidence.

#### `kulshan investigate cost`

Investigate generic monthly cost movement from CUR/Data Export evidence.

```bash
kulshan investigate cost [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--s3 TEXT` | S3 CUR/Data Export prefix to query with DuckDB httpfs |
| `--path PATH` | Local CUR/Data Export Parquet file or directory |
| `--month TEXT` | Billing month to investigate (YYYY-MM format, required) |
| `--confirm-scan` | Confirm S3 Parquet scan when estimate exceeds threshold |
| `-o, --output PATH` | Write investigation output to .json or .md |

**Examples:**

```bash
# Local investigation
kulshan investigate cost --path ./cur/ --month 2024-06

# S3 investigation
kulshan investigate cost --s3 s3://my-bucket/cur-prefix/ --month 2024-06

# Export as JSON
kulshan investigate cost --path ./cur/ --month 2024-06 -o report.json
```

---

#### `kulshan investigate ec2`

Produce a local EC2 investigation brief from Parquet CUR data.

```bash
kulshan investigate ec2 [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--cur PATH` | Local CUR/Data Exports Parquet file or directory (required) |
| `--month TEXT` | Current billing month to investigate (YYYY-MM format, defaults to latest) |
| `-o, --output PATH` | Write investigation output to .json or .md |

**Example:**

```bash
kulshan investigate ec2 --cur ./cur/ --month 2024-06 -o ec2-report.md
```

---

### Agent Commands

#### `kulshan mcp-serve`

Serve Kulshan MCP tools over stdio for agent integration.

```bash
kulshan mcp-serve
```

No options. Starts an MCP server that exposes Kulshan tools to compatible agents.

---

## Output Formats

### Terminal

Default output. Color-coded findings with severity indicators.

### HTML

Self-contained HTML report with charts. Can be opened in any browser and shared.

### JSON

Structured findings with all fields. Fields include:

- `severity`: critical, high, medium, low, info
- `title`: Finding title
- `description`: Detailed description
- `resource_id`, `resource_arn`: Affected resource
- `recommended_action`: Suggested next step (field name may change in a future release)
- `confidence`: Confidence assessment
- `fingerprint`: Unique identifier for deduplication
- `compliance_frameworks`: Relevant compliance mappings (CIS, SOC2, NIST)

### SARIF

Static Analysis Results Interchange Format. Compatible with GitHub Security tab and VS Code SARIF Viewer.

### CSV

Spreadsheet-friendly export with one row per finding.

---

## MCP Integration

Kulshan exposes its findings to MCP-compatible agents. The server returns deterministic evidence; the agent may reason over it.

### Configuration

Add to your MCP client configuration:

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

### Available Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `kulshan_doctor` | (none) | Check AWS caller identity using the default credential chain |
| `kulshan_report` | `packs: str = "cost"`, `days: int = 90`, `regions: str \| None` | Run selected audit packs and return compact findings |
| `kulshan_quick_security` | `region: str = "us-east-1"` | Fast security scan of a single region, returns critical/high findings only |
| `kulshan_list_packs` | (none) | List all available audit packs with descriptions |
| `kulshan_cur_validate` | `cur_path: str` | Validate local CUR/Data Export Parquet readability |
| `kulshan_investigate_ec2` | `cur_path: str`, `month: str \| None` | Investigate EC2 cost movement from local CUR |
| `kulshan_investigate_cost` | `s3_uri: str`, `month: str` | Investigate monthly cost from S3 CUR |

### Tool Output

Tools return JSON with:

- `status`: "ok" or error information
- `findings`: Array of compact finding objects
- Finding fields: `severity`, `title`, `service`, `dollar_impact`, `recommendation`

Kulshan produces deterministic, inspectable evidence that humans and AI systems can verify.

---

## IAM Policy

Kulshan requires read-only access to AWS APIs. The published policy contains:

- 147 read-only actions
- Zero actions that create, modify, or delete resources
- Every action is read-level per the AWS service authorization reference

[View the full policy](../iam/kulshan-readonly.json)

---

## Cost of Running

AWS Cost Explorer API requests cost $0.01 each. A typical Kulshan run costs approximately $0.15 to $0.25.

All non-cost packs (security, sweep, dr, age, drift, tag, pulse, limit, topo) use free AWS APIs.

The CLI asks for confirmation before running Cost Explorer queries. Use `--yes` to skip for CI/CD.

---

## Links

- [GitHub](https://github.com/azz-kikkr/kulshan)
- [PyPI](https://pypi.org/project/kulshan/)
- [IAM Policy](../iam/kulshan-readonly.json)
- [Mission FinOps](https://missionfinops.com)
