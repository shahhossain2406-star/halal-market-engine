#!/usr/bin/env python3
"""
Halal Market-Understanding & Analysis Engine  --  Phase 0

USAGE
-----
  # Daily capture using REAL data (run this on a schedule on your PC):
  python main.py collect

  # Halal screening (Phase 1; run monthly/quarterly, results are kept as history):
  python main.py screen [--source auto|halalterminal|self] [TICKER ...]

  # One-off: rebuild a year of past days from real price history:
  python main.py backfill --days 365

  # Build reports from whatever history exists:
  python main.py report daily
  python main.py report weekly
  python main.py report monthly       # monthly analysis + attribution
  python main.py report quarterly     # 3-month
  python main.py report halfyear      # 6-month
  python main.py report yearly        # 12-month

  # Try it instantly with OFFLINE demo data (fake numbers, safe to explore):
  python main.py demo --days 40       # backfill 40 fake capture-days
  python main.py report monthly       # then view any report

Everything is stored in data/engine.db and reports land in reports/.
"""

import os
import sys
import argparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

# Antivirus HTTPS scanning (e.g. Norton) re-signs traffic with a root cert that
# Windows trusts but yfinance's curl_cffi does not. certs/ca-bundle.pem = certifi
# + the Windows trust store; regenerate it if certificate errors reappear.
_bundle = os.path.join(ROOT, "certs", "ca-bundle.pem")
if os.path.exists(_bundle):
    os.environ.setdefault("CURL_CA_BUNDLE", _bundle)
    os.environ.setdefault("SSL_CERT_FILE", _bundle)

import engine as engine_mod          # noqa: E402
import storage as storage_mod        # noqa: E402
import reports as reports_mod        # noqa: E402


def _store():
    cfg = engine_mod.load_config(ROOT)
    return storage_mod.Storage(cfg, ROOT), cfg


def _last_weekday(year, month):
    import datetime as dt
    d = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1))
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def weekly_due(today, latest_report):
    """True if the most recent Friday (on/before today) has no weekly report yet."""
    import datetime as dt
    friday = today - dt.timedelta(days=(today.weekday() - 4) % 7)
    return latest_report is None or latest_report < friday


def period_due(today, latest_report, months=range(1, 13)):
    """True once the last weekday of one of `months` has arrived and no report
    dated on/after that day exists. months=1..12 -> monthly, (3,6,9,12) -> quarterly,
    (12,) -> yearly."""
    threshold = None
    y, m = today.year, today.month
    for _ in range(25):                       # look back up to ~2 years
        if m in months:
            t = _last_weekday(y, m)
            if t <= today:
                threshold = t
                break
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return threshold is not None and (latest_report is None or latest_report < threshold)


def monthly_due(today, latest_report):
    return period_due(today, latest_report)


def _latest_report_date(out, prefix):
    import re
    import datetime as dt
    best = None
    for f in os.listdir(out):
        m = re.fullmatch(prefix + r"_(\d{4}-\d{2}-\d{2})\.html", f)
        if m:
            d = dt.date.fromisoformat(m.group(1))
            best = d if best is None or d > best else best
    return best


def _email_weekly(store, cfg, out, dry_run=False):
    """Email the weekly report. Never lets an email problem break the capture."""
    import mailer as mailer_mod
    try:
        dates = store.all_rank_dates()
        cur = dates[-1]
        import datetime as dt
        target = (dt.date.fromisoformat(cur) - dt.timedelta(days=6)).isoformat()
        prev = next((d for d in reversed(dates) if d < target), dates[0])
        print("  email:", mailer_mod.send_weekly(
            ROOT, cfg, store, out, cur, prev, reports_mod.halal_view, dry_run=dry_run))
    except Exception as e:
        print(f"  ! email failed: {type(e).__name__}: {str(e)[:120]}")
    finally:
        store.set_view(None)


