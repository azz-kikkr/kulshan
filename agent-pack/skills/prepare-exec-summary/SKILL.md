---
name: Prepare Executive Summary
description: Produce a concise executive summary from a Kulshan scan for non-technical stakeholders
inclusion: manual
---

# Prepare Executive Summary

## Purpose

Transform a Kulshan JSON scan into a plain-language executive summary suitable for VPs, finance, or board-level review. Focus on business impact, not technical details.

## Prerequisites

- A completed Kulshan scan in JSON format (`kulshan-scan.json`)
- No AWS credentials needed (working from existing report)

## Steps

### 1. Read the scan

Load the JSON report. Extract:
- `overall_score` and `overall_grade`
- `top_actions` (sorted by `estimated_monthly_impact`)
- Per-pack scores from `tools`
- Critical and high severity finding count
- Total estimated addressable spend

### 2. Write the summary

Structure:

```
KULSHAN AUDIT SUMMARY
Date: [scan date]
Score: [grade] ([score]/100)

HEADLINE
One sentence: "Your AWS environment scores [grade]. 
Estimated [$/mo] in addressable savings identified across [N] findings."

TOP 3 ACTIONS (by monthly impact)
1. [Action] — est. $[X]/mo
2. [Action] — est. $[X]/mo
3. [Action] — est. $[X]/mo

RISK AREAS
- [Pack] scored [score]: [one-line explanation]
- [Pack] scored [score]: [one-line explanation]

STRENGTHS
- [Pack] scored [score]: [one-line explanation]

NEXT STEPS
- Review top actions with engineering team
- Schedule remediation for critical findings
- Re-scan after changes to measure improvement
```

### 3. Tone and language

- Write for business readers, not engineers
- Use dollars, not technical metrics
- "Addressable savings" not "waste"
- "Risk areas" not "failures"
- "Strengths" not "passing checks"
- Keep it to one page (under 300 words)

### 4. Output

Write to a local file:
```bash
# Save as markdown
echo "[summary content]" > kulshan-exec-summary.md
```

Or include in the chat response for copy-paste.

### 5. Do NOT

- Include account IDs or resource ARNs (unless --show-pii was used)
- Make guaranteed savings promises ("estimated" always)
- Recommend specific vendor products
- Suggest destructive actions without context
- Fabricate numbers not in the scan
