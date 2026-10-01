"""
storage.py -- the data-access layer.

This is the ONLY module that knows how data is physically stored. The rest of
the app calls these methods. Today it uses SQLite (a single local file). To
move to Supabase/Postgres later, you implement the same methods against
Postgres and switch `backend` in config/settings.yaml -- nothing else changes.

Design notes
------------
* Metrics are stored in LONG format: one row per (date, ticker, metric).
  This is what lets you keep "as many points as possible" and later explain
  exactly which points moved a company's rank (attribution).
* Everything is append-only and keyed by date, so history is never lost.
"""

from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    ticker       TEXT PRIMARY KEY,
    name         TEXT,
    grp          TEXT,
    halal_status TEXT,
    note         TEXT,
    added_date   TEXT
);

-- raw measured values (close, pe, div_yield, ret_1m, ma50, ...): one row each
CREATE TABLE IF NOT EXISTS metrics (
    date   TEXT NOT NULL,
    ticker TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL,
    PRIMARY KEY (date, ticker, metric)
);

-- normalised 0-100 scores per factor group
CREATE TABLE IF NOT EXISTS factor_scores (
    date   TEXT NOT NULL,
    ticker TEXT NOT NULL,
    factor TEXT NOT NULL,
    score  REAL,
    PRIMARY KEY (date, ticker, factor)
);

-- daily ranking
CREATE TABLE IF NOT EXISTS ranks (
    date        TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    total_score REAL,
    rank        INTEGER,
    PRIMARY KEY (date, ticker)
);

-- pointer to archived raw reference files (your evidence trail)
CREATE TABLE IF NOT EXISTS raw_archive (
    date       TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    source     TEXT,
    path       TEXT,
    fetched_at TEXT,
    PRIMARY KEY (date, ticker, source)
);

-- halal screening history: what a named source said about a ticker, and why
CREATE TABLE IF NOT EXISTS screening (
    date    TEXT NOT NULL,
    ticker  TEXT NOT NULL,
    source  TEXT NOT NULL,
    status  TEXT NOT NULL,      -- pass | fail | review | fund | error
    reason  TEXT,
    detail  TEXT,               -- JSON: ratios / raw API response
    PRIMARY KEY (date, ticker)
);

