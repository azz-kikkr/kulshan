"""IAM composition guard.

The composed policy at ``kulshan/iam/kulshan-readonly.json`` must be the
union of all per-check policies in ``kulshan/iam/per-check/*.json``. This
test prevents drift: if a per-check policy gains a new action without the
composed policy being regenerated, this fails.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
IAM_DIR = REPO_ROOT / "kulshan" / "iam"
COMPOSED_PATH = IAM_DIR / "kulshan-readonly.json"
PER_CHECK_DIR = IAM_DIR / "per-check"
REGISTRY_PATH = IAM_DIR / "registry.json"
HASH_PATH = IAM_DIR / "policy-hash.txt"

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


def _load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


# ─── Existing tests ───────────────────────────────────────────────────────────


def test_composed_policy_exists():
    assert COMPOSED_PATH.exists(), f"missing composed policy at {COMPOSED_PATH}"


def test_per_check_dir_has_expected_files():
    files = _per_check_files()
    assert len(files) == 11, (
        f"expected 11 per-check policies, got {len(files)}: {[f.name for f in files]}"
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


# ─── New tests (Commit 3) ─────────────────────────────────────────────────────


def test_no_write_access_level_in_baseline():
    """No action with baseline_eligible: true should have aws_access_level: Write,
    except actions explicitly documented as non-mutating despite Write classification."""
    registry = _load_registry()
    # Actions that AWS classifies as Write but do not modify resources.
    # cloudformation:DetectStackDrift starts a drift assessment but changes nothing.
    WRITE_LEVEL_ALLOWLIST = {"cloudformation:DetectStackDrift"}
    violations = [
        entry["iam_action"]
        for entry in registry["actions"]
        if entry.get("baseline_eligible")
        and entry.get("aws_access_level") == "Write"
        and entry["iam_action"] not in WRITE_LEVEL_ALLOWLIST
    ]
    assert not violations, (
        f"Baseline-eligible actions must not have Write access level: {violations}"
    )


def test_sts_assume_role_absent_from_baseline():
    """sts:AssumeRole must NOT be in the composed baseline policy."""
    composed = json.loads(COMPOSED_PATH.read_text(encoding="utf-8"))
    composed_actions = _actions(composed)
    assert "sts:AssumeRole" not in composed_actions, (
        "sts:AssumeRole should not be in the baseline policy"
    )


def test_registry_actions_present_in_policy():
    """Every registry action with baseline_eligible=true and status=required must
    be in the composed policy."""
    registry = _load_registry()
    composed = json.loads(COMPOSED_PATH.read_text(encoding="utf-8"))
    composed_actions = _actions(composed)

    missing = [
        entry["iam_action"]
        for entry in registry["actions"]
        if entry.get("baseline_eligible") and entry.get("status") == "required"
        and entry["iam_action"] not in composed_actions
    ]
    assert not missing, (
        f"Registry actions missing from composed policy: {missing}"
    )


def test_policy_hash_matches_recorded():
    """SHA256 of the composed policy must match the recorded hash."""
    content = COMPOSED_PATH.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
    actual_hash = hashlib.sha256(content).hexdigest()
    expected_hash = HASH_PATH.read_text(encoding="utf-8").strip()
    assert actual_hash == expected_hash, (
        f"Policy hash mismatch.\n  Expected: {expected_hash}\n  Actual:   {actual_hash}\n"
        "Regenerate with: python kulshan/iam/compose.py && "
        "python -c \"import hashlib; from pathlib import Path; "
        "print(hashlib.sha256(Path('kulshan/iam/kulshan-readonly.json').read_bytes()).hexdigest())\" "
        "> kulshan/iam/policy-hash.txt"
    )
