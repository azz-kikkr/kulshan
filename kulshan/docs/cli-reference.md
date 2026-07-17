# CLI Reference

Complete command, option, and flag reference for Kulshan v0.3.0.

---

## Global Options

These options apply to all commands when placed before the subcommand:

| Option | Type | Description |
|--------|------|-------------|
| `--version` | flag | Show version and exit |
| `--profile TEXT` | string | AWS CLI profile name |
| `--role-arn TEXT` | string | IAM role ARN to assume |
| `--workspace, -w TEXT` | string | Workspace name for multi-payer isolation |
| `--connection, -c TEXT` | string | Named AWS connection within the workspace |
| `--help` | flag | Show help and exit |

### Example

```bash
kulshan --profile prod --workspace acme report --packs all --regions us-east-1
```

---

## Exit Codes

| Code | Name | Meaning |
|------|------|---------|
| 0 | SUCCESS | Scan completed, no critical findings |
| 1 | FINDING_FAIL | Scan completed, critical findings present |
| 2 | RUNTIME_ERROR | Unrecoverable error during execution |
| 3 | CONFIG_ERROR | Invalid configuration, credentials, or arguments |

Use exit code 1 in CI/CD to fail pipelines when critical findings are detected.

---

## Commands

### `kulshan report`

Run an audit scan using the selected packs.

```bash
kulshan report [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--quick` | flag | — | Fast baseline, skips confirmation prompt |
| `--format` | choice | `terminal` | Output format: `terminal`, `json`, `html`, `sarif`, `csv` |
| `-o, --output PATH` | path | — | Write output to file (auto-detects format from extension) |
| `--days INTEGER` | int | 90 | Cost analysis lookback period (1–365 days) |
| `--show-pii` | flag | — | Show full account IDs in exported reports |
| `-y, --yes` | flag | — | Skip all confirmations (for CI/CD) |
| `--packs TEXT` | string | `cost` | Comma-separated pack names, or `all` |
| `--regions TEXT` | string | auto | Comma-separated regions to scan |
| `--no-history` | flag | — | Do not retain this scan in local history |
| `--perf` | flag | — | Show pack and AWS API timing details |
| `--deep` | flag | — | Run expensive deep checks instead of fast default |

**Format auto-detection:** When `-o` is provided without `--format`, the format is inferred from the file extension (`.html`, `.json`, `.sarif`, `.csv`).

**Region defaults:** The `cost` pack uses `us-east-1` only (Cost Explorer is global). Inventory packs (security, sweep, etc.) default to scanning up to 3 enabled regions. Use `--regions` to override.

**Examples:**

```bash
# Default cost baseline (~30s, ~$0.15)
kulshan report

# HTML report
kulshan report -o report.html

# Security scan of one region
kulshan report --packs security --regions us-east-1

# Full 10-pack scan, skip prompts
kulshan report --packs all --regions us-east-1,us-west-2 --yes

# JSON for CI/CD pipelines
kulshan report --format json -o scan.json --yes

# 30-day lookback instead of 90
kulshan report --days 30

# Deep checks (slower, more thorough)
kulshan report --packs security --regions us-east-1 --deep
```

---

### `kulshan preflight`

Check AWS connectivity and permissions without running a scan. No cost incurred.

```bash
kulshan preflight
```

No options. Validates:
- Python version
- AWS credentials
- STS caller identity
- Cost Explorer API access
- EC2 read access (for inventory packs)
- Organizations access (optional, for multi-account)

---

### `kulshan history`

Show past scan history with scores and trends.

