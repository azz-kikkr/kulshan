"""Drift guard and sanitization tests for ``samples/sample-report.{html,json}``.

Two failure modes:
1. Drift — renderer changed without regenerating samples.
2. Leakage — real AWS account/email in committed artifacts.

If failing after a renderer change, run:
    python scripts/generate_sample_report.py
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
WHEEL_DIR = TESTS_DIR.parent.parent
REPO_ROOT = WHEEL_DIR.parent

SCRIPT_PATH = WHEEL_DIR / "scripts" / "generate_sample_report.py"
SAMPLES_DIR = REPO_ROOT / "samples"
HTML_PATH = SAMPLES_DIR / "sample-report.html"
JSON_PATH = SAMPLES_DIR / "sample-report.json"


def _import_generator():
    spec = importlib.util.spec_from_file_location("generate_sample_report", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gsr():
    return _import_generator()


# ── drift guard ──────────────────────────────────────────────────────────────


class TestSampleReportDriftGuard:
    """Committed samples must match regeneration byte-for-byte."""

    _STALE_HINT = (
        "samples/sample-report.{ext} is stale. "
        "Run: python scripts/generate_sample_report.py"
    )

    def test_html_matches_regeneration(self, gsr):
        results = gsr.load_fixtures()
        score, grade, _findings, top = gsr.compute_inputs(results)
        expected = gsr.build_html(results, top, score, grade)
        actual = HTML_PATH.read_text(encoding="utf-8")
        assert actual == expected, self._STALE_HINT.format(ext="html")

    def test_json_matches_regeneration(self, gsr):
        results = gsr.load_fixtures()
        score, grade, findings, top = gsr.compute_inputs(results)
        expected = gsr.build_json(results, top, score, grade, findings)
        actual = JSON_PATH.read_text(encoding="utf-8")
        assert actual == expected, self._STALE_HINT.format(ext="json")


# ── sanitization ─────────────────────────────────────────────────────────────


class TestSampleReportSanitization:
    """No real AWS data in committed artifacts."""

    _REAL_ACCOUNT_RE = re.compile(r"\b(?!0{12}\b)\d{12}\b")
    _EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

    def test_no_real_account_ids_in_html(self):
        text = HTML_PATH.read_text(encoding="utf-8")
        assert not self._REAL_ACCOUNT_RE.search(text)

    def test_no_real_account_ids_in_json(self):
        text = JSON_PATH.read_text(encoding="utf-8")
        assert not self._REAL_ACCOUNT_RE.search(text)

    def test_no_email_addresses_in_html(self):
        text = HTML_PATH.read_text(encoding="utf-8")
        assert not self._EMAIL_RE.search(text)

    def test_no_email_addresses_in_json(self):
        text = JSON_PATH.read_text(encoding="utf-8")
        assert not self._EMAIL_RE.search(text)
