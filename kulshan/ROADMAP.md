# Kulshan - What's Built and What's Next

## What V0.1 Does (Current)

Kulshan V0.1 is a unified CLI that runs 10 AWS operational audits from a single command and displays a combined dashboard.

### What you get today

```
Kulshan Report --quick
```

This runs all 10 audit packs sequentially against your AWS account and shows:

- A Rich terminal view with scores, grades, and colored progress bars for each pack
- An overall weighted score (0-100) across all dimensions
- Severity breakdown (critical, high, medium, low)
- JSON export for programmatic consumption

### The 10 operational dimensions

| Tool | What it checks | Category |
|------|---------------|----------|
| Cost Analyzer | Anomaly detection, efficiency scoring, RI/SP coverage | FinOps |
| Security Scanner | 50+ checks, attack paths, CIS/NIST/SOC2 compliance | Security |
| Waste Detector | Orphaned EBS, EIPs, NAT GWs, idle RDS, stale Lambdas | Waste |
| DR Readiness | Backup coverage, multi-AZ, SPOF detection, failover | Resilience |
| Lifecycle Audit | EOL runtimes, expiring certs, staleness tax | Freshness |
| IaC Drift | CloudFormation drift, IaC coverage, severity classification | Governance |
| Tag Governance | Tag compliance, dark money, value chaos | FinOps |
| Observability | Blind spots, alarm coverage, logging gaps | Monitoring |
| Quota Headroom | Service limits, scaling event planner | Capacity |
| Network Topology | VPC mapping, CIDR overlaps, routing integrity | Architecture |

### Architecture

- `cli.py` - Click CLI with `report` command + passthrough to all 10 tool CLIs
- `orchestrator.py` - Runs all tools sequentially with Rich progress bar
- `adapters/` - 10 adapter modules (one per tool, ~30-50 lines each)
- `session.py` - Shared boto3 session with role assumption support
- `report/terminal.py` - Rich dashboard renderer with score bars and icons
- `models.py` - Full dataclass library (Finding, ScanResult, CombinedScanResult, etc.)

### What V0.1 does NOT have

- No AI/SLM narratives
- No HTML reports
- No licensing or paid tiers
- No scan history or trend tracking
- No config files (CLI flags only)
- No CI/CD integration
- No telemetry

---

## Roadmap to V1.0

### V0.2 - Reports, History, and Hidden Cost Detection

**Goal:** Make scan results persistent, shareable, and catch the cost drivers that surprise everyone.

> **Note on status:** This is the planning view. The shipping status of each item below is tracked at https://missionfinops.com/changelog/ and in `Kulshan/CHANGELOG.md`. Items here may be on the branch already, in flight, or still scheduled. Do not treat the bullets in this section as commitments.

What gets added:

**Reports and persistence:**
- Self-contained HTML report (single file, no CDN, inline SVG charts, dark mode)
- SQLite scan history with WAL mode (store results, view trends)
- `kulshan history` command with sparkline trends
- TOML config file support (`~/.config/Kulshan/config.toml`)
- IAM policy generator (`Kulshan iam-policy --tool all`)
- `Kulshan Report --format html --output report.html`

**New cost checks (from real-world feedback):**
- CloudWatch Logs cost analysis: flag high-ingestion log groups, missing retention policies, ingestion-vs-retention ratio
- S3 request cost decomposition: break S3 into storage vs. API requests, flag high PUT/LIST costs, detect missing lifecycle policies (no tiering, no version cleanup, no expiration)
- AWS Config cost awareness: flag continuous recording, estimate Config costs from resource churn, recommend periodic recording
- Cross-AZ traffic detection: specifically detect `DataTransfer-Regional-Bytes`, flag high cross-AZ patterns, distinguish from inter-region
- "AWS Tax" rollup: aggregate Config + GuardDuty + Security Hub + Inspector + CloudTrail into a single overhead line item

**Terminal report wow features:**
- "Money on Fire" callout: top 3 findings by monthly dollar impact, shown above the score table with 🔥 emoji
- Quick wins section: findings that take <30 min to fix and save >$100/month, sorted by ROI
- Score delta from last scan: ↑↓ arrows next to each tool score ("Cost: 72 → 78 ↑6")
- Waste dollar total: bold monthly waste estimate at the top ("Burning ~$X,XXX/month on orphaned resources")

**HTML report wow features:**
- Executive summary hero section: one-paragraph template-based overview at the top
- Interactive cost Sankey diagram: Account → Service → Usage Type → Cost, inline SVG, no dependencies
- Cost treemap: proportional rectangles by service, click to drill into usage types
- Findings heatmap: severity × tool grid, red squares show where pain concentrates
- "What Changed" diff panel: green/red delta badges showing improvements and regressions since last scan
- Remediation playbook: ranked actions with estimated savings, effort, and copy-pasteable CLI/IaC snippets, grouped by "Quick Wins" / "This Sprint" / "Next Quarter"
- Print-optimized layout: looks good as PDF for stakeholders who want a document

No rewrites needed. The data structures and orchestrator from V0.1 feed directly into the report generator and new checks.

### V0.3: TBD (SLM and AI narrative work deferred)

