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

Kulshan uses your existing AWS CLI credentials.

Recommended:

```bash
aws login
kulshan report
```

If your AWS CLI does not support `aws login`, use:

```bash
aws sso login
```

or configure credentials with:

```bash
aws configure
```

[Credential setup docs](https://github.com/azz-kikkr/kulshan/tree/master/docs)

## More Commands

```bash
kulshan --version                       # Show version
kulshan doctor                          # Verify credentials and permissions
kulshan report --quick                  # Fast scan (3 regions, ~60s)
kulshan report -o report.html           # Save as HTML
kulshan report --packs security,sweep   # Run specific packs
kulshan report --packs all              # Full 10-pack diagnostic
kulshan history                         # View past scans
kulshan shell                           # Interactive REPL
```

## All 10 Audit Packs

| Pack | What it checks |
|------|---------------|
| `cost` | Cost trends, anomalies, commitments (default) |
| `security` | IAM, encryption, network exposure |
| `sweep` | Orphaned/idle resources (waste detection) |
| `dr` | Backup coverage, multi-AZ, single points of failure |
| `age` | EOL runtimes, expiring certificates |
| `drift` | CloudFormation drift, IaC coverage |
| `tag` | Tag compliance, unattributed spend |
| `pulse` | Observability gaps, alarm coverage |
| `limit` | Service quota headroom |
| `topo` | VPC topology, CIDR overlaps, route integrity |

```bash
kulshan report --packs cost,security,sweep --regions us-east-1
kulshan report --packs all --regions us-east-1,us-west-2
```


## CUR/Data Export Investigations

Kulshan can investigate cost movements directly from CUR/Data Export Parquet files, both locally and from S3. No Athena, no Glue, no data warehouse required.

### Local Investigation (Recommended)

```bash
# Validate CUR Parquet structure
kulshan cur validate --path ./cur/

# Inspect schema mapping
kulshan cur schema --path ./cur/

# Investigate cost top-movers across all services
kulshan investigate cost --path ./cur/ --month 2024-06

# Investigate EC2-specific movements
kulshan investigate ec2 --cur ./cur/ --month 2024-06

# Export as JSON (for AI agents) or Markdown
kulshan investigate cost --path ./cur/ --month 2024-06 -o report.json
kulshan investigate ec2 --cur ./cur/ --month 2024-06 -o report.md
```

### S3-Native Investigation

```bash
# Check S3 readiness (no data download)
kulshan cur s3-check --s3 s3://bucket/prefix/

# Investigate from S3 via DuckDB httpfs
kulshan investigate cost --s3 s3://bucket/prefix/ --month 2024-06
```

### Evidence Contract

All investigation outputs include a full evidence contract for AI agent trust:

- `human_review_required: true` — always
- Structured confidence assessment (not a numeric score)
- Evidence items with unique IDs for traceability
- Full provenance (schema version, kulshan version, timestamps)
- Suggested deep dives and review questions

### What Investigations Include

**Cost Investigation (`investigate cost --path`):**
- Top movers by service, account, region, usage type
- Period-over-period delta with percentages
- Suggested next steps (e.g., "run investigate ec2" if EC2 is top mover)
- Review questions for finance meetings

**EC2 Investigation (`investigate ec2 --cur`):**
- Instance family, region, pricing model breakdowns
- Resource-level contributors
- Tag coverage analysis (owner, team, application tags)
- Owner candidate inference with confirmation required
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
