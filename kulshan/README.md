# Kulshan

The blood test for your AWS bill.

Generate a local AWS audit report in minutes.

```bash
pip install kulshan
aws login
kulshan report
```

No setup. No data uploads. No infrastructure changes.

Just your AWS account and your laptop.

## What is Kulshan?

Kulshan reads your AWS account and generates a business-ready report covering:

- Cost anomalies and trends
- Waste and orphaned resources
- Tag compliance and cost attribution
- Commitment health (RI/SP coverage)
- Spend forecasting and acceleration
- Security posture
- DR readiness

Think of it as a baseline before deeper FinOps work, platform evaluations, or leadership reviews.

## What You Get

An HTML report you can open in a browser and hand to your VP, CFO, or platform team. Also available as JSON, SARIF, and CSV.

The report scores your account 0-100 across each dimension, highlights the top actions by dollar impact, and provides an executive summary paragraph.

## Install

```bash
pip install kulshan
```

Requires Python 3.9+. macOS, Linux, Windows.

## AWS Credentials

Kulshan uses the same AWS credentials as the AWS CLI.

```bash
aws login
kulshan report
```

[Credential setup docs](https://github.com/azz-kikkr/kulshan/tree/master/docs)

## More Commands

```bash
kulshan doctor                          # Verify credentials and permissions
kulshan report --quick                  # Fast scan (3 regions, ~60s)
kulshan report -o report.html           # Save as HTML
kulshan report --packs security,sweep   # Run specific packs
kulshan report --packs all              # Full 10-pack diagnostic
kulshan shell                           # Interactive REPL
```

## Trust & Security

Read-only by design. No write permissions required. Published IAM policy included.

- 147 read-only audit actions, zero write actions
- Reports stay on your machine, no uploads
- No telemetry, no phone-home
- Open source: Apache 2.0

[View the IAM policy](https://github.com/azz-kikkr/kulshan/tree/master/iam)

## AWS API Cost

Typical run cost: approximately $0.15-$0.25 in AWS Cost Explorer API charges. All non-cost packs use free AWS APIs.

[Cost details](https://github.com/azz-kikkr/kulshan/tree/master/docs)

## About the Name

Kulshan is the Lummi name for the mountain known colonially as Mt. Baker, meaning "great white watcher." We acknowledge the Lummi and Nooksack peoples as the original namers of this mountain.

## Built by

[Mission FinOps](https://missionfinops.com) | Mission, BC, Canada.

## AI Agents

Kulshan works with Claude Code, Codex, Kiro, Cursor, and any agent that can run shell commands. See [`agents/`](https://github.com/azz-kikkr/kulshan/tree/master/agents) for integration docs.

## License

Apache 2.0. Free and open source forever.