```bash
kulshan history [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `-n, --limit INTEGER` | int | — | Maximum number of scans to display |
| `--show-pii` | flag | — | Show full account IDs (redacted by default) |
| `--account TEXT` | string | — | Filter by AWS account ID (12 digits) |
| `--direct-only` | flag | — | Only show scans from the current workspace (exclude linked) |

**Examples:**

```bash
kulshan history
kulshan history --limit 10
kulshan history --account 123456789012
kulshan history --direct-only
```

---

### `kulshan delete-history`

Permanently delete all locally stored scan history for the current workspace.

```bash
kulshan delete-history [OPTIONS]
```

| Option | Type | Description |
|--------|------|-------------|
| `--yes` | flag | Delete without interactive confirmation |

---

### `kulshan convert`

Re-render a previous JSON scan into a different format without re-running the scan.

```bash
kulshan convert [OPTIONS]
```

| Option | Type | Description |
|--------|------|-------------|
| `-i, --input PATH` | path | Path to a previous JSON scan result (required) |
| `--format` | choice | Output format: `terminal`, `html`, `json`, `sarif`, `csv` |
| `-o, --output PATH` | path | Write output to file |
| `--show-pii` | flag | Show full account IDs |

**Example:**

```bash
kulshan convert -i scan-2024-06-15.json --format html -o report.html
```

---

### `kulshan init`

Generate a starter `config.toml` in the current directory.

```bash
kulshan init [OPTIONS]
```

| Option | Type | Description |
|--------|------|-------------|
| `--force` | flag | Overwrite existing config file |

---

### `kulshan shell`

Launch an interactive REPL session with command completion and inline help.

```bash
kulshan shell
```

Inside the shell, type `?` after any command for inline help.

---

### `kulshan setup-completion`

Print shell completion script for your shell.

```bash
kulshan setup-completion [OPTIONS]
```

| Option | Type | Description |
|--------|------|-------------|
| `--shell` | choice | Shell type: `bash`, `zsh`, `fish`, `powershell` (auto-detected if omitted) |

**Installation:**

```bash
# Bash
kulshan setup-completion --shell bash >> ~/.bashrc

# Zsh
kulshan setup-completion --shell zsh >> ~/.zshrc

