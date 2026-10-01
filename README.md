# Halal Market-Understanding & Analysis Engine — Phase 0

A personal, halal-first **learning tool**. Every day it records market data for a
watchlist, ranks the companies on many transparent "points" (value, quality,
momentum, crowd activity), and keeps **every data point forever** so it can later
explain *why* rankings changed over a week, month, quarter, half-year and year.

> **This is not a buy-signal app and not financial advice.** It describes what the
> market did and which measurable factors moved with it. It does **not** predict
> prices. Confirm any stock's Shariah compliance yourself (e.g. Zoya / Musaffa)
> before making decisions.

---

## What it does

- **Collects** daily prices + fundamentals for your universe (free data via Yahoo Finance).
- **Ranks** every company 0–100 on four factor groups, combined by weights you control.
- **Stores** everything (long-format metrics + factor scores + ranks + raw file archive) in a local SQLite database — nothing is ever thrown away.
- **Reports**: daily snapshot, weekly review, and monthly / quarterly / half-year / yearly analyses with **rank-change attribution** (which factors pushed each top-10 name up or down, with the score deltas).

---

## Quick start (5 minutes)

You need **Python 3.10+**. In a terminal, inside this folder:

```bash
# 1. install dependencies
pip install -r requirements.txt

# 2. TRY IT NOW with offline demo data (fake numbers, safe to explore)
python main.py demo --days 60
python main.py report monthly      # open the file it prints, in your browser

# 3. When ready, delete the demo database and start collecting REAL data:
#    (Windows)  del data\engine.db
#    (Mac/Linux) rm data/engine.db
python main.py collect             # captures today's real data
python main.py report daily
```

Reports are written to the `reports/` folder as self-contained `.html` files —
double-click to open in any browser. The database lives at `data/engine.db`.

---

## Everyday use

Run **`python main.py collect`** once per trading day (see scheduling below).
Then whenever you want to think, generate a report:

| Command | What you get |
|---|---|
| `python main.py report daily`     | today's ranking + notable moves |
| `python main.py report weekly`    | ~7-day risers/fallers + top 10 |
| `python main.py report monthly`   | month-end ranking (all points) + **top-10 attribution** |
| `python main.py report quarterly` | same, over ~3 months |
| `python main.py report halfyear`  | same, over ~6 months |
| `python main.py report yearly`    | same, over ~12 months |

The longer you let `collect` run daily, the richer every analysis becomes. Per
your plan: watch quietly for about a month before drawing conclusions.

---

## Make it run automatically each day

Run it shortly after the ASX close (say **5:30pm Brisbane time**) on days your PC is on.

**Windows (Task Scheduler):**
1. Open *Task Scheduler* → *Create Basic Task*.
2. Trigger: Daily, 5:30 PM.
3. Action: *Start a program* → Program: `python`, Arguments: `main.py collect`,
   Start in: the full path to this folder.

**Mac/Linux (cron):** run `crontab -e` and add:
```
30 17 * * 1-5  cd /full/path/to/halal-market-engine && /usr/bin/python3 main.py collect >> data/cron.log 2>&1
```

> **Gap-free tip:** if your PC is often off, the data has gaps. A free fix is to run
> just `collect` on a GitHub Actions schedule instead (see the build plan, §17) so
> the archive fills even when your machine is asleep.

---

## Customise

- **`config/universe.yaml`** — add/remove companies. ASX tickers end in `.AX`
  (e.g. `WES.AX`); US tickers have no suffix. Set an honest `halal_status`.
- **`config/settings.yaml`** — change factor **weights** to match your philosophy
  (e.g. lean harder into value + quality for long-term halal investing).

The seed universe = the three Hejaz Shariah-compliant ASX ETFs, a set of global
large caps typical of halal equity funds (labelled `etf_screened*` — confirm each
individually), and a few ASX names tracked for learning only (`UNVERIFIED`).

---

## How the ranking works (transparent by design)

1. Each raw metric (P/E, dividend yield, 3-month return, position in 52-week range,
   volume ratio, margins, debt, …) is turned into a **0–100 percentile** vs the rest
   of the universe that day. "Lower is better" metrics (P/E, debt, being near the
   52-week high) are inverted so 100 always = most attractive.
2. A **factor-group score** = the average of its component scores.
3. The **total** = the weighted average of the four group scores.
4. Rank by total, highest first.

Because every group score is stored each day, the reports can compare a company's
scores across time and attribute its rank move to specific factors — e.g.
*"up 17 places: momentum +7.4, value −1.5"*.

---

## Growing beyond Phase 0

- **Phase 1** — swap manual halal-checking for a screening API (Halal Terminal free
  tier, or Musaffa which covers the ASX) so compliance is automatic.
- **Phase 2** — move to reliable paid ASX data (EODHD) and add a web dashboard.
- **Storage** — the `src/storage.py` layer is the *only* place that knows about the
  database. To move to Supabase/Postgres later, implement the same methods there and
  flip `backend` in `settings.yaml`. Keep the bulky `data/raw/` archive as files, not
  in the database. (See the full build plan, §16–17.)

---

## Files

```
halal-market-engine/
├─ main.py                 # command-line entry point
├─ requirements.txt
├─ config/
│  ├─ universe.yaml        # WHO to track   (edit me)
│  └─ settings.yaml        # weights/options (edit me)
├─ src/
│  ├─ providers.py         # data sources: yfinance (real) + demo (offline)
│  ├─ factors.py           # raw metrics -> "points"
│  ├─ ranking.py           # normalise + weight + rank
│  ├─ reports.py           # daily / weekly / monthly+attribution HTML
│  ├─ engine.py            # orchestration
│  └─ storage.py           # the swappable data layer (SQLite now)
├─ data/                   # engine.db + raw/ archive  (created on first run)
└─ reports/                # generated .html reports
```

*Reminder: rankings are descriptive, not predictive. Verify Shariah compliance
independently. Not financial advice.*

---

## Weekly email + phone use

Every Friday the daily task writes the weekly report and (once set up) emails it to you:
the **email body** is the plain-English summary; two **PDF attachments** (halal-only and
full) carry the graphical ranking. PDFs open cleanly on a phone.

**One-time setup**
1. Turn on 2-Step Verification for your Google account.
2. Create an *app password* at https://myaccount.google.com/apppasswords (name it e.g. "halal engine").
3. In `config/secrets.local.yaml` uncomment `gmail_app_password:` and paste the 16 characters.
4. Check `email.to` / `email.from` in `config/settings.yaml`.
5. Test: `python main.py email-weekly` (sends now). `--dry-run` saves a copy in `data/outbox/` instead.

Until the password exists the log (`logs/collect.log`) says "email skipped" and nothing is sent.

## Backups
Each daily run saves a compressed, verified copy of `data/engine.db` to `data/backups/`
(newest 14 + the last copy of each of the past 12 months). Restore: gunzip a file and
copy it over `data/engine.db`. These sit on the same drive - copy the folder to cloud
storage or another disk for protection against drive failure.

## Reading reports on your phone (private, anywhere)

1. Install **Tailscale** on the PC (https://tailscale.com/download) and on your phone
   (App Store / Google Play). Sign in with the same account on both.
2. On the PC run `python main.py serve --tailscale` (leave it running).
3. On your phone, with Tailscale on, open the address it prints (`http://100.x.y.z:8790/`).
   Bookmark it / add it to your home screen. You'll see the index of every report.

The server listens only on the Tailscale address, so nothing is exposed on your home
Wi-Fi or the internet. The PC must be on. `python main.py serve` (no flag) serves the
local Wi-Fi instead - anyone on that network could open it.
