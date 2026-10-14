"""Phone alerts via ntfy.sh: free push notifications, no account. Install the ntfy app and
subscribe to the topic in NTFY_TOPIC. Tapping an alert opens the posting."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

RATING_ORDER = {"Strong": 0, "Good": 1, "Stretch": 2}


def _short_loc(loc: str) -> str:
    first = loc.split(";")[0].split(" / ")[-1]
    first = first.split("(")[0].strip(" ,")
    extra = loc.count(";")
    return (first[:28] + (f" +{extra}" if extra else "")) or "location n/a"


def compose(matches: list[dict], site_url: str, first_run: bool = False,
            max_listed: int = 25) -> list[dict]:
    """One alert per new match (title, location, fit, reason; tap opens the posting), then one
    summary alert that opens the results page. Empty list = send nothing."""
    if not matches:
        return []
    site = site_url.rstrip("/") + "/"
    alerts = []
    for m in matches[:max_listed]:
        flag = "\nStart date may be before summer 2027." if m.get("early_start") else ""
        alerts.append({
            "title": f"{m['company']}: {m['title'][:90]}",
            "body": f"{m['rating']} fit · {_short_loc(m['location'])}\n{m['reason']}{flag}"
                    + "".join(f"\n{p}" for p in (m.get("pointers") or [])[:4]),
            "click": m["url"], "tags": "briefcase",
            "priority": "high" if m["rating"] == "Strong" else "default",
        })
    counts = {r: sum(1 for m in matches if m["rating"] == r) for r in RATING_ORDER}
    tally = ", ".join(f"{n} {r}" for r, n in counts.items() if n)
    n, more = len(matches), len(matches) - min(len(matches), max_listed)
    head = (f"First scan: {n} open matches" if first_run
            else f"{n} new match{'es' if n != 1 else ''} today")
    alerts.append({
        "title": f"Job Scanner: {head}",
        "body": f"{tally}." + (f" {more} more only on the results page." if more else "")
                + " Tap to open the full list.",
        "click": site, "tags": "clipboard", "priority": "default",
    })
    return alerts


def send(alerts: list[dict]) -> list[str]:
    """Posts each alert to ntfy. Returns a list of error strings (empty on success)."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return ["NTFY_TOPIC not set"]
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    site = alerts[-1]["click"]
    errors = []
    for a in alerts:
        params = {"title": a["title"], "click": a["click"], "tags": a["tags"], "priority": a["priority"]}
        if a["click"] != site:
            params["actions"] = f"view, Open posting, {a['click']}; view, Results page, {site}"
        url = f"{server}/{topic}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, data=a["body"].encode("utf-8"), method="POST")
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError as e:
            errors.append(f"ntfy {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except urllib.error.URLError as e:
            errors.append(f"ntfy connection error: {e}")
        time.sleep(1)                       # stay well inside ntfy's anonymous rate limit
    return errors


# ---------------------------------------------------------------- email (Strong matches)

def compose_email(matches: list[dict], site_url: str) -> dict | None:
    """One email listing every new match worth an email (Strong by default). None = send nothing."""
    if not matches:
        return None
    site = site_url.rstrip("/") + "/"
    n = len(matches)
    subject = (f"Strong match: {matches[0]['company']} - {matches[0]['title'][:70]}" if n == 1
               else f"{n} new Strong matches on Job Scanner")
    text, rows = [], []
    for m in matches:
        pts = [p for p in (m.get("pointers") or [])[:5]]
        text.append(f"{m['company']}: {m['title']}\n{m['location']}\n{m['reason']}\n"
                    + "".join(f"  {p}\n" for p in pts) + f"Posting: {m['url']}\n")
        esc = lambda x: (x or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        rows.append(
            f'<div style="border:1px solid #ddd;border-radius:10px;padding:14px;margin:0 0 12px">'
            f'<div style="color:#666;font-size:13px">{esc(m["company"])} · {esc(m["rating"])} fit</div>'
            f'<a href="{esc(m["url"])}" style="font-size:17px;font-weight:600;color:#1a56db">{esc(m["title"])}</a>'
            f'<div style="color:#666;font-size:13px;margin:4px 0 8px">{esc(m["location"][:160])}</div>'
            f'<div style="font-size:14px">{esc(m["reason"])}</div>'
            f'<ul style="font-size:13px;padding-left:18px">{"".join(f"<li>{esc(p)}</li>" for p in pts)}</ul>'
            f'<a href="{esc(m["url"])}" style="font-size:14px">Open the posting →</a></div>')
    html = (f'<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px">'
            f'<h2 style="font-size:18px">{"A new Strong match" if n == 1 else f"{n} new Strong matches"}</h2>'
            f'{"".join(rows)}<p style="font-size:14px"><a href="{site}">See every open match</a></p></div>')
    return {"subject": subject, "html": html,
            "text": "\n".join(text) + f"\nEvery open match: {site}\n"}


def send_email(mail: dict, to: str) -> list[str]:
    """Sends through Resend's free plan (3,000 emails/month) from the verified taskbytask.org
    domain. Needs the RESEND_API_KEY secret. Returns errors (empty on success)."""
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        return ["RESEND_API_KEY not set - email alerts are off until it is added as a GitHub secret"]
    body = {"from": os.environ.get("EMAIL_FROM", "Job Scanner <jobs@taskbytask.org>"), "to": [to],
            "subject": mail["subject"], "html": mail["html"], "text": mail["text"]}
    req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "job-scanner"}, method="POST")
    for attempt in range(3):
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return []
        except urllib.error.HTTPError as e:
            err = f"Resend {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
            if e.code not in (429, 500, 502, 503):
                return [err]
        except urllib.error.URLError as e:
            err = f"Resend connection error: {e}"
        time.sleep(3 * (attempt + 1))
    return [err]
