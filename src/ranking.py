"""
ranking.py -- normalise raw metrics across the universe and rank.

Method (transparent on purpose):
  1. For each raw metric used by a factor, rank every company against the others
     that day as a 0-100 percentile. "Higher is better" metrics keep their
     percentile; "lower is better" metrics (P/E, debt, being near the 52w high)
     are inverted so that 100 always = most attractive.
  2. A factor GROUP score = the average of its available component scores.
  3. The TOTAL score = the weighted average of the group scores (weights from
     settings.yaml), renormalised over whichever groups actually have data.
  4. Rank by total score, highest first.

Because each group score is stored, the later reports can compare a company's
group scores across time and explain *why* its rank moved.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from factors import FACTOR_MAP


def _percentile_scores(df: pd.DataFrame, metric: str, direction: int) -> pd.Series:
    if metric not in df.columns:
        return pd.Series(np.nan, index=df.index)
    col = df[metric].astype(float)
    if col.notna().sum() < 2:
        # not enough peers to compare -> neutral where present
        return col.notna().map(lambda x: 50.0 if x else np.nan)
    pct = col.rank(pct=True) * 100.0
    if direction < 0:
        pct = 100.0 - pct
    return pct


def rank_universe(raw_by_ticker: dict, weights: dict):
    """Returns (factor_scores_by_ticker, totals_by_ticker, ranked_tickers)."""
    tickers = list(raw_by_ticker.keys())
    df = pd.DataFrame.from_dict(raw_by_ticker, orient="index")

    # 1+2: component percentile scores -> group scores
    group_scores = {}  # group -> Series over tickers
    for group, comps in FACTOR_MAP.items():
        comp_scores = []
        for metric, direction in comps.items():
            comp_scores.append(_percentile_scores(df, metric, direction))
        if comp_scores:
            mat = pd.concat(comp_scores, axis=1)
            group_scores[group] = mat.mean(axis=1, skipna=True)
        else:
            group_scores[group] = pd.Series(np.nan, index=df.index)

    gs_df = pd.DataFrame(group_scores)  # index tickers, cols groups

    # 3: weighted total over available groups (renormalise weights)
    w = {g: float(weights.get(g, 0)) for g in FACTOR_MAP}
    totals = {}
    factor_scores_by_ticker = {}
    for t in tickers:
        row = gs_df.loc[t]
        avail = {g: row[g] for g in FACTOR_MAP if pd.notna(row[g])}
        fs = {g: (float(row[g]) if pd.notna(row[g]) else None) for g in FACTOR_MAP}
        factor_scores_by_ticker[t] = fs
        if avail:
            wsum = sum(w[g] for g in avail) or 1.0
            total = sum(avail[g] * w[g] for g in avail) / wsum
        else:
            total = float("nan")
        totals[t] = round(float(total), 3) if pd.notna(total) else None

    # 4: rank (highest total = rank 1); tickers with no score sink to bottom
    ranked = sorted(
        tickers,
        key=lambda t: (totals[t] is not None, totals[t] if totals[t] is not None else -1),
        reverse=True,
    )
    return factor_scores_by_ticker, totals, ranked
