---
name: Prepare Engineer Remediation Plan
description: Create a prioritized, actionable remediation plan from Kulshan findings for engineering teams
inclusion: manual
---

# Prepare Engineer Remediation Plan

## Purpose

Transform Kulshan findings into a structured, prioritized remediation plan that an engineering team can execute. Group by effort, risk, and impact. Include specific AWS actions — but never execute them automatically.

## Prerequisites

- A completed Kulshan scan in JSON format
- No AWS credentials needed (working from existing report)

## Steps

### 1. Read the scan

Load JSON. Extract all findings. Sort by:
1. Severity (critical → high → medium → low)
2. Estimated monthly impact (highest first)
3. Effort (low → medium → high)

### 2. Categorize into tiers

```
TIER 1: QUICK WINS (low effort, safe, high impact)
- Can be done in <30 minutes
- Low risk of service disruption
- Examples: delete unattached EBS, fix missing tags, enable backups

TIER 2: THIS SPRINT (medium effort, moderate impact)
- Requires planning but not architectural change
- Examples: rightsize instances, fix drift, add alarms

TIER 3: NEXT QUARTER (high effort, significant change)
- Architectural decisions needed
- Examples: multi-AZ migration, RI/SP strategy, network redesign
```

### 3. For each finding, provide

```
Finding: [title]
Severity: [critical/high/medium/low]
Pack: [which audit pack]
Impact: est. $[X]/mo
Effort: [low/medium/high]
Risk: [safe/moderate/requires-testing]

What to do:
  [Specific AWS action in plain language]

How to verify:
  [Console path or CLI command to confirm]

Rollback:
  [How to undo if something goes wrong]
```

### 4. Important warnings

For every finding that involves deletion or modification:

```
⚠️ REQUIRES HUMAN REVIEW
Do not execute without verifying:
- Resource is not in use
- No dependent services
- Snapshot/backup exists if applicable
```

### 5. Format the plan

Write to local file:
```
kulshan-remediation-plan.md
```

Structure:
- Header with scan date, score, finding count
- Tier 1: Quick Wins (table format)
- Tier 2: This Sprint (detailed cards)
- Tier 3: Next Quarter (brief descriptions)
- Appendix: Full finding details

### 6. Rules

- **Never execute remediation.** This is a plan, not an automation script.
- **Never suggest `aws ... delete` or `aws ... terminate` without the ⚠️ warning.**
- **Always include rollback guidance.**
- **Always recommend verifying in the Console first.**
- **Mark destructive actions clearly.** Use ⚠️ or 🔴 prefix.
- **Do not fabricate findings.** Only include what Kulshan reported.
- **Estimated impacts are projections, not promises.**

### 7. Tone

Write for senior engineers:
- Specific (resource IDs, regions, account hints)
- Actionable (exact steps, not vague guidance)
- Honest about risk (mark what could break things)
- Respectful of their judgment (present options, don't dictate)
