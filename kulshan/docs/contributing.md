# Contributing & Troubleshooting

Development setup, testing, common issues, and FAQ.

---

## Development Setup

```bash
git clone https://github.com/MissionFinOps/kulshan.git
cd kulshan
pip install -e ".[dev]"
```

The `[dev]` extra includes pytest, hypothesis, ruff, mypy, moto, and jsonschema.

```bash
pytest                        # Full test suite
ruff check src/ tests/        # Lint
mypy src/kulshan/             # Type check
```

---

## Code Style

- **Linter/formatter:** ruff (line-length 100, target py39)
- **Type checking:** mypy strict mode
- **Tests:** pytest with moto for AWS mocking, hypothesis for property tests

---

## Writing Audit Checks

Each pack lives under `src/kulshan/checks/<pack>/`. Every finding must follow the v2.0 schema:

```python
finding = {
    "id": make_finding_id(pack="sweep", kind="orphaned_volume", fingerprint=fp),
    "pack": "sweep",
    "kind": "orphaned_volume",
    "fingerprint": simple_fingerprint("sweep", "orphaned_volume", volume_id),
    "title": f"Unattached EBS volume {volume_id}",
    "severity": "medium",
    "score_impact": SEVERITY_SCORE_IMPACT["medium"],
    "estimated_monthly_impact": monthly_cost,
    "confidence": 0.95,
    "effort": "trivial",
    "risk": "safe",
    # ... location, description, evidence, recommended_action
    "schema_version": "2.0",
}
```

Required fields: `id`, `pack`, `kind`, `title` (non-empty strings), `severity` (critical/high/medium/low/info), `confidence` (float 0.0–1.0), `effort` (trivial/low/medium/high), `risk` (safe/low/medium/high).

---

## PR Process

1. Run `pytest`, `ruff check`, `mypy` before submitting
2. Update CHANGELOG.md for features/fixes
3. Update docs if user-facing behavior changes
4. Never add write API actions to IAM policies

Branch naming: `feature/short-desc`, `fix/issue-desc`, `docs/what-changed`

Commit style: `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`

---

## Release Process

Maintainer-managed: update `__version__.py` → CHANGELOG → tag → push tag → GitHub Actions publishes to PyPI.

---

## Troubleshooting

### Credential issues

| Error | Fix |
|-------|-----|
| "No valid AWS credentials found" | Run `aws sts get-caller-identity` to verify. Re-authenticate with `aws login`. |
| "Credential mismatch for workspace" | Switch profile (`--profile`) or use `--workspace default` |
| "ExpiredTokenException" | Re-authenticate: `aws login` or `aws sso login --profile X` |

### Permission issues

| Error | Fix |
|-------|-----|
| "Access Denied" on Cost Explorer | Activate CE in AWS Console (Billing section). Attach Kulshan policy. |
| "Access Denied" on other services | Run `kulshan preflight` to identify missing permissions. |
| "Organizations not available" | Normal for single-account mode. Kulshan works fine without it. |

### Performance

- Reduce regions: `--regions us-east-1`
- Run fewer packs: `--packs cost,security`
- The `limit` pack is slowest (~40s) due to Service Quotas pagination
- For large CUR files, download locally instead of querying S3

### Output issues

- **JSON mixed with status messages:** Use `-o file.json` or redirect (status goes to stderr automatically)
- **Account IDs redacted:** By design. Use `--show-pii` for full IDs.
- **HTML report blank:** Check exit code (3 = scan didn't complete)

### Installation

- **`kulshan: command not found`:** Ensure pip scripts dir is in PATH, or use `python -m kulshan`
- **`mcp-serve` fails:** Install the extra: `pip install kulshan[mcp]`

---

## FAQ

**How much does it cost?** Cost pack: ~$0.15 in CE API charges. All other packs use free APIs.

**Does it modify my AWS account?** No. Zero write actions. No write code paths.

**Safe for production?** Yes. Only reads metadata and cost data. No access to application data, secrets, or database contents.

**Scoring?** Weighted average of pack scores. Each pack starts at 100, deducts per finding (critical -15, high -10, medium -5, low -2).

**Can I suppress findings?** Not yet. Filter in post-processing with `jq` on JSON output.

**Multi-account?** Scan individual accounts via role assumption. Consolidated reports via workspace connections. Full Organizations discovery not yet implemented.

**Without Cost Explorer?** Yes: `kulshan report --packs security,sweep,dr --regions us-east-1` (free APIs only).

**Python versions?** 3.9, 3.10, 3.11, 3.12, 3.13.

**Windows?** Yes. macOS, Linux, and Windows all supported.

---

## Contact

- GitHub Issues: [github.com/MissionFinOps/kulshan/issues](https://github.com/MissionFinOps/kulshan/issues)
- Security: security@missionfinops.com
