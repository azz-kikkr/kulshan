from kulshan.billing_integrity import assess_billing_integrity


def test_current_month_estimated_and_no_history():
    r = assess_billing_integrity('2026-07', today='2026-07-17')
    assert r.period_finality == 'estimated'
    assert r.status == 'provisional'
    assert r.historical_comparison == 'not_available'


def test_closed_prior_month():
    r = assess_billing_integrity('2026-06', today='2026-07-17', sources=['cur'])
    assert r.period_finality == 'closed'
    assert r.status == 'trusted'


def test_extreme_discontinuity_suspect():
    r = assess_billing_integrity('2026-06', today='2026-07-17', current_value=10000, history=[100, 110, 105], sources=['cur'])
    assert r.status == 'suspect'
    assert 'Possible upstream AWS billing-data issue.' in r.warning


def test_tiny_amount_not_suspect():
    r = assess_billing_integrity('2026-06', today='2026-07-17', current_value=2, history=[1], sources=['cur'])
    assert r.status != 'suspect'


def test_source_disagreement_preserves_raw_values():
    r = assess_billing_integrity('2026-06', today='2026-07-17', current_value=2000, prior_value=1000, history=[1000, 1100], sources=['cost_explorer','cur'], source_values={'cost_explorer': 2000, 'cur': 1500})
    assert r.current_value == 2000
    assert r.cross_source_agreement == 'disagreement'
    assert r.status == 'suspect'


def test_agreement_is_not_independent_verification():
    r = assess_billing_integrity('2026-06', today='2026-07-17', current_value=100, prior_value=90, history=[90], sources=['cost_explorer','cur'], source_values={'cost_explorer': 100, 'cur': 100})
    assert r.cross_source_agreement == 'agreement'
    assert 'not independent verification' in ' '.join(r.reasons).lower()

