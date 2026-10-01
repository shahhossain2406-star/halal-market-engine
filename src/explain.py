"""
explain.py -- plain-English commentary for the daily report.

Everything here is templated from stored numbers (no model calls, nothing
invented), so the same data always produces the same words. It DESCRIBES what
the data shows; it never says "buy" or predicts a price.
"""

from __future__ import annotations
from html import escape

_GROUP_MEANING = {
    "value":    "how cheap the price looks compared with the others (near the low end of its "
                "yearly range, lower P/E ratio, higher dividend)",
    "quality":  "how healthy the business looks (profit margins, growth, low debt)",
    "momentum": "whether the share price has been trending upward lately",
    "crowd":    "how busy the trading is, and how calm the price swings are "
                "(busier and calmer both score higher here)",
}
_GROUP_LABEL = {"value": "Value", "quality": "Quality",
                "momentum": "Momentum", "crowd": "Crowd activity"}


def _band(score):
    if score is None:
        return None
    if score >= 75:
        return "among the best on the list"
    if score >= 55:
        return "better than most"
    if score >= 45:
        return "about average"
    if score >= 25:
        return "below average"
    return "among the weakest on the list"


def _pct(x, digits=0):
    return f"{x * 100:+.{digits}f}%"


def _halal_words(scr, halal_status):
    if scr:
        st = scr["status"]
        if st == "pass":
            return ("<b>Passed</b> the automated halal screening "
                    "(debt, cash and interest-income limits, and no haram main business).")
        if st == "review":
            return ("<b>Mixed result.</b> It passes the main standard (AAOIFI) but not every "
                    "screening standard, so check the details before relying on it.")
        if st == "fund":
            return "A fund whose issuer&rsquo;s Shariah board does the screening."
        if st == "fail":
            return "<b>Failed</b> the automated halal screening."
    if halal_status == "compliant":
        return "A fund whose issuer&rsquo;s Shariah board does the screening."
    return "Not screened yet &mdash; halal status unknown."


def _what_it_is(name, ticker, group, info):
    sector = (info or {}).get("sector") or ""
    industry = (info or {}).get("industry") or ""
    if group == "hejaz_etf":
        return "a halal-screened fund listed on the Australian exchange"
    where = "an Australian-listed" if ticker.endswith(".AX") else "a US-listed"
    if industry and sector and industry != sector:
        return f"{where} company in {sector.lower()} ({industry.lower()})"
    if sector or industry:
        return f"{where} company in {(sector or industry).lower()}"
    return f"{where} company"


def _strengths_and_watch(fs, m, is_fund):
    """Plain sentences per factor + watch-outs, drawn only from stored numbers."""
    lines = []

    v = fs.get("value")
    if v is not None:
        extra = []
        if m.get("pe"):
            extra.append(f"P/E {m['pe']:.0f}")
        if m.get("div_yield"):
            extra.append(f"dividend yield {m['div_yield']:.1f}%")
        tail = f" ({', '.join(extra)})" if extra else ""
        lines.append(("value", v, f"price looks {_cheap_word(v)} compared with the others{tail}"))

    q = fs.get("quality")
    if q is not None and not is_fund:
        extra = []
        if m.get("profit_margin") is not None:
            extra.append(f"keeps {m['profit_margin'] * 100:.0f}% of sales as profit")
        if m.get("debt_to_equity") is not None:
            extra.append("low debt" if m["debt_to_equity"] < 50 else
                         "moderate debt" if m["debt_to_equity"] < 100 else "high debt")
        tail = f" ({'; '.join(extra)})" if extra else ""
        lines.append(("quality", q, f"business health is {_band(q)}{tail}"))

    mo = fs.get("momentum")
    if mo is not None:
        extra = []
        if m.get("ret_3m") is not None:
            extra.append(f"{_pct(m['ret_3m'])} over 3 months")
        if m.get("ret_12m") is not None:
            extra.append(f"{_pct(m['ret_12m'])} over 12 months")
        tail = f" ({', '.join(extra)})" if extra else ""
        lines.append(("momentum", mo, f"price trend is {_band(mo)}{tail}"))

    c = fs.get("crowd")
    if c is not None:
        lines.append(("crowd", c, f"trading activity and calmness are {_band(c)}"))

    watch = []
    if m.get("pct_52w_range") is not None and m["pct_52w_range"] >= 0.90:
        watch.append("the price is near its 12-month high, meaning it has already risen "
                     "a lot over the past year")
    if m.get("pct_52w_range") is not None and m["pct_52w_range"] <= 0.10:
        watch.append("the price is near its 12-month low")
    if m.get("volatility_30") is not None and m["volatility_30"] >= 0.40:
        watch.append(f"the price swings a lot (about {m['volatility_30'] * 100:.0f}% a year "
                     "of movement recently)")
    if m.get("vol_ratio") is not None and m["vol_ratio"] >= 2.0:
        watch.append(f"trading volume is unusually high today ({m['vol_ratio']:.1f}&times; "
                     "its recent normal)")
    if m.get("pe") and m["pe"] >= 40:
        watch.append(f"the share price is high relative to profits (P/E {m['pe']:.0f})")
    return lines, watch


