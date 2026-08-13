"""Statistical signals for stock candidates — pure math, zero LLM.

Designed for post-earnings-drift (the primary alpha thesis): markets underreact
to earnings because algos price the headline but miss the nuance. Signals
measure where that underreaction is most likely.

Each signal returns -100 (strong SHORT) to +100 (strong LONG).
"""

import logging
import math
from typing import Optional

log = logging.getLogger("alphadesk.quant.signals")

DEFAULT_WEIGHTS: dict[str, float] = {
    "earnings_drift":       0.30,   # the core thesis: reaction + ongoing drift
    "volume_expansion":     0.20,   # post-report volume surge confirms new info
    "sector_divergence":    0.15,   # independent moves > sector-wide moves
    "short_interest_risk": -0.10,   # SHORT picks: squeeze risk penalty
    "price_structure":      0.15,   # ATR context, range position, reversal risk
    "liquidity":            0.10,   # tradeable depth + spread
}


def _clamp(v: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ── Individual signals ──────────────────────────────────────────────────────


def earnings_drift(reaction_pct: Optional[float], drift_pct: Optional[float],
                   gap_pct: Optional[float] = None,
                   implied_move: Optional[float] = None) -> float:
    """The core signal: post-earnings drift quality.

    Measures three things:
      1. Direction + magnitude of the reaction (market agrees something happened)
      2. How much of that reaction is still tradeable (drift vs gap)
      3. Underreaction: if the move is SMALLER than options-implied, the market
         hasn't fully repriced → room to drift further (stronger signal)

    A big gap with zero drift = fully priced, skip. A small gap with continued
    drift in the same direction = underreaction, strong signal.
    """
    total = reaction_pct or 0.0
    if abs(total) < 0.5:
        return 0.0
    direction = 1.0 if total > 0 else -1.0
    magnitude = min(abs(total), 25.0) / 25.0
    score = direction * magnitude * 60.0

    # Drift continuation: drift continuing in same direction as gap = underreaction
    if drift_pct is not None and gap_pct is not None:
        drift_dir = 1.0 if drift_pct > 0 else -1.0
        gap_dir = 1.0 if gap_pct > 0 else -1.0
        drift_mag = min(abs(drift_pct), 15.0) / 15.0
        if drift_dir == direction:
            score += direction * drift_mag * 30.0
        else:
            score -= direction * drift_mag * 20.0  # fading drift = spent
        if gap_dir == drift_dir:
            gap_share = min(abs(gap_pct) / max(abs(total), 0.01), 1.0)
            if gap_share < 0.4:
                score *= 1.25  # most of the move is still ahead
            elif gap_share > 0.85:
                score *= 0.6   # already priced in

    # Underreaction gauge: implied vs realized
    if implied_move is not None and implied_move > 0 and abs(total) > 0:
        ratio = abs(total) / implied_move
        if ratio < 0.5:
            score *= 1.3   # strong underreaction — market barely moved vs what options expected
        elif ratio < 0.8:
            score *= 1.15
        elif ratio > 1.5:
            score *= 0.7   # overreacted — likely spent

    return _clamp(score)


def volume_expansion(rvol: Optional[float], post_vol_ratio: Optional[float] = None,
                     reaction_pct: Optional[float] = None) -> float:
    """Post-report volume surge confirms genuine new information entered the market.
    Thin volume on a big move = noise. Elevated volume = institutional repositioning.

    SIGN FOLLOWS THE REACTION DIRECTION — high volume on a down move confirms SHORT,
    high volume on an up move confirms LONG. Neutral if direction is unclear.

    post_vol_ratio: today's (or post-report) volume ÷ trailing 20-day average.
    If not provided, falls back to rvol.
    """
    v = post_vol_ratio or rvol
    if v is None:
        return 0.0
    direction = 1.0 if (reaction_pct or 0) >= 0 else -1.0
    if v >= 4.0:
        return 35.0 * direction
    if v >= 2.5:
        return 25.0 * direction
    if v >= 1.8:
        return 15.0 * direction
    if v >= 1.2:
        return 5.0 * direction
    if v >= 0.7:
        return -5.0 * direction
    return -20.0 * direction


def sector_divergence(change_today: Optional[float], sector_change: Optional[float] = None) -> float:
    """Is this stock moving INDEPENDENTLY of its sector? Independent moves are
    higher signal — they're company-specific catalysts, not sector rotation.

    Same direction as sector = weaker signal (just sector rotation).
    Opposite direction or sector flat = company-specific → stronger signal.
    Direction follows the stock's move.
    """
    if change_today is None or sector_change is None:
        return 0.0
    divergence = abs(change_today - sector_change)
    if divergence <= 5:
        return 0.0
    same_dir = (change_today > 0) == (sector_change > 0)
    amplitude = min(divergence * 4.0, 30.0)
    direction = 1.0 if change_today > 0 else -1.0
    if same_dir:
        return direction * amplitude * 0.5  # sector move, weaker
    else:
        return direction * amplitude  # company-specific, stronger


def short_interest_risk(direction: Optional[str] = None,
                        short_float_pct: Optional[float] = None,
                        days_to_cover: Optional[float] = None) -> float:
    """Short squeeze / borrow risk. Only fires meaningfully when SI data exists.

    For LONG: high SI + positive catalyst = squeeze fuel (bonus).
    For SHORT: high SI = borrow cost + squeeze danger (penalty).

    Neutral if no SI data or direction unknown.
    """
    if direction is None or short_float_pct is None:
        return 0.0
    if short_float_pct <= 0:
        return 0.0
    base_risk = min(short_float_pct, 50.0) / 50.0 * 40.0  # 0-40 scale
    if days_to_cover is not None and days_to_cover > 5:
        base_risk = min(base_risk + 20, 60.0)
    if direction == "SHORT":
        return -base_risk  # penalty: squeeze danger + borrow cost
    else:
        return base_risk * 0.5  # bonus: squeeze could amplify the move


def price_structure(change_today: Optional[float], change_5d: Optional[float],
                    change_20d: Optional[float] = None,
                    atr_pct: Optional[float] = None,
                    rvol: Optional[float] = None) -> float:
    """Price context: trend strength, range position, and reversal risk.
    Combines momentum (continuation thesis) with exhaustion (reversal risk).

    ATR pct is the stock's typical daily range as % of price — used to tell
    whether today's move is routine or extraordinary.
    """
    s: float = 0.0

    # Trend: short-term direction (continuation thesis)
    if change_today is not None:
        s += _clamp(change_today * 2.5, -40, 40)
    if change_5d is not None:
        s += _clamp(change_5d * 1.0, -30, 30)

    # Exhaustion: is the cumulative move getting stretched?
    abs_5d = abs(change_5d or 0)
    abs_20d = abs(change_20d or 0)
    if atr_pct and atr_pct > 0:
        atr_mult_5d = abs_5d / atr_pct if atr_pct > 0 else 0
        atr_mult_20d = abs_20d / atr_pct if atr_pct > 0 else 0
        if atr_mult_5d > 5:
            s -= 25.0 if change_5d and change_5d > 0 else 25.0
        elif atr_mult_5d > 3:
            s -= 10.0 if change_5d and change_5d > 0 else 10.0
        if atr_mult_20d > 12:
            s -= 35.0
        elif atr_mult_20d > 8:
            s -= 20.0
    else:
        if abs_5d > 12:
            s -= 20.0
        elif abs_5d > 7:
            s -= 8.0
        if abs_20d > 25:
            s -= 30.0
        elif abs_20d > 15:
            s -= 15.0

    # Climax volume: exhaustion signal (big move on extreme volume = blow-off)
    if rvol is not None and rvol > 3.0 and abs_5d > 8:
        s -= 20.0

    return _clamp(s)


def liquidity(market_cap: Optional[float] = None,
              avg_dollar_vol: Optional[float] = None,
              spread_pct: Optional[float] = None) -> float:
    """Tradeability score. Factors:
      - Market cap tier (small caps have MORE post-earnings drift potential, but
        also more friction — bonus up to mid-cap, taper above)
      - Dollar volume (ability to enter/exit without impact)
      - Spread width (wide spreads = higher friction)

    Small enough to drift, large enough to trade = sweet spot.
    """
    s: float = 0.0

    # Dollar volume: basic tradeability floor
    if avg_dollar_vol is not None:
        if avg_dollar_vol >= 100_000_000:
            s += 10.0
        elif avg_dollar_vol >= 10_000_000:
            s += 5.0
        elif avg_dollar_vol >= 2_000_000:
            s += 0.0
        elif avg_dollar_vol >= 500_000:
            s -= 5.0
        else:
            s -= 15.0

    # Market cap: the drift sweet spot is $500M-$10B
    # Mega-caps (>$100B) price efficiently → less drift
    # Micro-caps (<$200M) are too noisy/illiquid
    if market_cap is not None:
        cap_b = market_cap / 1e9
        if cap_b > 100:
            s -= 10.0   # mega cap — efficient pricing, minimal drift
        elif cap_b > 10:
            s += 0.0    # large cap — ok but less drift
        elif cap_b > 2:
            s += 10.0   # mid cap — sweet spot for underreaction
        elif cap_b > 0.5:
            s += 15.0   # small cap — less analyst coverage, more drift
        elif cap_b > 0.1:
            s += 5.0    # micro cap — drift potential but high noise
        else:
            s -= 5.0    # nano cap — untradeable

    # Spread: wide spreads eat returns
    if spread_pct is not None:
        if spread_pct > 2.0:
            s -= 20.0
        elif spread_pct > 1.0:
            s -= 10.0
        elif spread_pct > 0.5:
            s -= 5.0
        elif spread_pct < 0.1:
            s += 5.0

    return _clamp(s)


# ── Composite scorer ─────────────────────────────────────────────────────────


def _signal_value(name: str, **ctx) -> float:
    try:
        if name == "earnings_drift":
            return earnings_drift(
                ctx.get("reaction_pct"), ctx.get("drift_pct"),
                ctx.get("gap_pct"), ctx.get("implied_move_pct"))
        if name == "volume_expansion":
            return volume_expansion(ctx.get("rvol"), ctx.get("post_vol_ratio"),
                                    ctx.get("reaction_pct"))
        if name == "sector_divergence":
            return sector_divergence(
                ctx.get("change_today"), ctx.get("sector_change_pct"))
        if name == "short_interest_risk":
            return short_interest_risk(
                ctx.get("direction"), ctx.get("short_float_pct"),
                ctx.get("days_to_cover"))
        if name == "price_structure":
            return price_structure(
                ctx.get("change_today"), ctx.get("change_5d"),
                ctx.get("change_20d"), ctx.get("atr_pct"), ctx.get("rvol"))
        if name == "liquidity":
            return liquidity(
                ctx.get("market_cap"), ctx.get("avg_dollar_vol"),
                ctx.get("spread_pct"))
    except Exception:
        return 0.0
    return 0.0


def compute_composite(ctx: dict, weights: dict[str, float] | None = None,
                      min_signals: int = 2) -> dict:
    """Compute a weighted composite score for one candidate.

    Returns {composite, direction, active_count, signals: {name: value}, ...}
    composite > 0 → LONG, < 0 → SHORT.
    """
    w = weights or DEFAULT_WEIGHTS
    scored: dict[str, float] = {}
    weighted_sum: float = 0.0
    active_weight: float = 0.0

    for name, weight in w.items():
        val = _signal_value(name, **ctx)
        scored[name] = val
        if val != 0.0 and weight != 0.0:
            weighted_sum += val * weight
            active_weight += abs(weight)

    if not math.isfinite(weighted_sum):
        # A NaN/inf weight or context value should never produce an unscorable
        # candidate — treat it as neutral rather than propagating NaN into a
        # round-to-int downstream (2026-08-13: a corrupted weights file did
        # exactly this and crashed autorun). See quant/calibrate.py's guards
        # for where this is meant to be prevented in the first place.
        log.warning("compute_composite: non-finite weighted_sum (weights=%r) — treating as neutral", w)
        weighted_sum = 0.0

    direction_side = "LONG" if weighted_sum > 0 else "SHORT"
    abs_composite = abs(weighted_sum)

    result = {
        "composite": round(weighted_sum, 2),
        "direction": direction_side,
        "score": round(min(abs_composite, 100.0), 2),
        "active_signals": sum(1 for v in scored.values() if v != 0.0),
        "signals": scored,
        "passed": (sum(1 for v in scored.values() if v != 0.0) >= min_signals
                   and abs_composite >= 5.0),
    }
    return result
