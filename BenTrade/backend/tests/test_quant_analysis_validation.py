from common.quant_analysis import CreditSpread, annualized_return


def test_annualized_ror_guard_short_dte_sets_warning() -> None:
    spread = CreditSpread(
        spread_type="put_credit",
        underlying_price=600.0,
        short_strike=580.0,
        long_strike=575.0,
        net_credit=1.2,
        dte=4,
        short_delta_abs=0.11,
        implied_vol=0.25,
    )

    summary = spread.summary()
    assert summary["annualized_ror_upper_bound"] is None
    assert "ANNUALIZE_SHORT_DTE" in summary.get("validation_warnings", [])


def test_annualized_ror_present_for_longer_dte() -> None:
    spread = CreditSpread(
        spread_type="call_credit",
        underlying_price=600.0,
        short_strike=620.0,
        long_strike=625.0,
        net_credit=1.0,
        dte=21,
        short_delta_abs=0.2,
        implied_vol=0.22,
    )

    summary = spread.summary()
    expected = annualized_return(spread.return_on_risk, spread.dte)
    assert summary["annualized_ror_upper_bound"] == expected
    assert "validation_warnings" not in summary
