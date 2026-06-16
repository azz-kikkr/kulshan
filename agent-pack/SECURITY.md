# Kulshan — Security Model for AI Agents

## Core principle

Kulshan is read-only by construction. This document defines the security model for any AI agent that invokes Kulshan or consumes its output.

## What Kulshan CAN do

- Read AWS configuration using Get, List, and Describe API calls
- Start a non-mutating CloudFormation drift assessment (`cloudformation:DetectStackDrift`)
- Write reports to local files (HTML, JSON, SARIF, CSV)
- Read local scan history from SQLite

## What Kulshan CANNOT do

- Create, modify, or delete any AWS resource
- Write to S3, DynamoDB, or any AWS data store
- Send data to any external endpoint
- Phone home or transmit telemetry
- Access customer data (it reads configuration, not data)
- Hold or store AWS credentials (uses the standard credential chain)

## IAM policy

The full policy is at `kulshan/iam/kulshan-readonly.json`:
- **147 actions** across 30 AWS services
- Exclusively `Get*`, `List*`, `Describe*` actions
- Plus `cloudformation:DetectStackDrift` (non-mutating assessment)
- **Zero** `Put*`, `Create*`, `Update*`, `Delete*`, `Modify*` actions

## Data flow

```
Your AWS credentials (existing, on your machine)
        ↓
    Kulshan CLI (runs locally, no network except AWS APIs)
        ↓
    AWS APIs (read-only calls only)
        ↓
    Local report files (JSON, HTML, SARIF, CSV)
        ↓
    Agent / MCP skill (reads local files)
        ↓
    User decides next steps
```

No vendor in the loop. No data leaves the machine. The user controls every step.

## Agent safety rules

### MUST

- Run `kulshan doctor` before any scan to verify permissions
- Write all output to local files (use `-o` flag)
- Present findings as investigative evidence, not guaranteed facts
- Recommend verification in the AWS Console before any action
- Inform users about AWS Cost Explorer API charges before scanning
- Require explicit human approval before suggesting any destructive action

### MUST NOT

- Execute remediation actions based on Kulshan findings
- Delete resources, modify security groups, or change IAM policies
- Upload reports to external services without explicit user consent
- Make guaranteed savings promises based on estimated impacts
- Bypass the `--yes` flag without user awareness
- Attempt to extend Kulshan with write capabilities
- Share account IDs, resource names, or cost figures in public channels

## Sensitive data handling

- Reports may contain AWS account IDs, resource ARNs, and cost data
- By default, Kulshan redacts account IDs in exported reports
- Use `--show-pii` only when the user explicitly requests unredacted output
- Never echo sensitive identifiers in chat or logs without consent

## Permissions boundary recommendation

For production environments, use Kulshan's IAM policy as both:
1. The permission policy (what the role can do)
2. A permissions boundary (the maximum it can ever do)

This ensures credentials cannot exceed Kulshan's read-only scope.

## Vulnerability disclosure

If you discover a code path that could mutate AWS resources:
- Email: security@missionfinops.com
- Do NOT open a public GitHub issue for security vulnerabilities
- Response: acknowledgment within 48 hours