def _periodic_reports(store, cfg, out):
    """Weekly (Friday), monthly (last weekday), quarterly (Mar/Jun/Sep/Dec month-end), half-year (Jun/Dec)
    and yearly (Dec month-end) reports, each full + halal-only.
    State-based, so a missed day is caught up on the next run."""
    import datetime as dt
    today = dt.date.today()
    w = cfg.get("weights", {})
    jobs = []
    if weekly_due(today, _latest_report_date(out, "weekly")):
        jobs.append(("weekly", lambda: reports_mod.weekly_report(store, out)))
    if monthly_due(today, _latest_report_date(out, "monthly")):
        jobs.append(("monthly",
                     lambda: reports_mod.monthly_report(store, out, w, "Monthly", 30)))
    if period_due(today, _latest_report_date(out, "quarterly"), (3, 6, 9, 12)):
        jobs.append(("quarterly",
                     lambda: reports_mod.monthly_report(store, out, w, "Quarterly", 91)))
    if period_due(today, _latest_report_date(out, "half_year"), (6, 12)):
        jobs.append(("half-year",
                     lambda: reports_mod.monthly_report(store, out, w, "Half Year", 182)))
    if period_due(today, _latest_report_date(out, "yearly"), (12,)):
        jobs.append(("yearly",
                     lambda: reports_mod.monthly_report(store, out, w, "Yearly", 365)))
    for name, fn in jobs:
        store.set_view(None)
        print(f"  {name} report: {fn()}")
        if reports_mod.halal_view(store):
            print(f"  {name} halal-only report: {fn()}")
        store.set_view(None)
        if name == "weekly":
            _email_weekly(store, cfg, out)


def backup_db(store, keep_daily=14, keep_months=12):
    """Compressed, consistent snapshot of the database -> data/backups/.
    Keeps the newest `keep_daily` copies plus the last copy of each of the
    most recent `keep_months` months."""
    import gzip
    import shutil
    import sqlite3
    import tempfile
    import datetime as dt
    bdir = os.path.join(os.path.dirname(store.path), "backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = dt.date.today().isoformat()
    final = os.path.join(bdir, f"engine_{stamp}.db.gz")
    tmp = os.path.join(tempfile.gettempdir(), f"engine_{stamp}.db")
    src, dst = sqlite3.connect(store.path), sqlite3.connect(tmp)
    try:
        src.backup(dst)                     # safe even while the db is in use
    finally:
        dst.close()
        src.close()
    try:
        with open(tmp, "rb") as f, gzip.open(final + ".part", "wb", 6) as g:
            shutil.copyfileobj(f, g)
        os.replace(final + ".part", final)
    finally:
        os.path.exists(tmp) and os.remove(tmp)
    files = sorted(f for f in os.listdir(bdir) if f.startswith("engine_") and f.endswith(".db.gz"))
    keep = set(files[-keep_daily:])
    by_month = {}
    for f in files:
        by_month[f[7:14]] = f               # last file of each YYYY-MM
    keep |= set(by_month[m] for m in sorted(by_month)[-keep_months:])
    for f in files:
        if f not in keep:
            os.remove(os.path.join(bdir, f))
    return final


def cmd_collect(args):
    date = engine_mod.collect(ROOT, mode="live")
    store, cfg = _store()
    out = os.path.join(ROOT, "reports")
    path = reports_mod.daily_report(store, date, out)
    print(f"  daily report: {path}")
    # halal-only daily report (screen pass + Hejaz ETFs); needs screening results
    if reports_mod.halal_view(store):
        print(f"  halal-only daily report: {reports_mod.daily_report(store, date, out)}")
    else:
        print("  halal-only report skipped: no screening results yet (run 'screen')")
    store.set_view(None)
    _periodic_reports(store, cfg, out)
    reports_mod.index_page(out)
    try:
        print(f"  backup: {backup_db(store)}")
    except Exception as e:          # a failed backup must never fail the capture
        print(f"  ! backup failed: {e}")


def cmd_backfill(args):
    n = engine_mod.backfill(ROOT, days=args.days)
    store, cfg = _store()
    print(f"  backfill: built {n} past days (tagged 'backfill')")


def cmd_screen(args):
    import screening as screening_mod
    store, cfg = _store()
    store.upsert_instruments(cfg["_universe"], added_date="")  # keep metadata fresh
    only = set(args.tickers) if args.tickers else None
    res = screening_mod.screen_universe(ROOT, store, cfg["_universe"],
                                        source=args.source, only=only)
    from collections import Counter
    print("  summary:", dict(Counter(r["status"] for _, r in res)))


def cmd_email_weekly(args):
    """Rebuild this week's reports, then email them (or --dry-run to save a .eml)."""
    store, cfg = _store()
    out = os.path.join(ROOT, "reports")
    store.set_view(None)
    reports_mod.weekly_report(store, out)
    if reports_mod.halal_view(store):
        reports_mod.weekly_report(store, out)
    store.set_view(None)
    _email_weekly(store, cfg, out, dry_run=args.dry_run)


def _tailscale_ip():
    """This PC's Tailscale IPv4 (100.64.0.0/10), or None if Tailscale isn't running."""
    import shutil
    import socket
    import subprocess
    for exe in (shutil.which("tailscale"), r"C:\Program Files\Tailscale	ailscale.exe"):
        if exe and os.path.exists(exe):
            try:
                out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True,
                                     timeout=15).stdout.split()
                if out:
                    return out[0]
            except Exception:
                pass
    try:
        for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = a[4][0]
            o = [int(x) for x in ip.split(".")]
            if o[0] == 100 and 64 <= o[1] <= 127:
                return ip
    except OSError:
        pass
    return None


