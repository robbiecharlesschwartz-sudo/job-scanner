"""Rates keyword survivors with an LLM (Groq free tier, or Claude): Strong / Good / Stretch / No, plus a one-line reason."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Literal

from pydantic import BaseModel


class Rating(BaseModel):
    id: str
    rating: Literal["Strong", "Good", "Stretch", "No"]
    reason: str
    starts_before_summer_2027: bool


class Ratings(BaseModel):
    ratings: list[Rating]


INSTRUCTIONS = """You screen job postings for one early-career candidate. Their profile is below.

For each posting, return:
- rating:
  Strong  = clearly entry-level (new grad / analyst / associate / 0-2 yrs) AND squarely in
            consulting or strategy (incl. sustainability/climate consulting, corporate strategy,
            strategy & ops, BizOps, chief of staff, founder's associate, internal consulting), US-based.
  Good    = entry-level and adjacent to that work (e.g. operations or transformation analyst,
            a consulting role with a technical tilt, a strategy role in a narrower domain).
  Stretch = plausible but a reach: slightly senior wording, a specialised domain, or unclear level.
  No      = not a fit: requires 3+ years, senior, non-US only, internship, or unrelated function
            (pure engineering, audit, tax, sales quota roles, clinical, etc.).
- reason: ONE short line (max ~15 words) on why it fits or doesn't, specific to this candidate
  (e.g. "Entry-level climate consulting; your CDP/EcoVadis work maps directly").
- starts_before_summer_2027: true only if the posting says the start date is before ~June 2027.

Level calibration (be strict - Strong should be rare, a handful per day at most):
- At Deloitte, EY, PwC, KPMG, Accenture, IBM, Guidehouse, Slalom, West Monroe and Kearney, a bare
  "Consultant" title is the level ABOVE new-grad entry (it usually means 2-4 years or an MBA).
  Rate it Stretch at most unless the posting says entry-level / new grad / campus / 2027 / 0-2 years.
- Entry-level titles there are: Analyst, Associate, Staff, Business Analyst, Associate Consultant,
  or anything marked new grad / campus / class of 2027.
- When the description is missing, judge from the title and lean one step lower.
- Strong also requires the work itself to be strategy, management, operations, transformation or
  sustainability consulting - not IT implementation (SAP, Salesforce, Workday, ServiceNow),
  cyber, or data engineering, which are Good at best.

Return one entry per posting, using the posting's id exactly as given.

CANDIDATE PROFILE
{profile}"""


def _posting_block(j) -> str:
    desc = (j.description or "").strip()[:2500]
    return (f"<posting id=\"{j.key}\">\nCompany: {j.company}\nTitle: {j.title}\n"
            f"Location: {j.location or 'unknown'}\nBoard hints: {j.meta or '-'}\n"
            f"Description: {desc or '(not available - judge from title)'}\n</posting>")


def score(jobs: list, profile: str, model: str, batch_size: int = 12) -> tuple[dict, str | None]:
    """Returns ({job.key: Rating}, error). Uses Groq's free tier (GROQ_API_KEY); Claude instead if
    ANTHROPIC_API_KEY is set. No key or an API failure -> keyword ratings, and the run carries on."""
    if not jobs:
        return {}, None
    system = INSTRUCTIONS.format(profile=profile)
    providers = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(("Claude", lambda chunk: _claude(system, chunk, model)))
    if os.environ.get("GROQ_API_KEY"):
        providers.append(("Groq", lambda chunk: _groq(system, chunk)))
    if not providers:
        return {}, "No GROQ_API_KEY or ANTHROPIC_API_KEY set - used keyword ratings"
    out, errors, dead = {}, [], set()
    for i in range(0, len(jobs), batch_size):
        chunk = jobs[i:i + batch_size]
        for name, call in providers:             # Claude first; Groq if Claude fails (e.g. no credit)
            if name in dead:
                continue
            try:
                for r in call(chunk):
                    out[r.id] = r
                break
            except Exception as e:
                msg = str(e)
                errors.append(f"{name}: {msg[:160]}")
                if "credit balance" in msg or "401" in msg[:40]:
                    dead.add(name)               # account-level problem: stop retrying it
    return out, "; ".join(dict.fromkeys(errors)) or None


def _claude(system: str, chunk: list, model: str) -> list[Rating]:
    import anthropic
    resp = anthropic.Anthropic().messages.parse(
        model=model, max_tokens=8000, system=system,
        messages=[{"role": "user", "content": "\n\n".join(_posting_block(j) for j in chunk)}],
        output_format=Ratings)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"Claude returned no ratings (stop_reason={resp.stop_reason})")
    return resp.parsed_output.ratings


GROQ_MODEL = "openai/gpt-oss-120b"


def _groq(system: str, chunk: list) -> list[Rating]:
    schema_hint = ('Respond with JSON only: {"ratings": [{"id": str, "rating": "Strong"|"Good"|'
                   '"Stretch"|"No", "reason": str, "starts_before_summer_2027": bool}]}')
    body = json.dumps({
        "model": GROQ_MODEL, "temperature": 0.2, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system + "\n\n" + schema_hint},
                     {"role": "user", "content": "\n\n".join(_posting_block(j) for j in chunk)}],
    }).encode()
    for attempt in range(5):
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=body, method="POST",
            headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                     "Content-Type": "application/json", "User-Agent": "job-scanner"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                content = json.load(r)["choices"][0]["message"]["content"]
            return Ratings.model_validate_json(content).ratings
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:     # free-tier rate limit: wait it out
                time.sleep(float(e.headers.get("retry-after") or 20))
                continue
            raise RuntimeError(f"Groq {e.code}: {e.read().decode('utf-8', 'replace')[:150]}")
    raise RuntimeError("Groq rate limit persisted")
