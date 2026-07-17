# Architecture

Last reviewed: 2026-05-06

## Overview

Kulshan is a single Python package containing ten internal audit packs.

```
┌─────────────────────────────────────────────────────────────┐
│                   Kulshan (single wheel)                   │
│  cli.py → orchestrator.py → Kulshan.checks.<key>           │
│                          → report/{terminal,html,json}      │
└────────────┬────────────────────────────────────────────────┘
             │ each pack exposes: run_scan(session, regions, *, quick=False, **kwargs) -> dict
             ▼
┌──────────┬──────────┬──────────┬──────────┬──────────┐
│   cost   │ security │  sweep   │    dr    │   age    │
├──────────┼──────────┼──────────┼──────────┼──────────┤
│  drift   │   tag    │  pulse   │  limit   │   topo   │
└──────────┴──────────┴──────────┴──────────┴──────────┘
     ↑ each lives at kulshan/src/kulshan/checks/<key>/
```

There is one customer-facing CLI (`Kulshan`) and one wheel (`kulshan`). The ten check packs are not separately installable.

## Check pack architecture

Every check pack follows the same internal structure:

```
kulshan/src/kulshan/checks/<key>/
├── __init__.py            # exposes top-level run_scan(...)
├── scanner/               # AWS API calls + data collection
│   ├── module_a.py
│   └── module_b.py
├── scoring/
│   └── engine.py          # 0-100 score calculation
├── output/                # legacy per-pack renderers (used standalone-mode only;
│   ├── terminal.py        #  the unified report uses Kulshan/report/* instead)
│   ├── html.py
│   └── json_output.py
└── utils/
    └── aws.py             # boto3 session, retry, pagination helpers
```

**Key patterns:**

- Each pack's `__init__.py` exposes `run_scan(session, regions, *, quick=False, **kwargs) -> dict`.
- The return shape is uniform: `{"tool": str, "scores": {"overall_score": int, "grade": str, "total_findings": int, "severity_counts": dict, "breakdown": dict, ...}, "errors": list, "skipped"?: bool}`.
- Scanner functions return structured data (lists of findings/orphans/issues).
- Scoring engines take scanner output and produce a 0-100 score with grade.
- All packs use boto3 with shared retry and pagination patterns.

## Kulshan internal layout

```
kulshan/src/kulshan/
├── __init__.py
├── __version__.py
├── cli.py                 # Click CLI: report, shell, setup-completion
├── orchestrator.py        # iterates TOOL_ORDER, calls each pack's run_scan
├── session.py             # shared boto3 session with role assumption
├── models.py              # dataclass library (Finding, ScanResult, etc.)
├── constants.py           # exit codes
├── errors.py              # custom exceptions
├── repl.py                # interactive shell (prompt_toolkit)
├── theme.py / completion.py / question_mark.py / help_formatter.py
├── checks/                # 10 audit packs (see above)
├── report/
│   ├── terminal.py        # Rich combined dashboard
│   ├── html.py            # self-contained HTML report (SVG dials, dark mode)
│   └── json_output.py     # JSON export
├── (planned modules)      # stubs exist for future work; not part of shipped V0.1
                           # see "Planned modules" section below for the
                           # current honest status of each
```

## Planned modules

These directories exist as stubs in the codebase but contain no shipped functionality. They reflect early architectural intent, not active development.

- `history/`: SQLite scan history. In progress for V0.2.
- `config/`: TOML config file support. In progress for V0.2.
- `ci/`, `license/`, `telemetry/`, `trust/`, `plugins/`: speculative. No active work. May be removed in a future cleanup.

If you are evaluating Kulshan today, treat anything in this list as "not shipped." If you need one of these features, tell me; that's a signal that affects what gets prioritized.

## Data flow

```
1. CLI parses args (profile, regions, quick mode, output format, days).
2. session.create_session() builds a shared boto3 session (with optional role assumption).
3. Orchestrator iterates TOOL_ORDER, importing Kulshan.checks.<key> per key
   and calling pack.run_scan(session, regions, quick=quick, profile=profile).
4. Each pack returns a normalized dict: {"tool", "scores", "errors", "skipped"?}.
5. Orchestrator collects all 10 results into a results dict.
6. compute_overall(results) → weighted average across TOOL_WEIGHTS.
7. report renderer produces terminal, HTML, or JSON output.
8. Process exit code reflects whether any pack reported critical severity.
```

## Scoring

Each pack produces a 0-100 score. Kulshan combines them with the weights defined in `kulshan/src/kulshan/orchestrator.py` (`TOOL_WEIGHTS`):

| Tool | Weight | Rationale |
|------|--------|-----------|
| Cost Analyzer | 15% | Direct financial impact |
| Security Scanner | 15% | Risk exposure |
| Waste Detector | 10% | Recoverable spend |
| DR Readiness | 12% | Business continuity |
| Lifecycle Audit | 8% | Technical debt |
| IaC Drift | 10% | Governance |
| Tag Governance | 8% | Cost attribution |
| Observability | 8% | Operational visibility |
| Quota Headroom | 6% | Scaling readiness |
| Network Topology | 8% | Architecture quality |

Weights sum to 1.00. The source of truth is `TOOL_WEIGHTS` in `orchestrator.py`; update both together.

Grade mapping (`_grade()` in `orchestrator.py`):

| Score | Grade |
|-------|-------|
| ≥ 97 | A+ |
| ≥ 93 | A |
| ≥ 90 | A− |
| ≥ 87 | B+ |
| ≥ 83 | B |
| ≥ 80 | B− |
| ≥ 77 | C+ |
| ≥ 73 | C |
| ≥ 70 | C− |
| ≥ 60 | D |
| < 60 | F |

## Key design decisions

1. **One wheel, one CLI.** The ten check packs live inside the Kulshan package as `Kulshan.checks.<key>`. They are not separately installable.
2. **Uniform pack contract.** Every pack exposes `run_scan(session, regions, *, quick=False, **kwargs) -> dict`. The orchestrator calls all packs the same way; no special casing.
3. **No network calls from the orchestrator.** All AWS API calls happen inside the check packs. The orchestrator just iterates and aggregates.
4. **Single-file HTML reports.** No CDN, no external CSS/JS. Everything is inlined. Reports work offline, can be emailed, and render in any browser.
