# Kulshan

**Free, open-source AWS audit CLI with no infrastructure mutation or remediation.**

Kulshan scans your AWS account across ten audit dimensions and produces a unified scored report. Scanning and report generation are local-first by default.

## Install

```bash
pip install kulshan
```

## Quick Start

```bash
kulshan report --quick              # Quick scan (3 regions)
kulshan report --format html        # Full HTML report
kulshan report --packs security,sweep  # Free packs only ($0 AWS cost)
kulshan shell                       # Interactive REPL
```

## What You Get

- **Cost analysis** — multi-method anomaly detection (z-score, IQR, MAD), RI/SP coverage, forecasting
- **Security posture** — 50+ checks across IAM, network, encryption, logging
- **Waste detection** — orphaned EBS, EIPs, snapshots, idle ALBs, NAT gateways
- **DR readiness** — backup coverage, Multi-AZ, single points of failure
- **Lifecycle audit** — EOL runtimes, expiring certs, stale AMIs
- **IaC drift** — CloudFormation drift detection, coverage gaps
- **Tag compliance** — untagged resources, cost attribution gaps
- **Observability** — alarm coverage, logging gaps, blind spots
- **Quota headroom** — service limits, scaling blockers
- **Network topology** — CIDR overlaps, route integrity, flow log coverage

Output formats: terminal (scored dashboard), JSON, HTML, SARIF, CSV.

## Trust & Security

- **No infrastructure mutation** — no remediation or customer-resource changes
- **Auditable permissions** — 147 explicit actions, including non-mutating `DetectStackDrift`
- **Local-first by default** — no active telemetry implementation
- **Explicit integrations** — optional webhooks send data only when deliberately invoked
- **Published IAM policy** — inspect every action before granting access
- **Open source** — Apache 2.0, read every line of code on GitHub
- **Sensitive-data masking** — common identifiers are masked by default; review reports before sharing

## AWS API Costs

- Cost pack: ~$0.20-0.40/run (Cost Explorer @ $0.01/request, billed by AWS)
- All other 9 packs: $0 (free-tier APIs only)
- Skip cost pack: `kulshan report --packs security,sweep,dr`

## About the Name

Kulshan is the Lummi name for the mountain known colonially as Mt. Baker — meaning "great white watcher." We acknowledge the Lummi and Nooksack peoples as the original namers of this mountain.

## License

Apache 2.0 — free and open source forever.

## Built by

[Mission FinOps](https://github.com/azz-kikkr/kulshan) — Mission, BC, Canada.

6+ years at AWS helping enterprise customers build FinOps solutions. Kulshan shows the art of the possible: what a proper FinOps pipeline looks like using Cost Explorer, without fancy tooling or $50K platforms.
