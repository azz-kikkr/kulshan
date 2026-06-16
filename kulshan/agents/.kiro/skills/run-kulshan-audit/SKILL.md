---
name: Run Kulshan Audit
description: Run a complete Kulshan AWS audit and produce a local report
inclusion: manual
---

# Run Kulshan Audit

## Purpose

Run Kulshan against the user's AWS account to produce a baseline audit report. This skill handles the full workflow: verify credentials, run the scan, and present results.

## Prerequisites

- Kulshan installed (`pip install kulshan`)
- AWS credentials configured (same as AWS CLI)

## Steps

### 1. Verify credentials

Run `kulshan doctor` first. If it fails, help the user fix their AWS credential configuration before proceeding.

```bash
kulshan doctor
```

If doctor reports issues:
- Missing credentials → suggest `aws configure` or `aws sso login`
- Missing permissions → point to `kulshan/iam/kulshan-readonly.json`
- Cost Explorer not enabled → suggest enabling in Billing console

### 2. Confirm scan parameters

Ask the user:
- Quick scan (3 regions, ~60s) or full scan (all regions, ~3min)?
- Which output format? (HTML for sharing, JSON for processing, both?)
- Skip the cost pack to avoid any AWS charges? (`--packs security,sweep,dr`)

### 3. Run the scan

```bash
# Quick scan with HTML output
kulshan report --quick -o kulshan-report.html --yes

# Full scan with JSON for later re-rendering
kulshan report --format json -o kulshan-scan.json --yes

# Free scan (no Cost Explorer charges)
kulshan report --quick --packs security,sweep,dr -o kulshan-report.html --yes
```

### 4. Present results

After the scan completes:
1. Report the overall score and grade
2. Highlight the top 3-5 actions by estimated impact
3. Flag any critical or high severity findings
4. Note which packs scored lowest (potential areas of concern)
5. Offer to re-render in a different format if needed

### 5. Do NOT

- Execute any remediation
- Delete, modify, or create AWS resources
- Make guaranteed savings promises
- Share the report externally without user consent

## Output

The scan produces a local file. Common paths:
- `kulshan-report.html` — self-contained HTML, open in browser
- `kulshan-scan.json` — machine-readable, re-renderable
- `kulshan-report.sarif` — for GitHub Security tab / CI integration
