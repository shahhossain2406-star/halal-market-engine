"""
screening.py -- Phase 1: halal (Shariah) screening for every instrument.

The engine NEVER certifies anything as halal. It records, per ticker and date,
what a named screening source said and why, and keeps that history. Two sources
sit behind one interface:

  * halalterminal -- the Halal Terminal API (AAOIFI/DJIM/FTSE/MSCI/S&P). Needs a
    free API key: set env HALAL_TERMINAL_API_KEY or put `halal_terminal_api_key`
    in config/secrets.local.yaml (git-ignored). Get one at
    https://api.halalterminal.com/ (email-only signup).
  * self -- an indicative AAOIFI-style check computed from Yahoo Finance data.
    No key needed, covers ASX. It is a SCREEN, not a fatwa: it cannot see the
    revenue split (e.g. alcohol sales inside a supermarket), so treat a "pass"
    as "no red flags found in the numbers we can see".

Result status values (stored in `screening.status`):
  pass     all screens passed
  fail     a business or financial screen failed
  review   borderline / needs a human look (e.g. defence, entertainment)
  fund     fund-level screened by its issuer (Hejaz ETFs) - not re-screened here
  error    source could not produce a result (reason in detail)
"""

from __future__ import annotations
import os
import json
import datetime as dt
import urllib.request
import urllib.error

import yaml

# ---- AAOIFI-style thresholds (self screener) ---------------------------------
MAX_DEBT_RATIO = 0.30      # total debt / total assets
MAX_CASH_RATIO = 0.30      # (cash + interest-bearing securities) / total assets
MAX_IMPURE_INCOME = 0.05   # interest income / total revenue

# Yahoo `industry` / `sector` substrings -> (verdict, reason)
_BUSINESS_FAIL = [
    ("banks", "conventional banking (interest-based)"),
    ("insurance", "conventional insurance"),
    ("credit services", "interest-based lending"),
    ("mortgage finance", "interest-based lending"),
    ("capital markets", "conventional financial services"),
    ("asset management", "conventional financial services"),
    ("brewers", "alcohol"),
    ("wineries", "alcohol"),
    ("distillers", "alcohol"),
    ("tobacco", "tobacco"),
    ("gambling", "gambling"),
    ("casinos", "gambling"),
    ("adult", "adult entertainment"),
]
_BUSINESS_REVIEW = [
    ("aerospace & defense", "defence/weapons exposure - check segments"),
    ("entertainment", "media content - check for haram content"),
    ("broadcasting", "media content - check for haram content"),
    ("restaurants", "may serve alcohol/pork - check"),
    ("grocery", "may sell alcohol/pork - check"),
    ("discount stores", "may sell alcohol/pork - check"),
    ("packaged foods", "may involve pork/alcohol - check"),
    ("hotels", "may derive alcohol/gambling income - check"),
]


def _load_key(project_root: str) -> str | None:
    k = os.environ.get("HALAL_TERMINAL_API_KEY")
    if k:
        return k.strip()
    path = os.path.join(project_root, "config", "secrets.local.yaml")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return (yaml.safe_load(f) or {}).get("halal_terminal_api_key")
        except Exception:
            return None
    return None


def _result(status, source, reason, **detail):
    return {"status": status, "source": source, "reason": reason, "detail": detail}


# ---------------------------------------------------------------------------
# self screener (Yahoo Finance)
# ---------------------------------------------------------------------------
def _first(df, names):
    """Latest value of the first matching row in a yfinance statement frame."""
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                return float(s.iloc[0])
    return None


def screen_self(ticker: str) -> dict:
    import yfinance as yf
    tk = yf.Ticker(ticker)
    try:
        info = tk.get_info() or {}
    except Exception as e:
        return _result("error", "self", f"could not fetch info: {str(e)[:80]}")
    sector, industry = info.get("sector") or "", info.get("industry") or ""
    ind = f"{industry} {sector}".lower()

    d = {"sector": sector, "industry": industry}

    # --- business-activity screen --------------------------------------------
    for key, why in _BUSINESS_FAIL:
        if key in ind:
            return _result("fail", "self", f"business screen: {why} ({industry})", **d)
    review = next((why for key, why in _BUSINESS_REVIEW if key in ind), None)

    # --- financial screens ---------------------------------------------------
    try:
        bs = tk.balance_sheet
        inc = tk.income_stmt
    except Exception as e:
        return _result("error", "self", f"no financial statements: {str(e)[:80]}", **d)

    assets = _first(bs, ["Total Assets"])
    debt = _first(bs, ["Total Debt"])
    cash = _first(bs, ["Cash Cash Equivalents And Short Term Investments",
                       "Cash And Cash Equivalents"])
    revenue = _first(inc, ["Total Revenue", "Operating Revenue"])
    interest = _first(inc, ["Interest Income", "Interest Income Non Operating"])
    mcap = info.get("marketCap")

    if not assets:
        return _result("error", "self", "no balance-sheet data", **d)

    debt_r = (debt or 0) / assets
    cash_r = (cash or 0) / assets
    impure = (interest / revenue) if (interest and revenue) else None
    d.update(debt_to_assets=round(debt_r, 4), cash_to_assets=round(cash_r, 4),
             interest_income_to_revenue=None if impure is None else round(impure, 4),
             debt_to_market_cap=(round(debt / mcap, 4) if debt and mcap else None),
             total_assets=assets, total_debt=debt, cash=cash, revenue=revenue,
             interest_income=interest, basis="total assets (AAOIFI)")

    fails = []
    if debt_r >= MAX_DEBT_RATIO:
        fails.append(f"debt/assets {debt_r:.1%} >= {MAX_DEBT_RATIO:.0%}")
    if cash_r >= MAX_CASH_RATIO:
        fails.append(f"cash+securities/assets {cash_r:.1%} >= {MAX_CASH_RATIO:.0%}")
    if impure is not None and impure >= MAX_IMPURE_INCOME:
        fails.append(f"interest income/revenue {impure:.1%} >= {MAX_IMPURE_INCOME:.0%}")
    if fails:
        return _result("fail", "self", "financial screen: " + "; ".join(fails), **d)
    if review:
        return _result("review", "self", f"financials pass, but {review}", **d)
    note = "" if impure is not None else " (interest-income split not available)"
    return _result("pass", "self",
                   f"debt {debt_r:.1%}, cash {cash_r:.1%} of assets - no red flags{note}", **d)


