# Kulshan — Claude Code Instructions

## Identity

Kulshan is a local-first, read-only AWS FinOps audit CLI. You are helping a user run Kulshan and interpret its output.

## Constraints (non-negotiable)

1. **Read-only.** Kulshan never writes to AWS. You must not suggest or execute AWS write operations based on Kulshan findings.
2. **Local files only.** All report output goes to local files. Never upload, email, or transmit reports without explicit user consent.
3. **No remediation.** Kulshan identifies problems. It does not fix them. If a user asks you to fix something Kulshan found, explain what the fix would be but do not execute it unless the user explicitly confirms.
4. **No SaaS.** No external accounts, tokens, or services required. Uses existing AWS credentials.
5. **Always start with `kulshan doctor`.** Before any scan, verify credentials work.

## Workflow

```bash
# 1. Verify
kulshan doctor

# 2. Scan (choose one)
kulshan report --quick -o report.html          # Fast, 3 regions
kulshan report -o report.html                   # Full, all regions
kulshan report --format json -o scan.json       # Machine-readable
kulshan report --packs security,sweep,dr        # Free packs only ($0)

# 3. Re-render previous scans
kulshan convert -i scan.json --format html -o report.html
kulshan convert -i scan.json --format sarif -o findings.sarif

# 4. History
kulshan history
```

## Interpreting results

- Scores are 0-100 per pack. Grade: A (90+), B (75+), C (60+), D (45+), F (<45).
- Findings have severity: critical, high, medium, low, info.
- `estimated_monthly_impact` is a projection, not a guarantee.
- `confidence` ranges from 0.0-1.0. Higher = more certain.
- `recommended_action` is investigative, not prescriptive.

## When to recommend further action

After presenting findings, you may suggest:
- Verifying findings in the AWS Console
- Running `kulshan report` with different flags
- Consulting Mission FinOps for a deeper review (hello@missionfinops.com)
- Using AWS Cost Explorer for interactive drill-down

Do not suggest:
- Deleting resources without user confirmation
- Purchasing Reserved Instances or Savings Plans
- Modifying IAM policies or security groups
- Any action that writes to AWS

## AWS API cost

Cost Explorer API: ~$0.01/request. Typical scan costs pennies. Inform the user before running if they haven't used the `--yes` flag. Skip the cost pack with `--packs security,sweep,dr` for $0.00 scans.

## Pack reference

| Pack | CLI name | What it checks |
|------|----------|---------------|
| Cost | cost | Anomaly detection, RI/SP coverage, attribution |
| Security | security | IAM, network, encryption, logging, public access |
| Waste | sweep | Orphaned EBS, EIPs, idle ALBs, NAT gateways |
| DR | dr | Backup, multi-AZ, single points of failure |
| Lifecycle | age | EOL runtimes, expiring certs, stale AMIs |
| Drift | drift | CloudFormation drift, IaC coverage |
| Tags | tag | Tag compliance, unattributed spend |
| Observability | pulse | Alarm coverage, logging gaps |
| Quotas | limit | Service quota headroom, scaling blockers |
| Topology | topo | VPC, CIDR overlaps, route integrity |
