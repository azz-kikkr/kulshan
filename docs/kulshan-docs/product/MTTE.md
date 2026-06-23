# Kulshan MTTE Product Spec

Read this document before building investigation features.

Kulshan should be an evidence investigator, not a cost narrator.

## Product thesis

The MVP risk is not missing features. It is adding features that do not directly reduce mean time to evidence (MTTE).

Kulshan should help someone answer a concrete pre-meeting question:

> EC2 is up 30%. What happened? Who owns it? What evidence supports that explanation? What evidence is missing? What should I ask before the meeting?

The product is the investigation brief. The CLI is the delivery mechanism.

Positioning:

> Kulshan is local-first cloud cost investigation.

Alternative tagline:

> The open-source evidence engine for AWS bills.

The ambition is closer to ffmpeg or VLC for AWS billing evidence than to a FinOps SaaS clone: free, open source, local-first, boring, and powerful.

Product boundary:

> If it helps people inspect AWS billing evidence locally, it belongs in Kulshan OSS.
> If it helps organizations operationalize that evidence across teams, it belongs in Mission FinOps paid services.

## OSS and paid boundary

Kulshan OSS should include:

- CLI workflows.
- Local CUR / AWS Data Exports querying.
- Cost Explorer fallback.
- Investigation briefs.
- Evidence contracts.
- Read-only AWS access.
- No telemetry.
- No billing-data upload.

Mission FinOps paid services should include:

- Helping enterprises enable AWS Data Exports.
- Self-hosted deployment support.
- Custom investigation packs.
- Executive reviews.
- FinOps operating model design.
- Private AI or agent workflows on top of the evidence layer.

Do not make the paid product "the real product." The free product must be genuinely useful on its own. Paid work should be support, implementation, governance, enterprise deployment, and trust.

## User

Primary users:

- FinOps practitioners preparing for cost reviews.
- Engineering leaders who need to explain cloud spend changes.
- Platform or cloud teams asked to identify ownership quickly.
- Consultants and MSPs preparing client-facing findings.

The user is time-constrained and likely preparing for a finance or engineering review. They do not need another dashboard. They need a defensible brief.

## Problem

Most AWS cost tooling can say that spend increased. That is not enough.

The useful workflow is:

1. Identify what changed.
2. Connect the change to resources, accounts, tags, and names.
3. Estimate likely ownership from available evidence.
4. Show which evidence supports the explanation.
5. Show which evidence is missing.
6. Generate the next human questions.

People do not investigate bills. They investigate changes.

## MVP command

```bash
kulshan investigate ec2
```

The default output should be a meeting-ready terminal brief.

```text
Investigation Summary

Service:
EC2

Period:
May -> June

Impact:
+$3,420

Top Contributors:
1. prod-asg-west +$1,920
2. analytics-cluster +$890
3. batch-workers +$410

Likely Owner:
Platform Team

Owner Confidence:
Tag match: High
Naming match: Medium
Account ownership: High
Overall: Medium-high

Evidence:
[available] Production account
[available] Naming convention match
[available] New resources detected

Missing:
[missing] Deployment record
[missing] CloudTrail correlation

Questions for Meeting:
1. Was a new workload launched?
2. Was ASG capacity increased?
3. Was there a migration?
```

## ASAP capability: local CUR query engine

Kulshan should be able to query AWS Data Exports / CUR data directly from S3 without requiring Athena, Redshift, a SaaS backend, or a dashboard.

Architecture details live in `docs/product/LOCAL_EVIDENCE_ENGINE.md`.

The first implementation should be local, read-only, and embedded:

```bash
kulshan investigate ec2 --cur s3://billing-bucket/exports/cur-2/
```

The engine should:

- Read CUR 2.0 or AWS Data Exports files directly from S3.
- Prefer Parquet because it supports column pruning and efficient scans.
- Use local SQL over S3 objects for investigation diffs, top contributors, service deltas, account deltas, usage type deltas, and resource-level drilldowns.
- Cache only local metadata and optional query fragments, never upload billing data.
- Fall back to Cost Explorer when CUR is unavailable.
- Treat missing CUR as an evidence gap, not a product failure.

Recommended first engine:

- DuckDB for embedded SQL over local files and S3 Parquet.
- Python integration first, because Kulshan is Python and DuckDB can run in process.
- Polars may be useful for dataframe transforms, but SQL is the better first interface for repeatable investigation queries.
- GPU acceleration is a later optimization, not an MVP dependency. Most first-pass CUR investigations are limited by S3 IO, Parquet pruning, partition layout, and aggregation strategy before they are limited by GPU compute.

GPU path:

- Keep the query layer engine-agnostic enough to support a later RAPIDS/cuDF or Polars GPU backend.
- Add GPU only after the DuckDB path proves the query contract and identifies real bottlenecks.
- Do not require CUDA for the default CLI experience.

This capability changes the evidence matrix: CUR should move from Phase 2 to ASAP/MVP for users who already have Data Exports enabled.

## CID companion positioning

Kulshan is not a Cloud Intelligence Dashboards clone.

AWS Cloud Intelligence Dashboards (CID) helps teams visualize and monitor their AWS cost estate through AWS-native dashboards. Its foundational architecture is:

```text
AWS Data Exports / CUR2
  -> S3
  -> Glue table schema
  -> Athena
  -> QuickSight / SPICE
  -> dashboards
```

Kulshan should use the same kind of billing evidence, but for a different job:

```text
AWS Data Exports / CUR
  -> S3 or local cache
  -> DuckDB
  -> evidence contract
  -> investigation brief
```

Product line:

> CID is the dashboard. Kulshan is the local detective.

Kulshan should copy CID's question catalog, not its UI. CID-style questions become local investigation packs:

- Cost overview.
- KPI snapshot.
- Service deltas.
- Account deltas.
- Resource deltas.
- Usage type deltas.
- Untagged spend.
- Commitment health.
- Data transfer.
- EC2 investigation.
- RDS investigation.
- S3 investigation.

Do not position Kulshan as local QuickSight. Position Kulshan as the local evidence layer for AWS billing investigations.

## Core workflow

### 1. Investigation diff

Compare the previous and current period.

```text
Previous month:
$11,200

Current month:
$14,620

Delta:
+$3,420

Top contributors:
1. prod-asg-west +$1,920
2. analytics-cluster +$890
3. batch-workers +$410
```

### 2. Timeline reconstruction

Humans think in timelines, not dashboards.

```text
Timeline

May 12
- 3 new m6i.4xlarge instances appear

May 14
- Production account EC2 spend begins increasing

May 15
- Auto Scaling Group size increases

May 18
- EC2 spend stabilizes at new baseline
```

### 3. Owner resolution

The owner answer must be explainable and defensible from available evidence.

```text
Likely Owner:
Platform Team

Owner Confidence:
Tag match: High
Naming match: Medium
Account ownership: High
Overall: Medium-high
```

Do not invent historical ownership confidence unless the product actually has historical ownership data.

### 4. Evidence quality

Score the evidence coverage, not the answer.

```text
Evidence Coverage:
7/10

Available:
[available] Cost data
[available] Account ownership
[available] Tags
[available] Resource inventory

Missing:
[missing] Deployment history
[missing] Change ticket
[missing] Utilization metrics
```

This is the guardrail against unsupported conclusions.

### 5. Questions for meeting

The output must generate useful next human questions, not only answers.

```text
Questions for Meeting:
1. Was a new workload launched?
2. Was ASG capacity increased?
3. Was there a migration?
```

This is a core feature. A user preparing five minutes before a finance meeting needs the next questions as much as the explanation.

### 6. Exportable artifact

The investigation should be exportable without requiring another system.

```bash
kulshan investigate ec2 --md
kulshan investigate ec2 --html
```

The artifact should be meeting-ready and easy to paste into Slack or attach to a review note.

## Investigation sources

| Evidence Source | Today | Future |
| --- | --- | --- |
| Cost Explorer | Yes | Yes |
| Resource Tags | Yes | Yes |
| Resource Inventory | Yes | Yes |
| Account Ownership | Yes | Yes |
| CUR / Data Exports in S3 | ASAP | Yes |
| CloudTrail | No | Phase 2 |
| Jira | No | Later |
| GitHub | No | Later |

Every owner, timeline, and confidence claim must be traceable to this matrix. If the source is not available today, the product should say what is missing rather than imply certainty.

## Evidence contract

Every investigation finding should include:

- Claim: the plain-language explanation.
- Impact: the quantified cost delta.
- Sources used: the evidence that supports the claim.
- Sources missing: the evidence that would improve the claim.
- Owner signal: tags, account ownership, naming convention, or other available ownership evidence.
- Confidence basis: separate component-level confidence, not a black-box score.
- Questions for meeting: the next human questions to ask.

## Explicit non-goals

Do not add these to the MVP:

- Chat
- MCP
- Slack integration
- Memory
- Jira integration
- Multi-cloud
- Agent workflows
- Dashboards
- Forecasting
- Recommendations
- "AI copilot" positioning

These may be useful later, but they distract from reducing MTTE.

## Success metric

A user can run:

```bash
kulshan investigate ec2
```

and paste the resulting brief into Slack or bring it to a finance meeting within five minutes.

If a feature does not make that outcome faster, clearer, or more defensible, it is probably out of scope.
