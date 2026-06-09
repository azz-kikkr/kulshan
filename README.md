# Kulshan 🏔️

**Free, open-source AWS audit CLI with no infrastructure mutation or remediation.**

```bash
pipx install kulshan        # recommended (isolated)
# or
pip install kulshan         # classic
```

```bash
kulshan report --quick      # scan your AWS account in under 5 minutes
```

[![PyPI](https://img.shields.io/pypi/v/kulshan)](https://pypi.org/project/kulshan/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE.txt)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)

---

## What It Does

10 non-mutating audit packs. One command. One score. Processing is local-first by default.

```
$ kulshan report --quick

  Overall Score: 62 / 100 [C]

  Cost       72/100 [B]    Security   48/100 [D]
  Sweep      81/100 [B]    DR         38/100 [D]
  Age        65/100 [C]    Drift      90/100 [A]
  Tag        34/100 [F]    Pulse      55/100 [C]
  Limit      88/100 [A]    Topo       70/100 [B]

  Top Actions:
  1. Enable Multi-AZ on prod-db (DR +15pts, ~$0/mo)
  2. Fix 4 unencrypted RDS instances (Security)
  3. Delete 23 orphaned EBS volumes (Sweep, ~$840/mo savings)
```

| Pack | What it checks |
|------|---------------|
| `cost` | Anomaly detection (z-score, IQR, MAD), RI/SP coverage, forecasting |
| `security` | IAM, network exposure, encryption, logging, public access |
| `sweep` | Orphaned EBS, EIPs, snapshots, idle ALBs, NAT gateways |
| `dr` | Backup coverage, Multi-AZ, single points of failure |
| `age` | EOL runtimes, expiring certificates, stale AMIs |
| `drift` | CloudFormation drift, IaC coverage gaps |
| `tag` | Tag compliance, unattributed spend |
| `pulse` | Alarm coverage, logging gaps, blind spots |
| `limit` | Service quota headroom, scaling blockers |
| `topo` | VPC topology, CIDR overlaps, route integrity |

Output: terminal, JSON, HTML (self-contained), SARIF (CI/CD), CSV.

---

## Install

```bash
# Recommended: isolated install via pipx
pipx install kulshan

# Or classic pip
pip install kulshan

# From source
git clone https://github.com/azz-kikkr/kulshan.git
cd kulshan
pip install -e kulshan
```

Requires: Python 3.9+ and AWS credentials scoped to the published audit policy.

---

## AWS Credentials

Kulshan uses the same credential chain as the AWS CLI. If `aws sts get-caller-identity` works in your terminal, Kulshan will work too.

### Quickest: Use your existing AWS CLI configuration

If you already have the AWS CLI configured (via `aws configure`, SSO, or `aws login`), just run:

```bash
kulshan report --quick
```

No additional setup needed. Kulshan picks up whatever credentials the AWS CLI uses.

### Authenticate via browser (`aws login`)

Requires AWS CLI v2.32+. No access keys needed — uses short-lived temporary credentials.

```bash
aws login
```

Browser opens → log in with your normal console credentials (MFA included) → click Allow → done.

```bash
kulshan report --quick
```

### Authenticate via SSO / Identity Center

```bash
aws sso login --profile your-profile
kulshan --profile your-profile report --quick
```

### Temporary keys from the Console

If `aws login` is blocked by your organization:

1. Log into the AWS Console
2. Click your name (top right) → "Command line or programmatic access"
3. Copy the temporary credentials:

**Windows (cmd):**
```cmd
set AWS_ACCESS_KEY_ID=ASIA...
set AWS_SECRET_ACCESS_KEY=...
set AWS_SESSION_TOKEN=...
```

**Linux/macOS:**
```bash
export AWS_ACCESS_KEY_ID=ASIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
```

These expire when your session ends. **Never use long-term access keys.**

### Assume a dedicated role

```bash
kulshan --role-arn arn:aws:iam::123456789012:role/KulshanAudit report --quick
```

### Verify your access

```bash
aws sts get-caller-identity
```

---

## IAM Permissions

### Minimum permissions (attach to your IAM user or role)

| Approach | What to attach |
|----------|---------------|
| **Quickest** | AWS managed policies: `ViewOnlyAccess` + `SecurityAudit` + `AWSBillingReadOnlyAccess` |
| **Minimal** | Our published policy: [`kulshan/iam/kulshan-readonly.json`](kulshan/iam/kulshan-readonly.json) (147 actions) |
| **Per-pack** | Individual policies in [`kulshan/iam/per-check/`](kulshan/iam/per-check/) |

### Enterprise-safe setup (permissions boundary)

For production AWS accounts, use the Kulshan policy as **both** the permission policy AND a [permissions boundary](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html). This limits the credentials to Kulshan's published non-mutating audit actions even if someone attaches additional policies by mistake.

**Steps:**
1. Go to **IAM → Policies → Create policy**
2. Paste the JSON from [`kulshan/iam/kulshan-readonly.json`](kulshan/iam/kulshan-readonly.json)
3. Name it `KulshanReadOnly` → Create
4. Create an IAM user (or role) for Kulshan
5. Attach `KulshanReadOnly` as the **permission policy**
6. Also select `KulshanReadOnly` as the **permissions boundary**

The boundary ensures that attaching `AdministratorAccess` later does not grant actions outside the 147-action Kulshan audit policy.

### What the policy contains

- **147 actions** across 30 AWS services
- Primarily `Get*`, `List*`, and `Describe*`, plus non-mutating `cloudformation:DetectStackDrift`
- **Zero** write actions: no `Put*`, `Create*`, `Update*`, `Delete*`, `Modify*`
- Published, auditable, version-controlled: [`kulshan/iam/kulshan-readonly.json`](kulshan/iam/kulshan-readonly.json)

---

## Quick Start

```bash
kulshan report --quick                    # Quick scan (3 regions)
kulshan report --format html -o report.html  # Full HTML report
kulshan report --packs security,sweep     # Only free packs ($0 AWS cost)
kulshan shell                             # Interactive REPL
kulshan history                           # View past scans
kulshan convert -i scan.json --format csv # Re-render without re-scanning
```

---

## Trust & Security

**Why should you trust this tool with your AWS account?**

| Guarantee | How to verify |
|-----------|--------------|
| **No infrastructure mutation** | Inspect the [IAM policy](kulshan/iam/kulshan-readonly.json): audit actions only; no remediation or resource mutation |
| **Local-first by default** | Scans and reports run locally; no telemetry implementation is active |
| **Explicit integrations** | Optional webhooks send selected data externally only when explicitly invoked with a destination URL |
| **Open source** | Apache 2.0 — read every line of code right here on GitHub |
| **Verifiable builds** | PyPI package built from this repo via GitHub Actions (tag → build → publish) |
| **Sensitive-data masking** | Common identifiers are masked by default; review every report before sharing |

The policy primarily uses `Get*`, `List*`, and `Describe*` actions.
`cloudformation:DetectStackDrift` starts a non-mutating assessment. The policy contains
no `Put*`, `Create*`, `Update*`, `Delete*`, or `Modify*` actions.

---

## AWS API Costs

| Scan mode | AWS cost | What's charged |
|-----------|----------|----------------|
| Full scan | ~$0.20-0.40 | Cost Explorer API @ $0.01/request |
| Quick scan (`--quick`) | ~$0.15-0.25 | Same, fewer calls |
| Security/sweep/DR only | **$0.00** | Free-tier APIs |
| Re-render (`kulshan convert`) | **$0.00** | No API calls |

**This is charged by AWS to your account, not by us.** Kulshan is free.

Skip the cost pack entirely: `kulshan report --packs security,sweep,dr`

---

## About the Name

Kulshan is the Lummi name for the mountain known colonially as Mt. Baker — meaning "great white watcher." Visible from Mission, BC where this project was built, the name reflects what this tool does: watch your cloud infrastructure from above, without touching it.

We acknowledge the Lummi and Nooksack peoples as the original namers of this mountain and do not claim to represent their nations.

---

## Built by

Yuvdeep Singh / [Mission FinOps](https://missionfinops.com) — Mission, BC, Canada.

6+ years at AWS helping enterprise customers build FinOps solutions — from integrating expensive third-party platforms to building in-house pipelines with native AWS tooling. Kulshan shows the art of the possible: what a proper FinOps pipeline can look like using a simple API without fancy tooling.

---

## License

Apache 2.0 — free and open source forever.

The IAM policy file is additionally offered under CC BY 4.0.

---

## Links

- **PyPI:** https://pypi.org/project/kulshan/
- **GitHub:** https://github.com/azz-kikkr/kulshan
- **Docs:** [docs/](docs/)
- **IAM Policy:** [kulshan/iam/kulshan-readonly.json](kulshan/iam/kulshan-readonly.json)
