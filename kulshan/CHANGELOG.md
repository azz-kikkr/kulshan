# Changelog

All notable changes to Kulshan will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-17

### Added
- Added structured preflight capability and pack-readiness reporting.
- Added canonical coverage disclosure across reports, SARIF, and local history.

### Fixed
- Corrected IAM ownership and source coverage validation.

## [0.4.2] - 2026-07-20

### Fixed
- Corrected three invalid S3 IAM action names in the published policy:
  - `s3:GetBucketEncryption` replaced with `s3:GetEncryptionConfiguration`
  - `s3:GetBucketLifecycleConfiguration` replaced with `s3:GetLifecycleConfiguration`
  - `s3:GetBucketReplication` replaced with `s3:GetReplicationConfiguration`
- Eliminated false-clean behavior: an authorization failure or API error during an S3 check can no longer produce a clean/passing result. Failed evaluations are now reported as "could not check" with the denied IAM action named.
- Applied the same fix to GuardDuty, AWS Config, and IAM Access Analyzer checks that previously swallowed AccessDenied errors silently.
- Fixed `access-analyzer:*` shorthand on the website policy page to accurately reflect the enumerated actions (`access-analyzer:Get*`, `access-analyzer:List*`).

### Security
- **Trust Ledger entry:** All versions prior to 0.4.2 could report a false clean for S3 encryption, lifecycle, or replication checks if the IAM policy granted the incorrect action names (which granted nothing). Previous policy SHA256: `3c9e7673091705aa70be9990e81ee5625bc876e2a4d68748e3761d4233decc7b`. Corrected policy SHA256: `ce96ca80037e1b9edc3e2d7125c6ff1acd41bae83e64f064c0ac1f0a1586d50e`.

## [0.4.1] - 2026-07-18

### Fixed
- Made Windows console output UTF-8 safe when the host terminal defaults to CP1252.
- Isolated AWS and DuckDB-backed MCP calls in bounded child processes so timeouts cannot leave the MCP server hung.
- Reported MCP domain failures as protocol tool errors instead of successful JSON envelopes.

## [0.3.4] - 2026-07-17

### Fixed
- All project URLs updated from legacy `azz-kikkr/kulshan` to `MissionFinOps/kulshan`
- Changelog link added to PyPI sidebar
- Em-dashes removed from all documentation files
- Contributing and getting-started docs point to correct repo

## [0.3.3] - 2026-07-17

### Changed
- README: restored mountain meaning in About the Name section, removed em-dashes
- Tagline updated to "Read-only AWS audit CLI"

## [0.3.2] - 2026-07-16

### Changed
- Project metadata: neutral description, org-level authorship, updated keywords
- README wording: removed consulting-oriented language, simplified maintainer attribution
- Removed legacy scaffolding and outdated directories from repository

## [0.3.1] - 2026-07-15

### Changed
- README rewritten: full v0.3 feature coverage, all links absolute for PyPI rendering
- Documentation suite: 9 focused guides (getting started, CLI reference, audit packs, workspaces, output formats, MCP, CI/CD, security/IAM, architecture, contributing)
- Consolidated docs from 16 files to 9 (merged configuration, troubleshooting, CUR analysis, security+IAM)

## [0.3.0] - 2026-07-15

### Added
- **Automatic AWS environment onboarding**: `aws login` then `kulshan report` automatically creates an isolated local environment using the verified STS identity. No manual workspace setup required.
- **Identity-based routing**: Kulshan identifies the active AWS principal via `GetCallerIdentity`, canonicalizes assumed-role ARNs (strips volatile session names), and routes to the correct local database automatically.
- **CUR payer binding**: When local CUR data contains a valid `bill_payer_account_id`, Kulshan automatically binds the environment to that payer. Mismatched or multiple payers are rejected.
- **Payer environment reconciliation**: `kulshan workspace reconcile` detects when multiple AWS identities access the same payer account and offers to link them into one environment. Never merges automatically.
- **Federated history**: `kulshan history` now includes read-only scans from linked (superseded) workspaces, showing a unified timeline per payer environment.
- **`--direct-only` flag**: `kulshan history --direct-only` shows only scans stored directly in the current workspace.
- **Consolidated payer reports**: When a workspace has multiple approved connections, `kulshan report` produces one report across all connections with deduplicated findings and per-connection coverage metadata.
- **Cost authority selection**: Payer-wide Cost Explorer data is only sourced from a connection whose account matches the verified payer, or an explicitly configured `cost_connection`. Arbitrary member accounts are never labeled payer-wide.
- **`kulshan workspace rename`**: Change display names without affecting the internal workspace ID or database path.
- **`scan_connections` table**: Per-connection execution metadata (account, status, duration, packs, errors) stored alongside the parent scan.
- **`scans.payer_account_id` column**: Consolidated scans store the verified payer separately from credential accounts.
- **Atomic consolidated persistence**: Parent scan and all connection metadata commit in one SQLite transaction; failure rolls back everything.

### Changed
- **Default UX**: Authenticated users are now auto-onboarded into isolated environments. The unbound-default-with-warning path is eliminated for users with valid AWS credentials.
- **Identity key**: Workspace identity is based on STS account + canonical principal ARN (v2), not profile name. Profile-based v1 keys still work for backward compatibility.
- **`payer_account_id` is now optional**: Auto-created workspaces start with `payer_account_id = null` until CUR evidence binds them. The STS account is no longer assumed to be the payer.
- **`history --account` filtering**: Now matches consolidated scans via `scan_connections.session_account_id` in addition to `scans.account_id`.
- **Display names derived from ARN**: e.g. `readonlyrole-cedar`, `billingaudit-river`, `alice-oak`.
- **Finding deduplication**: Composite key is now `fingerprint + account_id`. Same finding type on different accounts remains separate; same resource seen through two connections deduplicates to one.

