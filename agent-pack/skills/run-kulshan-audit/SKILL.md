---
name: Run Kulshan Audit
description: Run a complete Kulshan AWS audit and produce local reports
inclusion: manual
---

# Run Kulshan Audit

## Purpose

Run Kulshan against the user's AWS account to produce a baseline audit report. Handles the full workflow: verify credentials, run the scan, present results.

## Prerequisites

- Kulshan installed (`pip install kulshan`)
- AWS credentials configured (same as AWS CLI)

## Steps

### 1. Verify credentials

```bash
kulshan doctor
```

If it fails:
- Missing credentials → `aws configure` or `aws sso login`
- Missing permissions → point to `kulshan/iam/kulshan-readonly.json`
- Cost Explorer not enabled → enable in AWS Billing console

Do not proceed until doctor passes.

### 2. Confirm scan parameters

Ask the user:
- Quick scan (3 regions, ~60s) or full scan (all regions, ~3min)?
- Skip cost pack to avoid any AWS charges? (`--packs security,sweep,dr`)
- Mention: "Cost Explorer API calls may cost pennies. Other packs are free."

### 3. Run the scan

```bash
# Agent analysis (JSON)
kulshan report --quick --format json -o kulshan-scan.json --yes

# Human report (HTML) — run alongside or convert after
kulshan report --quick --format html -o kulshan-report.html --yes

# Or combined: scan once, render twice
kulshan report --quick --format json -o kulshan-scan.json --yes
kulshan convert -i kulshan-scan.json --format html -o kulshan-report.html
```

### 4. Present results

After the scan:
1. Overall score and grade
2. Top 3-5 actions by estimated monthly impact
3. Any critical or high severity findings (flag urgently)
4. Weakest packs (below 60) — areas needing attention
5. Offer to re-render in different format

### 5. Do NOT

- Execute any remediation without explicit human approval
- Delete, modify, or create AWS resources
- Make guaranteed savings promises
- Share reports externally without user consent
- Skip the `kulshan doctor` step
