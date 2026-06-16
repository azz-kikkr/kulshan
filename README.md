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

### macOS / Linux

```bash
# Recommended: isolated install via pipx
pipx install kulshan

# Or classic pip (use a virtualenv if you prefer)
pip install kulshan
```

### Windows

```powershell
# PowerShell — recommended: isolated install via pipx
pipx install kulshan

# Or classic pip
pip install kulshan
```

> **Requires:** Python 3.9+ ([python.org/downloads](https://python.org/downloads))
> If `python --version` shows 3.9 or higher, you're good.

### From source (all platforms)

```bash
git clone https://github.com/azz-kikkr/kulshan.git
cd kulshan/kulshan
pip install -e .
```

### Verify install

```bash
kulshan --version
kulshan doctor          # Check AWS readiness (no cost, no writes)
```

---

## AWS Credentials

### Recommended: Use AWS CLI v2 with your normal console login

Install the [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) and log in the same way you access the AWS Console. Kulshan uses the same credential chain — if `aws sts get-caller-identity` works, Kulshan works.

```bash
# Log in with your browser (AWS CLI v2.22+, no access keys needed)
aws sso login

# Then just run Kulshan — it picks up your session automatically
kulshan doctor            # Shows what your current access enables
kulshan report --quick    # Runs whatever packs your permissions allow
```

**You don't need the full Kulshan IAM policy to try it.** Run `kulshan doctor` to see exactly what works with your current access. Packs that need permissions you don't have will be skipped — the report still runs with whatever you've got.

### All authentication methods

| Method | Command | Best for |
|--------|---------|----------|
| **SSO / Identity Center** | `aws sso login --profile your-profile` | Enterprise teams |
| **IAM Identity Center** | `aws login` (browser popup) | Quickest start |
| **Named profile** | `kulshan --profile prod report` | Multi-account |
| **Role assumption** | `kulshan --role-arn arn:aws:iam::...:role/KulshanAudit report` | Cross-account |
| **Environment variables** | `export AWS_ACCESS_KEY_ID=...` | CI/CD pipelines |
| **Console temp keys** | Copy from Console → "Command line access" | When SSO is unavailable |

### Detailed examples

<details>
<summary>SSO login (click to expand)</summary>

```bash
aws sso login --profile your-profile
kulshan --profile your-profile report --quick
```
</details>

<details>
<summary>Temporary keys from the AWS Console</summary>

1. Log into the AWS Console
2. Click your name (top right) → "Command line or programmatic access"
3. Copy the temporary credentials:

**Windows (PowerShell):**
```powershell
$env:AWS_ACCESS_KEY_ID="ASIA..."
$env:AWS_SECRET_ACCESS_KEY="..."
$env:AWS_SESSION_TOKEN="..."
```

**macOS / Linux:**
```bash
export AWS_ACCESS_KEY_ID=ASIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
```

These expire when your session ends. **Never use long-term access keys.**
</details>

<details>
<summary>Assume a dedicated role</summary>

```bash
kulshan --role-arn arn:aws:iam::123456789012:role/KulshanAudit report --quick
```
</details>

### Verify your access

```bash
aws sts get-caller-identity      # Confirms credentials work
kulshan doctor                   # Shows which Kulshan packs your access enables
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

## Use with AI Agents

Kulshan is designed to work with AI coding agents — Claude Code, Codex, Kiro, Cursor, and any agent that can run shell commands.

### Quick setup

| Agent | What to do |
|-------|-----------|
| **Claude Code** | Copy `agent-pack/CLAUDE.md` to your project root |
| **Codex** | Copy `agent-pack/AGENTS.md` to your project root |
| **Kiro** | Copy skills from `agent-pack/skills/` to `.kiro/skills/` |
| **Any agent** | Just run `kulshan` commands via shell — output is local JSON/HTML |

### How agents use Kulshan

```bash
kulshan doctor                           # Agent verifies AWS access first
kulshan report --format json -o scan.json  # Agent runs audit, gets structured data
kulshan report -o report.html            # Agent generates HTML for human review
```

The agent reads the JSON output, interprets findings, and can produce executive summaries, remediation plans, or PR review comments — all locally.

### Data flow

```
Your AWS credentials → Kulshan CLI (local) → AWS APIs (read-only)
                                            → Local JSON + HTML files
                                            → Agent reads local files
                                            → Natural-language audit review
```

No SaaS. No uploads. No API keys beyond your existing AWS credentials.

Full agent integration docs: [`agent-pack/README.md`](agent-pack/README.md)

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
