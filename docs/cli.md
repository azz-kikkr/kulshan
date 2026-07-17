# CLI Reference

## Commands

### `Kulshan Report`

Run all 10 audit packs and display the combined dashboard.

```bash
Kulshan Report                                     # full scan, all enabled regions
Kulshan Report --quick                             # quick scan (3 regions)
Kulshan Report --format json --output r.json       # JSON export
Kulshan Report --format html --output r.html       # self-contained HTML
Kulshan Report --profile production                # named AWS profile
Kulshan Report --role-arn arn:aws:iam::...         # assume role
Kulshan Report --days 60                           # 60-day cost lookback
Kulshan Report --show-pii                          # disable PII redaction in exports
```

| Flag | Default | Description |
|------|---------|-------------|
| `--quick` | off | Scan only 3 regions |
| `--format` | terminal | Output: `terminal`, `json`, `html` |
| `--output`, `-o` | stdout | Write to file |
| `--profile` | default | AWS CLI profile name |
| `--role-arn` | none | IAM role to assume |
| `--days` | 30 | Cost analysis lookback period |
| `--show-pii` | off | Show full account IDs and PII in exports |

### `Kulshan Shell`

Launch the interactive REPL.

```bash
Kulshan Shell
Kulshan Shell --profile prod --role-arn arn:aws:iam::123:role/Audit
```

Features:
- **Tab completion** — auto-completes subcommands and flags
- **`?` help** — press `?` at an empty prompt for available commands
- **Persistent history** — saved to `~/.Kulshan_history`
- **Themed prompt** — Rich-style colored output

Inside the shell, run commands without the `Kulshan` prefix:

```
Kulshan> report --quick
Kulshan> report --format json -o out.json
Kulshan> help
Kulshan> exit
```

Exit with `exit`, `quit`, or Ctrl+D.

### `Kulshan setup-completion`

Print a shell completion script.

```bash
Kulshan setup-completion --shell bash
Kulshan setup-completion --shell zsh
Kulshan setup-completion --shell fish
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success — no critical findings |
| 1 | Success — critical findings detected |
| 2 | Runtime error (e.g. AWS auth failure) |
| 3 | Configuration error |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AWS_PROFILE` | AWS CLI profile (same as `--profile`) |
| `AWS_DEFAULT_REGION` | Default region |
| `Kulshan_CONFIG` | Path to config file |
| `Kulshan_NO_COLOR` | Disable colored output |

---

## Output Formats

### Terminal (default)

Rich-formatted dashboard: overall score, per-pack scores with colored progress bars, severity summary, top actions.

### HTML

Self-contained single-file HTML. No CDN, no external deps. Works offline, prints well.

- Dark mode (auto-detects OS preference, manual toggle)
- SVG score dials, finding cards, expandable pack details
- Responsive and print-optimized

### JSON

Machine-readable `CombinedScanResult` schema. Includes all scores, every finding with severity/confidence/resource ARN/dollar impact, ranked remediations, and scan metadata.

---

## PII Redaction

Exported reports (HTML, JSON) **redact sensitive data by default**:

- AWS account IDs → `XXXXXXXXXXXX`
- ARNs (account segments masked)
- Email addresses, IP addresses, S3 bucket names
- Filenames with account IDs

Terminal output is NOT redacted (you're already authenticated).

Disable with `--show-pii`. Redaction is display-layer masking, not encryption.

---

## Optional Extras

The core install gives you terminal, HTML, and JSON output:

```bash
pip install kulshan                # core
pip install "kulshan[pdf]"         # PDF export
pip install "kulshan[all]"         # all user-facing extras
pip install "kulshan[dev]"         # contributor tools
```

| Extra | Enables | Notes |
|-------|---------|-------|
| `pdf` | PDF export | Requires system deps on Linux (weasyprint) |
| `excel` | Excel export | --- |
| `pptx` | PowerPoint export | --- |
| `dev` | pytest, ruff, mypy, moto | For contributors only |
| `all` | pdf + excel + pptx + mcp | Does not include dev |

---

## Configuration (V0.2 — planned)

Config file at `~/.config/Kulshan/config.toml` (not yet functional in V0.1):

```toml
[aws]
profile = "default"
regions = ["us-east-1", "us-west-2", "eu-west-1"]

[output]
default_format = "html"
```

Precedence: CLI flags > env vars > config file > defaults.

---

## Report Customization

| What | Path |
|------|------|
| Jinja2 templates | `kulshan/src/kulshan/report/templates/` |
| CSS/JS assets | `kulshan/src/kulshan/report/assets/` |
| HTML generator | `kulshan/src/kulshan/report/html.py` |
| Terminal theme | `kulshan/src/kulshan/theme.py` |

- Dark mode uses CSS variables — override to restyle
- SVG dials generated inline
- Finding cards severity-coded (critical=red, high=orange, medium=yellow, low=blue)
