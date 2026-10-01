"""
reports.py -- build human-readable HTML reports from stored history.

  daily_report   : today's ranking + snapshot (capture; no decisions expected)
  weekly_report  : ~7-day review, risers/fallers, factor shifts
  monthly_report : month-end ranking + ATTRIBUTION (why the top names moved),
                   decomposing each rank move into factor-group contributions.

All reports are plain self-contained HTML (no external assets), saved to
reports/ and safe to open in any browser or archive.
"""

from __future__ import annotations
import os

import explain as explain_mod

GROUPS = ["value", "quality", "momentum", "crowd"]
GCOLOR = {"value": "#2563eb", "quality": "#059669",
          "momentum": "#d97706", "crowd": "#64748b"}

_CSS = """
:root{--bg:#ffffff;--fg:#0f172a;--muted:#64748b;--line:#e2e8f0;--card:#f8fafc;--spark:#2563eb;--sparkdot:#1d4ed8;}
@media(prefers-color-scheme:dark){:root{--bg:#0f172a;--fg:#e2e8f0;--muted:#94a3b8;--line:#1e293b;--card:#1e293b;--spark:#60a5fa;--sparkdot:#93c5fd;}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:28px;}
h1{font-size:22px;margin:0 0 2px}h2{font-size:17px;margin:26px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--muted);font-size:13px;margin-bottom:4px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;font-weight:600}
.b-compliant{background:#dcfce7;color:#166534}.b-etf{background:#dbeafe;color:#1e40af}
.b-unver{background:#fef3c7;color:#92400e}
.b-pass{background:#dcfce7;color:#166534}.b-fail{background:#fee2e2;color:#991b1b}
.b-review{background:#ffedd5;color:#9a3412}
.bar{height:8px;border-radius:4px;background:var(--line);position:relative;min-width:54px}
.bar>span{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
.up{color:#059669;font-weight:600}.down{color:#dc2626;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
.note{color:var(--muted);font-size:12.5px;margin-top:22px;border-top:1px solid var(--line);padding-top:12px}
.small{font-size:12px;color:var(--muted)}
.grid{display:flex;gap:6px;flex-wrap:wrap}
.bf{background:#fef3c7;color:#92400e;border:1px solid #fcd34d;border-radius:10px;padding:10px 14px;margin:10px 0;font-size:13px}
@media(prefers-color-scheme:dark){.bf{background:#422006;color:#fde68a;border-color:#854d0e}}
@media(max-width:700px){body{padding:14px;font-size:14px}h1{font-size:19px}
table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch;white-space:nowrap}
.card,.bf{padding:11px 12px}}
@media print{:root{--bg:#fff;--fg:#0f172a;--muted:#64748b;--line:#e2e8f0;--card:#f8fafc;--spark:#2563eb;--sparkdot:#1d4ed8}
body{padding:6px;font-size:11.5px}h2{break-after:avoid}.card,.bf,tr{break-inside:avoid}
table{font-size:11px}th,td{padding:4px 6px}}
.chip{font-size:11px;padding:2px 7px;border:1px solid var(--line);border-radius:6px}
"""


def _badge(status, scr=None):
    """Halal badge. A real screening result (Phase 1) beats the static label."""
    if scr and scr["status"] in ("pass", "fail", "review", "fund"):
        st = scr["status"]
        label = {"pass": "screen: pass", "fail": "screen: FAIL",
                 "review": "screen: review", "fund": "compliant ETF"}[st]
        cls = {"pass": "b-pass", "fail": "b-fail", "review": "b-review",
               "fund": "b-compliant"}[st]
        tip = f'{scr["source"]} {scr["date"]}: {scr["reason"] or ""}'.replace('"', "'")
        return f'<span class="badge {cls}" title="{tip}">{label}</span>'
    if status == "compliant":
        return '<span class="badge b-compliant">compliant ETF</span>'
    if status == "etf_screened":
        return '<span class="badge b-etf">etf-screened*</span>'
    return '<span class="badge b-unver">UNVERIFIED</span>'


def _bar(score):
    if score is None:
        return '<span class="small">n/a</span>'
    s = max(0, min(100, score))
    col = "#2563eb" if s >= 50 else "#94a3b8"
    return f'<div class="bar"><span style="width:{s:.0f}%;background:{col}"></span></div>'