def _cheap_word(v):
    if v >= 75:
        return "cheap"
    if v >= 55:
        return "fairly cheap"
    if v >= 45:
        return "fairly priced"
    if v >= 25:
        return "on the expensive side"
    return "expensive"


def how_to_read(n_names, view_label):
    return f"""
<details class="card" open><summary><b>How to read this report (start here)</b></summary>
<p>This is a <b>daily scoreboard</b> for {n_names} companies and funds ({view_label}). Every
day the engine records their prices and numbers, gives each one a score from 0 to 100 on four
things, and ranks them from best to worst overall.</p>
<ul>
<li><b>Value</b> &ndash; {_GROUP_MEANING['value']}.</li>
<li><b>Quality</b> &ndash; {_GROUP_MEANING['quality']}.</li>
<li><b>Momentum</b> &ndash; {_GROUP_MEANING['momentum']}.</li>
<li><b>Crowd activity</b> &ndash; {_GROUP_MEANING['crowd']}.</li>
</ul>
<p><b>Scores are relative, not absolute.</b> A score of 80 means &ldquo;better than about 80% of
the other names on this list today&rdquo;, not &ldquo;80% good&rdquo;. If everything on the list is
expensive, the least expensive still scores high. The <b>Total</b> is a weighted mix of the four
scores, and the <b>#</b> is its rank.</p>
<p><b>Halal badge.</b> Green means it passed an automated halal screen, orange means a mixed result
that needs a closer look, and &ldquo;compliant ETF&rdquo; means the fund&rsquo;s own Shariah board
screens it. The screening is automated and is <b>not a religious ruling</b>.</p>
<p><b>What this report is not.</b> It describes what the market did and which measurable
factors line up with it. It does <b>not</b> predict prices and is <b>not financial advice</b>. A
high rank is a reason to look closer, not a reason to buy.</p>
</details>"""


