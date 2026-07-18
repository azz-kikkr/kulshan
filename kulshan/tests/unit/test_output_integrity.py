import json

from rich.console import Console

from kulshan.cli import _emit_output


INTEGRITY = {
    "status": "provisional",
    "period_finality": "estimated",
    "sources": ["cost_explorer"],
    "retrieved_at": "2026-07-17T12:00:00+00:00",
    "historical_comparison": "not_available",
    "cross_source_agreement": "not_available",
    "confidence_effect": "limited",
    "reasons": ["No local historical comparison is available."],
    "current_value": 125.5,
    "prior_value": None,
}


def _write_output(tmp_path, fmt):
    suffix = {"json": "json", "sarif": "sarif", "html": "html"}[fmt]
    output = tmp_path / f"report.{suffix}"
    _emit_output(
        fmt=fmt,
        results={
            "cost": {
                "scores": {
                    "overall_score": 100,
                    "grade": "A",
                    "total_findings": 0,
                    "severity_counts": {},
                    "total_spend": 125.5,
                },
                "findings": [],
                "metadata": {},
            }
        },
        overall_score=100,
        overall_grade="A",
        account_id="123456789012",
        regions=["us-east-1"],
        duration=1.0,
        top_actions=[],
        all_findings=[],
        scan_metadata={},
        output=str(output),
        show_pii=True,
        console=Console(),
        coverage={},
        billing_data_integrity=INTEGRITY,
    )
    return output


def test_json_output_contains_billing_integrity(tmp_path):
    payload = json.loads(_write_output(tmp_path, "json").read_text(encoding="utf-8"))
    assert payload["billing_data_integrity"] == INTEGRITY


def test_sarif_output_contains_billing_integrity(tmp_path):
    payload = json.loads(_write_output(tmp_path, "sarif").read_text(encoding="utf-8"))
    properties = payload["runs"][0]["tool"]["driver"]["properties"]
    assert properties["billing_data_integrity"] == INTEGRITY


def test_html_output_contains_billing_integrity(tmp_path):
    html = _write_output(tmp_path, "html").read_text(encoding="utf-8")
    assert "Billing data integrity" in html
    assert "provisional" in html