# Fish
kulshan setup-completion --shell fish > ~/.config/fish/completions/kulshan.fish
```

---

### `kulshan workspace`

Manage workspace environments for multi-payer isolation.

#### `kulshan workspace list`

Show all local environments.

```bash
kulshan workspace list
```

#### `kulshan workspace show`

Show details of the current environment.

```bash
kulshan workspace show
```

#### `kulshan workspace create`

Create a new bound workspace with STS verification.

```bash
kulshan workspace create NAME [OPTIONS]
```

| Option | Type | Description |
|--------|------|-------------|
| `--profile TEXT` | string | AWS CLI profile to bind |
| `--payer-account TEXT` | string | Payer account ID (12 digits) |
| `--role-arn TEXT` | string | IAM role to assume |

#### `kulshan workspace rename`

Change the display name of a workspace.

```bash
kulshan workspace rename WORKSPACE_ID NEW_NAME
```

#### `kulshan workspace reconcile`

Detect and link workspaces that share the same payer account.

```bash
kulshan workspace reconcile
```

#### `kulshan workspace use`

Set the active workspace.

```bash
kulshan workspace use WORKSPACE_ID
```

#### `kulshan workspace connection add`

Add an AWS connection to a workspace.

```bash
kulshan workspace connection add WORKSPACE [OPTIONS]
```

| Option | Type | Description |
|--------|------|-------------|
| `--name TEXT` | string | Connection name (required) |
| `--profile TEXT` | string | AWS CLI profile |
| `--role-arn TEXT` | string | IAM role to assume |

#### `kulshan workspace default-connection`

Set the default connection for a workspace.

```bash
kulshan workspace default-connection WORKSPACE CONNECTION_NAME
```

---

### CUR & Analysis Commands

Kulshan can analyze cost movements directly from CUR/Data Export Parquet files (local or S3). No Athena, no Glue, no data warehouse. DuckDB queries your data in place.

#### `kulshan cur validate`

Validate CUR/Data Export Parquet structure (columns, types, cost data presence).

```bash
kulshan cur validate --path ./cur/
kulshan cur validate --s3 s3://bucket/prefix/ --month 2024-06
```

| Option | Type | Description |
|--------|------|-------------|
| `--path PATH` | path | Local Parquet file or directory |
| `--s3 TEXT` | string | S3 prefix for manifest validation |
| `--month TEXT` | string | Billing month (YYYY-MM) for S3 manifest |

#### `kulshan cur schema`

Show the resolved schema mapping and cost column selection.

```bash
kulshan cur schema --path ./cur/
```

#### `kulshan cur s3-check`

Quick S3 connectivity check without downloading data.

```bash
kulshan cur s3-check --s3 s3://bucket/prefix/
```

#### `kulshan analyze cost`

Top movers by service, account, region, usage type. Period-over-period delta. Suggested next steps.

```bash
kulshan analyze cost --path ./cur/ --month 2024-06
kulshan analyze cost --s3 s3://bucket/prefix/ --month 2024-06
kulshan analyze cost --path ./cur/ --month 2024-06 -o report.json
```

| Option | Type | Description |
|--------|------|-------------|
| `--s3 TEXT` | string | S3 prefix (DuckDB httpfs) |
| `--path PATH` | path | Local Parquet |
| `--month TEXT` | string | Billing month (YYYY-MM, required) |
| `--confirm-scan` | flag | Confirm large S3 scans |
| `-o, --output PATH` | path | Export to `.json` or `.md` |

#### `kulshan analyze ec2`

EC2-specific: instance family, pricing model, region breakdowns. Resource-level contributors. Tag coverage.

```bash
kulshan analyze ec2 --cur ./cur/ --month 2024-06
kulshan analyze ec2 --cur ./cur/ --month 2024-06 -o ec2-brief.json
```

| Option | Type | Description |
|--------|------|-------------|
| `--cur PATH` | path | Local Parquet (required) |
| `--month TEXT` | string | Billing month (YYYY-MM) |
| `-o, --output PATH` | path | Export to `.json` or `.md` |

#### Cost column selection

CUR probes columns in priority order, selects first with non-null data:
1. `line_item_net_unblended_cost` (negotiated pricing)
2. `line_item_unblended_cost` (standard)
3. `line_item_blended_cost` (last resort)

Fallback documented in output via `cost_basis.fallback_note`.

#### Investigation output schema

All investigation outputs include `human_review_required: true`, provenance (version, timestamp, data coverage), structured confidence components, evidence items with unique IDs, and suggested deep dives.

---

### Agent Commands

#### `kulshan mcp-serve`

Start an MCP server over stdio for agent integration.

```bash
kulshan mcp-serve
```

No options. See [MCP Integration](mcp-integration.md) for configuration details.

---

## Inline Help

Kulshan supports `?` inline help at any position:

```bash
kulshan ?                    # Show all commands
kulshan report ?             # Show report options
kulshan workspace ?          # Show workspace subcommands
```

---

## Profile Completion

When using `--profile`, Kulshan provides tab completion by reading `~/.aws/config` and `~/.aws/credentials`. Enable with `kulshan setup-completion`.

---

## Configuration

Most users need no configuration file — CLI options and environment variables cover common cases. Config files are useful for teams establishing consistent defaults.

### Precedence (highest wins)

1. CLI options
2. Environment variables (`KULSHAN_*`)
3. Project config (`./config.toml` or `./kulshan.toml`)
4. Global config (`~/.config/kulshan/config.toml` or `%APPDATA%\kulshan\config.toml`)
5. Built-in defaults

Generate a starter config with `kulshan init`.

### Example config.toml

```toml
[report]
packs = ["cost"]
days = 90
format = "terminal"
regions = []
yes = false
show_pii = false
history = true

[aws]
# profile = "default"
# role_arn = "arn:aws:iam::123456789012:role/KulshanAudit"

[tags]
required = ["Environment", "Team", "CostCenter"]
aliases = [
    ["env", "environment", "Environment"],
    ["team", "Team", "owner", "Owner"],
    ["cost-center", "CostCenter", "cost_center"],
]

[thresholds]
anomaly_zscore = 2.5
quota_warning = 80
quota_critical = 95
cert_expiry_warning = 60
cert_expiry_critical = 30
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `AWS_PROFILE` | AWS CLI profile |
| `KULSHAN_WORKSPACE` | Active workspace name |
| `KULSHAN_DAYS` | Cost lookback days |
| `KULSHAN_PACKS` | Default packs (comma-separated) |
| `KULSHAN_FORMAT` | Output format |
| `KULSHAN_YES` | Skip confirmations (set to `1`) |
| `KULSHAN_NO_HISTORY` | Disable history (set to `1`) |

### Data Storage Locations

| Purpose | Linux/macOS | Windows |
|---------|-------------|---------|
| Configuration | `~/.config/kulshan/` | `%APPDATA%\kulshan\` |
| Data (workspaces, history) | `~/.local/share/kulshan/` | `%LOCALAPPDATA%\kulshan\` |
| Cache | `~/.cache/kulshan/` | `%LOCALAPPDATA%\kulshan\cache\` |
