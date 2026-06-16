# Kulshan — Claude Code Instructions

## Identity

Kulshan is a local-first, read-only AWS FinOps audit CLI. You are helping a user run Kulshan and interpret its output. No SaaS. No telemetry. No write access.

## Rules (non-negotiable)

1. **Always start with `kulshan doctor`.** Before any scan.
2. **Read-only.** Never write to AWS. Never suggest executing AWS write operations.
3. **Local files only.** All output goes to local files via `-o` flag.
4. **Cost Explorer consent required.** Before running with the cost pack, tell the user it will create AWS Cost Explorer API charges (typically $0.15–$0.40). Get explicit approval. If declined, run with `--packs security,sweep,dr,age,drift,tag,pulse,limit,topo`.
5. **No remediation without approval.** If a user asks to fix something, explain the fix. Do not execute it unless they explicitly confirm.
6. **No SaaS.** No external accounts, tokens, or services.
7. **Never guarantee savings.** Findings have estimated impacts, not promises.

## Commands

```bash
# Verify credentials
kulshan doctor

# Full scan (JSON for you, HTML for humans)
kulshan report --format json -o scan.json --yes
kulshan report --format html -o report.html --yes

# Quick scan (3 regions, ~60s)
kulshan report --quick --format json -o scan.json --yes

# Free scan (no Cost Explorer charges)
kulshan report --packs security,sweep,dr --format json -o scan.json --yes

# Re-render previous scan
kulshan convert -i scan.json --format html -o report.html
kulshan convert -i scan.json --format sarif -o findings.sarif

# History
kulshan history
```

## Interpreting results

- Scores: 0-100 per pack. Grade: A (90+), B (75+), C (60+), D (45+), F (<45).
- Severity: critical, high, medium, low, info.
- `estimated_monthly_impact`: projection, not guarantee.
- `confidence`: 0.0-1.0. Higher = more certain.
- `recommended_action`: investigative, not prescriptive.

## After presenting findings

You may suggest:
- Verifying in the AWS Console
- Running a different pack combination
- Using AWS Cost Explorer for interactive drill-down
- Re-running with different flags or pack selection

You must NOT suggest:
- Deleting resources without explicit user confirmation
- Purchasing RIs or Savings Plans
- Modifying IAM policies or security groups
- Any action that writes to AWS

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
