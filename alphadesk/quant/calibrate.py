"""Adaptive signal weighting — learns which signals pay from graded outcomes.

Two modes:
  • Online: after each graded pick, nudge weights toward signals that predicted
    the correct direction.
  • Batch: re-analyze the last N closed trades to recalibrate from scratch.
"""

import json
import logging
import math
import os
from pathlib import Path

log = logging.getLogger("alphadesk.quant.calibrate")

DATA_DIR = Path(os.environ.get("ALPHADESK_DATA", "~/.alphadesk")).expanduser()
WEIGHTS_PATH = DATA_DIR / "quant_weights.json"

DEFAULT_WEIGHTS = {
    "earnings_drift":       0.30,
    "volume_expansion":     0.20,
    "sector_divergence":    0.15,
    "short_interest_risk": -0.10,
    "price_structure":      0.15,
    "liquidity":            0.10,
}

LEARNING_RATE = 0.01
MIN_WEIGHT = 0.005
BATCH_MIN_TRADES = 10
BATCH_LOOKBACK = 200


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    """Ensure weights sum to 1.0 by absolute value (direction-preserving).
    Falls back to defaults on a non-positive OR non-finite total. The old
    `total <= 0` check alone let a NaN total slip through — any comparison
    with NaN is False, so `nan <= 0` is False too — and divided every weight
    by NaN, permanently corrupting the persisted weights file (2026-08-13
    incident: a NaN pnl_pct into online_update poisoned all six weights)."""
    total = sum(abs(w) for w in weights.values())
    if not (total > 0 and math.isfinite(total)):
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in weights.items()}


def load_weights() -> dict[str, float]:
    """Load persisted weights, or return defaults. Rejects a corrupted file
    (missing key, non-numeric, or non-finite value) rather than feeding NaN/inf
    into every candidate scored this run — belt-and-suspenders alongside the
    write-side guards in online_update/_normalize."""
    try:
        if WEIGHTS_PATH.exists():
            data = json.loads(WEIGHTS_PATH.read_text())
            if (isinstance(data, dict) and "earnings_drift" in data
                    and all(isinstance(v, (int, float)) and math.isfinite(v)
                            for v in data.values())):
                return data
            log.warning("quant_weights.json invalid or non-finite — using defaults")
    except Exception:
        pass
    return dict(DEFAULT_WEIGHTS)


def save_weights(weights: dict[str, float]):
    """Persist weights to disk."""
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(weights, indent=2))


def online_update(weights: dict[str, float], signal_values: dict[str, float],
                  actual_direction: str, pnl_pct: float) -> dict[str, float]:
    """Nudge each signal's weight based on whether it predicted correctly.
    actual_direction: 'LONG' or 'SHORT' — the realised outcome.
    pnl_pct: the realised P&L (determines update magnitude).
    """
    if not math.isfinite(pnl_pct):
        # A NaN/inf outcome teaches nothing and, left unguarded, poisons every
        # weight it touches (see _normalize's docstring for the 2026-08-13
        # incident this caused). Skip the update rather than learn from garbage.
        log.warning("online_update: skipping non-finite pnl_pct=%r", pnl_pct)
        return weights
    impact = min(abs(pnl_pct), 100.0) / 100.0
    impact = max(impact, 0.1)

    result = dict(weights)
    for name, val in signal_values.items():
        if val == 0:
            continue
        predicted_long = val > 0
        actual_long = actual_direction == "LONG"
        correct = predicted_long == actual_long
        delta = LEARNING_RATE * impact * (1.0 if correct else -1.0)
        result[name] = result.get(name, 0.0) + delta
        if abs(result[name]) < MIN_WEIGHT:
            result[name] = MIN_WEIGHT * (1.0 if result[name] > 0 else -1.0)

    return _normalize(result)


def batch_calibrate(trades: list[dict]) -> dict[str, float]:
    """Re-calibrate weights from the last N closed trades.
    Each trade must have: {direction, pnl_pct, signals: {name: value}}.
    """
    if len(trades) < BATCH_MIN_TRADES:
        return load_weights()

    signal_scores: dict[str, float] = {}
    signal_counts: dict[str, int] = {}

    for t in trades:
        actual_direction = t.get("direction", "")
        pnl = float(t.get("pnl_pct", 0))
        signals = t.get("signals", {})

        for name, val in signals.items():
            if val == 0:
                continue
            predicted_long = val > 0
            actual_long = actual_direction == "LONG"
            correct = 1.0 if predicted_long == actual_long else -1.0
            score = math.tanh(pnl * 0.1) * correct
            signal_scores[name] = signal_scores.get(name, 0.0) + score
            signal_counts[name] = signal_counts.get(name, 0) + 1

    new_weights = {}
    for name in DEFAULT_WEIGHTS:
        total = signal_scores.get(name, 0.0)
        n = signal_counts.get(name, 0)
        avg = total / n if n > 0 else 0.0
        new_weights[name] = max(MIN_WEIGHT, avg) if avg > 0 else (-MIN_WEIGHT if avg < 0 else MIN_WEIGHT)

    result = _normalize(new_weights)
    save_weights(result)
    log.info("Batch calibrated weights from %d trades: %s", len(trades),
             {k: round(v, 3) for k, v in result.items()})
    return result


def optimize_exits(graded_picks: list[dict]) -> dict:
    """Adjust exit parameters based on graded outcomes. Checks:
      - Stop-out ratio: if >60% of exits are stops (not targets/give-backs), widen stop
      - Target-hit ratio: if <20% reach target, tighten target
    Returns dict of recommended adjustments. Non-destructive — returns suggestions only.
    """
    if len(graded_picks) < 20:
        return {}

    stops = 0
    targets = 0
    givebacks = 0
    total_exited = 0
    stop_losses: list[float] = []

    for g in graded_picks:
        reason = (g.get("exit_reason") or "").lower()
        if not reason:
            continue
        total_exited += 1
        if "stop" in reason and "trailing" not in reason:
            stops += 1
            alpha = float(g.get("alpha_net", 0))
            stop_losses.append(abs(alpha))
        elif "target" in reason or "take-profit" in reason:
            targets += 1
        elif "give-back" in reason:
            givebacks += 1

    if total_exited < 10:
        return {}

    recommendations = {}
    stop_ratio = stops / total_exited if total_exited else 0
    target_ratio = targets / total_exited if total_exited else 0
    avg_stop_loss = sum(stop_losses) / len(stop_losses) if stop_losses else 0

    # Too many stops → widen the stop
    if stop_ratio > 0.6:
        widen = min(stop_ratio - 0.5, 0.3)
        recommendations["PLAN_STOP_ATR"] = f"widen by +{round(widen, 2)} (current stop ratio {stop_ratio:.0%})"

    # Targets rarely hit → tighten target
    if target_ratio < 0.15:
        tighten = 0.5
        recommendations["PLAN_TARGET_ATR"] = f"tighten by -{tighten} (current target ratio {target_ratio:.0%})"

    # Give-backs firing often → increase retain fraction
    if givebacks / max(total_exited, 1) > 0.3:
        recommendations["GIVEBACK_RETAIN"] = "increase (give-backs {:.0%} of exits)".format(givebacks / total_exited)

    if recommendations:
        log.info("Exit param recommendations: %s", recommendations)

    return recommendations
