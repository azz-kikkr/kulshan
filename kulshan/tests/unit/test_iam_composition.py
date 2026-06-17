"""IAM composition guard.

The composed policy at ``kulshan/iam/kulshan-readonly.json`` must be the
union of all per-check policies in ``kulshan/iam/per-check/*.json``. This
test prevents drift: if a per-check policy gains a new action without the
composed policy being regenerated, this fails.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
IAM_DIR = REPO_ROOT / "kulshan" / "iam"
COMPOSED_PATH = IAM_DIR / "kulshan-readonly.json"
PER_CHECK_DIR = IAM_DIR / "per-check"

WRITE_VERBS = ("Put", "Delete", "Create", "Modify", "Terminate", "Detach", "Attach", "Update")


def _actions(policy: dict) -> set[str]:
    actions: set[str] = set()
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for stmt in statements:
        a = stmt.get("Action", [])
        if isinstance(a, str):
            actions.add(a)
        else:
            actions.update(a)
    return actions


def _is_write_action(action: str) -> bool:
    """An AWS action is a write iff its leading verb (the first word of the
    action name after the colon) starts with a write verb. ``iam:Attach...`` is a
    write; ``iam:ListAttachedRolePolicies`` is not."""
    if ":" not in action:
        return False
    name = action.split(":", 1)[1]
    return any(name.startswith(verb) for verb in WRITE_VERBS)


def _per_check_files() -> list[Path]:
    return sorted(PER_CHECK_DIR.glob("*.json"))


def test_composed_policy_exists():
    assert COMPOSED_PATH.exists(), f"missing composed policy at {COMPOSED_PATH}"


def test_per_check_dir_has_ten_files():
    files = _per_check_files()
    assert len(files) == 10, (
        f"expected 10 per-check policies, got {len(files)}: {[f.name for f in files]}"
    )


def test_composed_matches_union_of_per_check_actions():
    composed = json.loads(COMPOSED_PATH.read_text(encoding="utf-8"))
    composed_actions = _actions(composed)

    missing: dict[str, set[str]] = {}
    required_actions: set[str] = set()
    for f in _per_check_files():
        pack_actions = _actions(json.loads(f.read_text(encoding="utf-8")))
        required_actions.update(pack_actions)
        delta = pack_actions - composed_actions
        if delta:
            missing[f.stem] = delta

    extra = composed_actions - required_actions
    assert not missing and not extra, (
        "composed policy must equal the union of per-check policies. "
        f"Missing per pack: {missing}; extra actions: {sorted(extra)}"
    )


@pytest.mark.parametrize("policy_file", _per_check_files() + [COMPOSED_PATH])
def test_no_action_wildcards(policy_file: Path):
    actions = _actions(json.loads(policy_file.read_text(encoding="utf-8")))
    wildcard_actions = sorted(action for action in actions if "*" in action)
    assert not wildcard_actions, f"{policy_file.name} contains wildcards: {wildcard_actions}"


@pytest.mark.parametrize("policy_file", _per_check_files() + [COMPOSED_PATH])
def test_no_write_actions_anywhere(policy_file: Path):
    """Every action in every IAM policy must be read-only (its leading verb is
    not a write verb)."""
    actions = _actions(json.loads(policy_file.read_text(encoding="utf-8")))
    bad = [a for a in actions if _is_write_action(a)]
    assert not bad, f"{policy_file.name} contains write actions: {bad}"
