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


def compose(matches: list, site_url: str, max_individual: int) -> list[str]:
    """Returns the text bodies to send (empty list = send nothing)."""
    if not matches:
        return []
    host = site_url.replace("https://", "").replace("http://", "").rstrip("/")
    if len(matches) <= max_individual:
        out = []
        for j in matches:
            flag = " | starts pre-summer '27" if j.early_start else ""
            out.append(f"{j.company}: {j.title} | {_short_loc(j.location)} | {j.rating}{flag}\n"
                       f"{host}/j/{j.extra['code']}")
        return out
    counts = {r: sum(1 for j in matches if j.rating == r) for r in RATING_ORDER}
    parts = ", ".join(f"{n} {r}" for r, n in counts.items() if n)
    top = matches[0]
    return [f"{len(matches)} new matches ({parts}). Top: {top.company} - {top.title[:60]} ({top.rating}).\n"
            f"Full list: {host}/"]


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
