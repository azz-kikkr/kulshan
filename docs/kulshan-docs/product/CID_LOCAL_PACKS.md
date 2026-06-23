# Investigation Packs Inspired by CID

This document is an implementation plan for using AWS Cloud Intelligence Dashboards (CID) as inspiration without depending on CID infrastructure.

Read `docs/product/MTTE.md` and `docs/product/LOCAL_EVIDENCE_ENGINE.md` first.

## Positioning

CID is the dashboard. Kulshan is the local detective.

CID proves the question set: cost overview, KPIs, service movement, account movement, usage type movement, untagged spend, commitment posture, and service-specific deep dives.

Kulshan should reuse that question catalog locally:

```text
CUR / Data Exports in S3 or local files
  -> DuckDB SQL views
  -> investigation packs
  -> evidence packet
  -> terminal / Markdown / JSON brief
```

Do not copy CID's deployment model:

```text
Glue
Athena
QuickSight
SPICE
dashboard deployment
daemon
SaaS control plane
```

## Proposed CLI wedge

Build a proof-of-concept local CUR query engine.

Commands:

```bash
kulshan cur validate --path <s3-or-local-path>
kulshan cur schema --path <s3-or-local-path>
kulshan cur top-services --path <s3-or-local-path> --month YYYY-MM
kulshan investigate ec2 --cur <s3-or-local-path>
```

Goal:

Prove Kulshan can query CUR / Data Exports Parquet locally and produce a simple EC2 delta brief.

Do not add dashboard, daemon, GPU, LLM, MCP, Slack, Jira, Athena dependency, QuickSight dependency, or CID dependency.

## Proposed implementation structure

This structure belongs in the Kulshan CLI repository, not necessarily this website repository.

```text
kulshan/
  cur/
    __init__.py
    validate.py
    schema.py
    top_services.py
    duckdb_engine.py
    source.py
  investigations/
    __init__.py
    registry.py
    views/
      cost_overview.sql
      service_deltas.sql
      account_deltas.sql
      resource_deltas.sql
      usage_type_deltas.sql
      untagged_spend.sql
      commitment_health.sql
    packs/
      __init__.py
      ec2_investigation.py
      rds_investigation.py
      data_transfer.py
      commitments.py
      ownership.py
  investigate/
    __init__.py
    brief.py
    evidence.py
    commands.py
```

## First SQL view stubs

The first implementation should create SQL view files with stable names and comments, even if some start as minimal queries.

Required initial stubs:

- `cost_overview.sql`
- `service_deltas.sql`
- `account_deltas.sql`
- `resource_deltas.sql`
- `usage_type_deltas.sql`
- `untagged_spend.sql`
- `commitment_health.sql`

Each view should:

- Query a normalized CUR relation, not raw source paths directly.
- Accept period boundaries through the calling layer.
- Return deterministic numeric columns.
- Avoid business narrative.
- Avoid LLM-generated SQL.

## Pack registry

The pack registry should map a pack name to SQL views, required inputs, outputs, and evidence-contract sections.

Example shape:

```text
cudos-lite:
  views:
    - cost_overview
    - service_deltas
    - account_deltas
    - usage_type_deltas
    - untagged_spend
  outputs:
    - current_period_cost
    - previous_period_cost
    - delta
    - top_services
    - top_accounts
    - untagged_spend
```

## First command

Start with the EC2 investigation command:

```bash
kulshan investigate ec2 --cur <s3-or-local-path> --month YYYY-MM
```

The first version should reuse the local SQL engine and evidence contract, then emit the brief defined in `docs/product/MTTE.md`.

## Non-goals

- Do not build dashboards.
- Do not require CID.
- Do not require Athena.
- Do not require Glue.
- Do not require QuickSight or SPICE.
- Do not add a daemon.
- Do not add Slack, Jira, MCP, or chat.
- Do not use LLMs for SQL, accounting, joins, or owner primitives.

## Rule

AWS CID is the proven question set.

Kulshan is local execution plus an evidence brief.
