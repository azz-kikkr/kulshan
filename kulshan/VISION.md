# Kulshan: Product Vision

## What Kulshan Is

Kulshan is a local-first CLI that runs 10 AWS operational audits from a single command and scores your account across cost, security, waste, resilience, freshness, governance, monitoring, capacity, and architecture in one combined report. Core scans and reports stay local unless the user explicitly invokes an external integration.

## Why It Exists

Every AWS account has at least one "what is THIS line item" moment. Compute is the easy thing to monitor. The real waste hides in secondary services: the stuff that doesn't show up in a quick Cost Explorer glance.

Kulshan exists to surface those blind spots before they become $24K/month surprises.

## The Problem Space

AWS billing is death by a thousand cuts. Teams we've talked to (and Reddit threads we've mined) consistently report the same pattern:

**The headline EC2 cost is usually fine. The waste hides in secondary services.**

The most common surprise cost drivers, ranked by how often they catch teams off guard:

### Tier 1: Almost Everyone Gets Hit

- **CloudWatch Logs ingestion**: "set it and forget it" logging that quietly becomes a top-5 line item. Log ingestion costs more than retention. One team had a verbose GC logging flag left on that cost $200/day in NAT data transfer alone.
- **NAT Gateway data processing**: A single misconfigured service pulling 1TB/day through NAT = $1,300/month. Cross-AZ NAT traffic (`DataTransfer-Regional-Bytes`) is the non-obvious variant.
- **EC2-Other** (EBS volumes, snapshots, IOPS, data transfer): Often 30%+ of total spend. Giant EBS disks used for app-level backups instead of S3. Provisioned IOPS with no lifecycle policy.
- **EBS snapshots**: AWS Backup retention policies that nobody prunes. One team saved $24K/month by moving snapshots to S3.
- **Cross-AZ data transfer**: Inter-AZ bandwidth costs that exceed database costs. Obvious: egress. Non-obvious: cross-AZ.

### Tier 2: Grows With Scale

- **S3 request pricing vs. storage pricing**: A logging pipeline doing 1M LIST calls/minute cost $4K (storage was $20). Firehose-to-S3 PUT costs with small Iceberg partitions. Missing lifecycle policies for old data, versions, and tiering.
- **AWS Config**: Continuous recording across many accounts in ephemeral environments. Every config change logged, every rule evaluation billed. Config bills exceeding compute for some workloads.
- **Security Hub**: Per-finding, per-account, per-region pricing. Turn on controls across multiple accounts/regions and it stops being small.
- **GuardDuty / Inspector**: $600/month and "no idea what's happening" is a direct quote.
- **KMS CMK costs on S3**: Without bucket keys enabled, every S3 operation triggers a KMS API call. Nobody mentions it until the bill arrives.

### Tier 3: Specific But Painful

- **RDS/EKS Extended Support**: Keeping old versions running now costs extra. The staleness tax is real and has a dollar amount.
- **Cognito**: Was mostly free, but M2M auth and MAU charges have crept in over the last 12-18 months.
- **CloudWatch custom metrics**: Every new dimension multiplies cost. Excessive dimensions on metrics is a silent killer.
- **Replication**: Cross-region replication costs that nobody budgeted for.
- **Lambda@Edge / CloudFront Functions**: Cost-prohibitive at scale despite being useful.

### What People Call These

> "AWS taxes": charges that should be far cheaper or free considering what they are, but in larger organizations with higher compliance expectations, they have a taxing effect on AWS bills.

## What Kulshan Checks (V0.1)

| Dimension | Tool | What It Catches |
|-----------|------|----------------|
| FinOps | Cost Analyzer | Anomaly detection, efficiency scoring, RI/SP coverage |
| Security | Security Scanner | 50+ checks, attack paths, CIS/NIST/SOC2 compliance |
| Waste | Waste Detector | Orphaned EBS, EIPs, NAT GWs, idle RDS, stale Lambdas |
| Resilience | DR Readiness | Backup coverage, multi-AZ, SPOF detection, failover |
| Freshness | Lifecycle Audit | EOL runtimes, expiring certs, staleness tax |
| Governance | IaC Drift | CloudFormation drift, IaC coverage, severity classification |
| FinOps | Tag Governance | Tag compliance, dark money, value chaos |
| Monitoring | Observability | Blind spots, alarm coverage, logging gaps |
| Capacity | Quota Headroom | Service limits, scaling event planner |
| Architecture | Network Topology | VPC mapping, CIDR overlaps, routing integrity |

