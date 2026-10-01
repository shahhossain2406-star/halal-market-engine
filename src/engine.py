"""
engine.py -- orchestration: collect -> compute -> rank -> store -> archive.

One run = one dated capture for the whole universe.
"""

from __future__ import annotations
import os
import json
import datetime as dt

import yaml

import storage as storage_mod
import factors as factors_mod
import ranking as ranking_mod
import providers as providers_mod


def load_config(project_root: str) -> dict:
    with open(os.path.join(project_root, "config", "settings.yaml")) as f:
        settings = yaml.safe_load(f)
    with open(os.path.join(project_root, "config", "universe.yaml")) as f:
        universe = yaml.safe_load(f)
    settings["_universe"] = universe["instruments"]
    return settings


def _archive_raw(project_root, date, ticker, history, info):
    day_dir = os.path.join(project_root, "data", "raw", date)
    os.makedirs(day_dir, exist_ok=True)
    base = ticker.replace("/", "_")
    csv_path = os.path.join(day_dir, f"{base}.csv")
    json_path = os.path.join(day_dir, f"{base}.info.json")
    try:
        if history is not None and not history.empty:
            history.tail(60).to_csv(csv_path)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, default=str, ensure_ascii=False)
    except Exception:
        pass
    return day_dir


def collect(project_root: str, mode: str = "live", as_of: str | None = None,
            quiet: bool = False, archive: bool | None = None) -> str:
    cfg = load_config(project_root)
    opts = cfg.get("options", {})
    # `archive` overrides the config flag when given (demo backfill turns it off
    # for speed — no point writing thousands of tiny fake-data files).
    archive_raw = opts.get("archive_raw", True) if archive is None else archive
    store = storage_mod.Storage(cfg, project_root)

    date = as_of or dt.date.today().isoformat()
    store.upsert_instruments(cfg["_universe"], added_date=date)

    provider = providers_mod.get_provider(
        mode, history_days=opts.get("history_days", 400), as_of=as_of)

    raw_by_ticker = {}
    fetched_at = dt.datetime.now().isoformat(timespec="seconds")
    for inst in cfg["_universe"]:
        t = inst["ticker"]
        try:
            history, info = provider.fetch(t)
        except Exception as e:
            if not quiet:
                print(f"  ! {t}: fetch failed ({str(e)[:70]})")
            continue
        raw = factors_mod.compute_raw(history, info, opts)
        if not raw:
            if not quiet:
                print(f"  ! {t}: no data")
            continue
        raw_by_ticker[t] = raw
        store.save_metrics(date, t, raw)
        if archive_raw:
            path = _archive_raw(project_root, date, t, history, info)
            store.save_raw_ref(date, t, provider.name, path, fetched_at)

    if not raw_by_ticker:
        raise RuntimeError("No data collected for any instrument. "
                           "If live: check internet / yfinance. If demo: check config.")

    fs_by_ticker, totals, ranked = ranking_mod.rank_universe(
        raw_by_ticker, cfg.get("weights", {}))

    for rank, t in enumerate(ranked, start=1):
        store.save_factor_scores(date, t, fs_by_ticker[t])
        store.save_rank(date, t, totals[t] if totals[t] is not None else 0.0, rank)

    store.set_capture_source(date, "live" if mode == "live" else mode)

    if not quiet:
        print(f"  captured {len(raw_by_ticker)} instruments for {date} "
              f"[{provider.name}]")
    return date


def backfill(project_root: str, days: int = 365, max_gap_days: int = 5) -> int:
    """Rebuild past capture-days from real price history (one fetch per ticker).

    Price metrics are recomputed point-in-time (history truncated at each date).
    Yahoo only exposes CURRENT fundamentals, so those are carried across every
    backfilled day (static, flagged source='backfill'). Dates already captured
    are never overwritten.
    """
    import pandas as pd
    cfg = load_config(project_root)
    opts = cfg.get("options", {})
    store = storage_mod.Storage(cfg, project_root)
    store.upsert_instruments(cfg["_universe"], added_date=dt.date.today().isoformat())

    # need ~1y of lookback before the first backfilled day for 12m/200d metrics
    provider = providers_mod.YFinanceProvider(history_days=days + 420)
    data = {}
    for inst in cfg["_universe"]:
        t = inst["ticker"]
        try:
            hist, info = provider.fetch(t)
        except Exception as e:
            print(f"  ! {t}: fetch failed ({str(e)[:70]})")
            continue
        if hist is None or hist.empty:
            print(f"  ! {t}: no price history")
            continue
        hist = hist.dropna(subset=["Close"])
        hist.index = pd.DatetimeIndex(hist.index).tz_localize(None).normalize()
        data[t] = (hist, info)
    if not data:
        raise RuntimeError("Backfill fetched nothing - check internet / certs.")

    today = dt.date.today()
    start = pd.Timestamp(today - dt.timedelta(days=days))
    all_days = sorted({d for h, _ in data.values() for d in h.index
                       if start <= d < pd.Timestamp(today)})
    existing = {r for r in _existing_dates(store)}
    todo = [d for d in all_days if d.date().isoformat() not in existing]
    print(f"  backfill: {len(todo)} days to build ({len(existing)} already stored)")

    for i, d in enumerate(todo, 1):
        raw_by_ticker = {}
        for t, (hist, info) in data.items():
            h = hist.loc[:d]
            if h.empty or (d - h.index[-1]).days > max_gap_days:
                continue
            raw = factors_mod.compute_raw(h, info, opts)
            if raw:
                raw_by_ticker[t] = raw
        if not raw_by_ticker:
            continue
        ds = d.date().isoformat()
        for t, raw in raw_by_ticker.items():
            store.save_metrics(ds, t, raw)
        fs, totals, ranked = ranking_mod.rank_universe(
            raw_by_ticker, cfg.get("weights", {}))
        for rank, t in enumerate(ranked, start=1):
            store.save_factor_scores(ds, t, fs[t])
            store.save_rank(ds, t, totals[t] if totals[t] is not None else 0.0, rank)
        store.set_capture_source(ds, "backfill")
        if i % 25 == 0 or i == len(todo):
            print(f"    [{i}/{len(todo)}] {ds}", flush=True)
    return len(todo)


def _existing_dates(store):
    import sqlite3
    con = sqlite3.connect(store.path)
    try:
        return [r[0] for r in con.execute("SELECT DISTINCT date FROM ranks")]
    finally:
        con.close()
