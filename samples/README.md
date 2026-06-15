# Sample Reports

This directory contains sample Kulshan reports generated from synthetic fixture data.

- `sample-report.html` — Self-contained HTML report (same renderer as a real scan)
- `sample-report.json` — Machine-readable JSON output

These files are regenerated deterministically by `kulshan/scripts/generate_sample_report.py` and validated by CI. They use frozen placeholder data (account ID, regions, timestamps) so regeneration produces byte-identical output.

No AWS credentials or network access are needed to produce these files.
