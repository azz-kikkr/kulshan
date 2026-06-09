# Getting Started

> **Status:** Kulshan is pre-PyPI. Installation today requires cloning the repository. PyPI release is planned but not committed to a date.

## Prerequisites

- Python 3.9+
- AWS credentials configured (any method: `aws configure`, env vars, SSO, instance role)
- Cost Explorer API enabled in your AWS account (wait 24h after enabling)

## Install

From the repo root:

```bash
bash setup.sh
# equivalent to: pip install -e kulshan
```

## First run

```bash
Kulshan Report --quick                              # 3 regions, fast
Kulshan Report                                      # all enabled regions
Kulshan Report --format json --output out.json     # JSON
Kulshan Report --format html --output out.html     # self-contained HTML
```

`Kulshan Report` runs all ten internal check packs and produces a single combined report.

A per-pack-only CLI (e.g. `Kulshan scan <pack>`) is not exposed today: every check runs as part of `Kulshan Report`.

## AWS permissions

Kulshan does not remediate or mutate customer infrastructure. Three permission options, in increasing precision:

- **Quickest:** attach the AWS managed `ViewOnlyAccess` + `SecurityAudit` policies to your IAM user/role.
- **Composed minimal:** [`kulshan/iam/kulshan-readonly.json`](../kulshan/iam/kulshan-readonly.json): the union of every action used by all ten check packs.
- **Per-pack minimal:** [`kulshan/iam/per-check/<check>.json`](../kulshan/iam/per-check/): granular policy for each check pack, useful if you want to scope a scan.

For Cost Explorer specifically, also attach `AWSBillingReadOnlyAccess` or grant the `ce:*` actions in [`kulshan/iam/per-check/cost.json`](../kulshan/iam/per-check/cost.json).

## API costs

- **Cost Explorer API:** $0.01 per request. A quick scan makes ~12-15 calls (~$0.15). A full scan with attribution makes ~25-30 calls (~$0.25-$0.30).
- **All other check packs:** $0. They use Describe/List/Get calls (free tier — no charges).
- **This is charged by AWS, not by Kulshan.** Kulshan is free. AWS bills CE API usage to your account.
- **Default lookback: 90 days.** Change with `--days 30` for a shorter (cheaper) scan.
- **Suppress the cost notice in CI/CD:** `Kulshan Report --yes` (or `-y`)
- **Skip cost pack entirely:** `Kulshan Report --packs security,sweep,dr` ($0.00)
- **Re-render without re-scanning:** `kulshan convert -i scan.json --format html` costs $0.00.
- See [docs/api-costs.md](api-costs.md) for the full breakdown per API call.

## Output formats

`Kulshan Report` supports:

- **Terminal**: Rich-formatted dashboard (default)
- **HTML**: self-contained single-file report (`--format html --output report.html`)
- **JSON**: machine-readable (`--format json --output report.json`)

## Local history and retention

Kulshan stores summary-only scan history in the platform-specific user data directory:

- Linux: `~/.local/share/Kulshan/history.db`
- macOS: `~/Library/Application Support/Kulshan/history.db`
- Windows: `%LOCALAPPDATA%\\missionfinops\\Kulshan\\history.db`

History older than 365 days is removed when a new scan is saved. Use `--no-history`
to avoid retaining a scan, or `kulshan delete-history --yes` to delete all saved scans.
The cost score trend file is stored beside the history database rather than in the
current working directory.

## What's next

- [Architecture](architecture.md): how Kulshan is structured internally
- [Check packs](checks/): what each of the ten audits checks
- [CLI reference](Kulshan/cli-reference.md): all `Kulshan` commands and flags