def _gbars(fs):
    cells = []
    for g in GROUPS:
        v = fs.get(g)
        w = 0 if v is None else max(0, min(100, v))
        lab = "n/a" if v is None else f"{v:.0f}"
        cells.append(
            f'<div style="display:flex;align-items:center;gap:6px;margin:1px 0">'
            f'<span class="small" style="width:64px">{g}</span>'
            f'<div class="bar" style="width:90px"><span style="width:{w:.0f}%;background:{GCOLOR[g]}"></span></div>'
            f'<span class="small" style="width:22px">{lab}</span></div>')
    return "".join(cells)


def _poly(coords, dashed):
    if len(coords) < 2:
        return ""
    poly = " ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
    extra = ' stroke-dasharray="3 2" opacity="0.55"' if dashed else ""
    return (f'<polyline points="{poly}" fill="none" stroke="var(--spark)" '
            f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"{extra}/>')


def _rank_sparkline(dates, rankmap, universe_size, srcmap=None, w=104, h=26):
    """Inline-SVG line of a ticker's rank over time. Rank 1 sits at the top
    (y inverted); y-scale is the whole universe so rows are comparable."""
    pts = [(d, rankmap[d]) for d in dates if d in rankmap]
    if not pts:
        return '<span class="small">—</span>'
    pad = 3.0
    n = len(pts)
    lo, hi = 1, max(2, universe_size)
    def x(i):
        return pad if n == 1 else pad + i * (w - 2 * pad) / (n - 1)
    def y(rank):
        frac = (rank - lo) / (hi - lo)          # 0 = best rank -> top
        return pad + frac * (h - 2 * pad)
    coords = [(x(i), y(r)) for i, (_, r) in enumerate(pts)]

    first_r, last_r = pts[0][1], pts[-1][1]
    move = first_r - last_r                       # + = improved (rose)
    title = f"rank {first_r} → {last_r} over {n} captures"
    if n == 1:
        cx, cy = coords[0]
        return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
                f'role="img" aria-label="{title}"><title>{title}</title>'
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="var(--sparkdot)"/></svg>')
    ex, ey = coords[-1]
    # backfilled stretch dashed + faded, live stretch solid
    is_live = [(srcmap or {}).get(d, "live") != "backfill" for d, _ in pts]
    first_live = next((i for i, v in enumerate(is_live) if v), None)
    if first_live is None:
        lines = _poly(coords, dashed=True)
    elif first_live == 0:
        lines = _poly(coords, dashed=False)
    else:
        lines = (_poly(coords[:first_live + 1], dashed=True) +
                 _poly(coords[first_live:], dashed=False))
    arrow = "▲" if move > 0 else ("▼" if move < 0 else "•")
    acls = "up" if move > 0 else ("down" if move < 0 else "small")
    return (f'<span style="display:inline-flex;align-items:center;gap:5px" title="{title}">'
            f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{title}">'
            f'<title>{title}</title>'
            f'{lines}'
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="2.6" fill="var(--sparkdot)"/></svg>'
            f'<span class="{acls}" style="font-size:11px">{arrow}{abs(move) if move else ""}</span></span>')


def _coverage_banner(store, dates, prev=None, cur=None):
    """Tell the reader which days are real live captures and which are backfilled."""
    src = store.capture_sources()
    n_bf = sum(1 for d in dates if src.get(d) == "backfill")
    n_live = sum(1 for d in dates if src.get(d) in ("live", None))
    n_demo = sum(1 for d in dates if src.get(d) == "demo")
    if not n_bf and not n_demo:
        return ""
    if n_demo:
        return ('<div class="bf"><b>Demo data present.</b> ' + str(n_demo) +
                ' day(s) are FAKE offline demo data - do not use for decisions.</div>')
    first_live = next((d for d in dates if src.get(d) in ("live", None)), None)
    msg = (f'<b>Backfilled history:</b> {n_bf} of {len(dates)} capture days are '
           f'<b>backfilled</b> (dashed lines in Trend); {n_live} are real live captures'
           + (f', starting {first_live}' if first_live else '') + '. '
           'Backfilled days recompute price factors (momentum, 52-week range, volatility, '
           'volume) as they were, but <b>use today&rsquo;s fundamentals</b> (P/E, margins, '
           'debt, dividends), so Value/Quality changes before the first live day are not '
           'real history.')
    if prev and src.get(prev) == "backfill":
        msg += (f' The comparison baseline ({prev}) is a backfilled day, so '
                'Value/Quality attribution below reflects price-driven changes only.')
    return f'<div class="bf">{msg}</div>'


def _page(title, body):
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title><style>{_CSS}</style></head><body>{body}</body></html>"


_FOOT = ('<div class="note"><b>Not financial advice.</b> This is a learning/analysis '
         'tool. Rankings describe measurable factors — they are not buy signals and do '
         'not predict prices. <b>*etf-screened</b> names are typical halal-ETF holdings '
         'but must be confirmed individually (e.g. in Zoya/Musaffa); <b>UNVERIFIED</b> '
         'names have NOT been compliance-checked. Confirm Shariah status before any '
         'decision.</div>')


def _movers(store, cur_date, prev_date, top_n=None):
    """Return list of (ticker,name,cur_rank,prev_rank,delta) sorted by |delta|."""
    if not prev_date:
        return []
    cur = {r["ticker"]: r for r in store.ranking_on(cur_date)}
    prev = {r["ticker"]: r["rank"] for r in store.ranking_on(prev_date)}
    out = []
    for t, r in cur.items():
        if t in prev:
            delta = prev[t] - r["rank"]  # positive = moved UP
            out.append((t, r["name"], r["rank"], prev[t], delta))
    out.sort(key=lambda x: abs(x[4]), reverse=True)
    return out[:top_n] if top_n else out


# --------------------------------------------------------------------------- #
def daily_report(store, date, out_dir):
    screens = store.latest_screenings()
    rows = store.ranking_on(date)
    dates = store.all_rank_dates()
    prev = dates[-2] if len(dates) >= 2 else None

    body = [f"<h1>Daily Snapshot &mdash; {date}</h1>",
            f'<div class="sub">{len(rows)} instruments tracked · captured for your archive' +
            (' &middot; <b>' + store.capture_sources().get(date, 'live') + '</b> capture') +
            '</div>',
            _coverage_banner(store, dates), store.view_banner]

    view = ("halal-screened view" if store._allowed is not None
            else "full watchlist, including names that failed or are unscreened")
    body.append(explain_mod.how_to_read(len(rows), view))
    body.append(explain_mod.plain_english(store, date, rows, screens, prev))

    body.append("<h2>Ranking today</h2><table><tr><th>#</th><th>Ticker</th><th>Name</th>"
                "<th>Status</th><th>Factor scores (0&ndash;100)</th><th class='num'>Total</th></tr>")
    for r in rows:
        fs = store.factor_scores_on(date, r["ticker"])
        tot = "" if r["total_score"] is None else f'{r["total_score"]:.1f}'
        body.append(
            f'<tr><td class="num">{r["rank"]}</td><td><b>{r["ticker"]}</b></td>'
            f'<td>{r["name"] or ""}</td><td>{_badge(r["halal_status"], screens.get(r["ticker"]))}</td>'
            f'<td>{_gbars(fs)}</td><td class="num"><b>{tot}</b></td></tr>')
    body.append("</table>")

    movers = _movers(store, date, prev, top_n=6)
    if movers:
        body.append("<h2>Notable moves since last capture</h2><div class='grid'>")
        for t, name, cr, pr, d in movers:
            if d == 0:
                continue
            cls = "up" if d > 0 else "down"
            arrow = "▲" if d > 0 else "▼"
            body.append(f'<span class="chip"><b>{t}</b> <span class="{cls}">{arrow}{abs(d)}</span> '
                        f'<span class="small">#{pr}&rarr;#{cr}</span></span>')
        body.append("</div>")

    body.append(_FOOT)
    html = _page(f"Daily {date}", "".join(body))
    path = os.path.join(out_dir, f"daily{store.view_suffix}_{date}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def weekly_report(store, out_dir):
    screens = store.latest_screenings()
    dates = store.all_rank_dates()
    if not dates:
        return None
    cur = dates[-1]
    prev = next((d for d in reversed(dates) if d < _minus_days(cur, 6)), dates[0])
    rows = store.ranking_on(cur)

    body = [f"<h1>Weekly Review &mdash; {cur}</h1>",
            f'<div class="sub">compared with {prev} · {len(rows)} instruments</div>',
            _coverage_banner(store, dates, prev, cur), store.view_banner]
    body.extend(_plain_block(store, cur, prev, rows, screens, "week"))

    movers = _movers(store, cur, prev)
    ups = [m for m in movers if m[4] > 0][:8]
    downs = [m for m in movers if m[4] < 0][:8]

    body.append("<h2>Biggest risers</h2>" + _mover_table(ups) if ups else "")
    body.append("<h2>Biggest fallers</h2>" + _mover_table(downs) if downs else "")

    srcmap = store.capture_sources()
    recent = dates[-60:]
    usize = store.universe_size(cur)
    body.append("<h2>Full ranking, with score bars</h2>"
                '<div class="small" style="margin-bottom:8px">The coloured bars show the '
                'four scores (value, quality, momentum, crowd; longer = higher). The '
                '<b>Trend</b> line is each name&rsquo;s rank over the last '
                f'{len(recent)} captures (dashed = backfilled).</div>'
                "<table><tr><th>#</th><th>Ticker</th><th>Name</th><th>Status</th>"
                "<th>Factor scores (0&ndash;100)</th><th class='num'>Total</th>"
                "<th>Trend</th></tr>")
    for r in rows:
        fs = store.factor_scores_on(cur, r["ticker"])
        tot = "" if r["total_score"] is None else f'{r["total_score"]:.1f}'
        rmap = {d: rk for (d, rk) in store.rank_series(r["ticker"])}
        spark = _rank_sparkline(recent, rmap, usize, srcmap)
        body.append(f'<tr><td class="num">{r["rank"]}</td><td><b>{r["ticker"]}</b></td>'
                    f'<td>{r["name"] or ""}</td>'
                    f'<td>{_badge(r["halal_status"], screens.get(r["ticker"]))}</td>'
                    f'<td>{_gbars(fs)}</td><td class="num"><b>{tot}</b></td>'
                    f'<td>{spark}</td></tr>')
    body.append("</table>")
    body.append(_FOOT)

    html = _page(f"Weekly {cur}", "".join(body))
    path = os.path.join(out_dir, f"weekly{store.view_suffix}_{cur}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def _mover_table(movers):
    h = ["<table><tr><th>Ticker</th><th>Name</th><th class='num'>Move</th><th class='num'>Rank</th></tr>"]
    for t, name, cr, pr, d in movers:
        cls = "up" if d > 0 else "down"
        arrow = "▲" if d > 0 else "▼"
        h.append(f'<tr><td><b>{t}</b></td><td>{name or ""}</td>'
                 f'<td class="num {cls}">{arrow}{abs(d)}</td>'
                 f'<td class="num small">#{pr}&rarr;#{cr}</td></tr>')
    h.append("</table>")
    return "".join(h)


def monthly_report(store, out_dir, weights, period_label="Monthly", lookback_days=30):
    """Month-end ranking + attribution: WHY the top names moved."""
    screens = store.latest_screenings()
    dates = store.all_rank_dates()
    if not dates:
        return None
    cur = dates[-1]
    target = _minus_days(cur, lookback_days)
    prev = next((d for d in reversed(dates) if d <= target), dates[0])
    rows = store.ranking_on(cur)

    body = [f"<h1>{period_label} Analysis &mdash; {cur}</h1>",
            f'<div class="sub">full-period go-through · compared with {prev} '
            f'({len(dates)} capture days in archive)</div>',
            _coverage_banner(store, dates, prev, cur), store.view_banner]
    body.extend(_plain_block(store, cur, prev, rows, screens,
                             {"Monthly": "month", "Quarterly": "quarter", "Half Year": "half-year",
                                      "Yearly": "year"}.get(period_label, "period")))

    # full ranking with all group scores + a rank-over-time sparkline
    usize = store.universe_size(cur)
    srcmap = store.capture_sources()
    body.append("<h2>Ranking (all points)</h2>"
                '<div class="small" style="margin-bottom:8px">The <b>Trend</b> column '
                'traces each company\'s rank across all captures in the archive — the '
                'line rises when its rank improves (rank 1 = top).</div>'
                "<table><tr><th>#</th><th>Ticker</th><th>Name</th>"
                "<th>Status</th><th>Value</th><th>Quality</th><th>Momentum</th><th>Crowd</th>"
                "<th class='num'>Total</th><th>Trend</th></tr>")
    for r in rows:
        fs = store.factor_scores_on(cur, r["ticker"])
        def c(g):
            v = fs.get(g)
            return "—" if v is None else f"{v:.0f}"
        tot = "" if r["total_score"] is None else f'{r["total_score"]:.1f}'
        rmap = {d: rk for (d, rk) in store.rank_series(r["ticker"])}
        spark = _rank_sparkline(dates, rmap, usize, srcmap)
        body.append(f'<tr><td class="num">{r["rank"]}</td><td><b>{r["ticker"]}</b></td>'
                    f'<td>{r["name"] or ""}</td><td>{_badge(r["halal_status"], screens.get(r["ticker"]))}</td>'
                    f'<td class="num">{c("value")}</td><td class="num">{c("quality")}</td>'
                    f'<td class="num">{c("momentum")}</td><td class="num">{c("crowd")}</td>'
                    f'<td class="num"><b>{tot}</b></td><td>{spark}</td></tr>')
    body.append("</table>")

    # ---- ATTRIBUTION for the top 10 (and any big movers) -------------------
    body.append(f"<h2>Why the rankings changed &mdash; top 10 attribution</h2>")
    body.append('<div class="small" style="margin-bottom:8px">Each move is decomposed '
                'into how much each factor group contributed (Δgroup-score × its weight). '
                'This is evidence-based inference from tracked factors only.</div>')

    prev_rank_map = {r["ticker"]: r["rank"] for r in store.ranking_on(prev)}
    wsum = sum(float(weights.get(g, 0)) for g in GROUPS) or 1.0

    for r in rows[:10]:
        t = r["ticker"]
        cur_fs = store.factor_scores_on(cur, t)
        prev_fs = store.factor_scores_on(prev, t)
        pr = prev_rank_map.get(t)
        move = (pr - r["rank"]) if pr is not None else None

        contribs = []
        for g in GROUPS:
            a, b = prev_fs.get(g), cur_fs.get(g)
            if a is not None and b is not None:
                contribs.append((g, (b - a) * float(weights.get(g, 0)) / wsum, a, b))
        contribs.sort(key=lambda x: abs(x[1]), reverse=True)

        if move is None:
            headline = '<span class="small">new to ranking this period</span>'
        elif move > 0:
            headline = f'<span class="up">▲ up {move} places</span> <span class="small">#{pr} → #{r["rank"]}</span>'
        elif move < 0:
            headline = f'<span class="down">▼ down {abs(move)} places</span> <span class="small">#{pr} → #{r["rank"]}</span>'
        else:
            headline = f'<span class="small">unchanged at #{r["rank"]}</span>'

        parts = []
        for g, contrib, a, b in contribs:
            if abs(contrib) < 0.01:
                continue
            sign = "+" if contrib >= 0 else ""
            cls = "up" if contrib >= 0 else "down"
            parts.append(f'<span class="chip">{g} <span class="{cls}">{sign}{contrib:.1f}</span> '
                         f'<span class="small">({a:.0f}→{b:.0f})</span></span>')
        drivers = " ".join(parts) if parts else '<span class="small">no material factor change captured</span>'

        body.append(f'<div class="card"><b>#{r["rank"]} {t}</b> — {r["name"] or ""} '
                    f'&nbsp; {headline}<div class="grid" style="margin-top:8px">{drivers}</div></div>')

    body.append('<div class="card small"><b>Pattern note:</b> once several capture days '
                'exist, look here for what tended to drive rises vs falls across the top 10 '
                '(e.g. quality vs momentum). Moves not explained by the chips above came from '
                'factors outside the tracked set — a prompt to investigate manually.</div>')
    body.append(_FOOT)

    html = _page(f"{period_label} {cur}", "".join(body))
    fname = f"{period_label.lower().replace(' ', '_')}{store.view_suffix}_{cur}.html"
    path = os.path.join(out_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def _minus_days(date_str, days):
    import datetime as _dt
    d = _dt.date.fromisoformat(date_str) - _dt.timedelta(days=days)
    return d.isoformat()


def _plain_block(store, cur, prev, rows, screens, period):
    """How-to-read guide + what-changed summary + plain-English top 10/20."""
    view = ("halal-screened view" if store._allowed is not None
            else "full watchlist, including names that failed or are unscreened")
    return [explain_mod.how_to_read(len(rows), view),
            explain_mod.period_summary(store, cur, prev, rows, period),
            explain_mod.plain_english(store, cur, rows, screens, prev,
                                      compare=f"{prev} (start of the {period})")]


def halal_view(store, include_review=False):
    """Restrict the store to halal-screened names for the reports that follow.

    strict    : screen 'pass' + Hejaz ETFs ('fund')
    inclusive : also 'review' (e.g. passes AAOIFI but fails FTSE/MSCI)
    Unscreened or failed names are hidden. Ranks are renumbered within the
    shown set; factor scores stay percentiles vs the FULL watchlist.
    """
    screens = store.latest_screenings()
    ok = {"pass", "fund"} | ({"review"} if include_review else set())
    allowed = [t for t, sc in screens.items() if sc["status"] in ok]
    hidden = [i["ticker"] for i in store.get_instruments() if i["ticker"] not in allowed]
    mode = "pass + review + ETFs" if include_review else "pass + ETFs only"
    banner = (f'<div class="bf"><b>Halal-only view</b> ({mode}): showing {len(allowed)} '
              f'of {len(allowed) + len(hidden)} names; ranks are renumbered within this '
              f'set. Hidden: {", ".join(sorted(hidden)) or "none"}. Scores are still '
              'percentiles against the whole watchlist. Screening is automated and '
              'is not a fatwa &mdash; see each name&rsquo;s badge tooltip for the '
              'reason.</div>')
    store.set_view(allowed, banner, "_halal_plus" if include_review else "_halal")
    return allowed


# --------------------------------------------------------------------------- #
_KINDS = [("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"),
          ("quarterly", "Quarterly"), ("half_year", "Half-year"), ("yearly", "Yearly")]


def index_page(out_dir):
    """reports/index.html - a phone-friendly list of every report, newest first."""
    import re
    pat = re.compile(r"^(daily|weekly|monthly|quarterly|half_year|yearly)"
                     r"(_halal_plus|_halal)?_(\d{4}-\d{2}-\d{2})\.html$")
    found = {}                       # kind -> date -> {"halal": f, "full": f, "plus": f}
    for f in os.listdir(out_dir):
        m = pat.match(f)
        if m:
            kind, suffix, date = m.groups()
            slot = {"_halal": "halal", "_halal_plus": "plus", None: "full"}[suffix]
            found.setdefault(kind, {}).setdefault(date, {})[slot] = f

    def links(d):
        out = []
        for slot, label in (("halal", "Halal-only"), ("full", "Full watchlist"),
                            ("plus", "Halal + review")):
            if slot in d:
                out.append(f'<a href="{d[slot]}">{label}</a>')
        return " &middot; ".join(out)

    body = ["<h1>Halal Market reports</h1>",
            '<div class="sub">Tap a report to open it. Newest first. '
            'Not financial advice.</div>', "<h2>Latest</h2>"]
    for kind, label in _KINDS:
        if kind in found:
            date = max(found[kind])
            body.append(f'<div class="card"><b>{label}</b> '
                        f'<span class="small">&middot; {date}</span>'
                        f'<div style="margin-top:6px;font-size:16px">{links(found[kind][date])}</div></div>')
    body.append("<h2>Archive</h2>")
    for kind, label in _KINDS:
        if kind in found:
            rows = "".join(f'<div style="padding:6px 0;border-bottom:1px solid var(--line)">'
                           f'<span class="small" style="display:inline-block;min-width:92px">{d}</span>'
                           f'{links(found[kind][d])}</div>'
                           for d in sorted(found[kind], reverse=True))
            body.append(f'<details class="card"><summary><b>{label}</b> '
                        f'<span class="small">({len(found[kind])})</span></summary>{rows}</details>')
    html = _page("Halal Market reports", "".join(body))
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