## Known Gaps to Close

Based on real-world feedback, these are the blind spots Kulshan doesn't yet catch but should:

### High Priority (V0.2 candidates)

1. **CloudWatch Logs cost analysis**: Flag CW log groups with high ingestion rates. Identify log groups with no retention policy set (infinite retention = infinite cost). Surface the ingestion-vs-retention cost ratio.

2. **S3 request cost decomposition**: Break S3 costs into storage vs. API requests. Flag buckets with high PUT/LIST request costs relative to storage. Detect missing lifecycle policies (no tiering, no version cleanup, no expiration).

3. **AWS Config cost awareness**: Flag accounts with continuous recording enabled. Estimate Config costs based on resource churn rate. Recommend periodic recording where appropriate.

4. **Cross-AZ traffic detection**: Specifically detect `DataTransfer-Regional-Bytes` charges. Flag services with high cross-AZ traffic patterns. Distinguish from inter-region transfer (already tracked).

5. **"AWS Tax" rollup**: Aggregate the cost of security/governance tooling (Config, GuardDuty, Security Hub, Inspector, CloudTrail) into a single "AWS Tax" line item so teams can see the total overhead.

### Medium Priority (V0.3+ candidates)

6. **Security tooling cost-vs-value**: For GuardDuty, Security Hub, Inspector: show cost per finding. Help teams decide if the security tool is worth what they're paying.

7. **KMS bucket key check**: Flag S3 buckets using CMKs without bucket keys enabled. Estimate KMS API cost savings from enabling bucket keys.

8. **Extended Support surcharge detection**: Flag RDS/EKS instances on extended support. Show the dollar premium vs. upgrading to a supported version.

9. **EBS snapshot lifecycle analysis**: Go beyond "orphaned snapshots" to analyze backup retention policies. Flag AWS Backup vaults with aggressive retention and no tiering to S3.

10. **CloudWatch metrics dimension analysis**: Detect custom metrics with excessive dimensions. Estimate cost impact of dimension cardinality.

## Report "Wow" Features

The report is where Kulshan earns its keep. The terminal output gets attention; the HTML report closes deals.

### Terminal Report Enhancements (V0.2)

- **"Money on Fire" callout**: Top 3 findings by monthly dollar impact, shown above the score table. Red flame emoji. Hard to ignore.
- **Quick wins section**: Findings that take <30 minutes to fix and save >$100/month. Sorted by ROI (savings per effort-minute).
- **Score delta from last scan**: Show ↑↓ arrows and point changes next to each tool score when history is available. "Cost: 72 → 78 ↑6"
- **Waste dollar total**: Bold monthly waste estimate at the top. "You're burning approximately $X,XXX/month on orphaned resources."

### HTML Report Enhancements (V0.2)

- **Executive summary hero section**: One-paragraph AI-generated summary at the top. "Your account scores 74/100. The biggest risk is X. The biggest savings opportunity is Y. Three quick wins could save $Z/month."
- **Interactive Sankey diagram**: Where your money flows: Account → Service → Usage Type → Cost. Inline SVG, no external dependencies. Makes the "EC2-Other" problem visible instantly.
- **Cost treemap**: Proportional rectangles showing spend by service. Click to drill into usage types. The visual makes 80/20 distribution obvious.
- **Findings heatmap**: Grid of severity × tool. Red squares jump out. Shows where the pain concentrates.
- **"What Changed" diff panel**: When history exists, show what improved and what regressed since last scan. Green/red delta badges.
- **Remediation playbook**: Ranked list of actions with estimated savings, effort, and copy-pasteable CLI commands or IaC snippets. Grouped by "Quick Wins" / "This Sprint" / "Next Quarter."
- **Print-optimized layout**: The HTML report should look good printed to PDF for stakeholders who want a document, not a dashboard.

### Deferred: AI-powered narrative layer