def plain_english(store, date, rows, screens, prev_date, n_detail=10, n_brief=10,
                  compare="the previous capture day"):
    """HTML section: top `n_detail` companies in sentences, next `n_brief` in one line."""
    if not rows:
        return ""
    prev_rank = {r["ticker"]: r["rank"] for r in store.ranking_on(prev_date)} if prev_date else {}
    out = [f"<h2>In plain English &mdash; the top {min(n_detail, len(rows))}</h2>"
           '<div class="small" style="margin-bottom:8px">Each paragraph is written from '
           "the latest numbers. &ldquo;Rank&rdquo; change compares with " + compare +
           ".</div>"]

    for r in rows[:n_detail]:
        t = r["ticker"]
        fs = store.factor_scores_on(date, t)
        m = store.metrics_on(date, t)
        info = store.screening_info(t)
        scr = screens.get(t)
        is_fund = r.get("group") == "hejaz_etf"
        name = escape(r["name"] or t)
        tot = r["total_score"]

        pr = prev_rank.get(t)
        if pr is None:
            move = "new to the ranking"
        elif pr > r["rank"]:
            move = f"up {pr - r['rank']} place{'s' if pr - r['rank'] > 1 else ''} (was #{pr})"
        elif pr < r["rank"]:
            move = f"down {r['rank'] - pr} place{'s' if r['rank'] - pr > 1 else ''} (was #{pr})"
        else:
            move = f"unchanged since {compare}"

        lines, watch = _strengths_and_watch(fs, m, is_fund)
        best = max(lines, key=lambda x: x[1], default=None)
        worst = min(lines, key=lambda x: x[1], default=None)
        li = "".join(f"<li><b>{_GROUP_LABEL[g]} {s:.0f}</b> &ndash; {txt}.</li>"
                     for g, s, txt in lines)
        summary = ""
        if best and worst and best[0] != worst[0]:
            summary = (f"Its strongest area is <b>{_GROUP_LABEL[best[0]].lower()}</b> "
                       f"and its weakest is <b>{_GROUP_LABEL[worst[0]].lower()}</b>. ")
        wl = ("<div class='small' style='margin-top:6px'><b>Worth knowing:</b> "
              + "; ".join(watch) + ".</div>") if watch else ""
        fund_note = ("<div class='small'>As a fund it has no company profit or debt figures, "
                     "so its Quality score is missing and its total is averaged over fewer "
                     "factors.</div>") if is_fund else ""

        out.append(
            f'<div class="card"><b>#{r["rank"]} {name} ({escape(t)})</b> &mdash; '
            f'{_what_it_is(r["name"], t, r.get("group"), info)}.'
            f'<div class="small" style="margin:2px 0 6px">Overall score {tot:.0f}/100 &middot; '
            f'{move}</div>'
            f'<div>{summary}Halal check: {_halal_words(scr, r.get("halal_status"))}</div>'
            f'<ul style="margin:6px 0 0 18px;padding:0">{li}</ul>{wl}{fund_note}</div>')

    rest = rows[n_detail:n_detail + n_brief]
    if rest:
        out.append(f"<h2>Next {len(rest)} in one line each</h2><div class='card'><ul "
                   "style='margin:0 0 0 18px;padding:0'>")
        for r in rest:
            t = r["ticker"]
            fs = {g: s for g, s in store.factor_scores_on(date, t).items() if s is not None}
            top = max(fs, key=fs.get) if fs else None
            low = min(fs, key=fs.get) if fs else None
            scr = screens.get(t)
            hal = {"pass": "halal screen passed", "review": "halal screen: mixed result",
                   "fund": "halal fund", "fail": "failed halal screen"}.get(
                       scr["status"] if scr else "", "not screened")
            sw = (f" Best at {_GROUP_LABEL[top].lower()} ({fs[top]:.0f}), weakest at "
                  f"{_GROUP_LABEL[low].lower()} ({fs[low]:.0f}).") if top and top != low else ""
            out.append(f"<li><b>#{r['rank']} {escape(r['name'] or t)} ({escape(t)})</b> "
                       f"&ndash; score {r['total_score']:.0f}.{sw} {hal}.</li>")
        out.append("</ul></div>")
    return "".join(out)


def period_summary(store, cur, prev, rows, period):
    """What moved most over the period, in plain sentences with the main driver."""
    if not prev or prev == cur:
        return ""
    prev_rank = {r["ticker"]: r["rank"] for r in store.ranking_on(prev)}
    moves = []
    for r in rows:
        t = r["ticker"]
        if t in prev_rank:
            moves.append((prev_rank[t] - r["rank"], r))
    if not moves:
        return ""

    def driver(t):
        a, b = store.factor_scores_on(prev, t), store.factor_scores_on(cur, t)
        d = [(g, a[g], b[g]) for g in _GROUP_LABEL if a.get(g) is not None and b.get(g) is not None]
        if not d:
            return ""
        g, x, y = max(d, key=lambda z: abs(z[2] - z[1]))
        if abs(y - x) < 3:
            return " (no single factor changed much)"
        return (f" mainly because its <b>{_GROUP_LABEL[g].lower()}</b> score "
                f"{'rose' if y > x else 'fell'} from {x:.0f} to {y:.0f}")

    ups = sorted([m for m in moves if m[0] > 0], key=lambda m: -m[0])[:3]
    downs = sorted([m for m in moves if m[0] < 0], key=lambda m: m[0])[:3]
    out = [f"<h2>What changed this {period}</h2><div class='card'>"
           f"<div class='small' style='margin-bottom:6px'>Compared with {prev}. A rank "
           "change only means a name moved relative to the others, not that its price "
           "rose or fell by that much.</div>"]
    if ups:
        out.append("<div><b>Biggest risers</b></div><ul style='margin:4px 0 8px 18px;padding:0'>")
        for d, r in ups:
            out.append(f"<li><b>{escape(r['name'] or r['ticker'])}</b> climbed {d} place"
                       f"{'s' if d > 1 else ''} to #{r['rank']}{driver(r['ticker'])}.</li>")
        out.append("</ul>")
    if downs:
        out.append("<div><b>Biggest fallers</b></div><ul style='margin:4px 0 0 18px;padding:0'>")
        for d, r in downs:
            out.append(f"<li><b>{escape(r['name'] or r['ticker'])}</b> slipped {-d} place"
                       f"{'s' if -d > 1 else ''} to #{r['rank']}{driver(r['ticker'])}.</li>")
        out.append("</ul>")
    if not ups and not downs:
        out.append("<div>The order of names is unchanged over this period.</div>")
    out.append("</div>")
    return "".join(out)
