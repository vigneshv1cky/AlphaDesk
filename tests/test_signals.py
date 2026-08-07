"""Tests for quant signals — the core decision engine."""

from alphadesk.quant.signals import (
    compute_composite,
    earnings_drift,
    volume_expansion,
    sector_divergence,
    short_interest_risk,
    price_structure,
    liquidity,
)


def test_earnings_drift_strong_long():
    """Strong positive reaction + moderate drift continuing = strong LONG."""
    s = earnings_drift(reaction_pct=8.5, drift_pct=3.2, gap_pct=5.3)
    assert s > 20, f"expected strong LONG, got {s}"


def test_earnings_drift_spent():
    """Big gap, zero drift = fully priced, weak signal."""
    s = earnings_drift(reaction_pct=12.0, drift_pct=0.1, gap_pct=11.9)
    assert abs(s) < 20, f"expected weak/spent signal, got {s}"


def test_earnings_drift_reversal():
    """Gap up but drift down = fading, signal should be weaker."""
    s_up = earnings_drift(reaction_pct=8.0, drift_pct=3.0, gap_pct=5.0)
    s_down = earnings_drift(reaction_pct=8.0, drift_pct=-3.0, gap_pct=5.0)
    assert abs(s_down) < abs(s_up), f"expected fading signal weaker, got up={s_up} down={s_down}"


def test_earnings_drift_underreaction():
    """Options expected 5% move, stock only moved 2% → underreaction boost."""
    s_under = earnings_drift(reaction_pct=2.0, drift_pct=1.5, gap_pct=0.5, implied_move=5.0)
    s_no_implied = earnings_drift(reaction_pct=2.0, drift_pct=1.5, gap_pct=0.5)
    # With underreaction gauge, same numbers should score higher
    assert s_under >= s_no_implied * 1.2, f"expected underreaction boost, got under={s_under} no_implied={s_no_implied}"


def test_earnings_drift_null():
    """No reaction = zero signal."""
    assert earnings_drift(None, None) == 0.0
    assert earnings_drift(0.3, 0.1) == 0.0  # below 0.5% threshold


def test_volume_expansion_direction():
    """High volume on positive reaction = positive signal."""
    s = volume_expansion(rvol=2.5, reaction_pct=8.0)
    assert s > 0, f"expected positive, got {s}"


def test_volume_expansion_direction_negative():
    """High volume on negative reaction = negative signal."""
    s = volume_expansion(rvol=3.0, reaction_pct=-10.0)
    assert s < 0, f"expected negative, got {s}"


def test_volume_expansion_low():
    """Low volume = penalty."""
    s = volume_expansion(rvol=0.5, reaction_pct=5.0)
    assert s < 0, f"expected penalty for low vol, got {s}"


def test_sector_divergence():
    """Stock up 5%, sector flat = small divergence (5.0 not > 5 threshold)."""
    s = sector_divergence(change_today=5.0, sector_change=0.0)
    assert s == 0.0, f"expected no divergence at exactly 5, got {s}"


def test_sector_alignment():
    """Stock up 5%, sector up 5% = no divergence."""
    s = sector_divergence(change_today=5.0, sector_change=5.0)
    assert s == 0.0, f"expected no divergence, got {s}"


def test_sector_opposite():
    """Stock up 7% but sector down 2% = strong divergence in SAME direction."""
    s = sector_divergence(change_today=7.0, sector_change=-2.0)
    assert s > 25, f"expected strong divergence bonus, got {s}"


def test_short_interest_long_squeeze():
    """High SI on LONG = squeeze bonus."""
    s = short_interest_risk(direction="LONG", short_float_pct=30.0, days_to_cover=7)
    assert s > 0, f"expected squeeze bonus for LONG, got {s}"


def test_short_interest_short_danger():
    """High SI on SHORT = danger penalty."""
    s = short_interest_risk(direction="SHORT", short_float_pct=40.0, days_to_cover=10)
    assert s < -20, f"expected big penalty for SHORT with high SI, got {s}"


def test_short_interest_none():
    """No SI data = neutral."""
    assert short_interest_risk() == 0.0
    assert short_interest_risk(direction="LONG") == 0.0


def test_price_structure_strong_up():
    """Strong up day = positive momentum."""
    s = price_structure(change_today=6.0, change_5d=12.0)
    assert s > 0, f"expected positive momentum, got {s}"


def test_price_structure_exhausted():
    """Big 5d move on very high volume = exhaustion (blow-off)."""
    s = price_structure(change_today=3.0, change_5d=15.0, rvol=4.0, atr_pct=2.0)
    assert s < 10, f"expected exhaustion/weak signal, got {s}"


def test_liquidity_midcap_sweetspot():
    """$5B cap, $10M daily vol = sweet spot."""
    s = liquidity(market_cap=5e9, avg_dollar_vol=10_000_000)
    assert s > 0, f"expected positive liquidity, got {s}"


def test_liquidity_megacap():
    """Mega-caps price too efficiently."""
    s_mega = liquidity(market_cap=200e9, avg_dollar_vol=500_000_000)
    s_mid = liquidity(market_cap=3e9, avg_dollar_vol=20_000_000)
    assert s_mid > s_mega, f"expected mid > mega, got mid={s_mid} mega={s_mega}"


def test_liquidity_wide_spread():
    """Wide spread = penalty."""
    s = liquidity(avg_dollar_vol=5_000_000, spread_pct=3.0)
    assert s < 0, f"expected penalty for wide spread, got {s}"


def test_composite_long():
    """Full context should give LONG direction."""
    ctx = {
        "reaction_pct": 8.5, "drift_pct": 3.2, "gap_pct": 5.3,
        "change_today": 8.5, "change_5d": 12.0, "rvol": 2.5,
        "avg_dollar_vol": 10_000_000, "market_cap": 3e9,
    }
    result = compute_composite(ctx)
    assert result["direction"] == "LONG", f"expected LONG, got {result['direction']}"
    assert result["score"] > 5, f"expected meaningful score, got {result['score']}"
    assert result["active_signals"] >= 2, f"expected 2+ signals, got {result['active_signals']}"
    assert result["passed"]


def test_composite_short():
    """Negative reaction context should give SHORT direction."""
    ctx = {
        "reaction_pct": -7.0, "drift_pct": -3.0, "gap_pct": -4.0,
        "change_today": -7.0, "change_5d": -10.0, "rvol": 3.0,
        "avg_dollar_vol": 500_000, "market_cap": 200_000_000,
        "short_float_pct": 25.0, "days_to_cover": 8,
    }
    result = compute_composite(ctx)
    assert result["direction"] == "SHORT", f"expected SHORT, got {result['direction']}"


def test_composite_below_threshold():
    """Weak context should fail the pass filter."""
    ctx = {"change_today": 1.0, "rvol": 0.5}
    result = compute_composite(ctx)
    assert not result["passed"], f"expected fail, got {result['passed']}"


def test_composite_no_signals():
    """Empty context = nothing."""
    result = compute_composite({})
    assert result["active_signals"] == 0
    assert not result["passed"]
