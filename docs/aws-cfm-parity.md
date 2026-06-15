# AWS Cloud Financial Management — Parity and Consumption Plan

This document tells any AI agent (or human contributor) working on Kulshan what
capabilities AWS launched in their Cloud Financial Management suite (Nov 2025 –
Jun 2026 batch) and what Kulshan should do about each one: match it, consume its
data, or explicitly ignore it and why.

Sources (all from the AWS Cloud Financial Management blog):

1. [Cost Efficiency Metric](https://aws.amazon.com/blogs/aws-cloud-financial-management/measuring-cloud-cost-efficiency-with-the-new-cost-efficiency-metric-by-aws/) — Nov 2025
2. [State of Cost Efficiency Report](https://aws.amazon.com/blogs/aws-cloud-financial-management/the-aws-state-of-cost-efficiency-report/) — Jun 2026
3. [Six New Idle Resource Recommendations](https://aws.amazon.com/blogs/aws-cloud-financial-management/announcing-six-new-idle-resource-recommendations-in-aws-compute-optimizer/) — Jun 2026
4. [Target Coverage in Savings Plans Purchase Analyzer](https://aws.amazon.com/blogs/aws-cloud-financial-management/introducing-target-coverage-in-savings-plans-purchase-analyzer/) — Jun 2026
5. [Intelligent Cost Explanations in Cost Explorer](https://aws.amazon.com/blogs/aws-cloud-financial-management/introducing-intelligent-cost-explanations-in-aws-cost-explorer/) — Jun 2026

---

## 1. Cost Efficiency Metric

### What AWS ships

A single score (0–100%) in Cost Optimization Hub combining rightsizing, idle
cleanup, and commitment savings:

```
Cost Efficiency = [1 − (Potential Savings / Total Optimizable Spend)] × 100%
```

- Rolling 30-day spend window, refreshes daily.
- 90 days of historical data on enable.
- Queryable via `list-efficiency-metrics` CLI/SDK (group by account, region; daily/monthly granularity).
- Requires opt-in to Compute Optimizer + Cost Optimization Hub + Cost Explorer.

### What Kulshan should do

| Action | Pack | Detail |
|--------|------|--------|
| **Consume** | `cost` | Call `cost-optimization-hub:ListEfficiencyMetrics` to pull the customer's current score and 90-day trend. Display it in the cost pack summary as "AWS Cost Efficiency Score" alongside Kulshan's own anomaly and spend analysis. |
| **Consume** | `cost` | Use the score as an input to Kulshan's overall scoring — e.g. weight it as an efficiency dimension or cross-reference it against Kulshan's own waste findings. |
| **Surface delta** | `report` | If Kulshan has historical scans, show the score delta since last run. |
| **Do NOT replicate** | — | Do not re-derive the formula. AWS already computes it from data Kulshan cannot fully access (internal Compute Optimizer state, post-discount savings). Consume it as a signal. |

### IAM additions needed

```json
"compute-optimizer:GetEnrollmentStatus",
"cost-optimization-hub:ListEfficiencyMetrics",
"cost-optimization-hub:GetPreferences"
```

---

## 2. State of Cost Efficiency Report (Benchmarks)

### What AWS published

Aggregate benchmarks from 71,000+ customers:

- Median score: 83, mean: 79.
- Only 17.7% have EC2 memory metrics enabled; enabling them gives 8–30pp higher savings per recommendation.
- Customizing Compute Optimizer preferences → +3–4 points.
- "Shrink first, then commit" — rightsizing + Savings Plans together improves score 4× faster.
- High SP coverage (95–100%) masks 65–80% of rightsize/Graviton opportunity.

### What Kulshan should do

| Action | Pack | Detail |
|--------|------|--------|
| **Benchmark context** | `cost` | When displaying the customer's Cost Efficiency score, include the published median (83) and mean (79) as peer benchmarks. Phrase: "Your score: X. AWS median: 83." |
| **Memory-metrics check** | `pulse` or `cost` | Detect whether EC2 memory metrics are enabled (check CloudWatch for `mem_used_percent` metric presence across instances). If <50% of instances have it, emit a finding: "EC2 memory metrics not enabled — associated with 8–30pp higher savings per recommendation." |
| **Compute Optimizer preference check** | `cost` | Call `compute-optimizer:GetRecommendationPreferences` — if defaults are unchanged, surface an advisory finding recommending customization. |
| **Narrative framing** | `report` | Use the "shrink first, then commit" insight in remediation guidance: when Kulshan finds both idle/oversized resources AND low SP coverage, recommend cleanup first. |

### IAM additions needed

```json
"compute-optimizer:GetRecommendationPreferences",
"compute-optimizer:GetEnrollmentStatus",
"cloudwatch:ListMetrics"   (already present)
```

---

## 3. Six New Idle Resource Recommendations

### What AWS ships

Compute Optimizer now detects idle resources for six additional services:

| Service | Lookback | Idle signals |
|---------|----------|--------------|
| DynamoDB (provisioned) | 14 days | Zero consumed read + write capacity |
| ElastiCache (Redis/Valkey) | 14 days | Zero new connections, minimal CPU, zero hits/misses |
| MemoryDB | 14 days | Zero new connections, minimal CPU, zero keyspace hits/misses |
| DocumentDB (provisioned + serverless) | 14 days | Zero DB connections |
| WorkSpaces (AlwaysOn) | 63 days | Zero user connections |
| SageMaker Endpoints | 14 days | Zero invocations |

Available via `GetIdleRecommendations` API and Cost Optimization Hub `ListRecommendations`.

### What Kulshan should do

| Action | Pack | Detail |
|--------|------|--------|
| **Consume native findings** | `sweep` | Call `compute-optimizer:GetIdleRecommendations` for these resource types. Map each to a Kulshan Finding with severity, estimated savings, and remediation text. This is lower-effort than replicating the CloudWatch metric logic ourselves. |
| **Match independently (stretch)** | `sweep` | For services where Kulshan already has IAM access (DynamoDB, ElastiCache, RDS/DocumentDB), replicate the idle heuristic directly by querying CloudWatch metrics. This makes Kulshan work even when the customer hasn't opted into Compute Optimizer. |
| **New IAM for net-new services** | `sweep` | WorkSpaces and SageMaker are not in Kulshan's current policy. Add read-only permissions only if independent detection is implemented. |

#### Consumption path (preferred, lower effort)

```json
"compute-optimizer:GetIdleRecommendations",
"cost-optimization-hub:ListRecommendations"
```

#### Independent detection path (stretch, higher coverage)

```json
"dynamodb:DescribeTable",            (already present)
"dynamodb:ListTables",               (already present)
"cloudwatch:GetMetricStatistics",    (already present)
"elasticache:DescribeCacheClusters", (already present)
"elasticache:DescribeReplicationGroups", (already present)
"memorydb:DescribeClusters",
"docdb:DescribeDBClusters",
"workspaces:DescribeWorkspaces",
"sagemaker:DescribeEndpoint",
"sagemaker:ListEndpoints"
```

### Priority

High. The sweep pack is one of Kulshan's flagship differentiators. Missing these
six services when AWS's native tooling catches them undermines the value prop.
Consumption path first (fast), independent detection second (robust).

---

## 4. Target Coverage in Savings Plans Purchase Analyzer

### What AWS ships

A new "Target Coverage" mode where you enter a desired SP coverage percentage
(10–100%) and get back the hourly commitment needed. It accounts for existing
plans, lets you exclude expiring ones, and provides hourly utilization/coverage
charts.

API: `SavingsPlansPurchaseAnalysisConfiguration` in the Cost Management API.

### What Kulshan should do

| Action | Pack | Detail |
|--------|------|--------|
| **Surface coverage gap** | `cost` | Kulshan already pulls SP utilization via `ce:GetSavingsPlansUtilization`. Add a companion call to compute current SP *coverage* percentage. If coverage is below common targets (70%, 80%, 90%), emit a finding with the gap size. |
| **Reference the tool** | `cost` | In remediation text for low-coverage findings, link to the Savings Plans Purchase Analyzer and describe the Target Coverage workflow. Kulshan is read-only — it should not recommend a specific dollar commitment, but it can say "you're at 62% coverage; use the Purchase Analyzer's Target Coverage mode to model what 80% would cost." |
| **Do NOT replicate** | — | Kulshan does not make purchase recommendations. It is read-only and should stay that way. Pointing customers to the right console page is enough. |

### IAM additions needed

```json
"ce:GetSavingsPlansCoverage"
```

(Already have `ce:GetSavingsPlansUtilization` and `ce:GetSavingsPlansPurchaseRecommendation`.)

---

## 5. Intelligent Cost Explanations (Amazon Q in Cost Explorer)

### What AWS ships

An "Analyze with Amazon Q" button in Cost Explorer that produces natural-language
explanations of cost reports — trend narration, anomaly context, optimization
guidance, and forecast explanations. Conversational follow-ups. No additional
charge.

### What Kulshan should do

| Action | Pack | Detail |
|--------|------|--------|
| **Acknowledge, don't compete** | — | This is an interactive console feature powered by a foundation model. Kulshan cannot and should not replicate it. It requires a chat UI and real-time access to Amazon Q. |
| **Complement it** | `cost`, `report` | Kulshan's value is *offline, CI-friendly, deterministic* analysis. Position the two as complementary: Kulshan catches issues in CI and generates artifact-based reports; Amazon Q explains ad-hoc questions in the console. |
| **Surface the capability** | `report` | In HTML report remediation sections for cost findings, add a note: "For interactive drill-down, use 'Analyze with Amazon Q' in Cost Explorer." This is a trust-building move — shows Kulshan isn't trying to lock users in. |
| **Do NOT build a chat layer** | — | No LLM narratives. Kulshan's "receipts" philosophy (the number, the query, the evidence, the opinion) remains the approach. |

### IAM additions needed

None. This is a console-only capability.

---

## Summary: IAM Policy Additions

All new actions needed to implement the consumption paths above:

```json
"ce:GetSavingsPlansCoverage",
"compute-optimizer:GetEnrollmentStatus",
"compute-optimizer:GetIdleRecommendations",
"compute-optimizer:GetRecommendationPreferences",
"cost-optimization-hub:GetPreferences",
"cost-optimization-hub:ListEfficiencyMetrics",
"cost-optimization-hub:ListRecommendations"
```

Optional (independent idle detection for net-new services):

```json
"docdb:DescribeDBClusters",
"memorydb:DescribeClusters",
"sagemaker:DescribeEndpoint",
"sagemaker:ListEndpoints",
"workspaces:DescribeWorkspaces"
```

---

## Implementation Priority

| # | Item | Effort | Impact | Pack |
|---|------|--------|--------|------|
| 1 | Consume idle recommendations from Compute Optimizer | Low | High | `sweep` |
| 2 | Pull and display Cost Efficiency score + benchmark | Low | Medium | `cost` |
| 3 | Add SP coverage gap finding | Low | Medium | `cost` |
| 4 | EC2 memory-metrics enablement check | Low | Medium | `pulse`/`cost` |
| 5 | Compute Optimizer preference customization check | Low | Low | `cost` |
| 6 | Independent idle detection for DynamoDB/ElastiCache/MemoryDB/DocumentDB | Medium | High | `sweep` |
| 7 | Independent idle detection for WorkSpaces/SageMaker | Medium | Medium | `sweep` |

Items 1–5 are quick wins (API calls + finding emission). Items 6–7 require
CloudWatch metric analysis logic comparable to what Compute Optimizer does internally.

---

## Design Principles (for any agent implementing this)

1. **Consume native signals first.** If AWS computes it, pull it. Don't re-derive what you can't fully replicate (e.g., post-discount savings math).
2. **Independent detection as fallback.** Many customers haven't opted into Compute Optimizer. Kulshan should still catch idle resources from raw CloudWatch metrics when the native API returns nothing.
3. **Stay read-only.** No purchase recommendations, no resource modifications, no write calls.
4. **Receipts, not narratives.** Surface the number, the source, the evidence. Don't generate prose explanations — that's Amazon Q's job.
5. **IAM policy is the contract.** Every new API call must be reflected in `kulshan-readonly.json` before the code ships.
6. **Benchmarks are context, not judgment.** Show the customer where they stand relative to the published median. Don't grade them against it — every environment is different.