# ---------------------------------------------------------------------------
# Halal Terminal API
# ---------------------------------------------------------------------------
class HalalTerminal:
    BASE = "https://api.halalterminal.com"

    def __init__(self, key: str):
        self.key = key

    def _call(self, method, path):
        req = urllib.request.Request(
            self.BASE + path, method=method,
            headers={"X-API-Key": self.key, "User-Agent": "halal-market-engine/1.0",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def screen(self, ticker: str) -> dict:
        sym = ticker  # API expects the exchange symbol; ASX form is tried by caller
        try:
            j = self._call("POST", f"/api/screen/{sym}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:160]
            return _result("error", "halalterminal", f"HTTP {e.code}: {body}")
        except Exception as e:
            return _result("error", "halalterminal", f"{type(e).__name__}: {str(e)[:100]}")

        return parse_halalterminal(j)


def parse_halalterminal(j: dict) -> dict:
    """Turn one /api/screen response into our result shape (pure; testable)."""
    if j.get("error"):
        return _result("error", "halalterminal",
                       j.get("error_message") or str(j.get("error"))[:120], raw=j)
    bm = j.get("by_methodology") or {}
    verdict = j.get("is_compliant")
    # per-standard pass/fail, and any standard whose two denominators disagree
    fails = [m for m, v in bm.items() if v.get("is_compliant") is False]
    split = []
    for m, v in bm.items():
        alt = v.get("alternate_basis") or {}
        if v.get("bases_disagree") or alt.get("is_compliant") is False:
            split.append(f"{m}: passes on {v.get('basis')} basis but fails on "
                         f"{alt.get('basis')} ({alt.get('reason')})")
    # headline verdict + AAOIFI (the standard Hejaz/Musaffa use) decide pass/fail;
    # other standards failing, or denominators disagreeing, = needs a human look.
    if verdict is False or "AAOIFI" in fails:
        status = "fail"
    elif verdict is True and (fails or split):
        status = "review"
    elif verdict is True:
        status = "pass"
    else:
        status = "review"
    parts = []
    if j.get("business_screen_pass") is False:
        parts.append(f"business: {j.get('business_screen_reason') or 'failed'}")
    if fails:
        parts.append("fails " + ", ".join(fails) +
                     (" (passes AAOIFI, headline compliant)" if "AAOIFI" not in fails else ""))
    explanation = j.get("compliance_explanation")
    if explanation and not fails and not split:
        parts.append(explanation.rstrip("."))
    if split:
        parts.append("; ".join(split))
    pr = j.get("purification_rate")
    if pr is not None:
        parts.append(f"purify {pr}% of dividends")
    bi = j.get("business_income") or {}
    if bi.get("status") == "needs_review":
        parts.append("business-income split needs review (advisory)")
    return _result(status, "halalterminal", " | ".join(parts) or "see detail", raw=j)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def screen_universe(project_root, store, instruments, source="auto", only=None):
    """Screen every non-fund instrument; returns list of (ticker, result)."""
    key = _load_key(project_root)
    ht = HalalTerminal(key) if key and source in ("auto", "halalterminal") else None
    if source == "halalterminal" and not ht:
        raise SystemExit("No Halal Terminal API key. Set HALAL_TERMINAL_API_KEY or add "
                         "halal_terminal_api_key to config/secrets.local.yaml "
                         "(free key: https://api.halalterminal.com/).")
    today = dt.date.today().isoformat()
    out = []
    for inst in instruments:
        t = inst["ticker"]
        if only and t not in only:
            continue
        if inst.get("group") == "hejaz_etf":
            res = _result("fund", "issuer", "fund-level screened by Hejaz (Shariah board)")
        else:
            res = None
            if ht:
                res = ht.screen(t)
                if res["status"] == "error" and source == "auto":
                    res = None            # e.g. ticker/exchange not covered -> fall back
            if res is None and source in ("auto", "self"):
                res = screen_self(t)
        store.save_screening(today, t, res)
        out.append((t, res))
        print(f"  {res['status']:<7} {t:<8} [{res['source']}] {res['reason'][:90]}")
    return out