**Status:** The original V0.3 plan added a local SLM (Qwen3-4B-Instruct) for AI-powered narratives. That work is deferred without a committed date. See "Deferred / future consideration" in `VISION.md` for the reasoning.

Kulshan instead leans on the receipts model. Every finding ships the number, the query that produced it, the evidence, the opinion that fired the rule, and a remediation hint. There is no narrative layer between the number and the source.

The cost-pack additions originally tagged for V0.3 are still planned, just unscheduled:

- Security tooling cost-vs-value (GuardDuty, Security Hub, Inspector cost per finding)
- KMS bucket key check (flag S3 buckets using CMKs without bucket keys; estimate KMS API savings)
- Extended Support surcharge detection (flag RDS/EKS on extended support; show dollar premium vs. upgrading)
- EBS snapshot lifecycle analysis (analyze AWS Backup retention policies; flag aggressive retention without S3 tiering)
- CloudWatch metrics dimension analysis (detect excessive dimensions; estimate cost impact of cardinality)

These will move into V0.2 or beyond depending on what gets shipped first. Status, again, lives at https://missionfinops.com/changelog/.

### V0.4 - CI/CD and Notifications

**Goal:** Make Kulshan sticky in engineering workflows.

What gets added:
- `Kulshan comment --provider github` posts scan summary to PRs
- `Kulshan comment --provider gitlab` for GitLab MRs
- SARIF v2.1.0 output for GitHub Security tab
- Slack Block Kit notifications via webhook
- Microsoft Teams Adaptive Card notifications
- Configurable exit codes (0=pass, 1=findings, 2=error, 3=config)
- CI threshold config: fail on severity, cost increase %, waste dollars
- GitHub Action (official)

### V0.5 - Telemetry and Trust

**Goal:** Build the feedback loop and trust signals.

What gets added:
- Opt-in anonymous telemetry (disabled by default, first-run prompt)
- Crash reporting (separate opt-in, per-crash confirmation)
- PyPI Trusted Publishers with Sigstore attestations
- SBOM generation (CycloneDX) in CI
- SECURITY.md with vulnerability disclosure process
- Strict dependency pinning via lock files

### V1.0 - Paid Product (TBD)

**Goal:** Add a paid upgrade path for the features that justify charging.

What gets added (subject to scope at release time):

- JWT RS256 license validation with offline support
- Possible paid packaging, not yet committed. Candidate shapes could include individual, team, or enterprise tiers, but names, limits, billing model, and pricing are intentionally undecided. The CLI is free today and that posture stays at minimum.
- Paid features (candidates, none committed): scan history, multi-account orchestration, advanced report exports, custom compliance mappings
- Swappable billing-provider abstraction
- License-token validation with offline grace period

Specific tier prices and packaging are not published in this roadmap.

Mission FinOps's current services (a free Kulshan-output review, a structured FinOps deep dive, ongoing advisory, and a consultancy or MSP license arrangement) are scoped after fit and documented at https://missionfinops.com/work-with-me/ and https://missionfinops.com/msp/. Those are present-tense services for the practice today, not the future product-tier structure described above.

### Post V1.0

Possibilities, none committed:

- Expansion packs (DR, security depth, AI cost) bundled into higher tiers as they prove out
- SOC2 evidence pack generation
- Partner program for FinOps consultancies and fractional CTOs

Explicitly deferred (no committed date):

- **MCP server.** Tracked as a future possibility; receipts framing tested first.
- **FOCUS 1.3 ingestion.** Tracked, not scheduled.
- **Bedrock and SageMaker AI cost analysis.** Deferred; depends on AWS pricing-API stability and customer demand.
- **Local SLM narrative layer.** See V0.3 above.

Not on this list:

- Multi-cloud support. AWS only.

---

## Version Summary

| Version | Focus | Key Deliverable |
|---------|-------|----------------|
| V0.1 | Ship it | 10-tool CLI with Rich dashboard |
| V0.2 | Persist, share, and surface hidden costs | HTML reports (Sankey, treemap, heatmap), scan history, CloudWatch/S3/Config/cross-AZ checks, "AWS Tax" rollup |
| V0.3 | Deferred (SLM/AI narratives parked) | Receipts framing covers the trust need. Cost-pack additions unscheduled. |
| V0.4 | Workflow | CI/CD, PR comments, Slack/Teams |
| V0.5 | Trust | Telemetry, supply chain security |
| V1.0 | Revenue | Licensing, paid tiers, monetization |

---

## File Count (V0.1)

| Category | Files | Lines (approx) |
|----------|-------|----------------|
| Core (cli, session, orchestrator) | 3 | ~250 |
| Check packs (10 internal) | varies | ~3500 |
| Terminal renderer | 1 | ~160 |
| Data models | 1 | ~600 |
| Constants + errors | 2 | ~30 |
| Tests | 3 | ~350 |
| Config (pyproject, setup.sh) | 2 | ~100 |
| **Total new V0.1 code** | **~23 files** | **~1,890 lines** |

The full architecture for V1.0 is already designed and documented in `.kiro/specs/aws-ops-suite/`. Every component has exact code, data models, prompt templates, CSS, JS, database schemas, and CI/CD workflows ready to implement when the time comes.
