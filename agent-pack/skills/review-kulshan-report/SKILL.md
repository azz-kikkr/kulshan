---
name: Review Kulshan Report
description: Analyze and interpret a previously generated Kulshan audit report
inclusion: manual
---

# Review Kulshan Report

## Purpose

Help the user understand a Kulshan report. Interpret scores, prioritize findings, recommend next steps — without modifying any AWS resources.

## Prerequisites

- A Kulshan report file exists (JSON preferred for analysis)
- No AWS credentials needed for review (report already generated)

## Steps

### 1. Locate the report

Ask for the file path. Common locations:
- `kulshan-scan.json` or `kulshan-report.html` in current directory
- Check `kulshan history` for past scans

### 2. Read the JSON report

Key fields:
- `overall_score`: 0-100
- `overall_grade`: A-F
- `tools.<pack>.scores.overall_score`: per-pack score
- `findings[]`: all findings across all packs
- `top_actions[]`: pre-ranked highest-impact items

### 3. Provide summary

Present:
1. **Overall grade** and what it means
2. **Strongest packs** (85+) — good practices to maintain
3. **Weakest packs** (below 60) — attention needed
4. **Top 5 actions** with estimated monthly impact
5. **Critical/high count** — anything urgent

### 4. Deep-dive on request

If the user asks about a specific finding:
- What was detected (title + description)
- Evidence (from `evidence` field)
- Recommended action
- Effort and risk level
- How to verify in the AWS Console

### 5. Compare scans

If multiple scans exist:
```bash
kulshan history
```

Highlight: score trends, new findings, resolved findings.

### 6. Re-render

```bash
kulshan convert -i scan.json --format html -o report.html
kulshan convert -i scan.json --format csv -o findings.csv
```

### 7. Do NOT

- Suggest automatic remediation without human approval
- Make guaranteed savings promises
- Modify AWS resources
- Fabricate findings not in the report