def cmd_serve(args):
    """Serve reports/ on your home network so a phone on the same Wi-Fi can read them."""
    import socket
    import http.server
    import functools
    out = os.path.join(ROOT, "reports")
    reports_mod.index_page(out)
    if args.tailscale:
        ip = _tailscale_ip()
        if not ip:
            raise SystemExit("  Tailscale not found/running. Install it from "
                             "https://tailscale.com/download, sign in, then retry.")
        args.host = ip            # bind ONLY to the private Tailscale address
    else:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = "YOUR-PC-IP"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=out)
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    print(f"  Reports are being served. Press Ctrl+C to stop.")
    if args.tailscale:
        print(f"  On your phone (Tailscale on): http://{ip}:{args.port}/")
        print("  Only your own Tailscale devices can reach this address.")
    else:
        print(f"  On this PC : http://localhost:{args.port}/")
        if args.host != "127.0.0.1":
            print(f"  On your phone (same Wi-Fi): http://{ip}:{args.port}/")
            print("  Anyone on this Wi-Fi network can read these pages while it runs.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")


def cmd_demo(args):
    import datetime as dt
    store, cfg = _store()
    today = dt.date.today()
    # count business days up front so we can show progress
    bdays = []
    d = today - dt.timedelta(days=args.days)
    while d <= today:
        if d.weekday() < 5:  # Mon-Fri
            bdays.append(d)
        d += dt.timedelta(days=1)

    total = len(bdays)
    print(f"  demo: building {total} capture-days (offline, fake data)...")
    for i, d in enumerate(bdays, 1):
        # archive=False -> skip the thousands of tiny raw files (big speed win)
        engine_mod.collect(ROOT, mode="demo", as_of=d.isoformat(),
                            quiet=True, archive=False)
        if i % 5 == 0 or i == total:
            print(f"    [{i}/{total}] {d.isoformat()}", flush=True)

    print(f"  demo: done - {total} capture-days ending {today.isoformat()}")
    latest = store.latest_date()
    path = reports_mod.daily_report(store, latest, os.path.join(ROOT, "reports"))
    print(f"  daily report: {path}")


def cmd_report(args):
    store, cfg = _store()
    out = os.path.join(ROOT, "reports")
    w = cfg.get("weights", {})
    kind = args.kind
    if args.halal_only or args.include_review:
        n = len(reports_mod.halal_view(store, include_review=args.include_review))
        if not n:
            print("  No screened names yet. Run 'python main.py screen' first.")
            return
    if kind == "daily":
        d = store.latest_date()
        if not d:
            print("No data yet. Run 'collect' or 'demo' first.")
            return
        p = reports_mod.daily_report(store, d, out)
    elif kind == "weekly":
        p = reports_mod.weekly_report(store, out)
    elif kind == "monthly":
        p = reports_mod.monthly_report(store, out, w, "Monthly", 30)
    elif kind == "quarterly":
        p = reports_mod.monthly_report(store, out, w, "Quarterly", 91)
    elif kind == "halfyear":
        p = reports_mod.monthly_report(store, out, w, "Half Year", 182)
    elif kind == "yearly":
        p = reports_mod.monthly_report(store, out, w, "Yearly", 365)
    else:
        print(f"Unknown report kind: {kind}")
        return
    print(f"  report: {p}" if p else "  (no history yet)")
    reports_mod.index_page(out)


