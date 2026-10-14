"""Rates keyword survivors with an LLM (Claude, else free NVIDIA / Groq): Strong / Good / Stretch / No,
with a headline reason and a few pointers comparing the full posting against the candidate."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Literal

from pydantic import BaseModel

# What each rating means for the candidate, and what to do about it. Shown to the AI and on the page.
RATING_GUIDE = {
    "Strong": ("You meet every stated requirement and it's squarely your target: an entry-level "
               "strategy, management or sustainability consulting role, or in-house corporate "
               "strategy, based in NYC.",
               "Apply now and put it first. Look for a referral, tailor your resume to the posting, "
               "and lead with the GDI ESG work."),
    "Good": ("You meet the hard requirements, but it's a step to the side of your core lanes "
             "(operations or transformation consulting, a rotational program, strategic finance, "
             "business development), or one detail isn't confirmed.",
             "Worth applying. Tailor your resume to the angle it emphasises; slightly less urgent "
             "than Strong."),
    "Stretch": ("Realistic but a reach: one thing sits a little above you, like ~2 years "
                "asked for, a Consultant title with no stated level, or a specialised industry.",
                "Apply if you have time, ideally after networking with someone on the team. Lead "
                "with transferable proof (the GDI procurement and AI tool); expect lower odds."),
}


class Rating(BaseModel):
    id: str
    rating: Literal["Strong", "Good", "Stretch", "No"]
    reason: str
    pointers: list[str]
    undergrad_eligible: Literal["yes", "no", "unclear"]
    starts_before_summer_2027: bool


class Ratings(BaseModel):
    ratings: list[Rating]


INSTRUCTIONS = """You screen job postings for one early-career candidate. Their profile, including
hard requirements, is below. Hard requirements are strict, but the candidate does want to see a
few close calls: roles slightly out of range that they could still realistically get.

READ THE WHOLE POSTING before rating: title, location list, responsibilities, required AND
preferred qualifications, degree requirements, years of experience, start date or cohort,
citizenship/clearance/sponsorship terms, and travel. Then compare each against the candidate.

For each posting, return:
- rating (use the whole scale - a typical day should have more Good and Stretch than Strong):
  Strong  = {strong}
  Good    = {good}
  Stretch = {stretch}
  No      = misses a hard requirement: not NYC, senior, 3+ years, MBA / graduate degree or prior
            full-time work required, internship, wrong start date, technical/implementation work,
            or a bank or Kearney "Associate" (post-MBA titles).
  Examples: Strong - "Analyst, Functional & Operations Strategy (Deloitte), entry-level, NYC";
  Good - "2027 Leadership Development Program, rotational, NYC"; Stretch - "Strategy Consultant,
  ~2 years preferred, NYC".
- reason: ONE headline line (max ~15 words) for the rating.
- pointers: 3-5 short bullets (max ~20 words each) explaining the rating, each tying a specific
  fact FROM THE POSTING to a specific fact about THE CANDIDATE. Start each with "+ " (fits),
  "- " (gap or risk) or "~ " (neutral / unknown). Cover level/years, location, function/lane,
  and any requirement the candidate meets or misses. Quote the posting's own words where useful.
  Good: "+ Asks for CDP/EcoVadis or sustainability reporting - you ran both at GDI".
  Good: "- Prefers 1-2 years' consulting experience; you'd have one internship".
  Bad (too generic): "+ Good fit for candidate".
- undergrad_eligible: can someone finishing a BACHELOR'S degree in April 2027, with no further
  degree planned and only internship experience, apply? "yes" if it asks for a bachelor's /
  undergraduate degree or targets students, new grads or campus hires; "no" if it requires (or
  wants candidates pursuing) an MBA, master's, PhD, JD or other graduate degree, or prior
  full-time work; "unclear" if the posting doesn't say. A "no" means the rating must be No.
  Always include one pointer on the degree requirement, quoting the posting.
- starts_before_summer_2027: true if the posting needs a start before ~May 2027 or a cohort
  after 2027 (e.g. 2028 availability).
If a posting has a "Screening note", the keyword screen found it a step off-lane: rate it Good
or Stretch, not Strong.

Level calibration:
- At Deloitte, EY, PwC, KPMG, Accenture, IBM, Guidehouse, Slalom and West Monroe, a bare
  "Consultant" title is often the level above entry. Rate it No if the posting asks for 3+ years,
  Stretch if it asks ~2 years or says nothing about level, higher only if it says new grad /
  campus / 2027.
- If the description is missing, judge from the title, lean one step lower, and say so in a "~ "
  pointer.

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
    desc = (j.description or "").strip()[:9000]
    hint = f"Screening note: {j.reason}\n" if j.verdict == "stretch" else ""
    return (f"<posting id=\"{j.key}\">\nCompany: {j.company}\nTitle: {j.title}\n{hint}"
            f"Location: {j.location or 'unknown'}\nBoard hints: {j.meta or '-'}\n"
            f"Description: {desc or '(not available - judge from title)'}\n</posting>")


def score(jobs: list, profile: str, model: str, batch_size: int = 12) -> tuple[dict, str | None]:
    """Returns ({job.key: Rating}, error). Uses Groq's free tier (GROQ_API_KEY); Claude instead if
    ANTHROPIC_API_KEY is set. No key or an API failure -> keyword ratings, and the run carries on."""
    if not jobs:
        return {}, None
    system = INSTRUCTIONS.format(profile=profile, examples=_examples(),
                                 **{k.lower(): " ".join(v[0].split()) for k, v in RATING_GUIDE.items()})
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
                   '"Stretch"|"No", "reason": str, "pointers": [str, ...], '
                   '"undergrad_eligible": "yes"|"no"|"unclear", "starts_before_summer_2027": bool}]}')
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
