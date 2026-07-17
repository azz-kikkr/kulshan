# Kulshan

Read-only AWS audit CLI.

```bash
pip install kulshan
kulshan report
```

One command. One report. Zero writes to your AWS account.

[![PyPI](https://img.shields.io/pypi/v/kulshan)](https://pypi.org/project/kulshan/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE.txt)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)

[Documentation](https://github.com/MissionFinOps/kulshan/tree/master/kulshan/docs) | [IAM Policy](https://github.com/MissionFinOps/kulshan/blob/master/kulshan/iam/kulshan-readonly.json) | [Changelog](https://github.com/MissionFinOps/kulshan/blob/master/kulshan/CHANGELOG.md)

---

## What Kulshan does

Ten read-only audit packs in one CLI. Cost anomalies, security posture, waste detection, DR gaps, drift, tag compliance, observability blind spots, quota headroom, and network topology - scored 0-100, exportable as HTML, JSON, SARIF, or CSV.

Reads your Cost Explorer data and your own CUR/Data Export Parquet files in place. No data leaves your machine. No SaaS account. No telemetry. Nothing to opt out of, because nothing exists.

---

## What Kulshan does not do

- Does not write to AWS. The IAM policy contains only Get, List, and Describe actions.
- Does not phone home. No telemetry, no update checks, no analytics.
- Does not require infrastructure. No databases, no containers, no SaaS.
- Does not hold credentials. Uses the same credential chain as the AWS CLI.

---

## Install

```bash
pip install kulshan
```

Python 3.9+. macOS, Linux, Windows. Optional extras: `kulshan[pdf]`, `kulshan[excel]`, `kulshan[pptx]`, `kulshan[mcp]`, or `kulshan[all]`.

---

## Credentials

If `aws sts get-caller-identity` works, Kulshan works.

```bash
aws login
kulshan report
```

Named profiles, environment variables, and role assumption all work:

```bash
kulshan --profile production report
kulshan --role-arn arn:aws:iam::123456789012:role/KulshanAudit report
```

Run `kulshan doctor` to verify connectivity and permissions without incurring any cost.

---

## The 10 Audit Packs

| Pack | What it watches |
|------|-----------------|
| `cost` | Anomalies (z-score, IQR, MAD), commitment gaps, spend acceleration, forecasts |
| `security` | IAM, encryption, network exposure, logging, public access, GuardDuty |
| `sweep` | Orphaned volumes, unused EIPs, idle LBs, detached ENIs, empty repos |
| `dr` | Backup coverage, single-AZ, single points of failure, missing replication |
| `age` | EOL runtimes, expiring certs, stale AMIs, outdated engines |
| `drift` | CloudFormation drift, IaC coverage, severity classification |
| `tag` | Missing required tags, unattributed spend, key inconsistencies |
| `pulse` | Alarm gaps, missing metric filters, blind spots |
| `limit` | Quota headroom, at-limit services, scaling risk |
| `topo` | CIDR overlaps, route integrity, peering issues, TGW misconfigs |

```bash
kulshan report                                    # cost only (default, ~$0.15)
kulshan report --packs security,sweep             # specific packs (free APIs)
kulshan report --packs all --regions us-east-1    # full diagnostic
```

---

## Automatic Environment Isolation

On first run, Kulshan identifies your AWS principal, creates an isolated local environment, and routes all data there. Different identities get separate environments. No flags required.

```
✓ Created environment readonlyrole-cedar
  Using readonlyrole-cedar · account 1234…5678
```

When CUR data reveals a payer account, the environment binds to that payer. Multiple identities accessing the same payer can be reconciled into a unified timeline:

```bash
kulshan workspace reconcile
```

Workspaces with multiple connections produce consolidated reports automatically - one scan, all connections, deduplicated findings, per-connection coverage metadata.

---

## CUR / Data Export Investigation

Query your CUR Parquet files locally or from S3. No Athena, no Glue, no data warehouse. DuckDB queries in place.

```bash
kulshan cur validate --path ./cur/
kulshan investigate cost --path ./cur/ --month 2024-06
kulshan investigate ec2 --cur ./cur/ --month 2024-06
kulshan investigate cost --s3 s3://bucket/prefix/ --month 2024-06
```

Top movers by service, account, region, usage type. Period-over-period deltas. Resource-level contributors. Tag coverage. All outputs include provenance, evidence IDs, and `human_review_required: true`.

---

## MCP Server

Kulshan exposes its findings to MCP-compatible agents (Claude Desktop, Cursor, Kiro, others). Deterministic evidence in, agent reasoning out.

```bash
kulshan mcp-serve
```

```json
{
  "mcpServers": {
    "kulshan": { "command": "kulshan", "args": ["mcp-serve"] }
  }
}
```

Seven tools: `kulshan_doctor`, `kulshan_report`, `kulshan_quick_security`, `kulshan_list_packs`, `kulshan_cur_validate`, `kulshan_investigate_ec2`, `kulshan_investigate_cost`.

---

## Output Formats

```bash
kulshan report -o report.html           # Self-contained HTML report
kulshan report --format json -o s.json  # Structured, machine-readable
kulshan report --format sarif -o r.sarif # GitHub Security tab
kulshan report --format csv -o f.csv    # Spreadsheet / JIRA import
kulshan convert -i scan.json -o r.html  # Re-render without re-scanning
```

Account IDs redacted by default. `--show-pii` for full IDs. Atomic writes prevent partial files.

---

## CI/CD

```bash
kulshan report --packs security --format sarif -o results.sarif --yes --no-history
```

Exit code 1 when critical findings are present - use as a quality gate. SARIF uploads to GitHub Code Scanning. Full GitHub Actions and GitLab CI examples in [docs/ci-cd.md](https://github.com/MissionFinOps/kulshan/blob/master/docs/ci-cd.md).

---

## Trust and Security

> Read-only by construction, not read-only by default. There is no cleanup mode to leave off, no write path to enable. The published IAM policy contains zero actions that create, modify, or delete resources.

- 147 read-only actions, zero write actions. [Read every line.](https://github.com/MissionFinOps/kulshan/blob/master/iam/kulshan-readonly.json)
- Reports stay on your machine
- No telemetry, no phone-home
- Open source: Apache 2.0. IAM policy additionally CC BY 4.0.
- Per-pack least-privilege policies at [`iam/per-check/`](https://github.com/MissionFinOps/kulshan/tree/master/iam/per-check)
- Compliance metadata: CIS, SOC 2, NIST 800-53, Well-Architected

---

## Quick Reference

```bash
kulshan --version                       # Version
kulshan doctor                          # Check credentials and permissions
kulshan report                          # Cost baseline (default)
kulshan report --quick                  # Skip confirmation
kulshan report -o report.html           # HTML report
kulshan report --packs all --regions us-east-1 --deep  # Full deep scan
kulshan report --perf                   # Show API timing
kulshan history                         # Past scans
kulshan history --direct-only           # Current workspace only
kulshan workspace list                  # All environments
kulshan workspace reconcile             # Link shared-payer environments
kulshan shell                           # Interactive REPL
kulshan convert -i scan.json -o r.html  # Re-render
```

---

## AWS API Cost

Cost pack: ~$0.15 (CE API at $0.01/request). All other packs use free APIs. Kulshan confirms before making CE calls. Use `--yes` in CI/CD.

---

## About the Name

Kulshan is the Lummi name for the mountain known colonially as Mt. Baker, meaning "great white watcher." The mountain is visible from Mission, BC and is an active volcano in the Cascade Range. We acknowledge the Lummi and Nooksack peoples as the original namers of this mountain.

---

## Maintained by

[Mission FinOps](https://missionfinops.com) - open-source AWS audit tooling.

---

## License

Apache 2.0. Free and open source forever.
