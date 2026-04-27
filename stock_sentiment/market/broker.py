import json
import math
import os
import re
from datetime import datetime, timedelta, timezone, tzinfo

from rich.console import Console

console = Console()

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, TrailingStopOrderRequest, StopOrderRequest,
        GetOrdersRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestTradeRequest
except ImportError:
    TradingClient = None  # type: ignore[assignment,misc]
    StockHistoricalDataClient = None  # type: ignore[assignment,misc]

_COOLDOWN_FILE = os.path.expanduser("~/.stock_screener/cooldowns.json")
_HELD_CACHE_FILE = os.path.expanduser("~/.stock_screener/held_cache.json")
_EXECUTION_LOG_FILE = os.path.expanduser("~/.stock_screener/last_execution.json")
_COOLDOWN_HOURS = 1
_SHORT_ENTRY_MAX_SCORE = 35

_ET: tzinfo
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _ET = _ZoneInfo("America/New_York")
except ImportError:
    _ET = timezone(timedelta(hours=-4))  # EDT fallback


def _is_long_position(p) -> bool:
    return str(getattr(p, "side", "")).lower() in ("long", "positionside.long")


class PaperBroker:
    """Automated paper trading executor using Alpaca with Smart Conviction Swapping."""

    def __init__(self):
        self.trail_percent = 3.0
        self.max_positions = 10        # total portfolio cap
        self.max_short_cap = 8         # never go more than 8 shorts (keeps at least 2 long slots)
        self.buy_threshold = 60.0
        self._data_client = None

        from stock_sentiment.config import load_settings
        settings = load_settings()
        self.fixed_position_dollars = float(settings.get("fixed_position_dollars", 0))

        env_paper = os.environ.get("ALPACA_PAPER")
        paper_mode = env_paper.lower() != "false" if env_paper is not None else settings.get("alpaca_paper", True)

        if paper_mode:
            self.api_key = os.environ.get("ALPACA_API_KEY", "")
            self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        else:
            self.api_key = os.environ.get("ALPACA_LIVE_API_KEY") or settings.get("alpaca_live_api_key", "")
            self.secret_key = os.environ.get("ALPACA_LIVE_SECRET_KEY") or settings.get("alpaca_live_secret_key", "")

        if not self.api_key or not self.secret_key:
            mode_label = "paper" if paper_mode else "live"
            print(f"WARNING: Alpaca {mode_label} keys missing. Trade execution disabled.")
            self.client = None
        elif TradingClient is None:
            print("WARNING: alpaca-py is not installed. Trade execution disabled.")
            self.client = None
        else:
            try:
                if not paper_mode:
                    print("WARNING: Live trading mode active. Real money at risk.")
                self.client = TradingClient(self.api_key, self.secret_key, paper=paper_mode)
            except Exception as e:
                print(f"WARNING: Failed to initialize Alpaca TradingClient: {e}. Trade execution disabled.")
                self.client = None

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def _slot_size_for_score(self, portfolio_value: float) -> float:
        """Return dollar allocation for a single position.

        Uses fixed_position_dollars if set; otherwise 5% of portfolio.
        Floor of $50."""
        return max(50.0, round(portfolio_value * 0.09, 2))

    # ------------------------------------------------------------------
    # Profit-tier stop tightening
    # ------------------------------------------------------------------

    def _desired_trail_pct(self, position, trade_type: str = "SWING") -> float:
        """Return trailing stop % for a position.
        Day trades: fixed 1.5% (closed EOD anyway).
        Swing trades: tighten on large gains."""
        if trade_type == "DAY":
            return 1.5
        try:
            gain_pct = float(position.unrealized_plpc) * 100
        except (AttributeError, ValueError, TypeError):
            return self.trail_percent
        if gain_pct >= 30:
            return 0.8
        if gain_pct >= 15:
            return 1.5
        return self.trail_percent

    # ------------------------------------------------------------------
    # Cooldown persistence
    # ------------------------------------------------------------------

    def _load_cooldowns(self) -> dict:
        try:
            if os.path.exists(_COOLDOWN_FILE):
                with open(_COOLDOWN_FILE) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_cooldowns(self, cooldowns: dict) -> None:
        os.makedirs(os.path.dirname(_COOLDOWN_FILE), exist_ok=True)
        now = datetime.now(timezone.utc)
        active = {
            sym: ts for sym, ts in cooldowns.items()
            if (now - datetime.fromisoformat(ts)).total_seconds() < _COOLDOWN_HOURS * 3600
        }
        with open(_COOLDOWN_FILE, "w") as f:
            json.dump(active, f, indent=2)

    def _in_cooldown(self, symbol: str, cooldowns: dict) -> bool:
        ts = cooldowns.get(symbol)
        if not ts:
            return False
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
        return elapsed < _COOLDOWN_HOURS * 3600

    def _classify_trade(self, prediction) -> str:
        """DAY if high conviction (>=85) AND strong intraday volume surge (RVOL>=2); else SWING."""
        if prediction.overall_score >= 85 and (prediction.volume_ratio or 0) >= 2.0:
            return "DAY"
        return "SWING"

    def _can_short(self, symbol: str) -> bool:
        """Check if Alpaca allows shorting this asset (shortable AND easy_to_borrow)."""
        try:
            asset = self.client.get_asset(symbol)
            return bool(asset.shortable) and bool(asset.easy_to_borrow)
        except Exception:
            return False

    def _close_eod_day_trades(self, held_cache: dict, positions) -> set:
        """Close DAY long trades and ALL short positions at or after 3:45 PM ET.
        Returns closed symbols."""
        closed: set = set()
        try:
            now_et = datetime.now(timezone.utc).astimezone(_ET)
            if not (now_et.hour > 15 or (now_et.hour == 15 and now_et.minute >= 30)):
                return closed
            eod_syms = {
                p.symbol for p in positions
                if held_cache.get(p.symbol, {}).get("type") == "DAY"
                or held_cache.get(p.symbol, {}).get("direction") == "SHORT"
                or not _is_long_position(p)
            }
            if not eod_syms:
                return closed
            console.print(f"  [yellow]⏰ EOD close: {len(eod_syms)} position(s): {', '.join(sorted(eod_syms))}[/yellow]")
            for sym in eod_syms:
                try:
                    self._close_position_safely(sym)
                    closed.add(sym)
                except Exception as e:
                    console.print(f"  [red]✖ EOD close failed {sym}: {e}[/red]")
        except Exception as e:
            console.print(f"  [red]✖ EOD close error: {e}[/red]")
        return closed

    def _load_held_cache(self) -> dict:
        """Returns {symbol: {"type": "DAY"|"SWING", "direction": "LONG"|"SHORT", "entered_at": ISO}}."""
        try:
            if os.path.exists(_HELD_CACHE_FILE):
                with open(_HELD_CACHE_FILE) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return {s: {"type": "SWING", "direction": "LONG", "entered_at": ""} for s in data}
                # Back-fill "direction" for older cache entries written before short support
                for meta in data.values():
                    if isinstance(meta, dict) and "direction" not in meta:
                        meta["direction"] = "LONG"
                return data
        except Exception:
            pass
        return {}

    def _save_held_cache(self, cache: dict) -> None:
        os.makedirs(os.path.dirname(_HELD_CACHE_FILE), exist_ok=True)
        with open(_HELD_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)

    # ------------------------------------------------------------------
    # Trade cycle
    # ------------------------------------------------------------------

    def execute_trades(self, predictions, trigger="SCHEDULED"):
        """Execute trades with equal sizing and fixed position limits."""
        if not self.client:
            console.print("[yellow]⚠  Alpaca client not initialized — trade execution skipped.[/yellow]")
            return {}

        buy_threshold = self.buy_threshold

        exec_log: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trigger": trigger,
            "bought": [],
            "sold": [],
            "swapped": [],
            "shorted": [],
            "covered": [],
        }

        console.rule("[bold cyan]💼  Trade Execution[/bold cyan]")

        try:
            # Load persistent state from previous cycle
            cooldowns = self._load_cooldowns()
            held_cache = self._load_held_cache()
            prev_held = set(held_cache.keys())

            # 0. Cancel stale pending market orders from previous cycles
            for side in [OrderSide.BUY, OrderSide.SELL]:
                pending = self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, side=side))
                stale = [
                    o for o in pending
                    if str(getattr(o, "order_type", "")).lower() in ("market", "ordertype.market")
                ]
                if stale:
                    console.print(f"  [dim]Canceling {len(stale)} stale {side} market order(s)[/dim]")
                    for order in stale:
                        self.client.cancel_order_by_id(order_id=order.id)

            # 1. Fetch live account state
            account = self.client.get_account()
            cash = float(account.cash)
            portfolio_value = float(account.equity)
            positions = self.client.get_all_positions()

            long_positions = [p for p in positions if _is_long_position(p)]
            short_positions = [p for p in positions if not _is_long_position(p)]
            long_symbols: set = {p.symbol for p in long_positions}
            short_symbols: set = {p.symbol for p in short_positions}
            held_symbols = long_symbols | short_symbols

            over_cap = len(held_symbols) > self.max_positions
            console.print(
                f"  [dim]Portfolio:[/dim] [bold]${portfolio_value:,.2f}[/bold]"
                f"  [dim]Cash:[/dim] [bold]${cash:,.2f}[/bold]"
                f"  [dim]Positions:[/dim] [bold]{len(held_symbols)}/{self.max_positions}[/bold]"
                f"  [dim]({len(long_symbols)}L / {len(short_symbols)}S)[/dim]"
                + ("  [bold red]⚠ OVER CAP — trimming weakest[/bold red]" if over_cap else "")
                + f"  [dim]Threshold:[/dim] [bold]{buy_threshold:.0f}[/bold]"
            )

            # Build prediction lookup early — needed for cap enforcement and BEARISH exits
            pred_map = {p.symbol: p for p in predictions}

            # 1b. Total cap enforcement: close lowest-conviction positions until back at limit
            if over_cap:
                excess = len(held_symbols) - self.max_positions
                # Sort by distance from 50 ascending — least conviction closed first
                ranked = sorted(
                    held_symbols,
                    key=lambda s: abs(pred_map[s].overall_score - 50) if s in pred_map else 0.0,
                )
                to_trim = ranked[:excess]
                console.print(f"  [yellow]✂  Trimming {len(to_trim)} over-cap position(s): {', '.join(to_trim)}[/yellow]")
                for sym in to_trim:
                    try:
                        self._close_position_safely(sym)
                        long_symbols.discard(sym)
                        short_symbols.discard(sym)
                        held_symbols.discard(sym)
                        held_cache.pop(sym, None)
                        exec_log["sold"].append({"symbol": sym, "reason": "Position cap enforcement", "detail": f"Trimmed to {self.max_positions}-slot limit"})
                    except Exception as e:
                        console.print(f"  [red]✖ Trim failed {sym}: {e}[/red]")
                cash = float(self.client.get_account().cash)

            # 2. Safety Audit: ensure every position has a stop, tighten on large gains
            self._ensure_trailing_stops(positions, held_cache)

            # 2b. EOD close: liquidate all DAY trades at 3:45 PM ET
            eod_closed = self._close_eod_day_trades(held_cache, positions)
            for sym in eod_closed:
                held_symbols.discard(sym)
                long_symbols.discard(sym)
                short_symbols.discard(sym)
                held_cache.pop(sym, None)
                exec_log["sold"].append({"symbol": sym, "reason": "EOD close", "detail": "Day trade — closed at 3:45 PM ET"})

            # 3. Mandatory exits for long positions downgraded to BEARISH
            bearish_sold: set = set()
            for p in long_positions:
                symbol = p.symbol
                if symbol in eod_closed or symbol not in long_symbols:
                    continue
                if symbol in pred_map and pred_map[symbol].prediction == "BEARISH":
                    console.print(f"  [red]✖  SELL {symbol}[/red]  [dim]AI downgraded to BEARISH[/dim]")
                    self._close_position_safely(symbol)
                    long_symbols.discard(symbol)
                    held_symbols.discard(symbol)
                    bearish_sold.add(symbol)
                    exec_log["sold"].append({
                        "symbol": symbol,
                        "reason": "BEARISH downgrade",
                        "detail": "; ".join(pred_map[symbol].reasoning[:2]) if pred_map[symbol].reasoning else "",
                    })

            # 3a. Cover short positions that flipped BULLISH
            bullish_covered: set = set()
            for p in short_positions:
                symbol = p.symbol
                if symbol in eod_closed or symbol not in short_symbols:
                    continue
                if symbol in pred_map and pred_map[symbol].prediction == "BULLISH":
                    console.print(f"  [green]✔  COVER {symbol}[/green]  [dim]AI upgraded to BULLISH — closing short[/dim]")
                    self._close_position_safely(symbol)
                    short_symbols.discard(symbol)
                    held_symbols.discard(symbol)
                    bullish_covered.add(symbol)
                    exec_log["covered"].append({
                        "symbol": symbol,
                        "reason": "BULLISH upgrade",
                        "detail": "; ".join(pred_map[symbol].reasoning[:2]) if pred_map[symbol].reasoning else "",
                    })

            if bearish_sold or bullish_covered:
                cash = float(self.client.get_account().cash)
                console.print(f"  [dim]Cash after exits: ${cash:,.2f}[/dim]")

            # 3b. Earnings proximity exits (both directions)
            earnings_closed: set = set()
            for p in positions:
                symbol = p.symbol
                if symbol in bearish_sold or symbol in bullish_covered or symbol in eod_closed:
                    continue
                pred = pred_map.get(symbol)
                if pred and pred.days_to_earnings is not None and pred.days_to_earnings <= 3:
                    console.print(f"  [yellow]⚡ CLOSE {symbol}[/yellow]  [dim]earnings in {pred.days_to_earnings}d — gap risk[/dim]")
                    self._close_position_safely(symbol)
                    held_symbols.discard(symbol)
                    long_symbols.discard(symbol)
                    short_symbols.discard(symbol)
                    earnings_closed.add(symbol)
                    exec_log["sold"].append({
                        "symbol": symbol,
                        "reason": f"Earnings in {pred.days_to_earnings}d",
                        "detail": "Pre-emptive close to avoid gap risk",
                    })

            if earnings_closed:
                cash = float(self.client.get_account().cash)
                console.print(f"  [dim]Cash after earnings exits: ${cash:,.2f}[/dim]")

            # Detect stop-triggered exits (symbols in prev cache but no longer held)
            all_intentional_exits = bearish_sold | bullish_covered | earnings_closed | eod_closed
            stopped_out = prev_held - held_symbols - all_intentional_exits
            if stopped_out:
                now_iso = datetime.now(timezone.utc).isoformat()
                for sym in stopped_out:
                    cooldowns[sym] = now_iso
                    exec_log["sold"].append({"symbol": sym, "reason": "Trailing stop triggered", "detail": f"{_COOLDOWN_HOURS}h re-entry cooldown applied"})
                console.print(
                    f"  [dim]Stop-out detected: [/dim][yellow]{', '.join(stopped_out)}[/yellow]"
                    f"  [dim]→ {_COOLDOWN_HOURS}h re-entry cooldown applied[/dim]"
                )
                self._save_cooldowns(cooldowns)

            # 4. Process New Long Buy Opportunities
            all_bullish = [p for p in predictions if p.prediction == "BULLISH"]
            above_threshold = [p for p in all_bullish if p.overall_score >= buy_threshold]
            on_cooldown = [p for p in above_threshold if self._in_cooldown(p.symbol, cooldowns)]
            buy_candidates = [
                p for p in above_threshold
                if p.symbol not in held_symbols
                and not self._in_cooldown(p.symbol, cooldowns)
            ]
            buy_candidates.sort(key=lambda x: x.overall_score, reverse=True)

            console.print(
                f"\n  [dim]Long funnel:[/dim]  "
                f"[green]{len(all_bullish)} BULLISH[/green]  →  "
                f"[bold]{len(above_threshold)} above threshold[/bold]  →  "
                f"[cyan]{len(buy_candidates)} actionable[/cyan]"
                + (f"  [dim]({len(on_cooldown)} on cooldown)[/dim]" if on_cooldown else "")
            )

            buys_executed = 0
            swaps_executed = 0
            swapped_out: set = set()

            for new_pick in buy_candidates:
                if len(held_symbols) >= self.max_positions:
                    break
                trade_type = self._classify_trade(new_pick)
                slot = self._slot_size_for_score(portfolio_value)
                has_cash = cash >= slot
                has_slot = len(held_symbols) < self.max_positions

                # CASE 1: Standard Entry
                if has_cash and has_slot:
                    price, qty = self._place_market_buy(new_pick.symbol, new_pick.current_price, portfolio_value, trade_type)
                    if qty:
                        long_symbols.add(new_pick.symbol)
                        held_symbols.add(new_pick.symbol)
                        held_cache[new_pick.symbol] = {"type": trade_type, "direction": "LONG", "entered_at": datetime.now(timezone.utc).isoformat()}
                        cash -= slot
                        buys_executed += 1
                        console.print(f"  [dim]→ classified as [bold]{trade_type}[/bold] LONG[/dim]")
                        exec_log["bought"].append({
                            "symbol": new_pick.symbol,
                            "score": round(new_pick.overall_score, 1),
                            "archetype": new_pick.archetype,
                            "trade_type": trade_type,
                            "direction": "LONG",
                            "price": price,
                            "qty": qty,
                            "cost": round(price * qty, 2),
                            "reasons": new_pick.reasoning,
                        })

                # CASE 2: Swap — portfolio full, replace weakest long with better pick
                elif long_symbols and len(held_symbols) >= self.max_positions:
                    held_scores = [
                        (s, pred_map[s].overall_score)
                        for s in long_symbols
                        if s in pred_map
                    ]
                    if not held_scores:
                        continue

                    held_scores.sort(key=lambda x: x[1])
                    weakest_symbol, weakest_score = held_scores[0]

                    if new_pick.overall_score > (weakest_score + 5):
                        swap_reason = "FULL SLOTS" if not has_slot else "LOW CASH"
                        console.print(
                            f"  [yellow]⇄  SWAP ({swap_reason}):[/yellow]"
                            f"  [red]{weakest_symbol} {weakest_score:.1f}[/red]"
                            f"  →  [green]{new_pick.symbol} {new_pick.overall_score:.1f}[/green]"
                        )
                        try:
                            self._close_position_safely(weakest_symbol)
                            long_symbols.discard(weakest_symbol)
                            held_symbols.discard(weakest_symbol)
                            held_cache.pop(weakest_symbol, None)
                            swapped_out.add(weakest_symbol)
                            price, qty = self._place_market_buy(new_pick.symbol, new_pick.current_price, portfolio_value, trade_type)
                            if qty:
                                long_symbols.add(new_pick.symbol)
                                held_symbols.add(new_pick.symbol)
                                held_cache[new_pick.symbol] = {"type": trade_type, "direction": "LONG", "entered_at": datetime.now(timezone.utc).isoformat()}
                                swaps_executed += 1
                                console.print(f"  [dim]→ classified as [bold]{trade_type}[/bold] LONG[/dim]")
                                exec_log["swapped"].append({
                                    "out": weakest_symbol,
                                    "out_score": round(weakest_score, 1),
                                    "in": new_pick.symbol,
                                    "in_score": round(new_pick.overall_score, 1),
                                    "trade_type": trade_type,
                                    "direction": "LONG",
                                    "reason": swap_reason,
                                    "price": price,
                                    "qty": qty,
                                    "in_reasons": new_pick.reasoning,
                                })
                        except Exception as e:
                            console.print(f"  [red]✖  Swap failed for {new_pick.symbol}: {e}[/red]")

            # 5. Process Short Opportunities (BEARISH predictions with strong conviction)
            all_bearish = [p for p in predictions if p.prediction == "BEARISH"]
            short_candidates = [
                p for p in all_bearish
                if p.overall_score <= _SHORT_ENTRY_MAX_SCORE
                and p.symbol not in held_symbols
                and p.symbol not in swapped_out
            ]
            short_candidates.sort(key=lambda x: x.overall_score)  # lowest score = strongest BEARISH first

            if short_candidates:
                console.print(
                    f"\n  [dim]Short funnel:[/dim]  "
                    f"[red]{len(all_bearish)} BEARISH[/red]  →  "
                    f"[bold]{len(short_candidates)} short candidates (score≤{_SHORT_ENTRY_MAX_SCORE})[/bold]"
                )

            shorts_executed = 0
            for new_short in short_candidates:
                if not self._can_short(new_short.symbol):
                    console.print(f"  [dim]Skip short {new_short.symbol}: not shortable[/dim]")
                    continue
                trade_type = "DAY"
                slot = self._slot_size_for_score(portfolio_value)
                has_short_slot = len(short_symbols) < self.max_short_cap and len(held_symbols) < self.max_positions
                has_cash = cash >= slot

                # CASE 1: Standard short entry
                if has_cash and has_short_slot:
                    price, qty = self._place_market_short(new_short.symbol, new_short.current_price, portfolio_value, trade_type)
                    if qty:
                        short_symbols.add(new_short.symbol)
                        held_symbols.add(new_short.symbol)
                        held_cache[new_short.symbol] = {"type": trade_type, "direction": "SHORT", "entered_at": datetime.now(timezone.utc).isoformat()}
                        cash -= slot
                        shorts_executed += 1
                        console.print(f"  [dim]→ classified as [bold]{trade_type}[/bold] SHORT[/dim]")
                        exec_log["shorted"].append({
                            "symbol": new_short.symbol,
                            "score": round(new_short.overall_score, 1),
                            "archetype": new_short.archetype,
                            "trade_type": trade_type,
                            "direction": "SHORT",
                            "price": price,
                            "qty": qty,
                            "proceeds": round(price * qty, 2),
                            "reasons": new_short.reasoning,
                        })

                # CASE 2: Short swap — replace weakest short with stronger signal
                elif short_symbols and (len(short_symbols) >= self.max_short_cap or len(held_symbols) >= self.max_positions):
                    held_short_scores = [
                        (s, pred_map[s].overall_score)
                        for s in short_symbols
                        if s in pred_map
                    ]
                    if not held_short_scores:
                        continue
                    # Highest score = least bearish = weakest short
                    held_short_scores.sort(key=lambda x: x[1], reverse=True)
                    weakest_symbol, weakest_score = held_short_scores[0]
                    if new_short.overall_score < (weakest_score - 5):
                        console.print(
                            f"  [yellow]⇄  SHORT SWAP:[/yellow]"
                            f"  [dim]{weakest_symbol} {weakest_score:.1f}[/dim]"
                            f"  →  [red]{new_short.symbol} {new_short.overall_score:.1f}[/red]"
                        )
                        try:
                            self._close_position_safely(weakest_symbol)
                            short_symbols.discard(weakest_symbol)
                            held_symbols.discard(weakest_symbol)
                            held_cache.pop(weakest_symbol, None)
                            price, qty = self._place_market_short(new_short.symbol, new_short.current_price, portfolio_value, trade_type)
                            if qty:
                                short_symbols.add(new_short.symbol)
                                held_symbols.add(new_short.symbol)
                                held_cache[new_short.symbol] = {"type": trade_type, "direction": "SHORT", "entered_at": datetime.now(timezone.utc).isoformat()}
                                shorts_executed += 1
                                console.print(f"  [dim]→ classified as [bold]{trade_type}[/bold] SHORT SWAP[/dim]")
                                exec_log["swapped"].append({
                                    "out": weakest_symbol,
                                    "out_score": round(weakest_score, 1),
                                    "in": new_short.symbol,
                                    "in_score": round(new_short.overall_score, 1),
                                    "trade_type": trade_type,
                                    "direction": "SHORT",
                                    "reason": "STRONGER SHORT SIGNAL",
                                    "price": price,
                                    "qty": qty,
                                    "in_reasons": new_short.reasoning,
                                })
                        except Exception as e:
                            console.print(f"  [red]✖  Short swap failed for {new_short.symbol}: {e}[/red]")
                else:
                    break

            # Persist held cache — prune to symbols still held
            held_cache = {sym: meta for sym, meta in held_cache.items() if sym in held_symbols}
            self._save_held_cache(held_cache)

            exec_log["summary"] = {
                "bought": buys_executed,
                "shorted": shorts_executed,
                "sold": len(exec_log["sold"]),
                "covered": len(exec_log["covered"]),
                "swapped": swaps_executed,
                "held_long": len(long_symbols),
                "held_short": len(short_symbols),
            }

            # Write execution log for the dashboard
            try:
                os.makedirs(os.path.dirname(_EXECUTION_LOG_FILE), exist_ok=True)
                with open(_EXECUTION_LOG_FILE, "w") as f:
                    json.dump(exec_log, f, indent=2)
            except Exception:
                pass

            # Cycle summary
            console.print(
                f"\n  [dim]Cycle result:[/dim]  "
                f"[green]+{buys_executed} long[/green]  ·  "
                f"[red]↓{shorts_executed} short[/red]  ·  "
                f"[yellow]{swaps_executed} swapped[/yellow]  ·  "
                f"[red]{len(exec_log['sold'])} exits · {len(exec_log['covered'])} covered[/red]  ·  "
                f"[dim]{len(long_symbols)}L / {len(short_symbols)}S held[/dim]"
            )

        except Exception as e:
            console.print(f"  [bold red]CRITICAL: Trade cycle failed: {e}[/bold red]")

        console.rule()
        return exec_log

    # ------------------------------------------------------------------
    # Stop management
    # ------------------------------------------------------------------

    def _ensure_trailing_stops(self, positions, held_cache: dict):
        """Ensure every position has stop-loss protection; tighten stops on large gains.

        DAY trades: fixed 1.5% stop (closed EOD anyway).
        SWING trades profit tiers:
          ≥ 30% gain → 0.8%  |  ≥ 15% gain → 1.5%  |  default → 3.0%

        Long positions: SELL-side trailing stop (fires if price drops trail% from peak).
        Short positions: BUY-side trailing stop (fires if price rises trail% from trough).
        Fractional shares: stop-market order instead (Alpaca trailing-stop limitation).
        """
        try:
            open_orders = self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
            stop_map = {
                o.symbol: o for o in open_orders
                if str(getattr(o, "order_type", "")).lower() in ("trailing_stop", "stop")
            }

            stops_set = 0
            stops_tightened = 0
            stops_skipped = []

            for p in positions:
                try:
                    is_long = _is_long_position(p)
                    stop_side = OrderSide.SELL if is_long else OrderSide.BUY
                    trade_type = held_cache.get(p.symbol, {}).get("type", "SWING")
                    desired_trail = self._desired_trail_pct(p, trade_type)
                    qty = float(p.qty)

                    if p.symbol in stop_map:
                        existing = stop_map[p.symbol]
                        existing_side_str = str(getattr(existing, "side", "")).lower()
                        correct_side = ("sell" in existing_side_str) if is_long else ("buy" in existing_side_str)
                        if correct_side:
                            existing_trail = getattr(existing, "trail_percent", None)
                            if existing_trail is not None:
                                existing_trail = float(existing_trail)
                                if existing_trail <= desired_trail:
                                    continue
                                gain_pct = float(getattr(p, "unrealized_plpc", 0)) * 100
                                console.print(
                                    f"  [dim]Stop tightened[/dim] {p.symbol}: "
                                    f"[yellow]{existing_trail:.1f}% → {desired_trail:.1f}%[/yellow]"
                                    f"  [dim](gain: +{gain_pct:.1f}%)[/dim]"
                                )
                                self.client.cancel_order_by_id(existing.id)
                                stops_tightened += 1
                            else:
                                continue
                        # Wrong-side stop: fall through to place the correct-side stop

                    if qty.is_integer():
                        self.client.submit_order(TrailingStopOrderRequest(
                            symbol=p.symbol,
                            qty=qty,
                            side=stop_side,
                            time_in_force=TimeInForce.GTC,
                            trail_percent=desired_trail,
                        ))
                        stops_set += 1
                    else:
                        market_price = float(p.current_price)
                        if is_long:
                            stop_price = round(market_price * (1 - desired_trail / 100), 2)
                            valid_stop = stop_price < market_price
                        else:
                            stop_price = round(market_price * (1 + desired_trail / 100), 2)
                            valid_stop = stop_price > market_price
                        if not valid_stop:
                            continue
                        try:
                            self.client.submit_order(StopOrderRequest(
                                symbol=p.symbol,
                                qty=str(qty),
                                side=stop_side,
                                time_in_force=TimeInForce.DAY,
                                stop_price=stop_price,
                            ))
                            stops_set += 1
                        except Exception as stop_err:
                            err_str = str(stop_err)
                            live_match = re.search(r'"market_price"\s*:\s*"([\d.]+)"', err_str)
                            if live_match:
                                live_price = float(live_match.group(1))
                                if is_long:
                                    adj_stop = round(live_price * (1 - desired_trail / 100), 2)
                                    valid_adj = adj_stop < live_price
                                else:
                                    adj_stop = round(live_price * (1 + desired_trail / 100), 2)
                                    valid_adj = adj_stop > live_price
                                if valid_adj:
                                    console.print(f"  [dim]Stop retry {p.symbol}: live=${live_price:.2f} → stop=${adj_stop:.2f}[/dim]")
                                    self.client.submit_order(StopOrderRequest(
                                        symbol=p.symbol,
                                        qty=str(qty),
                                        side=stop_side,
                                        time_in_force=TimeInForce.DAY,
                                        stop_price=adj_stop,
                                    ))
                                    stops_set += 1
                                else:
                                    console.print(f"  [yellow]⚠  Cannot stop {p.symbol}: live ${live_price:.2f} — stop invalid[/yellow]")
                            else:
                                console.print(f"  [red]✖  Stop failed {p.symbol}: {stop_err}[/red]")

                except Exception:
                    # Shares locked by an existing order (e.g. pending liquidation)
                    stops_skipped.append(p.symbol)

            if stops_set or stops_tightened or stops_skipped:
                skipped_str = f"  [dim]·[/dim]  [yellow]{len(stops_skipped)} skipped: {', '.join(stops_skipped)}[/yellow]" if stops_skipped else ""
                console.print(f"  [dim]Stop audit:[/dim] {stops_set} set · {stops_tightened} tightened{skipped_str}")
        except Exception as e:
            console.print(f"  [red]✖  Safety audit setup failed: {e}[/red]")

    # ------------------------------------------------------------------
    # Position helpers
    # ------------------------------------------------------------------

    def _close_position_safely(self, symbol: str):
        """Cancel open orders for the symbol to unlock shares, then close at market.
        Works for both long (market sell) and short (market buy to cover) positions."""
        try:
            open_orders = self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol]))
            for order in open_orders:
                self.client.cancel_order_by_id(order_id=order.id)
            self.client.close_position(symbol_or_asset_id=symbol)
        except Exception as e:
            console.print(f"  [red]✖  Error closing {symbol}: {e}[/red]")
            raise

    def _get_live_price(self, symbol: str, fallback: float) -> float:
        """Fetch the latest trade price from Alpaca; fall back to screener price on error."""
        try:
            if StockHistoricalDataClient is None:
                return fallback
            if self._data_client is None:
                self._data_client = StockHistoricalDataClient(self.api_key, self.secret_key)
            trade = self._data_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=symbol)
            )
            return float(trade[symbol].price)
        except Exception:
            return fallback

    def _place_market_buy(self, symbol: str, current_price: float, portfolio_value: float, trade_type: str = "SWING") -> tuple[float, int]:
        """Submit a market buy (long entry) using whole shares.
        Returns (price, qty) on success, (0.0, 0) on skip or error."""
        live_price = self._get_live_price(symbol, current_price)
        price_source = "live" if live_price != current_price else "screener"

        if live_price <= 0:
            console.print(f"  [dim]Skipping {symbol}: invalid price[/dim]")
            return 0.0, 0

        slot = self._slot_size_for_score(portfolio_value)
        shares_to_buy = math.floor(slot / live_price)

        if shares_to_buy <= 0:
            console.print(f"  [dim]Skipping {symbol}: ${live_price:.2f} exceeds slot ${slot:.0f}[/dim]")
            return 0.0, 0

        actual_cost = shares_to_buy * live_price
        console.print(
            f"  [green]✔  BUY {symbol}[/green]"
            f"  [dim]qty=[/dim][bold]{shares_to_buy}[/bold]"
            f"  [dim]@[/dim] ${live_price:.2f} [dim]({price_source})[/dim]"
            f"  [dim]≈[/dim] [bold]${actual_cost:.2f}[/bold]"
        )

        try:
            self.client.submit_order(MarketOrderRequest(
                symbol=symbol,
                qty=shares_to_buy,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            ))
            return live_price, shares_to_buy
        except Exception as e:
            console.print(f"  [red]✖  Market buy failed for {symbol}: {e}[/red]")
            return 0.0, 0

    def _place_market_short(self, symbol: str, current_price: float, portfolio_value: float, trade_type: str = "SWING") -> tuple[float, int]:
        """Submit a market sell to open a short position using whole shares.
        Returns (price, qty) on success, (0.0, 0) on skip or error."""
        live_price = self._get_live_price(symbol, current_price)
        price_source = "live" if live_price != current_price else "screener"

        if live_price <= 0:
            console.print(f"  [dim]Skipping short {symbol}: invalid price[/dim]")
            return 0.0, 0

        slot = self._slot_size_for_score(portfolio_value)
        shares_to_short = math.floor(slot / live_price)

        if shares_to_short <= 0:
            console.print(f"  [dim]Skipping short {symbol}: ${live_price:.2f} exceeds slot ${slot:.0f}[/dim]")
            return 0.0, 0

        actual_value = shares_to_short * live_price
        console.print(
            f"  [red]✔  SHORT {symbol}[/red]"
            f"  [dim]qty=[/dim][bold]{shares_to_short}[/bold]"
            f"  [dim]@[/dim] ${live_price:.2f} [dim]({price_source})[/dim]"
            f"  [dim]≈[/dim] [bold]${actual_value:.2f}[/bold]"
        )

        try:
            self.client.submit_order(MarketOrderRequest(
                symbol=symbol,
                qty=shares_to_short,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            ))
            return live_price, shares_to_short
        except Exception as e:
            console.print(f"  [red]✖  Market short failed for {symbol}: {e}[/red]")
            return 0.0, 0