def cmd_paper(args):
    import datetime as dt
    import paper as paper_mod
    store, cfg = _store()
    out = os.path.join(ROOT, "reports")
    try:
        if args.action == "init":
            paper_mod.init(store, args.cash, args.members,
                           dt.date.today().isoformat())
            print(f"  paper portfolio created: {args.cash:,.2f} shared by "
                  f"{args.members} members ({args.cash / args.members:,.2f} each)")
        elif args.action in ("buy", "sell"):
            date = args.date or store.latest_date() or dt.date.today().isoformat()
            px = paper_mod.trade(store, args.action, args.ticker, args.qty, date,
                                 price=args.price, fee=args.fee, note=args.note or "",
                                 force=args.force)
            print(f"  recorded: {args.action} {args.qty:g} {args.ticker.upper()} "
                  f"@ {px:,.2f} on {date}")
        elif args.action == "status":
            date = store.latest_date()
            if not date:
                raise ValueError("no market data yet. Run 'collect' first.")
            v = paper_mod.valuation(store, date)
            print(f"  as of {date}: value {v['nav']:,.2f} ({v['ret_pct']:+.2f}%), "
                  f"cash {v['cash']:,.2f}")
            for r in v["positions"]:
                print(f"    {r['ticker']:8} {r['qty']:g} @ {r['avg_cost']:,.2f} "
                      f"-> {r['price']:,.2f}  P/L {r['pnl']:+,.2f}")
            print(f"  each of {v['members']} members: {v['share_value']:,.2f} "
                  f"({v['share_pnl']:+,.2f})")
        elif args.action == "report":
            p = paper_mod.paper_report(store, out)
            reports_mod.index_page(out)
            print(f"  report: {p}")
    except ValueError as e:
        raise SystemExit(f"  ! {e}")


def main():
    ap = argparse.ArgumentParser(description="Halal Market Analysis Engine (Phase 0)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("paper", help="shared paper-trading (pretend-money) portfolio")
    pp.add_argument("action", choices=["init", "buy", "sell", "status", "report"])
    pp.add_argument("ticker", nargs="?", help="buy/sell: ticker, e.g. AAPL or WTC.AX")
    pp.add_argument("qty", nargs="?", type=float, help="buy/sell: number of shares")
    pp.add_argument("--cash", type=float, default=100000, help="init: total starting cash")
    pp.add_argument("--members", type=int, default=10, help="init: number of members")
    pp.add_argument("--price", type=float, help="default: latest stored close")
    pp.add_argument("--date", help="YYYY-MM-DD (default: latest data date)")
    pp.add_argument("--fee", type=float, default=0.0)
    pp.add_argument("--note", help="REQUIRED for trades: why the group decided this")
    pp.add_argument("--force", action="store_true",
                    help="allow a buy that is not screen-pass (recorded in the log)")

    sub.add_parser("collect", help="capture today's data (real, via yfinance)")

    b = sub.add_parser("backfill", help="rebuild past days from real price history")
    b.add_argument("--days", type=int, default=365)

    em = sub.add_parser("email-weekly", help="email the weekly report now")
    em.add_argument("--dry-run", action="store_true",
                    help="build the email and save it to data/outbox instead of sending")

    sv = sub.add_parser("serve", help="serve reports to a phone on your Wi-Fi")
    sv.add_argument("--port", type=int, default=8790)
    sv.add_argument("--tailscale", action="store_true",
                    help="serve only on your private Tailscale address (recommended)")
    sv.add_argument("--host", default="0.0.0.0",
                    help="0.0.0.0 = reachable from your phone; 127.0.0.1 = this PC only")

    sc = sub.add_parser("screen", help="halal-screen the watchlist (Phase 1)")
    sc.add_argument("--source", choices=["auto", "halalterminal", "self"],
                    default="auto", help="auto = Halal Terminal if a key is set, else self")
    sc.add_argument("tickers", nargs="*", help="only these tickers (default: all)")

    d = sub.add_parser("demo", help="backfill offline demo data to explore")
    d.add_argument("--days", type=int, default=40)

    r = sub.add_parser("report", help="build a report from stored history")
    r.add_argument("kind", choices=["daily", "weekly", "monthly",
                                    "quarterly", "halfyear", "yearly"])

    r.add_argument("--halal-only", action="store_true",
                   help="show only screen-pass names + Hejaz ETFs (ranks renumbered)")
    r.add_argument("--include-review", action="store_true",
                   help="with --halal-only: also include 'review' names")

    args = ap.parse_args()
    if args.cmd == "collect":
        cmd_collect(args)
    elif args.cmd == "email-weekly":
        cmd_email_weekly(args)
    elif args.cmd == "serve":
        cmd_serve(args)
    elif args.cmd == "screen":
        cmd_screen(args)
    elif args.cmd == "backfill":
        cmd_backfill(args)
    elif args.cmd == "demo":
        cmd_demo(args)
    elif args.cmd == "report":
        cmd_report(args)
    elif args.cmd == "paper":
        if args.action in ("buy", "sell") and (not args.ticker or not args.qty):
            ap.error("paper buy/sell needs TICKER and QTY, e.g. paper buy AAPL 10 --note \"why\"")
        cmd_paper(args)


if __name__ == "__main__":
    main()
