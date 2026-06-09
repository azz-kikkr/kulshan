# AWS API Costs — Kulshan scan

Kulshan is free and open source. **AWS charges for Cost Explorer API calls only** — all other API calls are free.

---

## Cost Summary

| Scan Mode | Estimated AWS Cost | What gets charged |
|-----------|-------------------|-------------------|
| `Kulshan Report --quick` | **$0.15 – $0.25** | Cost Explorer API only |
| `Kulshan Report` (full) | **$0.20 – $0.40** | Cost Explorer API only |
| Individual non-cost packs only | **$0.00** | Resource inventory and assessment APIs used by these packs |

---

## Per-Pack Breakdown

### Cost Pack (the only pack with paid API calls)

AWS charges **$0.01 per Cost Explorer API request**. Here's what Kulshan calls:

| API Call | Count | Cost | Purpose |
|----------|-------|------|---------|
| `ce:GetCostAndUsage` | 3-5 | $0.03-0.05 | Daily cost by service, attribution drill-downs |
| `ce:GetCostForecast` | 1 | $0.01 | 30-day cost prediction |
| `ce:GetReservationCoverage` | 1 | $0.01 | RI coverage by service |
| `ce:GetSavingsPlansUtilization` | 1 | $0.01 | SP utilization percentage |
| `ce:GetReservationUtilization` | 1 | $0.01 | RI utilization by service |
| `ce:GetAnomalies` | 1 | $0.01 | AWS Cost Anomaly Detection results |
| `ce:GetRightsizingRecommendation` | 1 | $0.01 | EC2 rightsizing suggestions |
| `ce:GetCostAndUsageComparisons` | 1 | $0.01 | Period-over-period comparison |
| `ce:GetSavingsPlansPurchaseRecommendation` | 1 | $0.01 | SP purchase suggestions |
| Attribution drill-downs (top 5 anomalies) | 0-20 | $0.00-0.20 | Service → account/region/usage_type |
| **Subtotal (baseline)** | **~12** | **~$0.12** | |
| **Subtotal (with attribution)** | **~25-30** | **~$0.25-0.30** | |

### All Other Packs ($0.00 — free tier)

| Pack | APIs Called | Cost |
|------|-----------|------|
| Security | IAM, EC2, S3, RDS, KMS, GuardDuty, Access Analyzer, Config | $0.00 |
| Sweep | EC2 (volumes, snapshots, EIPs, ENIs, AMIs), ELB, RDS, S3, ECR | $0.00 |
| DR | Backup, EC2, RDS, S3, Route53 | $0.00 |
| Age | Lambda, ACM, EC2, RDS, EKS | $0.00 |
| Drift | CloudFormation (DescribeStackResourceDrifts, ListStacks) | $0.00 |
| Tag | Resource Groups Tagging API | $0.00 |
| Pulse | CloudWatch, CloudTrail, X-Ray | $0.00 |
| Limit | Service Quotas, EC2, IAM, RDS, CloudFormation | $0.00 |
| Topo | EC2 (VPCs, subnets, route tables, flow logs, NAT gateways) | $0.00 |

The non-cost-pack APIs used here are not billed per request at the time of writing. `DetectStackDrift` is also non-mutating and is not a Cost Explorer API.

---

## Why Does Cost Explorer Charge?

AWS bills Cost Explorer API calls because:
- The CE API queries a pre-aggregated billing data warehouse
- It's separate from the free resource-description APIs
- AWS charges $0.01 per `GetCost*`, `GetReservation*`, `GetSavings*`, and `GetAnomaly*` request
- This is an AWS decision, not a Kulshan decision

---

## How to Minimize Costs

1. **Use `--quick` mode** — runs fewer attribution drill-downs (~$0.15 instead of ~$0.30)
2. **Use `kulshan convert`** — re-render a saved JSON report without re-scanning (costs $0.00)
3. **Skip the cost pack** — when it's available: `Kulshan Report --exclude cost` (roadmap feature)
4. **Run less frequently** — weekly scans cost ~$1-2/month total

---

## Comparison with Alternatives

| Tool | Monthly cost for weekly scans |
|------|------------------------------|
| Kulshan | ~$1-2 (CE API charges only) |
| CloudHealth/VMware | $1,000+/month (SaaS subscription) |
| Spot.io/NetApp | $500+/month |
| Infracost | $0 (IaC only, no runtime scanning) |
| Prowler | $0 (security only, no cost analysis) |

Kulshan's cost is negligible compared to the savings it identifies.
