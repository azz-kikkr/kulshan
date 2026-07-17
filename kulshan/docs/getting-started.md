# Getting Started

This guide covers installation, credential setup, and running your first Kulshan report.

---

## Prerequisites

- Python 3.9 or later
- AWS credentials configured (any method supported by the AWS SDK)
- macOS, Linux, or Windows

---

## Installation

### From PyPI (recommended)

```bash
pip install kulshan
```

### From source

```bash
git clone https://github.com/MissionFinOps/kulshan.git
cd kulshan
pip install -e .
```

### Optional extras

```bash
# PDF export support
pip install kulshan[pdf]

# Excel export
pip install kulshan[excel]

# PowerPoint export
pip install kulshan[pptx]

# MCP server for agent integration
pip install kulshan[mcp]

# Everything
pip install kulshan[all]
```

### Verify installation

```bash
kulshan --version
```

---

## AWS Credentials

Kulshan uses the standard AWS credential chain. Any method that makes `aws sts get-caller-identity` work will work with Kulshan.

### AWS Identity Center (SSO)

```bash
aws login
kulshan report
```

Or with a named profile:

```bash
aws sso login --profile your-profile
kulshan --profile your-profile report
```

### Environment variables

```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...    # if using temporary credentials
kulshan report
```

### Named profile

```bash
kulshan --profile production report
```

### Role assumption

```bash
kulshan --role-arn arn:aws:iam::123456789012:role/KulshanAudit report
```

### Verify credentials

```bash
kulshan preflight
```

Preflight validates:
- AWS credentials are configured
- STS caller identity resolves
- Cost Explorer API is reachable
- Required permissions are present

No data is written. No cost is incurred. Safe to run repeatedly.

---

## Your First Report

### Default cost baseline

```bash
kulshan report
```

This runs the `cost` pack against AWS Cost Explorer with a 90-day lookback. Typical cost: ~$0.15 in CE API charges. The CLI asks for confirmation before making API calls.

### Save as HTML

```bash
kulshan report -o report.html
```

Produces a self-contained HTML file you can open in any browser and share with stakeholders.

### Skip confirmation (CI/CD)

```bash
kulshan report --yes --format json -o results.json
```

### Run multiple packs

```bash
# Cost + security + waste detection
kulshan report --packs cost,security,sweep --regions us-east-1

# All 10 packs
kulshan report --packs all --regions us-east-1
```

---

## What Happens on First Run

When you run `kulshan report` for the first time with valid AWS credentials, Kulshan:

1. Calls `sts:GetCallerIdentity` to identify your AWS principal
2. Creates a local environment named after your role or user (e.g., `readonlyrole-cedar`)
3. Runs the selected audit packs
4. Stores the scan in local history
5. Outputs the report

```text
✓ Created environment readonlyrole-cedar
  Using readonlyrole-cedar · account 1234…5678

  Running cost baseline (90 days)…
```

Future runs with the same identity reuse the same environment automatically. Different identities get separate environments. No manual setup required.

---

## Automatic Environment Isolation

Kulshan maintains separate local databases per AWS identity. This means:

- Consultants can switch between client accounts without data mixing
- History, findings, and scores are isolated per identity
- No `--workspace` flags needed for the common case

See [Workspaces](workspaces.md) for advanced multi-environment management.

---

## Understanding the Output

### Terminal output

The terminal report shows:

- Overall score (0–100) with letter grade
- Per-pack scores and finding counts
- Top findings by severity and dollar impact
- Suggested next steps

### Scores

| Grade | Score Range |
|-------|-------------|
| A+    | 97–100      |
| A     | 90–96       |
| B     | 80–89       |
| C     | 70–79       |
| D     | 60–69       |
| F     | 0–59        |

### Severity levels

| Severity | Meaning |
|----------|---------|
| critical | Immediate action required — active security exposure or major cost leak |
| high     | Should be addressed soon — significant risk or waste |
| medium   | Plan to address — moderate impact |
| low      | Awareness — minor or cosmetic |
| info     | Informational — no action required |

---

## Next Steps

- [CLI Reference](cli-reference.md) — all commands and options
- [Audit Packs](audit-packs.md) — what each pack detects
- [Output Formats](output-formats.md) — export as HTML, JSON, SARIF, CSV
- [Workspaces](workspaces.md) — multi-environment management
- [CI/CD Integration](ci-cd.md) — automate with GitHub Actions or GitLab CI
