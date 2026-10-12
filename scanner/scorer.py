"""Rates keyword survivors with Claude: Strong / Good / Stretch / No, plus a one-line reason."""
from __future__ import annotations

import os
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

Return one entry per posting, using the posting's id exactly as given.

CANDIDATE PROFILE
{profile}"""


def _posting_block(j) -> str:
    desc = (j.description or "").strip()[:2500]
    return (f"<posting id=\"{j.key}\">\nCompany: {j.company}\nTitle: {j.title}\n"
            f"Location: {j.location or 'unknown'}\nBoard hints: {j.meta or '-'}\n"
            f"Description: {desc or '(not available - judge from title)'}\n</posting>")


def score(jobs: list, profile: str, model: str, batch_size: int = 12) -> tuple[dict, str | None]:
    """Returns ({job.key: Rating}, error). Missing API key or any API failure -> ({}, message);
    the caller then falls back to rule-based ratings so the run still sends results."""
    if not jobs:
        return {}, None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}, "ANTHROPIC_API_KEY not set - used keyword ratings"
    import anthropic

    client = anthropic.Anthropic()
    system = INSTRUCTIONS.format(profile=profile)
    out, errors = {}, []
    for i in range(0, len(jobs), batch_size):
        chunk = jobs[i:i + batch_size]
        try:
            resp = client.messages.parse(
                model=model,
                max_tokens=8000,
                system=system,
                messages=[{"role": "user", "content": "\n\n".join(_posting_block(j) for j in chunk)}],
                output_format=Ratings,
            )
        except anthropic.APIStatusError as e:
            errors.append(f"Claude API {e.status_code}: {e.message}")
            continue
        except anthropic.APIConnectionError as e:
            errors.append(f"Claude API connection error: {e}")
            continue
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            errors.append(f"Claude returned no ratings for a batch (stop_reason={resp.stop_reason})")
            continue
        for r in resp.parsed_output.ratings:
            out[r.id] = r
    return out, "; ".join(dict.fromkeys(errors)) or None
