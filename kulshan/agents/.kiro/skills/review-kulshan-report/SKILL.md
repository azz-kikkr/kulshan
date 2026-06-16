---
name: Review Kulshan Report
description: Analyze and interpret a previously generated Kulshan audit report
inclusion: manual
---

# Review Kulshan Report

## Purpose

Help the user understand a Kulshan report they have already generated. Interpret scores, prioritize findings, and recommend next steps — without modifying any AWS resources.

## Prerequisites

- A Kulshan report file exists (JSON format preferred for analysis)
- No AWS credentials needed for review (the report is already generated)

## Steps

### 1. Locate the report

Ask the user for the report file path. Common locations:
- `kulshan-report.html` (current directory)
- `kulshan-scan.json` (current directory)
- Check `kulshan history` for past scan paths

### 2. Read the JSON report

If the user has a JSON report, read it directly. Key fields:

```
overall_score: 0-100
overall_grade: A-F
tools.<pack>.scores.overall_score: per-pack score
findings[]: all findings across all packs
top_actions[]: pre-ranked highest-impact items
```

### 3. Provide a summary

Present:
1. **Overall grade** and what it means
2. **Strongest packs** (scores 85+) — areas of good practice
3. **Weakest packs** (scores below 60) — areas needing attention
4. **Top 5 actions** with estimated monthly impact
5. **Critical/high findings count** — anything requiring urgent attention

### 4. Deep-dive on request

If the user asks about a specific finding:
- Explain what was detected
- Show the evidence (from the `evidence` field)
- Explain the recommended action
- Estimate effort and risk
- Suggest verification steps in the AWS Console

### 5. Compare with previous scans

If the user has multiple scans:
```bash
kulshan history
```

Highlight:
- Score trends (improving or declining?)
- New findings since last scan
- Resolved findings (things that got better)

### 6. Re-render if needed

```bash
# Convert JSON to HTML for sharing
kulshan convert -i kulshan-scan.json --format html -o report.html

# Convert to SARIF for CI
kulshan convert -i kulshan-scan.json --format sarif -o findings.sarif

# Convert to CSV for spreadsheet analysis
kulshan convert -i kulshan-scan.json --format csv -o findings.csv
```

### 7. Do NOT

- Suggest automatic remediation
- Make guaranteed savings promises
- Modify AWS resources
- Share findings externally without user consent
- Fabricate findings not present in the report

## Interpretation guide

| Grade | Score | Meaning |
|-------|-------|---------|
| A | 90-100 | Well-optimized, low risk |
| B | 75-89 | Good, some opportunities |
| C | 60-74 | Moderate issues, review recommended |
| D | 45-59 | Significant concerns |
| F | 0-44 | Critical attention needed |

| Severity | Meaning |
|----------|---------|
| critical | Immediate attention — active risk or major waste |
| high | Address soon — significant impact |
| medium | Plan to address — moderate impact |
| low | Nice to fix — minor impact |
| info | Informational — no action required |
