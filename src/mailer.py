"""
mailer.py -- email the weekly report.

What you receive:
  * the email BODY is the plain-English summary (what changed + top 10), written
    with inline styles so Gmail/phone mail apps show it properly;
  * two PDF attachments (graphical ranking with score bars and trend lines):
    the halal-only weekly report and the full-watchlist weekly report.
    PDFs open cleanly on a phone; the raw .html reports do not in most mail apps.

Setup (one time): config/settings.yaml -> email.to / email.from, and put a Gmail
APP PASSWORD (not your normal password) in config/secrets.local.yaml as
`gmail_app_password`. Nothing is sent until that exists.
"""

from __future__ import annotations
import os
import re
import ssl
import shutil
import smtplib
import subprocess
import tempfile
import datetime as dt
from email.message import EmailMessage

import yaml

import explain as explain_mod

_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _find_browser():
    for b in _BROWSERS:
        if os.path.exists(b):
            return b
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)          # Linux (GitHub Actions runners have Chrome)
        if found:
            return found
    return None


def html_to_pdf(html_path: str, pdf_path: str) -> bool:
    """Render a report to PDF with the installed Edge/Chrome. False if unavailable."""
    exe = _find_browser()
    if not exe:
        return False
    prof = tempfile.mkdtemp(prefix="hme_pdf_")
    try:
        url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
        subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
             f"--user-data-dir={prof}", f"--print-to-pdf={os.path.abspath(pdf_path)}", url],
            capture_output=True, timeout=120)
        return os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000
    except Exception:
        return False
    finally:
        shutil.rmtree(prof, ignore_errors=True)


# --- make report HTML email-safe: class -> inline style -------------------------
_STYLES = {
    "card": "background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px;margin:10px 0;",
    "small": "color:#64748b;font-size:12px;",
    "up": "color:#059669;font-weight:600;",
    "down": "color:#dc2626;font-weight:600;",
    "bf": "background:#fef3c7;color:#92400e;border:1px solid #fcd34d;border-radius:8px;padding:10px 12px;margin:10px 0;font-size:13px;",
}
_TAG_STYLES = {
    "h2": "font-size:17px;margin:22px 0 8px;border-bottom:1px solid #e2e8f0;padding-bottom:5px;",
    "ul": "margin:6px 0 6px 18px;padding:0;",
    "li": "margin:3px 0;",
    "details": "background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 14px;margin:10px 0;",
}


def _inline(html: str) -> str:
    def fix(m):
        tag, attrs = m.group(1), m.group(2)
        cls = re.search(r'class="([^"]*)"', attrs)
        sty = re.search(r'style="([^"]*)"', attrs)
        parts = [_TAG_STYLES.get(tag.lower(), "")]
        if cls:
            parts += [_STYLES.get(c, "") for c in cls.group(1).split()]
        if sty:
            parts.append(sty.group(1))
        rest = re.sub(r'\s*(class|style)="[^"]*"', "", attrs)
        style = "".join(parts)
        return f"<{tag}{rest}" + (f' style="{style}"' if style else "") + ">"
    return re.sub(r"<([a-zA-Z0-9]+)((?:\s[^>]*)?)>", fix, html)


def build_body(store, cur, prev, rows, screens, view_label) -> str:
    parts = [
        f"<p style='font-size:15px'>Your weekly halal market review for <b>{cur}</b> "
        f"({view_label}). The graphical ranking is in the attached PDF; the "
        "plain-English summary is below.</p>",
        explain_mod.period_summary(store, cur, prev, rows, "week"),
        explain_mod.plain_english(store, cur, rows, screens, prev,
                                  compare=f"{prev} (start of the week)"),
        "<p style='color:#64748b;font-size:12px;margin-top:18px'><b>Not financial advice.</b> "
        "Rankings describe measurable factors and do not predict prices. Halal screening is "
        "automated and is not a religious ruling.</p>",
    ]
    return ("<div style=\"font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
            "color:#0f172a;max-width:680px;line-height:1.5\">" + _inline("".join(parts)) + "</div>")


# --- config / sending -----------------------------------------------------------
def _secrets(root: str) -> dict:
    path = os.path.join(root, "config", "secrets.local.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def build_message(cfg_email: dict, subject: str, html_body: str, attachments) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg_email["from"]
    msg["To"] = cfg_email["to"]
    msg.set_content("Your email app can't show HTML. Open the attached PDF for the weekly report.")
    msg.add_alternative(html_body, subtype="html")
    for path in attachments:
        with open(path, "rb") as f:
            data = f.read()
        if path.lower().endswith(".pdf"):
            msg.add_attachment(data, maintype="application", subtype="pdf",
                               filename=os.path.basename(path))
        else:
            msg.add_attachment(data, maintype="text", subtype="html",
                               filename=os.path.basename(path))
    return msg


def send_weekly(root, cfg, store, reports_dir, cur, prev, halal_view_fn, dry_run=False):
    """Build and send (or, with dry_run, save as .eml) the weekly email."""
    em = cfg.get("email") or {}
    if not em.get("enabled", False):
        return "email disabled in settings.yaml"
    if not em.get("to") or not em.get("from"):
        return "email skipped: set email.to and email.from in settings.yaml"
    password = os.environ.get("GMAIL_APP_PASSWORD") or _secrets(root).get("gmail_app_password")
    if not password and not dry_run:
        return ("email skipped: no gmail_app_password in config/secrets.local.yaml "
                "(see README: Weekly email)")

    # body = halal-only plain English
    store.set_view(None)
    if not halal_view_fn(store):
        return "email skipped: no screening results yet"
    rows = store.ranking_on(cur)
    screens = store.latest_screenings()
    body = build_body(store, cur, prev, rows, screens, "halal-screened view")
    store.set_view(None)

    out = os.path.join(root, "data", "outbox")
    os.makedirs(out, exist_ok=True)
    attachments = []
    for tag, label in (("weekly_halal", "Halal"), ("weekly", "Full")):
        html = os.path.join(reports_dir, f"{tag}_{cur}.html")
        if not os.path.exists(html):
            continue
        pdf = os.path.join(out, f"Halal-Market-Weekly-{label}-{cur}.pdf")
        attachments.append(pdf if html_to_pdf(html, pdf) else html)

    msg = build_message(em, f"Halal Market weekly review — {cur}", body, attachments)
    if dry_run:
        path = os.path.join(out, f"weekly_{cur}.eml")
        with open(path, "wb") as f:
            f.write(bytes(msg))
        return f"dry run: saved {path} ({len(attachments)} attachment(s))"

    ctx = ssl.create_default_context()
    bundle = os.path.join(root, "certs", "ca-bundle.pem")
    if os.path.exists(bundle):                    # antivirus HTTPS/SMTP scanning
        ctx.load_verify_locations(bundle)
    host = em.get("smtp_host", "smtp.gmail.com")
    port = int(em.get("smtp_port", 587))
    # 587 + STARTTLS is the default: antivirus mail scanning (Norton) breaks 465.
    smtp = (smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) if port == 465
            else smtplib.SMTP(host, port, timeout=60))
    with smtp as s:
        if port != 465:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
        s.login(em["from"], password)
        s.send_message(msg)
    return f"sent to {em['to']} with {len(attachments)} attachment(s)"
