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


INSTRUCTIONS = """You screen job postings for one early-career candidate. Their profile, including
hard requirements, is below. Hard requirements are
strict, but the candidate does want to see a few close calls: roles slightly out of range that
they could still realistically get.

For each posting, return:
- rating (use the whole scale - a typical day should have more Good and Stretch than Strong):
  Strong  = meets every hard requirement AND is squarely lane 1 or 2 (strategy / management /
            sustainability consulting analyst, or in-house corporate strategy / chief of staff
            office analyst), explicitly new-grad or 0-1 years, NYC. Example: "Strategy Analyst,
            Monitor Deloitte - 2027 new grad, New York".
  Good    = meets the hard requirements but is a step to the side: operations / supply-chain /
            transformation consulting, a rotational program, strategic finance, business
            development, or a lane 1-2 role that doesn't state its level. Example: "Kearney
            Associate, Strategic Operations".
  Stretch = slightly out of range but realistic: asks for about 2 years, sits next to the lanes
            (analytics, product or marketing strategy, risk advisory), or the level is a
            reasonable reach. Example: "Strategy Associate - 1-2 years preferred".
  No      = misses a hard requirement: not NYC, senior, 3+ years, MBA, internship, wrong start
            date, technical/implementation work, or a bank "Associate".
- reason: ONE short line (max ~15 words) saying why, specific to this candidate.
- starts_before_summer_2027: true if the posting needs a start before ~May 2027 or a cohort
  after 2027 (e.g. 2028 availability).
If a posting has a "Screening note", the keyword screen found it a step off-lane: rate it Good
or Stretch, not Strong.

Level calibration:
- At Deloitte, EY, PwC, KPMG, Accenture, IBM, Guidehouse, Slalom, West Monroe and Kearney, a bare
  "Consultant" title is often the level above entry. Rate it No if the posting asks for 2+ years,
  Stretch if it says nothing about level, higher only if it says new grad / campus / 2027.
- If the description is missing, judge from the title and lean one step lower.

Return one entry per posting, using the posting's id exactly as given.

CANDIDATE PROFILE
{profile}

YOUR PAST VERDICTS - the candidate's own calls on real postings. Match their judgement:
{examples}"""


def _examples() -> str:
    """examples.toml: postings the candidate has judged, shown to the AI as precedent."""
    import tomllib
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "examples.toml"
    if not p.exists():
        return "(none yet)"
    rows = tomllib.loads(p.read_text()).get("example", [])
    return "\n".join(f"- {e['company']} - {e['title']}: {e['rating']}. {e['why']}" for e in rows) or "(none yet)"


def _posting_block(j) -> str:
    desc = (j.description or "").strip()[:2500]
    hint = f"Screening note: {j.reason}\n" if j.verdict == "stretch" else ""
    return (f"<posting id=\"{j.key}\">\nCompany: {j.company}\nTitle: {j.title}\n{hint}"
            f"Location: {j.location or 'unknown'}\nBoard hints: {j.meta or '-'}\n"
            f"Description: {desc or '(not available - judge from title)'}\n</posting>")


def score(jobs: list, profile: str, model: str, batch_size: int = 12) -> tuple[dict, str | None]:
    """Returns ({job.key: Rating}, error). Uses Groq's free tier (GROQ_API_KEY); Claude instead if
    ANTHROPIC_API_KEY is set. No key or an API failure -> keyword ratings, and the run carries on."""
    if not jobs:
        return {}, None
    system = INSTRUCTIONS.format(profile=profile, examples=_examples())
    providers = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(("Claude", lambda chunk: _claude(system, chunk, model)))
    for name, url, key_env, m in OPENAI_COMPATIBLE:        # free tiers, best first
        if os.environ.get(key_env):
            providers.append((name, lambda chunk, url=url, key_env=key_env, m=m:
                              _openai_compatible(system, chunk, url, key_env, m)))
    if not providers:
        return {}, "No AI key set (NVIDIA_API_KEY, GROQ_API_KEY or ANTHROPIC_API_KEY) - used keyword ratings"
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


# Free OpenAI-compatible APIs, tried in order. (name, endpoint, key env var, model)
OPENAI_COMPATIBLE = [
    ("NVIDIA", "https://integrate.api.nvidia.com/v1/chat/completions", "NVIDIA_API_KEY", "moonshotai/kimi-k3"),
    ("Groq", "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "openai/gpt-oss-120b"),
]


def _openai_compatible(system: str, chunk: list, url: str, key_env: str, model: str) -> list[Rating]:
    schema_hint = ('Respond with JSON only, no prose: {"ratings": [{"id": str, "rating": "Strong"|"Good"|'
                   '"Stretch"|"No", "reason": str, "starts_before_summer_2027": bool}]}')
    body = json.dumps({
        "model": model, "temperature": 0.2, "max_tokens": 4000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system + "\n\n" + schema_hint},
                     {"role": "user", "content": "\n\n".join(_posting_block(j) for j in chunk)}],
    }).encode()
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {os.environ[key_env]}", "Content-Type": "application/json",
            "User-Agent": "job-scanner"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                content = json.load(r)["choices"][0]["message"]["content"]
            content = content[content.find("{"):content.rfind("}") + 1]   # tolerate stray prose
            return Ratings.model_validate_json(content).ratings
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:     # free-tier rate limit: wait it out
                time.sleep(float(e.headers.get("retry-after") or 20))
                continue
            raise RuntimeError(f"{e.code}: {e.read().decode('utf-8', 'replace')[:150]}")
    raise RuntimeError("rate limit persisted")
