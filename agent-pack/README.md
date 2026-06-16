# Kulshan Agent Pack

Agent integrations and AI skill definitions for Kulshan — the local AWS FinOps audit CLI.

## What's in this directory

| File | Purpose |
|------|---------|
| `POWER.md` | Kiro Power definition (tools, keywords, constraints) |
| `SECURITY.md` | Read-only security model for all agents |
| `AGENTS.md` | Instructions for Codex and general-purpose agents |
| `CLAUDE.md` | Instructions for Claude Code |
| `skills/run-kulshan-audit/SKILL.md` | Skill: run a full audit and produce a report |
| `skills/review-kulshan-report/SKILL.md` | Skill: interpret an existing Kulshan report |
| `skills/prepare-exec-summary/SKILL.md` | Skill: produce an executive summary from a scan |
| `skills/prepare-engineer-remediation-plan/SKILL.md` | Skill: create a prioritized remediation plan |

## How to use

**Claude Code:** Place `CLAUDE.md` in your project root.

**Codex:** Place `AGENTS.md` in your project root.

**Kiro:** Copy skills to `.kiro/skills/` in your workspace.

**Any agent:** Run `kulshan` commands via shell. All output is local JSON/HTML files.

## Rules (all agents)

1. Local-first only. No SaaS. No API keys. No telemetry.
2. No write actions. Kulshan is read-only. Never modify AWS resources.
3. Always start with `kulshan doctor` to verify credentials.
4. Prefer `kulshan report --format json` for agent analysis.
5. Also generate HTML for human consumption.
6. Warn users that Cost Explorer API calls may cost pennies.
7. Never suggest destructive remediation without explicit human review.
8. Use existing AWS credentials. No additional setup required.

## Data flow

```
Your AWS credentials (existing)
        ↓
    Kulshan CLI (runs locally)
        ↓
    AWS APIs (read-only: Get, List, Describe)
        ↓
    Local report files (JSON + HTML)
        ↓
    Agent skill / local MCP
        ↓
    Natural-language audit review
```

Your data. Your control. 100% local.

## Kulshan vs. typical SaaS cost tools

| What matters | Kulshan | Typical SaaS |
|---|---|---|
| Data access | Reads Cost Explorer & read-only APIs with your credentials | Requires data export, agent, or SaaS ingestion |
| Where data lives | Stays in your AWS account and on your machine | Leaves your environment |
| Setup | One command. No accounts. No uploads. | Sales call, onboarding, setup, agents |
| Cost | Free forever. Typical scan: pennies. | $/month or $/GB ingested + user seats |
| Speed to value | Minutes | Weeks to months |
| Use case | Baseline, audit, investigation, automation | Ongoing monitoring & dashboards |
| You own it | Yes. Open source. Apache 2.0. | No. Vendor lock-in. |

## Built for both humans and AI agents

**For humans:**
- Executive HTML for stakeholders
- Engineer JSON & SARIF for CI/CD
- Clear scores, findings, and actions
- One baseline. Many decisions.

**For AI agents:**
- Local MCP server (one command)
- Skill packs for popular agents
- Ask questions. Get audit answers.
- Automate reviews, PR checks, reports.

## Roadmap (planned, not yet implemented)

- Local MCP server (`kulshan mcp-serve`)
- Kiro Power with live tool integration
- Claude Code native integration
- Codex native integration
- MCP-compatible agent workflows with caching

These are tracked in `docs/mcp-server-plan.md`. Today, agents use Kulshan via shell commands and local file output.

## Links

- GitHub: https://github.com/azz-kikkr/kulshan
- Sample report: https://missionfinops.com/sample/
- IAM Policy: https://missionfinops.com/policy/
