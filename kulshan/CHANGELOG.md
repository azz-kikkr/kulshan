# Changelog

All notable changes to Kulshan will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-07

### Added
- Local CUR/Data Export schema inspection (`kulshan cur schema --path`)
- Local CUR/Data Export validation (`kulshan cur validate --path`)
- Local EC2 investigation brief with period-over-period delta analysis (`kulshan investigate ec2 --cur --month`)
- Account, region, resource, and usage-type delta breakdowns in EC2 investigation
- Tag coverage analysis (owner, team, application, cost center, environment tags)
- S3 readiness check for CUR/Data Export prefixes (`kulshan cur s3-check --s3`)
- S3-native cost investigation via DuckDB httpfs (`kulshan investigate cost --s3 --month`)
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