### Fixed
- Assumed-role ARNs with volatile session names no longer create a new workspace on every login.
- Old raw-ARN registry entries are automatically migrated to canonical keys on first lookup.

### Upgrade Notes
- **Existing workspaces are preserved**: Default and manually created workspaces continue to work unchanged.
- **Schema migration is automatic**: New columns (`report_status`, `payer_account_id`) and `scan_connections` table are added on first database open. Non-destructive.
- **Registry gains v2 keys**: The identity registry now stores both v1 (profile-based) and v2 (identity-based) keys. Existing v1 entries are still used for lookup.
- **Behavioral change**: Running `kulshan report` without `--workspace` now auto-onboards if AWS credentials are available, instead of showing the unbound-workspace warning. Use `--workspace default` to force legacy behavior.

## [0.2.3] - 2026-07-13

### Fixed
- Cost column selection now uses null-aware probing in `analyze cost --path`, matching `cur validate` behavior
- Previously, `analyze cost` would fail with "No cost data found" when `line_item_net_unblended_cost` column existed but was all NULL (common in many CUR exports)
- Now correctly falls back to `line_item_unblended_cost` (or next available) when preferred column has no data
- `cost_basis.fallback_note` in JSON output now correctly reflects when a fallback column was used

### Changed
- Unified cost column selection logic: `cur validate` and `analyze cost` now use the same shared `select_nonnull_cost_column()` function
- Added `COST_COLUMN_CANDIDATES` constant to `kulshan.cur.schema` for consistent column preference order
- `CurColumnMapping` dataclass now includes `cost_fallback_note` field to track fallback selection

## [0.2.2] - 2026-07-13

### Fixed
- Handle Decimal types from DuckDB in cost investigation (fixes `unsupported operand type` error)

## [0.2.1] - 2026-07-13

### Added
- Local CUR cost investigation (`kulshan analyze cost --path`) for multi-service top-mover detection
- Generic `export_brief()` function for unified JSON/markdown/terminal output across all investigation types
- Full provenance on all investigation outputs (schema_version, kulshan_version, generated_at, data_through)
- Structured confidence assessment (label, source_agreement, data_completeness, ownership_confidence)
- Owner candidate inference with explicit `confirmation_required` flag
- Evidence items with unique IDs for traceability
- Suggested deep dives based on top movers (e.g., "kulshan analyze ec2" when EC2 is top mover)
- Review questions tailored to cost movement direction

### Changed
- `analyze cost` command now supports both `--path` (local) and `--s3` (remote) sources
- All investigation briefs now include `human_review_required: true`

### Design Decisions
- Confidence is structured components, NOT a numeric score
- Owner is always a candidate requiring human confirmation
- Every output includes provenance for reproducibility
- Evidence items have unique IDs for cross-referencing

## [0.2.0] - 2026-07-07

### Added
- Local CUR/Data Export schema inspection (`kulshan cur schema --path`)
- Local CUR/Data Export validation (`kulshan cur validate --path`)
- Local EC2 investigation brief with period-over-period delta analysis (`kulshan analyze ec2 --cur --month`)
- Account, region, resource, and usage-type delta breakdowns in EC2 investigation
- Tag coverage analysis (owner, team, application, cost center, environment tags)
- S3 readiness check for CUR/Data Export prefixes (`kulshan cur s3-check --s3`)
- S3-native cost investigation via DuckDB httpfs (`kulshan analyze cost --s3 --month`)
- Scan byte estimation with user confirmation threshold for S3 queries
- JSON and Markdown export for investigation commands (`--output file.json` or `--output file.md`)

## [0.1.3] - 2026-06-18

### Fixed
- Quick Start: `aws sso login` → `aws login` (correct command for AWS Identity Center)

## [0.1.2] - 2026-06-18

### Changed
- README rewritten: report-first positioning, compressed credentials/IAM/cost sections, 20-second Reddit test

## [0.1.1] - 2026-06-18

### Changed
- PyPI description rewritten: business-outcome-first positioning
- README rewritten: simplified Quick Start, em dashes replaced with colons, section reorder (humans first, agents second)
- Package description: "Local-first, read-only AWS audit CLI. Generate a VP/CFO-ready AWS audit report in minutes."

## [0.1.0] - 2026-04-21

### Added
- Initial project scaffold with CLI entry point and all component modules
- 10 tool subcommands: cost, sweep, tag, age, dr, pulse, limit, drift, topo, security
- Unified `Kulshan Report` command that runs all operational audits
- Shell tab completion support for bash, zsh, and fish (`Kulshan setup-completion`)
- Interactive `?` inline help (type `Kulshan ?`, `Kulshan cost ?`, etc.)
- AWS profile tab completion for `--profile` option (reads ~/.aws/config and ~/.aws/credentials)
- Rich-formatted `--help` output with "Mission FinOps" branded footer
- Themed CLI banners with purple accents and mountain flair
- Per-tool color themes (11 themes: Kulshan parent + 10 tool subcommands)
- `setup-completion` subcommand with auto-detection of user's shell

### Internal
- `Kulshan.theme` module with ToolTheme dataclass and THEME_REGISTRY
- `Kulshan.completion` module with AWSProfileType, completion script generation
- `Kulshan.question_mark` module with sys.argv interception and Rich help overlay
- `Kulshan.help_formatter` module with monkey-patched format_help footer
- `Kulshan.setup` module with one-call `setup_cli()` orchestrator
- Shared test fixtures in conftest.py (mock AWS config, Click group factories, Rich output capture)
