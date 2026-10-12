"""Twilio SMS. Plain REST call - no SDK needed for one endpoint."""
from __future__ import annotations

import base64
import os
import urllib.parse
import urllib.request

RATING_ORDER = {"Strong": 0, "Good": 1, "Stretch": 2}


def _short_loc(loc: str) -> str:
    first = loc.split(";")[0].split(" / ")[-1]
    first = first.split("(")[0].strip(" ,")
    extra = loc.count(";")
    return (first[:28] + (f" +{extra}" if extra else "")) or "location n/a"


def compose(matches: list[dict], site_url: str, first_run: bool = False,
            max_listed: int = 25, max_chars: int = 1200) -> list[str]:
    """Text bodies to send (empty list = send nothing). Every match gets a line and a link to
    the posting; the last text links to the results page. Twilio joins a long body into one
    message on the phone (it bills per 153-character segment)."""
    if not matches:
        return []
    host = site_url.replace("https://", "").replace("http://", "").rstrip("/")
    counts = {r: sum(1 for m in matches if m["rating"] == r) for r in RATING_ORDER}
    tally = ", ".join(f"{n} {r}" for r, n in counts.items() if n)
    n = len(matches)
    head = (f"Job Scanner first scan: {n} open matches ({tally})." if first_run
            else f"Job Scanner: {n} new match{'es' if n != 1 else ''} ({tally}).")
    listed = matches[:max_listed]
    lines = []
    for m in listed:
        flag = " | starts pre-summer '27" if m.get("early_start") else ""
        lines.append(f"{m['company']}: {m['title'][:80]} | {_short_loc(m['location'])} | "
                     f"{m['rating']}{flag}\n{host}/j/{m['code']}")
    tail = (f"+{n - len(listed)} more on the page: {host}/" if n > len(listed)
            else f"All results: {host}/")

    bodies, cur = [], head
    for line in lines:
        if len(cur) + len(line) + 2 > max_chars:
            bodies.append(cur)
            cur = line
        else:
            cur += "\n\n" + line
    if len(cur) + len(tail) + 2 > max_chars:
        bodies.append(cur)
        cur = tail
    else:
        cur += "\n\n" + tail
    bodies.append(cur)
    return bodies


def send(bodies: list[str]) -> list[str]:
    """Sends via Twilio. Returns a list of error strings (empty on success)."""
    sid, token = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
    sender, to = os.environ.get("TWILIO_FROM"), os.environ.get("SMS_TO")
    if not (sid and token and sender and to):
        return ["Twilio env vars missing (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM, SMS_TO)"]
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    errors = []
    for body in bodies:
        # TWILIO_FROM may be a phone number or a Messaging Service SID (MG...).
        field = "MessagingServiceSid" if sender.startswith("MG") else "From"
        data = urllib.parse.urlencode({"To": to, field: sender, "Body": body}).encode()
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=data,
            headers={"Authorization": f"Basic {auth}"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError as e:
            errors.append(f"Twilio {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        except urllib.error.URLError as e:
            errors.append(f"Twilio connection error: {e}")
    return errors
