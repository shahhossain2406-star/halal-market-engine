"""
providers.py -- where market data comes from.

Two providers with the SAME interface:

  * YFinanceProvider -- real data via the free `yfinance` library. This is what
    you run on your PC. (Requires internet; Yahoo is free & covers ASX + US.)

  * DemoProvider -- synthetic, offline, deterministic data. Lets you (and the
    build/test process) run the FULL pipeline with no internet, just to see how
    it behaves. Numbers are fake -- never use demo output for real decisions.

Each provider returns, per ticker:
  history: pandas DataFrame indexed by date with columns
           [Open, High, Low, Close, Volume]
  info:    dict of fundamentals (any may be None)
"""

from __future__ import annotations
import math
import pandas as pd


class YFinanceProvider:
    name = "yfinance"

    def __init__(self, history_days: int = 400):
        self.history_days = history_days
        import yfinance as yf  # imported lazily so demo mode needs no install
        self._yf = yf

    def fetch(self, ticker: str):
        tk = self._yf.Ticker(ticker)
        period = f"{max(self.history_days, 60)}d"
        hist = tk.history(period=period, auto_adjust=True)
        if hist is not None and not hist.empty:
            hist = hist[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")
        info = {}
        try:
            raw = tk.get_info() or {}
        except Exception:
            raw = {}
        info = _normalise_info(raw)
        return hist, info


def _normalise_info(raw: dict) -> dict:
    """Map yfinance's info keys to our stable metric names."""
    def g(*keys):
        for k in keys:
            v = raw.get(k)
            if v not in (None, "", "Infinity"):
                return v
        return None
    return {
        "pe":              g("trailingPE"),
        "forward_pe":      g("forwardPE"),
        "pb":              g("priceToBook"),
        "div_yield":       g("dividendYield", "trailingAnnualDividendYield"),
        "profit_margin":   g("profitMargins"),
        "revenue_growth":  g("revenueGrowth"),
        "earnings_growth": g("earningsGrowth", "earningsQuarterlyGrowth"),
        "market_cap":      g("marketCap"),
        "debt_to_equity":  g("debtToEquity"),
        "beta":            g("beta"),
    }


class DemoProvider:
    """Deterministic fake data so the pipeline can be exercised offline."""
    name = "demo"

    def __init__(self, history_days: int = 400, as_of: str | None = None):
        self.history_days = history_days
        self.as_of = as_of  # optional 'YYYY-MM-DD' end date for reproducible demos

    def fetch(self, ticker: str):
        seed = sum(ord(c) for c in ticker)
        n = self.history_days
        end = pd.Timestamp(self.as_of) if self.as_of else pd.Timestamp.today().normalize()
        idx = pd.bdate_range(end=end, periods=n)

        base = 20 + (seed % 380)                 # per-ticker base price 20..400
        drift = ((seed % 7) - 3) / 4000.0        # gentle long-run trend per day
        amp = 0.02 + (seed % 6) / 100.0          # cyclical amplitude
        period = 15 + (seed % 45)                # per-ticker cycle length (days)
        phase = (seed % 360) * math.pi / 180.0

        # Anchor the walk to ABSOLUTE dates so different as-of windows differ and
        # cross-sectional rankings genuinely rotate over time (different tickers
        # peak at different times because their cycle lengths differ).
        anchor = pd.Timestamp("2024-01-01").toordinal()
        closes = []
        for d in idx:
            o = d.toordinal() - anchor
            trend = base * (1.0 + drift * o)
            cyc = amp * base * math.sin(o / period + phase)
            noise = (((o * (seed + 1)) % 97) - 48) / 48.0 * amp * base * 0.25
            closes.append(max(1.0, trend + cyc + noise))

        s = pd.Series(closes, index=idx)
        hist = pd.DataFrame({
            "Open":  s.shift(1).fillna(s.iloc[0]),
            "High":  s * (1 + amp / 2),
            "Low":   s * (1 - amp / 2),
            "Close": s,
            "Volume": [(1_000_000 + (seed * 7919) % 5_000_000) *
                       (1 + 0.5 * math.sin((i + seed) / 9.0)) for i in range(n)],
        }, index=idx)

        info = {
            "pe":              8 + (seed % 40),
            "forward_pe":      7 + (seed % 35),
            "pb":              1 + (seed % 12),
            "div_yield":       ((seed % 6) / 100.0),
            "profit_margin":   0.02 + (seed % 30) / 100.0,
            "revenue_growth":  -0.05 + (seed % 40) / 100.0,
            "earnings_growth": -0.10 + (seed % 50) / 100.0,
            "market_cap":      1e9 * (5 + seed % 500),
            "debt_to_equity":  (seed % 150),
            "beta":            0.6 + (seed % 12) / 10.0,
        }
        return hist, info


def get_provider(mode: str, history_days: int, as_of: str | None = None):
    if mode == "demo":
        return DemoProvider(history_days=history_days, as_of=as_of)
    return YFinanceProvider(history_days=history_days)
