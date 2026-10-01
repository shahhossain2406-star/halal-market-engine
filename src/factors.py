"""
factors.py -- turn raw price history + fundamentals into measurable "points".

This produces the RAW metrics for one ticker on one day. Ranking/normalisation
happens later in ranking.py (it needs the whole universe to compare against).

Every metric here is a distinct, stored "point" -- the more we capture, the
richer the later attribution ("which points moved this company's rank").
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def _ret(close: pd.Series, bars: int):
    if len(close) <= bars:
        return None
    past = close.iloc[-bars - 1]
    if past and not np.isnan(past) and past != 0:
        return float(close.iloc[-1] / past - 1.0)
    return None


def compute_raw(history: pd.DataFrame, info: dict, options: dict) -> dict:
    m: dict = {}
    min_hist = options.get("min_history_for_factor", 60)

    if history is not None and not history.empty:
        close = history["Close"].dropna()
        vol = history["Volume"].dropna() if "Volume" in history else pd.Series(dtype=float)

        if len(close):
            m["close"] = float(close.iloc[-1])

        # --- momentum / trend ------------------------------------------------
        if len(close) >= min_hist:
            m["ret_1m"] = _ret(close, 21)
            m["ret_3m"] = _ret(close, 63)
            m["ret_6m"] = _ret(close, 126)
            m["ret_12m"] = _ret(close, 252)

            if len(close) >= 50:
                ma50 = close.rolling(50).mean().iloc[-1]
                if ma50 and not np.isnan(ma50):
                    m["ma50_gap"] = float(close.iloc[-1] / ma50 - 1.0)
            if len(close) >= 200:
                ma200 = close.rolling(200).mean().iloc[-1]
                if ma200 and not np.isnan(ma200):
                    m["ma200_gap"] = float(close.iloc[-1] / ma200 - 1.0)

            # --- position in 52-week range (0 = at low, 1 = at high) ---------
            window = close.iloc[-252:] if len(close) >= 252 else close
            lo, hi = float(window.min()), float(window.max())
            if hi > lo:
                m["pct_52w_range"] = float((close.iloc[-1] - lo) / (hi - lo))

            # --- volatility (annualised std of daily returns, ~30d) ----------
            rets = close.pct_change().dropna()
            if len(rets) >= 20:
                m["volatility_30"] = float(rets.iloc[-30:].std() * np.sqrt(252))

        # --- crowd / liquidity ----------------------------------------------
        if len(vol) >= 30:
            avg_vol = vol.iloc[-30:].mean()
            if avg_vol and avg_vol > 0:
                m["vol_ratio"] = float(vol.iloc[-1] / avg_vol)

    # --- fundamentals (pass through what the provider gave us) --------------
    for k in ("pe", "forward_pe", "pb", "div_yield", "profit_margin",
              "revenue_growth", "earnings_growth", "market_cap",
              "debt_to_equity", "beta"):
        v = info.get(k)
        if v is not None:
            try:
                m[k] = float(v)
            except (TypeError, ValueError):
                pass

    return m


# Which raw metrics feed which factor group, and whether higher-raw = better.
# (+1 = higher is more attractive, -1 = lower is more attractive)
FACTOR_MAP = {
    "value": {
        "pct_52w_range": -1,   # nearer the low = cheaper
        "pe":            -1,
        "forward_pe":    -1,
        "pb":            -1,
        "div_yield":     +1,
    },
    "quality": {
        "profit_margin":   +1,
        "revenue_growth":  +1,
        "earnings_growth": +1,
        "debt_to_equity":  -1,   # less debt = stronger (also matters for halal)
    },
    "momentum": {
        "ret_3m":    +1,
        "ret_6m":    +1,
        "ret_12m":   +1,
        "ma50_gap":  +1,
        "ma200_gap": +1,
    },
    "crowd": {
        "vol_ratio":      +1,   # unusual activity = attention
        "volatility_30":  -1,   # calmer = treated as more attractive here
    },
}
