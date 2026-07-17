# MCP Server & Agent Integration

Kulshan exposes its audit capabilities to MCP-compatible agents (Claude Desktop, Cursor, Kiro, and others) via a stdio-based Model Context Protocol server.

---

## Overview

The MCP server provides a programmatic interface to Kulshan's audit packs, CUR validation, and investigation commands. Agents call Kulshan tools, receive deterministic evidence, and may reason over the results.

Key principles:
- Kulshan returns **deterministic, inspectable evidence** — not opinions or recommendations
- The agent reasons over evidence; Kulshan produces it
- All tool outputs include schema versions and provenance
- `human_review_required: true` is always set on investigation outputs

---

## Starting the Server

```bash
kulshan mcp-serve
```

The server communicates over stdio (stdin/stdout) using the MCP protocol. It does not open network ports.

---

## Client Configuration

Add to your MCP client config (`.kiro/settings/mcp.json`, Claude Desktop config, `.cursor/mcp.json`, or equivalent):

```json
{
  "mcpServers": {
    "kulshan": {
      "command": "kulshan",
      "args": ["mcp-serve"]
    }
  }
}
```

Any MCP 1.0+ compatible client can connect by spawning `kulshan mcp-serve` as a subprocess communicating over stdio.

---

## Available Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `kulshan_preflight` | (none) | Check AWS caller identity using the default credential chain |
| `kulshan_report` | `packs`, `days`, `regions` | Run selected audit packs and return compact findings |
| `kulshan_quick_security` | `region` | Fast security scan of a single region (critical/high only) |
| `kulshan_list_packs` | (none) | List all available audit packs with descriptions |
| `kulshan_cur_validate` | `cur_path` | Validate local CUR/Data Export Parquet readability |
| `kulshan_analyze_ec2` | `cur_path`, `month` | Analyze EC2 cost movement from local CUR |
| `kulshan_analyze_cost` | `s3_uri`, `month` | Analyze monthly cost movement from S3 CUR |

---

## Tool Schemas

### kulshan_preflight

```json
{
  "name": "kulshan_preflight",
  "description": "Check AWS caller identity using the default credential chain",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

**Returns:**
```json
{
  "status": "ok",
  "account_id": "123456789012",
  "arn": "arn:aws:iam::123456789012:role/KulshanAudit",
  "user_id": "AROA..."
}
```

### kulshan_report

```json
{
  "name": "kulshan_report",
  "description": "Run selected audit packs and return compact findings",
  "inputSchema": {
    "type": "object",
    "properties": {
      "packs": {
        "type": "string",
        "description": "Comma-separated pack names or 'all'. Default: 'cost'",
        "default": "cost"
      },
      "days": {
        "type": "integer",
        "description": "Cost lookback in days (1-365). Default: 90",
        "default": 90
      },
      "regions": {
        "type": "string",
        "description": "Comma-separated regions. Default: auto-detect",
        "default": null
      }
    },
    "required": []
  }
}
```

**Returns:**
```json
{
  "status": "ok",
  "overall_score": 78,
  "overall_grade": "C",
  "findings": [
    {
      "severity": "high",
      "title": "EC2 spend anomaly: +42% week-over-week",
      "pack": "cost",
      "service": "Amazon Elastic Compute Cloud",
      "dollar_impact": 1250.00,
      "recommendation": "Review recent EC2 launches..."
    }
  ],
  "pack_scores": {
    "cost": { "score": 72, "grade": "C", "findings": 5 }
  }
}
```

### kulshan_quick_security

```json
{
  "name": "kulshan_quick_security",
  "description": "Fast security scan of a single region, returns critical/high findings only",
  "inputSchema": {
    "type": "object",
    "properties": {
      "region": {
        "type": "string",
        "description": "AWS region to scan. Default: 'us-east-1'",
        "default": "us-east-1"
      }
    },
    "required": []
  }
}
```

### kulshan_list_packs

```json
{
  "name": "kulshan_list_packs",
  "description": "List all available audit packs with descriptions",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

**Returns:**
```json
{
  "status": "ok",
  "packs": [
    { "name": "cost", "label": "Cost Analyzer", "description": "Cost trends, anomalies, commitment gaps" },
    { "name": "security", "label": "Security Scanner", "description": "IAM, encryption, network exposure" }
  ]
}
```

### kulshan_cur_validate

```json
{
  "name": "kulshan_cur_validate",
  "description": "Validate local CUR/Data Export Parquet readability",
  "inputSchema": {
    "type": "object",
    "properties": {
      "cur_path": {
        "type": "string",
        "description": "Path to local CUR Parquet file or directory"
      }
    },
    "required": ["cur_path"]
  }
}
```

### kulshan_investigate_ec2

```json
{
  "name": "kulshan_investigate_ec2",
  "description": "Analyze EC2 cost movement from local CUR Parquet",
  "inputSchema": {
    "type": "object",
    "properties": {
      "cur_path": {
        "type": "string",
        "description": "Path to local CUR Parquet"
      },
      "month": {
        "type": "string",
        "description": "Billing month (YYYY-MM). Defaults to latest available."
      }
    },
    "required": ["cur_path"]
  }
}
```

### kulshan_investigate_cost

```json
{
  "name": "kulshan_investigate_cost",
  "description": "Analyze monthly cost movement from S3 CUR",
  "inputSchema": {
    "type": "object",
    "properties": {
      "s3_uri": {
        "type": "string",
        "description": "S3 CUR prefix (s3://bucket/prefix/)"
      },
      "month": {
        "type": "string",
        "description": "Billing month (YYYY-MM)"
      }
    },
    "required": ["s3_uri", "month"]
  }
}
```

---

## Installation Requirement

The MCP server requires the `mcp` extra:

```bash
pip install kulshan[mcp]
```

Without this extra, `kulshan mcp-serve` will exit with an error indicating the missing dependency.

---

## Credential Handling

The MCP server uses the same credential chain as the CLI. Ensure AWS credentials are available in the environment where the MCP server runs.

For agents running in IDE environments, this typically means:
- AWS CLI is configured with a profile or SSO session
- Environment variables are set in the shell that launches the IDE
- The agent's MCP server process inherits the credential environment

---

## Error Handling

Tool errors are returned as structured responses, not exceptions:

```json
{
  "status": "error",
  "error": "No valid AWS credentials found",
  "suggestion": "Run 'aws login' or configure AWS_PROFILE"
}
```

Agents should check the `status` field before processing results.

---

## Design Philosophy

Kulshan's MCP integration follows these principles:

1. **Evidence, not advice.** Tools return scored findings with evidence. The agent decides what to say about them.
2. **Deterministic.** Same inputs produce the same outputs. No randomness in the detection path.
3. **Inspectable.** Every finding has an ID, fingerprint, and schema version. Humans can verify any claim.
4. **Bounded cost.** The `kulshan_report` tool respects the same API cost model as the CLI. Cost Explorer calls are ~$0.01 each.
5. **No side effects.** Tools only read from AWS. Nothing is created, modified, or deleted.
