# Kulshan

Local-first AWS cost analysis and evidence CLI.

Something changed in AWS. Kulshan helps you investigate what moved, what evidence supports the explanation, what evidence is missing, and how confident the conclusion should be.

```bash
pip install kulshan
aws sso login
kulshan preflight
kulshan report
```

[![PyPI](https://img.shields.io/pypi/v/kulshan)](https://pypi.org/project/kulshan/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE.txt)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)

---

## What you get

Kulshan produces a local HTML and JSON report containing:

- Executive cost movement summary
- Supporting evidence (anomaly detection, usage-type attribution, period deltas)
- Contradicting or incomplete evidence (missing tags, gaps in coverage)
- Ownership and attribution confidence
- Coverage disclosure and unknowns
- Recommended next investigation steps

A check is marked clean only when the required AWS evidence was successfully retrieved and evaluated. Failed evaluations are reported as "could not check" with the denied action named, never silently passed.

[View the synthetic sample report](https://missionfinops.com/sample/)

![Kulshan report preview showing cost investigation structure: question, conclusion, supporting evidence, contradicting evidence, ownership confidence, and next step](https://missionfinops.com/assets/report-preview.svg)

Local-first. Read-only. No SaaS. No telemetry.

---

## How it works

```bash
kulshan report                                    # cost investigation (default)
kulshan report --packs security,sweep             # add security + waste detection
kulshan report --packs all --regions us-east-1    # full diagnostic across all packs
kulshan analyze cost --path ./cur/ --month 2026-06  # investigate CUR data locally
```

Reads Cost Explorer data and your own CUR/Data Export Parquet files in place. No data leaves your machine. DuckDB queries locally. No Athena, no Glue, no data warehouse.

---

## Trust model

Read-only by construction, not by default. There is no cleanup mode, no write path, no telemetry to opt out of.

- 159 read-only IAM actions, zero write actions. [Verify the policy.](https://github.com/MissionFinOps/kulshan/blob/master/kulshan/iam/kulshan-readonly.json)
- Reports stay on your machine.
- Per-pack least-privilege policies at [`iam/per-check/`](https://github.com/MissionFinOps/kulshan/tree/master/kulshan/iam/per-check).
- Open source: Apache 2.0. IAM policy additionally CC BY 4.0.
- [Policy page with SHA256 attestation](https://missionfinops.com/policy/)

[Documentation](https://github.com/MissionFinOps/kulshan/tree/master/kulshan/docs) | [IAM Policy](https://github.com/MissionFinOps/kulshan/blob/master/kulshan/iam/kulshan-readonly.json) | [Changelog](https://github.com/MissionFinOps/kulshan/blob/master/kulshan/CHANGELOG.md)

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
aws sso login
kulshan preflight
kulshan report
```

Named profiles, environment variables, and role assumption all work:

```bash
kulshan --profile production report
kulshan --role-arn arn:aws:iam::123456789012:role/KulshanAudit report
```

Run `kulshan preflight` to check connectivity and permissions before scanning.

---

## CUR / Data Export Investigation

Query your CUR Parquet files locally or from S3. No Athena, no Glue, no data warehouse. DuckDB queries in place.

```bash
kulshan cur validate --path ./cur/
kulshan analyze cost --path ./cur/ --month 2024-06
kulshan analyze ec2 --cur ./cur/ --month 2024-06
kulshan analyze cost --s3 s3://bucket/prefix/ --month 2024-06
```

Top movers by service, account, region, usage type. Period-over-period deltas. Resource-level contributors. Tag coverage. All outputs include provenance, evidence IDs, and `human_review_required: true`.

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

Seven tools: `kulshan_preflight`, `kulshan_report`, `kulshan_quick_security`, `kulshan_list_packs`, `kulshan_cur_validate`, `kulshan_analyze_ec2`, `kulshan_analyze_cost`.

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

## Quick Reference

```bash
kulshan --version                       # Version
kulshan preflight                          # Check credentials and permissions
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