-- how each date was captured: 'live' (real, that day) or 'backfill'
-- (price metrics recomputed from history; fundamentals = values as of backfill)
CREATE TABLE IF NOT EXISTS capture_source (
    date   TEXT PRIMARY KEY,
    source TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metrics_ticker ON metrics(ticker, metric);
CREATE INDEX IF NOT EXISTS idx_scores_ticker  ON factor_scores(ticker, factor);
CREATE INDEX IF NOT EXISTS idx_ranks_date      ON ranks(date);
"""


class Storage:
    def __init__(self, cfg: dict, project_root: str):
        backend = cfg.get("storage", {}).get("backend", "sqlite")
        if backend != "sqlite":
            raise NotImplementedError(
                f"backend '{backend}' not implemented in Phase 0. "
                "Keep 'sqlite' for now; a Postgres/Supabase adapter goes here later."
            )
        rel = cfg.get("storage", {}).get("sqlite_path", "data/engine.db")
        self.path = os.path.join(project_root, rel)
        # optional report view: only these tickers, ranks renumbered within them
        self._allowed = None
        self.view_banner = ""
        self.view_suffix = ""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.path)
        # Speed: avoid an fsync on every tiny write. For a personal, rebuildable
        # local tool this is a safe, large speedup (worst case: a hard crash
        # mid-run loses that run's writes, which you can simply re-run).
        try:
            con.execute("PRAGMA synchronous=OFF")
            con.execute("PRAGMA journal_mode=MEMORY")
            con.execute("PRAGMA temp_store=MEMORY")
        except Exception:
            pass
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_schema(self):
        with self._conn() as con:
            con.executescript(SCHEMA)

    # ---- instruments --------------------------------------------------------
    def upsert_instruments(self, rows: Iterable[dict], added_date: str):
        with self._conn() as con:
            for r in rows:
                con.execute(
                    """INSERT INTO instruments(ticker,name,grp,halal_status,note,added_date)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(ticker) DO UPDATE SET
                         name=excluded.name, grp=excluded.grp,
                         halal_status=excluded.halal_status, note=excluded.note""",
                    (r["ticker"], r.get("name"), r.get("group"),
                     r.get("halal_status"), r.get("note"), added_date),
                )

    def get_instruments(self) -> list[dict]:
        with self._conn() as con:
            cur = con.execute(
                "SELECT ticker,name,grp,halal_status,note FROM instruments")
            return [dict(ticker=t, name=n, group=g, halal_status=h, note=no)
                    for (t, n, g, h, no) in cur.fetchall()]

    # ---- filtered view (halal-only) ------------------------------------------
    def set_view(self, allowed=None, banner="", suffix=""):
        self._allowed = set(allowed) if allowed is not None else None
        self.view_banner, self.view_suffix = banner, suffix

    def _view_ranks(self):
        """{date: {ticker: rank}} renumbered within the allowed set (one query)."""
        qs = ",".join("?" * len(self._allowed))
        with self._conn() as con:
            rows = con.execute(
                f"SELECT date,ticker,total_score FROM ranks WHERE ticker IN ({qs}) "
                "ORDER BY date, total_score DESC", tuple(self._allowed)).fetchall()
        out = {}
        for d, t, _ in rows:
            out.setdefault(d, {})[t] = len(out.get(d, {})) + 1
        return out

    def metrics_on(self, date: str, ticker: str) -> dict:
        with self._conn() as con:
            return dict(con.execute(
                "SELECT metric,value FROM metrics WHERE date=? AND ticker=?",
                (date, ticker)).fetchall())

    def screening_info(self, ticker: str) -> dict:
        """sector/industry from the newest screening detail that has them."""
        import json
        with self._conn() as con:
            rows = con.execute(
                "SELECT detail FROM screening WHERE ticker=? ORDER BY date DESC",
                (ticker,)).fetchall()
        for (d,) in rows:
            try:
                j = json.loads(d or "{}")
            except ValueError:
                continue
            j = j.get("raw", j)
            if j.get("sector") or j.get("industry"):
                return {"sector": j.get("sector"), "industry": j.get("industry")}
        return {}

    # ---- halal screening ----------------------------------------------------
    def save_screening(self, date: str, ticker: str, res: dict):
        import json
        with self._conn() as con:
            con.execute(
                "INSERT INTO screening(date,ticker,source,status,reason,detail) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(date,ticker) DO UPDATE SET "
                "source=excluded.source,status=excluded.status,"
                "reason=excluded.reason,detail=excluded.detail",
                (date, ticker, res["source"], res["status"], res.get("reason"),
                 json.dumps(res.get("detail") or {}, default=str)))

    def latest_screenings(self) -> dict:
        """{ticker: {date,source,status,reason}} - newest non-error result wins."""
        with self._conn() as con:
            rows = con.execute(
                "SELECT ticker,date,source,status,reason FROM screening "
                "ORDER BY date").fetchall()
        out = {}
        for t, d, so, st, rs in rows:
            if st == "error" and t in out:
                continue
            out[t] = dict(date=d, source=so, status=st, reason=rs)
        return out

    # ---- capture source -----------------------------------------------------
    def set_capture_source(self, date: str, source: str):
        with self._conn() as con:
            con.execute(
                "INSERT INTO capture_source(date,source) VALUES(?,?) "
                "ON CONFLICT(date) DO UPDATE SET source=excluded.source",
                (date, source))

    def capture_sources(self) -> dict:
        """{date: 'live'|'backfill'|'demo'}. Dates with no row count as 'live'
        only if captured before this table existed - treat missing as 'unknown'."""
        with self._conn() as con:
            return dict(con.execute("SELECT date, source FROM capture_source"))

    def get_capture_source(self, date: str):
        with self._conn() as con:
            r = con.execute("SELECT source FROM capture_source WHERE date=?",
                            (date,)).fetchone()
            return r[0] if r else None

    # ---- metrics ------------------------------------------------------------
    def save_metrics(self, date: str, ticker: str, metrics: dict):
        with self._conn() as con:
            for k, v in metrics.items():
                if v is None:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                con.execute(
                    """INSERT INTO metrics(date,ticker,metric,value) VALUES(?,?,?,?)
                       ON CONFLICT(date,ticker,metric) DO UPDATE SET value=excluded.value""",
                    (date, ticker, k, fv),
                )

    # ---- factor scores ------------------------------------------------------
    def save_factor_scores(self, date: str, ticker: str, scores: dict):
        with self._conn() as con:
            for f, s in scores.items():
                if s is None:
                    continue
                con.execute(
                    """INSERT INTO factor_scores(date,ticker,factor,score) VALUES(?,?,?,?)
                       ON CONFLICT(date,ticker,factor) DO UPDATE SET score=excluded.score""",
                    (date, ticker, f, float(s)),
                )

    # ---- ranks --------------------------------------------------------------
    def save_rank(self, date: str, ticker: str, total_score: float, rank: int):
        with self._conn() as con:
            con.execute(
                """INSERT INTO ranks(date,ticker,total_score,rank) VALUES(?,?,?,?)
                   ON CONFLICT(date,ticker) DO UPDATE SET
                     total_score=excluded.total_score, rank=excluded.rank""",
                (date, ticker, total_score, rank),
            )

    def save_raw_ref(self, date, ticker, source, path, fetched_at):
        with self._conn() as con:
            con.execute(
                """INSERT INTO raw_archive(date,ticker,source,path,fetched_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(date,ticker,source) DO UPDATE SET
                     path=excluded.path, fetched_at=excluded.fetched_at""",
                (date, ticker, source, path, fetched_at),
            )

    # ---- reads used by reports ---------------------------------------------
    def latest_date(self) -> str | None:
        with self._conn() as con:
            cur = con.execute("SELECT MAX(date) FROM ranks")
            row = cur.fetchone()
            return row[0] if row else None

    def all_rank_dates(self) -> list[str]:
        with self._conn() as con:
            cur = con.execute("SELECT DISTINCT date FROM ranks ORDER BY date")
            return [r[0] for r in cur.fetchall()]

    def ranking_on(self, date: str) -> list[dict]:
        if self._allowed is not None:
            vr = self._view_ranks().get(date, {})
            rows = [dict(r, rank=vr[r["ticker"]]) for r in self._ranking_on_all(date)
                    if r["ticker"] in vr]
            return sorted(rows, key=lambda r: r["rank"])
        return self._ranking_on_all(date)

    def _ranking_on_all(self, date: str) -> list[dict]:
        with self._conn() as con:
            cur = con.execute(
                """SELECT r.rank, r.ticker, i.name, i.grp, i.halal_status,
                          r.total_score
                   FROM ranks r LEFT JOIN instruments i ON i.ticker=r.ticker
                   WHERE r.date=? ORDER BY r.rank""", (date,))
            return [dict(rank=a, ticker=b, name=c, group=d, halal_status=e,
                         total_score=f) for (a, b, c, d, e, f) in cur.fetchall()]

    def factor_scores_on(self, date: str, ticker: str) -> dict:
        with self._conn() as con:
            cur = con.execute(
                "SELECT factor,score FROM factor_scores WHERE date=? AND ticker=?",
                (date, ticker))
            return {f: s for (f, s) in cur.fetchall()}

    def metric_series(self, ticker: str, metric: str) -> list[tuple]:
        with self._conn() as con:
            cur = con.execute(
                "SELECT date,value FROM metrics WHERE ticker=? AND metric=? ORDER BY date",
                (ticker, metric))
            return cur.fetchall()

    def rank_series(self, ticker: str) -> list[tuple]:
        """Full (date, rank) history for one ticker, oldest first."""
        if self._allowed is not None:
            return [(d, m[ticker]) for d, m in sorted(self._view_ranks().items())
                    if ticker in m]
        with self._conn() as con:
            cur = con.execute(
                "SELECT date,rank FROM ranks WHERE ticker=? ORDER BY date",
                (ticker,))
            return cur.fetchall()

    def universe_size(self, date: str) -> int:
        if self._allowed is not None:
            return len(self._view_ranks().get(date, {})) or 1
        with self._conn() as con:
            cur = con.execute("SELECT COUNT(*) FROM ranks WHERE date=?", (date,))
            return cur.fetchone()[0] or 1

    def rank_on_or_before(self, date: str, ticker: str):
        with self._conn() as con:
            cur = con.execute(
                """SELECT date,rank,total_score FROM ranks
                   WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1""",
                (ticker, date))
            return cur.fetchone()
