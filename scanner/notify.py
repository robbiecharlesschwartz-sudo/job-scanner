"""Phone alerts via ntfy.sh: free push notifications, no account. Install the ntfy app and
subscribe to the topic in NTFY_TOPIC. Tapping an alert opens the posting."""
from __future__ import annotations

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