The original V0.3 plan added a local SLM (Qwen3-4B-Instruct) for plain-English anomaly explanations, risk narratives, waste stories, and pre-generated Q&A pairs. That work is deferred without a committed date.

The reason: receipts beat prose. Every Kulshan finding already ships with the number, the query that produced it, the evidence, the opinion that fired the rule, and a remediation hint. That is a stronger trust signal than narrative prose because there is nothing for an SLM to hallucinate. If receipts turn out to be insufficient with real buyers in practice, the SLM work resumes; for now, no committed date.

## Design Principles

1. **Local-first**: Scans and reports run on your machine by default. Optional integrations may send selected data externally only when explicitly configured and invoked.
2. **Receipts, not prose**: Every finding ships with the number, the query that produced it, the evidence, the opinion applied, and a remediation hint. Trust comes from showing the work, not from a narrative wrapper.
3. **Opinionated defaults, escape hatches everywhere**: Works out of the box with zero config. Every default is overridable.
4. **Show the money**: Every finding should have a dollar impact estimate where possible. Abstract severity levels are less motivating than "$1,300/month."
5. **Earn trust through transparency**: Show what was checked, what was skipped, and why. No black boxes.
6. **Reports are the deliverable**: The terminal output gets attention. The HTML report is what closes a real conversation.

## Competitive Positioning

The 2026 landscape includes audit-style CLIs (Prowler, cloud-audit, Steampipe), the AWS-native option (Trusted Advisor), self-hosted topology + cost (Hyperglance), and FinOps SaaS (Vantage, CloudZero, CloudHealth, Finout). Differences are factual at time of writing; tools change.

| | Kulshan | Prowler | cloud-audit | Steampipe | Trusted Advisor | Vantage | CloudZero | Hyperglance |
|---|---|---|---|---|---|---|---|---|
| Local-only, no SaaS | Yes | Yes | Yes | Yes | No | No | No | Self-hosted option |
| Multi-domain in one run | 10 packs | Security | Security | Query model | Limited | Cost | Cost | Cost + topology |
| Read-only IAM by construction | Yes | Yes | Yes | Configurable | N/A | N/A | N/A | Configurable |
| Free CLI | Yes | Yes | Yes | Yes | Tier-gated | Free up to limits | Custom | Per-resource |

Kulshan's edge is **multi-domain breadth in one local read-only pass**: cost, security, DR, drift, tags, observability, quota, topology, age, and network in one CLI run. Other tools go deeper in one domain (Prowler in security, CloudZero in cost analytics) or operate as SaaS (Vantage, CloudHealth, Finout). For a buyer who already runs Prowler for security and CloudZero for cost, Kulshan is the second-opinion sweep across the other eight domains, not a replacement.

## Target Users

1. **Solo DevOps / Platform Engineers**: Running 1-3 AWS accounts, want a quick health check without signing up for another SaaS.
2. **FinOps practitioners**: Need data to justify optimization work to leadership. The HTML report is their deliverable.
3. **Security-conscious teams**: Want posture assessment without sending scan data to a third party.
4. **Consultancies and fractional CTOs**: Run Kulshan against client accounts and hand over the report as part of an engagement. The license arrangement for this use case is at https://missionfinops.com/msp/.

## Deferred / future consideration

These items appear in earlier roadmap drafts but are explicitly NOT promised for any current Kulshan version. They have no committed date. They are listed here so future contributors can see what was considered and parked, and why.

- **MCP server.** A Model Context Protocol server that lets AI assistants run Kulshan audits directly. Tracked as a future possibility. The receipts framing should be tested with real buyers first; if the MCP shape proves load-bearing for adoption, the work resumes.
- **FOCUS 1.3 ingestion.** Reading FOCUS-shaped cost exports as a parallel input path to CUR. Tracked, not scheduled.
- **Local SLM narrative layer.** The Qwen3-based AI narrative work originally scoped for V0.3. Deferred because receipts beat prose for trust.
- **Bedrock and SageMaker AI cost analysis.** A specialty cost pack for AI workloads. Deferred; depends on AWS pricing-API stability and customer demand.

When any of these become real, they appear in `Kulshan/CHANGELOG.md` and at https://missionfinops.com/changelog/ as scheduled or shipped, not as future plans.
