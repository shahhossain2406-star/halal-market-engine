"""
paper.py -- a shared PAPER (pretend-money) portfolio for the group.

No real money moves. The group agrees a trade on a call, one person records it
here with the reason, and the report shows holdings, the decision log, the value
over time and each member's equal share of the gain or loss.

Rules enforced (matching a halal, long-term, no-leverage approach):
  * buys only for names whose latest screening is 'pass' or 'fund'
    (override with force=True, which is recorded in the note)
  * no shorting, no margin: you cannot sell more than you hold or spend more
    cash than you have
  * every trade carries a written reason

All amounts are in one virtual currency. Prices are the stock's own currency
(USD for US names, AUD for .AX names), so treat mixed US/ASX portfolios as
approximate -- there is no FX conversion.
"""

from __future__ import annotations
import os

import reports as reports_mod

_OK_STATUS = ("pass", "fund")


def init(store, cash: float, members: int, start_date: str):
    if store.paper_get("start_cash") is not None:
        raise ValueError("paper portfolio already exists (start_cash is set). "
                         "Delete the paper_* tables' rows to start over.")
    if cash <= 0 or members < 1:
        raise ValueError("cash must be > 0 and members >= 1")
    store.paper_set("start_cash", cash)
    store.paper_set("members", members)
    store.paper_set("start_date", start_date)


def _require_init(store):
    if store.paper_get("start_cash") is None:
        raise ValueError("no paper portfolio yet. Run: python main.py paper init")


def state(store, as_of: str | None = None):
    """(cash, positions, realized_pnl) after replaying trades up to `as_of`.
    positions = {ticker: {"qty", "avg_cost"}} using average cost."""
    _require_init(store)
    cash = float(store.paper_get("start_cash"))
    pos, realized = {}, 0.0
    for t in store.paper_trades():
        if as_of and t["date"] > as_of:
            break
        gross = t["qty"] * t["price"]
        p = pos.setdefault(t["ticker"], {"qty": 0.0, "avg_cost": 0.0})
        if t["side"] == "buy":
            cash -= gross + t["fee"]
            new_qty = p["qty"] + t["qty"]
            p["avg_cost"] = (p["qty"] * p["avg_cost"] + gross + t["fee"]) / new_qty
            p["qty"] = new_qty
        else:
            cash += gross - t["fee"]
            realized += (t["price"] - p["avg_cost"]) * t["qty"] - t["fee"]
            p["qty"] -= t["qty"]
    return cash, {k: v for k, v in pos.items() if v["qty"] > 1e-9}, realized


def _price(store, ticker, date):
    r = store.close_on_or_before(ticker, date)
    return r[1] if r else None


def trade(store, side, ticker, qty, date, price=None, fee=0.0, note="", force=False):
    _require_init(store)
    ticker = ticker.upper()
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    if qty <= 0:
        raise ValueError("quantity must be positive")
    if not note.strip():
        raise ValueError("a reason is required (--note \"why the group decided this\")")
    if price is None:
        price = _price(store, ticker, date)
        if price is None:
            raise ValueError(f"no stored price for {ticker} on/before {date}; "
                             "pass --price, or run 'collect' first")
    cash, pos, _ = state(store, date)
    if side == "buy":
        scr = store.latest_screenings().get(ticker)
        status = scr["status"] if scr else "unscreened"
        if status not in _OK_STATUS:
            if not force:
                raise ValueError(f"{ticker} screening status is '{status}', not pass/fund. "
                                 "Run 'screen', or use --force (it is recorded).")
            note = f"[FORCED despite screening={status}] {note}"
        cost = qty * price + fee
        if cost > cash + 1e-9:
            raise ValueError(f"not enough cash: need {cost:,.2f}, have {cash:,.2f} "
                             "(no margin allowed)")
    else:
        held = pos.get(ticker, {}).get("qty", 0.0)
        if qty > held + 1e-9:
            raise ValueError(f"cannot sell {qty:g} {ticker}: hold {held:g} (no shorting)")
    store.paper_add_trade(date, ticker, side, qty, price, fee, note)
    return price


def valuation(store, date):
    """Everything the report/status needs as of `date`."""
    cash, pos, realized = state(store, date)
    start = float(store.paper_get("start_cash"))
    n = int(store.paper_get("members"))
    rows, mkt = [], 0.0
    for t, p in sorted(pos.items()):
        px = _price(store, t, date)
        px_used = px if px is not None else p["avg_cost"]
        val = p["qty"] * px_used
        mkt += val
        rows.append(dict(ticker=t, qty=p["qty"], avg_cost=p["avg_cost"], price=px_used,
                         value=val, pnl=val - p["qty"] * p["avg_cost"],
                         stale=px is None))
    nav = cash + mkt
    return dict(date=date, cash=cash, positions=rows, market_value=mkt, nav=nav,
                start=start, members=n, total_pnl=nav - start, realized=realized,
                ret_pct=(nav / start - 1) * 100, share_start=start / n,
                share_value=nav / n, share_pnl=(nav - start) / n)


