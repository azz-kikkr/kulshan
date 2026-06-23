# Kulshan Local Evidence Engine Architecture

Read this document before building the CUR / Data Exports query path.

This is the architecture companion to `docs/product/MTTE.md`. The MTTE spec defines the product workflow. This document defines the local evidence engine that should power it.

## Executive recommendation

Kulshan should be built as a local-first analytical toolchain:

```text
AWS evidence in S3 or read-only APIs
  -> local normalization
  -> partitioned Parquet evidence cache
  -> SQLite metadata and investigation state
  -> DuckDB analytical queries
  -> deterministic evidence packet
  -> optional narrative brief
```

The long-term architecture should look more like `git + sqlite + duckdb + parquet` than like a mini SaaS FinOps platform.

Recommended default choices:

- Analytical engine: DuckDB.
- Evidence substrate: Parquet.
- Metadata and investigation state: SQLite.
- Runtime model: local CLI, no required daemon, no required cloud control plane.
- Execution model: CPU-first, vectorized, deterministic.
- LLM usage: optional narrative synthesis after deterministic evidence selection, never core accounting.

## Implementation language stance

The architectural north star can be a single Rust CLI embedding DuckDB and SQLite. Rust is attractive for a durable local engine because it offers memory safety, strong concurrency primitives, cross-platform binaries, and low operational overhead.

But the current Kulshan materials identify Python 3.9+ as the project language. Do not block the ASAP CUR feature on a full Rust rewrite.

Recommended path:

1. Implement the first `kulshan investigate <service> --cur ...` path in the existing Python CLI using DuckDB.
2. Keep query contracts, cache layout, evidence packet schemas, and SQL isolated from Python-specific presentation code.
3. Revisit a Rust core once the query contract and evidence cache have proven useful.

The highest-risk decision today is not Python versus Rust. It is delaying the local CUR investigator until after a rewrite.

## Engine choice

DuckDB should be the default engine because it is embedded, local, vectorized, cross-platform, and excellent at SQL over Parquet. It fits the product goal: serious local analytics without running a database service.

Use DuckDB for:

- Reading S3-hosted Parquet CUR / Data Exports.
- Reading local Parquet cache files.
- Aggregating service, account, usage type, region, resource, and tag deltas.
- Producing deterministic top-contributor tables.
- Joining normalized billing slices to local metadata tables or exported resource inventory.

Other engines:

- DataFusion is the best future Rust-native extensibility path if Kulshan needs custom planning or domain-specific operators.
- Polars is useful for dataframe transforms and possible later GPU acceleration, but should not be the first center of gravity.
- ClickHouse Local is useful as a benchmark or specialist backend, not the default embedded engine.
- SQLite is for durable metadata and small state, not the billing scan engine.
- Arrow is an interoperability substrate, not the product engine.
- Iceberg / Delta should be optional later for users who already have those layouts, not a required MVP lakehouse layer.

## Storage model

Kulshan should use a two-tier evidence model.

Remote evidence:

- AWS Data Exports / CUR in S3.
- Cost Explorer summaries.
- Resource inventory.
- Resource tags.
- Account ownership metadata.
- CloudTrail later.

Local durable evidence:

- Partitioned Parquet cache for normalized billing and resource slices.
- SQLite database for source manifests, cache indexes, investigation runs, ownership maps, tag normalization maps, evidence hashes, and human overrides.

Do not ingest everything into a proprietary local database by default. Also do not query S3 forever for every repeated investigation. First touch can read S3 directly; repeated or multi-source investigations should promote useful slices into local Parquet.

## Cache policy

Use SQLite to track source manifests and cache state.

Cache keys should include:

- Source URI.
- Billing period.
- Account.
- Service.
- Region when applicable.
- Export schema or source version.
- Selected column set.
- Source object etag, size, or last modified timestamp when available.

Materialize local Parquet when:

- The same period/service/account slice is queried repeatedly.
- The investigation needs joins across cost, tags, inventory, and events.
- S3 scans become the slow part of interactive investigation.
- The user explicitly asks for local cache or offline replay.

Avoid repeated wide scans. Normalize and persist only the columns needed for investigation-grade joins.

## Deterministic pipeline

The investigation pipeline should be:

```text
Evidence acquisition
  -> schema normalization
  -> deterministic change detection
  -> deterministic candidate generation
  -> auditable ownership resolution
  -> evidence scoring and gap analysis
  -> brief assembly
  -> optional LLM narrative polish
```

These stages must be deterministic:

- Raw source parsing.
- Schema mapping and column projection.
- Currency, account, region, and time normalization.
- Cost delta calculations.
- Joins across billing, tags, inventory, and ownership metadata.
- Evidence lineage tracking.
- Final numeric tables.

These stages may use auditable heuristics:

- Ownership ranking when tags are incomplete.
- Cause clustering.
- Evidence relevance scoring.
- Missing-evidence prioritization.
- Confidence explanation.

LLMs may help write prose after the evidence packet is assembled. LLMs must not own parsing, accounting, joins, owner attribution primitives, evidence inclusion rules, or final cost deltas.

## MVP query scope

The first local CUR engine should support:

```bash
kulshan investigate ec2 --cur s3://billing-bucket/exports/cur-2/
kulshan investigate ec2 --cur ./exports/cur-2/
```

Minimum query outputs:

- Previous period cost.
- Current period cost.
- Delta and percentage change.
- Top accounts by delta.
- Top regions by delta.
- Top usage types by delta.
- Top resources by delta when resource IDs exist.
- Tag coverage for the changed slice.
- Untagged spend in the changed slice.
- Evidence source manifest.

The first version does not need a dashboard, daemon, GPU path, Iceberg catalog, or custom query planner.

## GPU stance

Do not require GPU for the default CLI experience.

GPU acceleration is plausible later for repeated, local, memory-resident grouped aggregations and joins. It is not the first bottleneck for most billing investigations. The first bottlenecks are usually S3 IO, file layout, Parquet column pruning, partition pruning, decompression, and repeated full scans.

Design the query layer so a future backend can use RAPIDS/cuDF, Polars GPU, or another accelerator. Do not let that future path delay the CPU-first DuckDB implementation.

## Roadmap

MVP:

- Existing CLI language path.
- DuckDB local query engine.
- S3 and local Parquet CUR / Data Exports input.
- SQLite manifests and investigation state.
- Local Parquet cache for normalized slices.
- Deterministic evidence packet.
- Markdown, JSON, and terminal investigation brief.

Next:

- Incremental cache refresh.
- Stronger ownership inference.
- CloudTrail correlation.
- Evidence snapshotting.
- Plugin surface for evidence sources and custom rules.
- Optional Iceberg / Delta reads for users who already have those formats.

Later:

- Rust core if packaging, performance, or safety justify it.
- DataFusion components if domain-specific operators outgrow DuckDB.
- Optional GPU backend if profiling proves CPU vectorization is the bottleneck.
- Reproducible investigation bundles for replay and audit.

## Product constraints

The architecture must preserve these constraints:

- Local-first.
- Read-only AWS access.
- No telemetry.
- No billing-data upload.
- No required daemon.
- No required SaaS control plane.
- Deterministic accounting.
- Evidence before narrative.

The engine should be boring in the best sense: auditable, replayable, and dependable.
