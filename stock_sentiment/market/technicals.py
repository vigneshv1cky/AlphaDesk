"""Technical indicator computation from OHLCV data."""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from stock_sentiment.market.price_fetcher import PriceData


@dataclass
class TechnicalIndicators:
    symbol: str
    rsi_14: Optional[float] = None  # 0-100


class TechnicalAnalyzer:
    """Computes RSI from OHLCV DataFrames."""

    def analyze(self, price_data: PriceData) -> TechnicalIndicators:
        df = price_data.ohlcv
        if df is None or df.empty or len(df) < 5:
            return TechnicalIndicators(symbol=price_data.symbol)

        closes = df["Close"].astype(float)
        return TechnicalIndicators(
            symbol=price_data.symbol,
            rsi_14=self.compute_rsi(closes, 14),
        )

    def analyze_batch(
        self, price_map: dict[str, PriceData]
    ) -> dict[str, TechnicalIndicators]:
        return {
            symbol: self.analyze(pd)
            for symbol, pd in price_map.items()
        }

    @staticmethod
    def compute_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
        """RSI using Wilder's smoothing method."""
        if len(closes) < period + 1:
            return None

        deltas = closes.diff().dropna()
        gains = deltas.where(deltas > 0, 0.0)
        losses = (-deltas).where(deltas < 0, 0.0)

        avg_gain = gains.rolling(window=period, min_periods=period).mean().iloc[-1]
        avg_loss = losses.rolling(window=period, min_periods=period).mean().iloc[-1]

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return float(100 - (100 / (1 + rs)))