def history(store):
    """[(date, nav)] on every captured date from the first trade onwards."""
    trades = store.paper_trades()
    if not trades:
        return []
    first = trades[0]["date"]
    return [(d, valuation(store, d)["nav"])
            for d in store.all_rank_dates() if d >= first]


def _money(x):
    return f"{x:,.2f}"


def _sign(x, fmt="{:+,.2f}"):
    cls = "up" if x > 0 else "down" if x < 0 else ""
    return f'<span class="{cls}">{fmt.format(x)}</span>'


def _chart(hist, start, w=640, h=140):
    if len(hist) < 2:
        return '<div class="small">Value chart appears after two or more captured days.</div>'
    vals = [v for _, v in hist] + [start]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pts = " ".join(f"{10 + i * (w - 20) / (len(hist) - 1):.1f},"
                   f"{h - 10 - (v - lo) / span * (h - 20):.1f}"
                   for i, (_, v) in enumerate(hist))
    ys = h - 10 - (start - lo) / span * (h - 20)
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" '
            f'aria-label="Portfolio value over time">'
            f'<line x1="10" x2="{w - 10}" y1="{ys:.1f}" y2="{ys:.1f}" '
            f'stroke="var(--muted)" stroke-dasharray="4 4"/>'
            f'<polyline fill="none" stroke="var(--spark)" stroke-width="2" points="{pts}"/>'
            f'</svg><div class="small">{hist[0][0]} &rarr; {hist[-1][0]} &middot; '
            f'dashed line = starting amount</div>')


def paper_report(store, out_dir):
    _require_init(store)
    date = store.latest_date()
    if not date:
        raise ValueError("no market data yet. Run 'collect' first.")
    v = valuation(store, date)
    trades = store.paper_trades()
    hist = history(store)

    hold = "".join(
        f'<tr><td><b>{r["ticker"]}</b></td><td class="num">{r["qty"]:g}</td>'
        f'<td class="num">{_money(r["avg_cost"])}</td>'
        f'<td class="num">{_money(r["price"])}{" *" if r["stale"] else ""}</td>'
        f'<td class="num">{_money(r["value"])}</td>'
        f'<td class="num">{_sign(r["pnl"])}</td></tr>' for r in v["positions"]
    ) or '<tr><td colspan="6" class="small">No holdings yet &mdash; all cash.</td></tr>'

    log = "".join(
        f'<tr><td>{t["date"]}</td><td>{t["side"].upper()}</td><td><b>{t["ticker"]}</b></td>'
        f'<td class="num">{t["qty"]:g}</td><td class="num">{_money(t["price"])}</td>'
        f'<td style="white-space:normal">{t["note"] or ""}</td></tr>'
        for t in reversed(trades)
    ) or '<tr><td colspan="6" class="small">No trades recorded yet.</td></tr>'

    body = [
        f'<h1>Paper Portfolio &mdash; {date}</h1>',
        '<div class="sub">Pretend money only. Same amount per member, gains and losses '
        'shared equally. Decisions are made together; the reason is recorded for each.</div>',
        '<div class="card">'
        f'<b>Group value:</b> {_money(v["nav"])} &nbsp;({_sign(v["ret_pct"], "{:+.2f}%")} '
        f'since start {_money(v["start"])})<br>'
        f'<b>Cash:</b> {_money(v["cash"])} &nbsp; <b>Invested:</b> {_money(v["market_value"])} '
        f'&nbsp; <b>Realised P/L:</b> {_sign(v["realized"])}</div>',
        '<div class="card">'
        f'<b>Each of the {v["members"]} members</b> started with {_money(v["share_start"])} '
        f'and now holds {_money(v["share_value"])} '
        f'({_sign(v["share_pnl"])}). Everyone shares the same result.</div>',
        '<h2>Value over time</h2>', _chart(hist, v["start"]),
        '<h2>Holdings</h2>',
        '<table><tr><th>Ticker</th><th>Qty</th><th>Avg cost</th><th>Price</th>'
        '<th>Value</th><th>Unrealised P/L</th></tr>' + hold + '</table>',
        '<div class="small">* no stored price; shown at cost.</div>',
        '<h2>Decision log</h2>',
        '<table><tr><th>Date</th><th>Side</th><th>Ticker</th><th>Qty</th><th>Price</th>'
        '<th>Why the group decided</th></tr>' + log + '</table>',
        '<div class="note"><b>Paper trading &mdash; not real money, not financial advice.</b> '
        'No fees, slippage or tax are modelled, and US/ASX prices are not currency-converted. '
        'Results do not predict real performance. Confirm Shariah compliance independently.</div>',
    ]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"paper_{date}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(reports_mod._page("Paper Portfolio", "".join(body)))
    return path
