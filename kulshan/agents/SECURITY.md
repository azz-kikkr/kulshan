# Kulshan — Security Model for AI Agents

## Core principle

Kulshan is read-only by construction. This document explains the security model for AI agents consuming Kulshan's output or invoking it as a tool.

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

## Agent safety rules

### DO

- Run `kulshan doctor` before any scan to verify permissions
- Write all output to local files
- Present findings as investigative evidence, not guaranteed facts
- Recommend verification in the AWS Console
- Inform users about AWS Cost Explorer API charges before scanning

### DO NOT

- Execute remediation actions based on Kulshan findings
- Delete resources, modify security groups, or change IAM policies
- Upload reports to external services without explicit user consent
- Make guaranteed savings promises based on estimated impacts
- Bypass the `--yes` flag for unattended runs without user awareness
- Attempt to extend Kulshan with write capabilities

## Sensitive data handling

- Reports may contain AWS account IDs, resource ARNs, and cost data
- By default, Kulshan redacts account IDs in exported reports
- Use `--show-pii` only when the user explicitly requests unredacted output
- Never echo account IDs, resource names, or cost figures in public channels

## Permissions boundary recommendation

For production environments, use Kulshan's IAM policy as both:
1. The permission policy (what the role can do)
2. A permissions boundary (the maximum it can ever do)

This ensures that even if additional policies are attached, the credentials cannot exceed Kulshan's read-only scope.

## Vulnerability disclosure

If you discover a code path that could mutate AWS resources, report immediately:
- Email: security@missionfinops.com
- Do NOT open a public GitHub issue for security vulnerabilities

## Trust model

```
User's AWS credentials (existing)
        ↓
    Kulshan CLI (local)
        ↓
    AWS APIs (read-only)
        ↓
    Local report file
        ↓
    User decides next steps
```

No vendor in the loop. No data leaves the machine. The user controls every step.
