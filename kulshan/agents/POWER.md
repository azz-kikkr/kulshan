# Kulshan — Kiro Power

Local-first AWS FinOps audit CLI. Read-only. No SaaS. No telemetry.

## What this power provides

Kulshan runs 10 read-only audit packs against your AWS account using your existing credentials and produces a local HTML/JSON/SARIF report. It does not write to AWS, phone home, or require any SaaS backend.

## Prerequisites

- Python 3.9+
- AWS credentials configured (`aws sts get-caller-identity` works)
- Kulshan installed: `pip install kulshan`

## Tools (via MCP server)

| Tool | Description |
|------|-------------|
| `kulshan_doctor` | Validate AWS connectivity and permissions before scanning |
| `kulshan_report` | Run all 10 audit packs and write a local report |
| `kulshan_report_quick` | Run a fast scan (3 regions, skip slow packs) |
| `kulshan_convert` | Re-render a previous JSON scan to HTML/SARIF/CSV without re-scanning |
| `kulshan_history` | List past scan results with scores and trends |

## Constraints

- **Read-only.** Kulshan never writes to AWS. The IAM policy contains only Get, List, and Describe actions.
- **Local-only.** All reports are written to local files. No data leaves your machine.
- **No remediation.** Kulshan identifies findings. It does not fix them. Do not attempt to implement write/remediation tools.
- **No API keys required.** Uses your existing AWS credential chain.
- **No SaaS.** No accounts, tokens, or external services.

## Workflow

1. Always start with `kulshan doctor` to verify credentials and permissions.
2. Run `kulshan report` to produce the audit.
3. Review the output file (HTML for humans, JSON for machines, SARIF for CI).
4. Use `kulshan convert` to re-render previous scans without re-running API calls.

## AWS API Cost

The cost pack calls AWS Cost Explorer API (~$0.01 per paginated request). Typical scan: pennies. Other 9 packs use free-tier AWS APIs. Kulshan caps pagination to keep costs predictable even in large Organizations.

## Keywords

aws, finops, cost, audit, security, cloud-cost, cost-explorer, read-only, local-first
