# EC2 CUR MVP Implementation Plan

Read these first:

- `docs/product/MTTE.md`
- `docs/product/LOCAL_EVIDENCE_ENGINE.md`
- `docs/product/CID_LOCAL_PACKS.md`

This plan is only for the shortest path to:

```bash
kulshan investigate ec2 --cur <path>
```

using Python, DuckDB, and CUR / AWS Data Exports Parquet.

Do not discuss or implement Rust, GPU, dashboards, SaaS, agents, MCP, Slack, Jira, or future architecture in this phase.

## Goal

Prove that Kulshan can generate a useful EC2 investigation brief from local or S3-hosted CUR / Data Exports Parquet.

The moat is not DuckDB, Parquet, or SQLite. The moat is:

```text
Question
  -> Evidence
  -> Missing Evidence
  -> Meeting Questions
```

The product artifact is the investigation brief.

## Required modules

Proposed CLI repository structure:

```text
kulshan/
  cur/
    __init__.py
    source.py
    duckdb_engine.py
    validate.py
    schema.py
    top_services.py
    normalize.py
  investigate/
    __init__.py
    commands.py
    ec2.py
    evidence.py
    brief.py
    models.py
  investigations/
    __init__.py
    registry.py
    views/
      service_deltas.sql
      account_deltas.sql
      region_deltas.sql
      usage_type_deltas.sql
      resource_deltas.sql
      tag_coverage.sql
      untagged_spend.sql
```

Responsibilities:

- `cur/source.py`: resolve local paths and S3 paths into DuckDB-readable Parquet globs.
- `cur/duckdb_engine.py`: create DuckDB connection, install/load `httpfs` when S3 is used, register CUR relation.
- `cur/validate.py`: verify readable Parquet files and required billing columns.
- `cur/schema.py`: print detected columns and map them to normalized names.
- `cur/normalize.py`: create a normalized CUR view with stable column names.
- `investigate/ec2.py`: orchestrate EC2-specific query flow.
- `investigate/evidence.py`: assemble evidence and missing-evidence sections.
- `investigate/brief.py`: render terminal, Markdown, and JSON later; terminal first.
- `investigations/registry.py`: map investigation names to views and required evidence.

## CLI commands

Minimum commands:

```bash
kulshan cur validate --path <s3-or-local-path>
kulshan cur schema --path <s3-or-local-path>
kulshan cur top-services --path <s3-or-local-path> --month YYYY-MM
kulshan investigate ec2 --cur <s3-or-local-path> --month YYYY-MM
```

The first successful product moment is:

```bash
kulshan investigate ec2 --cur ./exports --month 2026-06
```

## SQL views

All SQL should query a normalized CUR relation, not raw file paths directly.

Normalize common CUR / Data Exports columns into:

```text
billing_period
usage_start_date
linked_account_id
linked_account_name
region
service
usage_type
operation
resource_id
unblended_cost
amortized_cost
tag_owner
tag_application
tag_environment
```

Required initial views:

- `service_deltas.sql`: current vs previous period by service.
- `account_deltas.sql`: EC2 delta by linked account.
- `region_deltas.sql`: EC2 delta by region.
- `usage_type_deltas.sql`: EC2 delta by usage type.
- `resource_deltas.sql`: EC2 delta by resource ID when present.
- `tag_coverage.sql`: tag presence and owner/application/environment coverage.
- `untagged_spend.sql`: EC2 spend without useful ownership tags.

First EC2 investigation queries:

```text
1. Find current and previous EC2 cost.
2. Calculate absolute and percentage delta.
3. Rank top accounts by delta.
4. Rank top regions by delta.
5. Rank top usage types by delta.
6. Rank top resource IDs by delta when available.
7. Calculate tag coverage for the changed slice.
8. Identify missing evidence.
9. Generate meeting questions from missing evidence and top deltas.
```

## Cache layout

Use the user's local cache directory, with a configurable override later.

```text
~/.kulshan/
  cache/
    cur/
      normalized/
        source_hash=<hash>/
          billing_month=YYYY-MM/
            service=AmazonEC2/
              part-000.parquet
  state/
    kulshan.sqlite
```

For the MVP, cache can be optional. The first implementation may query files directly and only write SQLite state for manifests and runs.

Do not upload data. Do not write to S3. Do not create Athena or Glue resources.

## SQLite schema

Minimum durable state:

```sql
CREATE TABLE cur_sources (
  id INTEGER PRIMARY KEY,
  source_uri TEXT NOT NULL,
  source_type TEXT NOT NULL,
  detected_format TEXT,
  schema_hash TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE cur_source_objects (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL,
  object_uri TEXT NOT NULL,
  etag TEXT,
  size_bytes INTEGER,
  last_modified TEXT,
  FOREIGN KEY (source_id) REFERENCES cur_sources(id)
);

CREATE TABLE investigation_runs (
  id INTEGER PRIMARY KEY,
  command TEXT NOT NULL,
  service TEXT NOT NULL,
  billing_month TEXT NOT NULL,
  source_id INTEGER,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  FOREIGN KEY (source_id) REFERENCES cur_sources(id)
);

CREATE TABLE evidence_items (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  value TEXT,
  source_view TEXT,
  source_query_hash TEXT,
  FOREIGN KEY (run_id) REFERENCES investigation_runs(id)
);
```

## Sample output

```text
Investigation Summary

Service:
EC2

Period:
2026-05 -> 2026-06

Impact:
$11,200 -> $14,620
+$3,420 (+30.5%)

Top Contributors:
1. Account 123456789012 +$1,920
2. us-west-2 +$1,340
3. BoxUsage:m6i.4xlarge +$980

Likely Owner:
Unknown

Owner Confidence:
Tag match: Low
Account ownership: Unknown
Naming match: Not evaluated
Overall: Low

Evidence:
[available] CUR/Data Exports Parquet
[available] EC2 service delta
[available] Account delta
[available] Usage type delta

Missing:
[missing] Resource inventory
[missing] Owner tags for changed spend
[missing] CloudTrail correlation
[missing] Deployment record

Questions for Meeting:
1. Which team owns the account with the largest EC2 delta?
2. Was new m6i.4xlarge capacity launched during the period?
3. Was this expected production growth or temporary workload expansion?
```

## Estimated implementation order

1. Add DuckDB dependency and a tiny connection wrapper.
2. Implement local Parquet path resolution.
3. Implement S3 path support through DuckDB `httpfs`.
4. Add `kulshan cur schema --path`.
5. Add `kulshan cur validate --path`.
6. Create normalized CUR view with stable column aliases.
7. Add `kulshan cur top-services --path --month`.
8. Add EC2 SQL views for service/account/region/usage/resource deltas.
9. Add evidence packet model.
10. Add terminal brief renderer.
11. Add `kulshan investigate ec2 --cur --month`.
12. Add tests using a tiny synthetic Parquet fixture.

## Acceptance test

Given a tiny synthetic CUR Parquet fixture with May and June EC2 rows, this command:

```bash
kulshan investigate ec2 --cur ./tests/fixtures/cur_parquet --month 2026-06
```

must print:

- Previous period EC2 cost.
- Current period EC2 cost.
- Absolute delta.
- Percentage delta.
- Top contributors.
- Evidence available.
- Evidence missing.
- Questions for meeting.

If this works, the local evidence thesis is validated enough to keep building.
