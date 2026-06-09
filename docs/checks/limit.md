# Limit (Capacity / Quota Headroom)

**Check pack:** `Kulshan.checks.limit`
**Orchestrator key:** `limit`
**Score weight:** 6% (see `TOOL_WEIGHTS` in `kulshan/src/kulshan/orchestrator.py`)
**IAM policy:** [`kulshan/iam/per-check/limit.json`](../../kulshan/iam/per-check/limit.json)

## What it does

Checks every service quota against current usage, predicts when limits will be hit, and scores headroom 0-100. Includes a scaling-event planner that identifies which quotas will block a traffic spike.

## Scoring breakdown (0-100): quota headroom

| Category | Weight | What it measures |
|----------|--------|------------------|
| Critical Limits | 40% | Quotas above 80% utilization |
| Growth Trajectory | 25% | Average utilization across all quotas |
| Monitoring Breadth | 20% | % of quotas with usage data available |
| Adjustability | 15% | Whether critical quotas can be increased |

## How to run

This pack runs as part of the unified Kulshan scan:

```bash
Kulshan Report                                   # all packs, all enabled regions
Kulshan Report --quick                           # 3 regions, top services only
Kulshan Report --format html -o report.html      # HTML output
```

A per-pack-only CLI (`Kulshan scan limit`) and the scaling-event planner are not exposed today; both are on the roadmap.

## Permissions

Non-mutating audit access. Key actions: `servicequotas:Get*`, `servicequotas:List*`, `cloudwatch:GetMetricStatistics`, plus service-specific Describe/List. Granular per-pack policy at [`kulshan/iam/per-check/limit.json`](../../kulshan/iam/per-check/limit.json).

## Cost

$0.
