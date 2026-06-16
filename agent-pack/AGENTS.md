# Kulshan — Agent Instructions (Codex / General)

## What is Kulshan

Kulshan is a free, open-source, read-only AWS audit CLI. It runs 10 audit packs locally using your existing AWS credentials and produces a baseline report. Apache 2.0. No SaaS. No telemetry.

## Install

```bash
pip install kulshan
```

## Rules (non-negotiable)

1. **Always start with `kulshan doctor`.** Do not skip this step.
2. **Never write to AWS.** Kulshan is read-only. Do not create, modify, or delete AWS resources based on findings.
3. **Write reports to local files.** Always use `-o` flag.
4. **Prefer JSON for analysis.** Use `--format json` for machine consumption.
5. **Also generate HTML.** Use `--format html` for human sharing.
6. **Warn about CE costs.** Cost Explorer API calls cost ~$0.01/request. Typical scan: pennies.
7. **Never suggest destructive remediation without human review.** Always require explicit confirmation.
8. **Do not fabricate findings.** Only report what Kulshan actually found.
9. **Do not make guaranteed savings claims.** Estimated impacts are projections.
10. **No SaaS, no API keys, no telemetry.** Existing AWS credentials only.

## Workflow

```bash
# 1. Verify
kulshan doctor

# 2. Scan
kulshan report --format json -o kulshan-scan.json --yes    # For agents
kulshan report --format html -o kulshan-report.html --yes  # For humans

# 3. Quick scan (faster, 3 regions)
kulshan report --quick --format json -o kulshan-scan.json --yes

# 4. Free scan (no CE charges)
kulshan report --packs security,sweep,dr --format json -o kulshan-scan.json --yes

# 5. Re-render without re-scanning
kulshan convert -i kulshan-scan.json --format html -o report.html
kulshan convert -i kulshan-scan.json --format sarif -o findings.sarif
kulshan convert -i kulshan-scan.json --format csv -o findings.csv

# 6. History
kulshan history
```

## JSON output structure

```json
{
  "overall_score": 84,
  "overall_grade": "B",
  "tools": { "<pack>": { "scores": {...}, "findings": [...] } },
  "findings": [ { "severity": "...", "title": "...", "evidence": {...}, "recommended_action": "..." } ],
  "top_actions": [ { "title": "...", "estimated_monthly_impact": 220.0 } ]
}
```

## Packs

| Pack | CLI name | What it checks |
|------|----------|---------------|
| Cost | cost | Anomaly detection, RI/SP coverage, spend attribution |
| Security | security | IAM, network exposure, encryption, logging |
| Waste | sweep | Orphaned EBS, EIPs, idle ALBs, NAT gateways |
| DR | dr | Backup coverage, multi-AZ, single points of failure |
| Lifecycle | age | EOL runtimes, expiring certs, stale AMIs |
| Drift | drift | CloudFormation drift, IaC coverage gaps |
| Tags | tag | Tag compliance, unattributed spend |
| Observability | pulse | Alarm coverage, logging gaps, blind spots |
| Quotas | limit | Service quota headroom, scaling blockers |
| Topology | topo | VPC topology, CIDR overlaps, route integrity |

## AWS Cost Explorer consent

The cost pack calls AWS Cost Explorer APIs. AWS bills these at ~$0.01 per request. A typical scan costs about $0.15–$0.40 depending on pagination.

**Agent behavior:**
- Before running Kulshan with the cost pack enabled, inform the human that Cost Explorer API calls will create a small AWS charge (typically under $0.40).
- The human must explicitly approve before the agent runs the cost pack.
- If the human declines, run Kulshan with `--packs security,sweep,dr,age,drift,tag,pulse,limit,topo` (excludes cost).
- In non-interactive/CI mode, use `--yes` to skip the prompt (explicit opt-in).

## When analyzing Kulshan output

- Prefer JSON over HTML for programmatic analysis.
- Use `top_actions` first — these are pre-ranked by impact.
- Present findings before recommendations.
- Never invent savings estimates. Use only `estimated_monthly_impact` from the report.
- Never invent AWS resources. Reference only what the report contains.
- Distinguish facts (from evidence) from assumptions (from interpretation).
- Reference report `evidence` fields whenever possible.

## Links

- GitHub: https://github.com/azz-kikkr/kulshan
- IAM Policy: `kulshan/iam/kulshan-readonly.json`
- Sample report: https://missionfinops.com/sample/
