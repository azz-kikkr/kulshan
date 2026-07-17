#!/usr/bin/env python3
"""Compose the union of all per-check IAM policies into kulshan-readonly.json."""
from __future__ import annotations

import json
from pathlib import Path

IAM_DIR = Path(__file__).resolve().parent
PER_CHECK_DIR = IAM_DIR / "per-check"
OUTPUT_PATH = IAM_DIR / "kulshan-readonly.json"


def collect_actions() -> set[str]:
    actions: set[str] = set()
    for f in sorted(PER_CHECK_DIR.glob("*.json")):
        policy = json.loads(f.read_text(encoding="utf-8"))
        for stmt in policy.get("Statement", []):
            a = stmt.get("Action", [])
            if isinstance(a, str):
                actions.add(a)
            else:
                actions.update(a)
    return actions


def main():
    actions = sorted(collect_actions())
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "KulshanReadOnly",
                "Effect": "Allow",
                "Action": actions,
                "Resource": "*",
            }
        ],
    }
    OUTPUT_PATH.write_text(
        json.dumps(policy, indent=4) + "\n", encoding="utf-8"
    )
    services = sorted({a.split(":")[0] for a in actions})
    print(f"Composed policy: {len(actions)} actions across {len(services)} services")
    print(f"Services: {', '.join(services)}")
    print(f"Written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
