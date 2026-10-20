import pytest
from app.services.planner import _nper

def test_nper_zero_rate():
    res = _nper(pv=0, pmt=1000, fv=12000, r=0)
    assert res == 12.0

def test_nper_compound():
    pv = 6500.0
    pmt = 1450.0
    fv = 18540.0
    r = 0.03 / 12.0

    res = _nper(pv=pv, pmt=pmt, fv=fv, r=r)
    assert res is not None
    assert round(res, 1) == 8.1

    # Loop simulation check
    wealth = pv
    months = 0
    prev_wealth = pv
    while wealth < fv and months < 100:
        prev_wealth = wealth
        wealth = wealth * (1.0 + r) + pmt
        months += 1

    frac_months = (months - 1) + (fv - prev_wealth) / (wealth - prev_wealth)
    assert abs(res - frac_months) <= 0.5
    assert abs(res - 8.1) <= 0.1
