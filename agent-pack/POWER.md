# Kulshan — Kiro Power

A local AWS FinOps audit tool for humans and AI agents.

## What this power provides

Kulshan runs 10 read-only audit packs against your AWS account using your existing credentials and produces local HTML/JSON/SARIF reports. No SaaS. No CUR upload. No telemetry. No write access.

## Prerequisites

- Python 3.9+
- AWS credentials configured (`aws sts get-caller-identity` works)
- Kulshan installed: `pip install kulshan`

## Tools (via shell commands)

| Command | Description |
|---------|-------------|
| `kulshan doctor` | Validate AWS connectivity and permissions before scanning |
| `kulshan report --format json -o scan.json` | Run all 10 audit packs and write a local JSON report |
| `kulshan report --quick --format json -o scan.json` | Run a fast scan (3 regions, skip slow packs) |
| `kulshan convert -i scan.json --format html -o report.html` | Re-render a previous scan without re-scanning |
| `kulshan history` | List past scan results with scores and trends |

Note: A dedicated MCP server (`kulshan mcp-serve`) is planned but not yet implemented. Today, agents invoke Kulshan via shell commands.

## Constraints (non-negotiable)

- **Read-only.** Kulshan never writes to AWS. 147 Get/List/Describe actions. Zero writes.
- **Local-only.** All reports are written to local files. No data leaves your machine.
- **No remediation.** Kulshan identifies findings. It does not fix them.
- **No API keys required.** Uses your existing AWS credential chain.
- **No SaaS.** No accounts, tokens, or external services.
- **No telemetry.** Zero phone-home code.

## Workflow

1. Always start with `kulshan doctor` to verify credentials and permissions.
2. Run `kulshan report --format json -o scan.json` for agent analysis.
3. Run `kulshan report --format html -o report.html` for human review.
4. Use `kulshan convert` to re-render previous scans without API calls.

## AWS API Cost

The cost pack reads AWS Cost Explorer API (~$0.01 per paginated request). Typical scan: pennies. Kulshan caps pagination to keep costs predictable even in large Organizations (1000+ accounts). Other 9 packs use free-tier AWS APIs. Skip the cost pack entirely with `--packs security,sweep,dr` for $0.00 scans.

## Keywords

aws, finops, cost, audit, security, cloud-cost, cost-explorer, read-only, local-first, mcp, agent
