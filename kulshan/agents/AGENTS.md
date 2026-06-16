# Kulshan — Agent Instructions (Codex / General)

## What is Kulshan

Kulshan is a free, open-source, read-only AWS audit CLI. It runs 10 audit packs locally using your existing AWS credentials and produces a baseline report. Apache 2.0.

## Quick facts

- **Install:** `pip install kulshan`
- **Language:** Python 3.9+
- **Cloud:** AWS only
- **License:** Apache 2.0
- **IAM:** 147 read-only actions (Get, List, Describe). Zero write actions.

## How to use Kulshan as an agent

### Step 1: Verify credentials

Always run this first:

```bash
kulshan doctor
```

If this fails, do not proceed. Fix the AWS credential issue before continuing.

### Step 2: Run an audit

```bash
# Quick scan (3 regions, ~60s)
kulshan report --quick --format json -o kulshan-scan.json

# Full scan (all regions, ~3min)
kulshan report --format json -o kulshan-scan.json

# HTML for humans
kulshan report --format html -o kulshan-report.html
```

### Step 3: Read and interpret the report

The JSON output contains:
- `overall_score`: 0-100 score
- `overall_grade`: A-F letter grade
- `tools`: per-pack results with scores and findings
- `findings`: all findings with severity, evidence, and recommended actions
- `top_actions`: prioritized list of highest-impact items

### Step 4: Re-render without re-scanning

```bash
kulshan convert -i kulshan-scan.json --format html -o report.html
kulshan convert -i kulshan-scan.json --format sarif -o findings.sarif
kulshan convert -i kulshan-scan.json --format csv -o findings.csv
```

## Rules for agents

1. **Always start with `kulshan doctor`.** Do not skip this step.
2. **Never write to AWS.** Kulshan is read-only. Do not attempt to create, modify, or delete AWS resources based on Kulshan findings.
3. **Write reports to local files.** Always use `-o` flag to write output locally.
4. **Do not fabricate findings.** Only report what Kulshan actually found.
5. **Do not make guaranteed savings claims.** Findings have estimated impacts, not promises.
6. **Do not share unredacted reports externally.** Reports may contain account IDs. Use `--show-pii` only when explicitly requested.
7. **No SaaS, no API keys, no telemetry.** Kulshan uses your existing AWS credential chain only.

## Available packs

| Pack | What it checks |
|------|---------------|
| cost | Anomaly detection, RI/SP coverage, spend attribution |
| security | IAM, network exposure, encryption, logging |
| sweep | Orphaned EBS, EIPs, idle ALBs, NAT gateways |
| dr | Backup coverage, multi-AZ, single points of failure |
| age | EOL runtimes, expiring certs, stale AMIs |
| drift | CloudFormation drift, IaC coverage gaps |
| tag | Tag compliance, unattributed spend |
| pulse | Alarm coverage, logging gaps, blind spots |
| limit | Service quota headroom, scaling blockers |
| topo | VPC topology, CIDR overlaps, route integrity |

## Cost

The cost pack calls AWS Cost Explorer API (~$0.01/request). Typical scan: pennies. Skip it with `--packs security,sweep,dr` for $0.00 scans.

## Links

- GitHub: https://github.com/azz-kikkr/kulshan
- IAM Policy: `kulshan/iam/kulshan-readonly.json`
- Sample report: https://missionfinops.com/sample/
