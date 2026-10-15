"""Rates keyword survivors with an LLM (Claude, else free NVIDIA / Groq).

For each posting the AI returns, separately: eligibility (can the candidate apply?), role fit
(Strong / Good / Stretch / No), 1-5 company-fit dimension scores (or null when there is no
evidence), and a short job-specific write-up. Company context comes from the cached profiles in
intel/ (see scanner/intel.py); the AI is told to use it as context, never as proof of how a
particular team works. scanner/fit.py turns the answer into the final classification."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Literal

from pydantic import BaseModel, ValidationError

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


NO_MEANING = ("Fails a hard requirement in your profile (location, level, degree, start date or "
              "off-target work). Never shown on the page.")


class Dims(BaseModel):
    learning: int | None
    support: int | None
    differentiation: int | None
    motivation: int | None


class Rating(BaseModel):
    id: str
    rating: Literal["Strong", "Good", "Stretch", "No"]
    reason: str
    pointers: list[str]
    undergrad_eligible: Literal["yes", "no", "unclear"]
    starts_before_summer_2027: bool
    eligibility: Literal["eligible", "ineligible", "unclear"]
    eligibility_note: str
    why_role: str
    why_company: str
    support: str
    support_evidence: Literal["verified_policy", "supporting_evidence", "tentative_signal", "unknown"]
    development: str
    investigate: list[str]
    dims: Dims


# Fields older or weaker models may leave out; filled before validation instead of losing the posting.
DEFAULTS = {"eligibility": "unclear", "eligibility_note": "", "why_role": "", "why_company": "",
            "support": "", "support_evidence": "unknown", "development": "", "investigate": [],
            "dims": {"learning": None, "support": None, "differentiation": None, "motivation": None},
            "pointers": [], "undergrad_eligible": "unclear", "starts_before_summer_2027": False}


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
- eligibility: "ineligible" ONLY if the posting clearly fails one of the candidate's HARD
  requirements (then rating must be No); "eligible" if the posting states or clearly implies
  requirements the candidate meets (level, degree, location, start); "unclear" if something
  material is unstated. Strong requires "eligible". Preferences are never eligibility.
- eligibility_note: one short line naming what decides eligibility or what is unknown.
If a posting has a "Screening note", the keyword screen found it a step off-lane: rate it Good
or Stretch, not Strong.

COMPANY CONTEXT. Each batch includes cached research on the companies involved, with every
finding labelled verified_policy (the company documents it), supporting_evidence (independent
context) or tentative_signal (indirect or self-reported). Rules:
- Use it as background. Judge the ROLE from the posting; a strong company never rescues a weak
  role, and a company with little research is never marked down for it.
- Never upgrade a label: a documented policy shows the policy exists, not that it works well in
  every team. Hybrid work alone is not evidence of flexibility. Marketing language is not evidence.
- Never invent policies, programs, culture or employee experiences. If neither the posting nor
  the context covers something, say it is unknown or leave the field empty.
Write-up fields (plain sentences, max ~30 words each, no hype, nothing generic that could be
pasted onto a competitor):
- why_role: how the actual responsibilities connect to the candidate's background and goals.
- why_company: one specific business, service-line, client or industry differentiator relevant
  to THIS role. Empty if nothing specific is known.
- support: the most relevant employee-support or flexibility point (beyond pay and standard
  benefits) and what it practically means for a new grad, phrased to match its label ("documents
  a ... policy", not "offers great ..."). Empty if none on file or in the posting.
- support_evidence: the label of the evidence behind `support`, or "unknown" if support is empty.
- development: credible learning, mentorship or responsibility this role or its program offers.
- investigate: 1-3 short items the candidate should check before applying (travel, workload,
  team placement, unclear eligibility, location rules). Only real uncertainties.
- dims: 1-5 (5 best) or null when there is no real evidence:
    learning        - training, mentorship, responsibility and a credible path from this role
    support         - documented employee support / flexibility (null if the context has none)
    differentiation - how specifically this role connects to what makes the company distinct
    motivation      - how plausible a specific, genuine interest is, given the candidate's lanes
                      and stated interests (not keyword overlap, not the company's enthusiasm)

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

CANDIDATE PREFERENCES (influence role fit explanations and company-fit scores, never eligibility;
an empty item means "not stated" - do not assume either way):
{preferences}

YOUR PAST VERDICTS - the candidate's own calls on real postings. Match their judgement on similar
postings; when a new posting differs materially (level, location, kind of work), judge it on its
own facts rather than copying the nearest example:
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


def preferences_text(prefs: dict) -> str:
    """preferences.toml [preferences] rendered for the prompt; blanks stated as 'not stated'."""
    out = []
    for k, v in (prefs or {}).items():
        val = "; ".join(v) if isinstance(v, list) else str(v)
        out.append(f"- {k.replace('_', ' ')}: {val or 'not stated'}")
    return "\n".join(out) or "(none stated)"


def system_prompt(profile: str, preferences: str = "") -> str:
    return INSTRUCTIONS.format(profile=profile, examples=_examples(), preferences=preferences or "(none stated)",
                               **{k.lower(): " ".join(v[0].split()) for k, v in RATING_GUIDE.items()})


def batches(jobs: list, size: int) -> list[list]:
    """Same-company postings travel together so their company context is sent once."""
    ordered = sorted(jobs, key=lambda j: (j.company, j.priority))
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]


def user_message(chunk: list, company_context: dict | None) -> str:
    ctx = company_context or {}
    blocks = [ctx[c] for c in dict.fromkeys(j.company for j in chunk) if ctx.get(c)]
    head = ("COMPANY CONTEXT\n" + "\n".join(blocks) + "\n\nPOSTINGS\n") if blocks else ""
    return head + "\n\n".join(_posting_block(j) for j in chunk)


def parse_ratings(content: str, ids: set[str]) -> tuple[list[Rating], list[str]]:
    """Validates each rating on its own: one malformed entry costs that posting, not the batch.
    Unknown ids are ignored. Raises only if the reply has no usable JSON at all."""
    data = json.loads(content[content.find("{"):content.rfind("}") + 1])
    items = data.get("ratings") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("reply has no 'ratings' list")
    good, problems = [], []
    for it in items:
        if not isinstance(it, dict) or str(it.get("id")) not in ids:
            problems.append(f"ignored entry for unknown id {str(it)[:60] if not isinstance(it, dict) else it.get('id')}")
            continue
        it = {**DEFAULTS, **{k: v for k, v in it.items() if v is not None or k == "dims"}}
        it["dims"] = {**DEFAULTS["dims"], **(it.get("dims") if isinstance(it.get("dims"), dict) else {})}
        for k in ("rating", "eligibility", "undergrad_eligible"):   # "strong" -> "Strong" etc.
            if isinstance(it.get(k), str):
                it[k] = it[k].strip().capitalize() if k == "rating" else it[k].strip().lower()
        try:
            good.append(Rating.model_validate(it))
        except ValidationError as e:
            problems.append(f"invalid rating for {it.get('id')}: {str(e).splitlines()[0][:100]}")
    return good, problems


def score(jobs: list, profile: str, model: str, batch_size: int = 12,
          company_context: dict | None = None, preferences: str = "") -> tuple[dict, str | None]:
    """Returns ({job.key: Rating}, error). Claude if ANTHROPIC_API_KEY is set, then the free
    providers. Postings left without a rating are the caller's to retry next scan."""
    if not jobs:
        return {}, None
    system = system_prompt(profile, preferences)
    providers = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(("Claude", lambda msg, ids: _claude(system, msg, model)))
    for name, url, key_env, m in OPENAI_COMPATIBLE:        # free tiers, best first
        if os.environ.get(key_env):
            providers.append((name, lambda msg, ids, url=url, key_env=key_env, m=m:
                              _openai_compatible(system, msg, ids, url, key_env, m)))
    if not providers:
        return {}, "No AI key set (NVIDIA_API_KEY, GROQ_API_KEY or ANTHROPIC_API_KEY) - postings queued for rating"
    out, errors, dead = {}, [], set()
    for chunk in batches(jobs, batch_size):
        msg, ids = user_message(chunk, company_context), {j.key for j in chunk}
        for name, call in providers:             # Claude first; free providers if it fails
            if name in dead:
                continue
            try:
                got = call(msg, ids)
                for r in got:
                    if r.id in ids:
                        out[r.id] = r
                if got:
                    break
                errors.append(f"{name}: no usable ratings in reply")
            except Exception as e:
                msg_ = str(e)
                errors.append(f"{name}: {msg_[:160]}")
                if "credit balance" in msg_ or "401" in msg_[:40]:
                    dead.add(name)               # account-level problem: stop retrying it
    missing = len(jobs) - len(out)
    if missing:
        errors.append(f"{missing} posting(s) left unrated this scan")
    return out, "; ".join(dict.fromkeys(errors)) or None


def _claude(system: str, message: str, model: str) -> list[Rating]:
    import anthropic
    # The system prompt (instructions + profile + examples) is identical across batches: cache it.
    resp = anthropic.Anthropic().messages.parse(
        model=model, max_tokens=12000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": message}],
        output_format=Ratings)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"Claude returned no ratings (stop_reason={resp.stop_reason})")
    return resp.parsed_output.ratings


# Free OpenAI-compatible APIs, tried in order. (name, endpoint, key env var, model)
OPENAI_COMPATIBLE = [
    ("NVIDIA", "https://integrate.api.nvidia.com/v1/chat/completions", "NVIDIA_API_KEY", "moonshotai/kimi-k3"),
    ("Groq", "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "openai/gpt-oss-120b"),
]


SCHEMA_HINT = ('Respond with JSON only, no prose: {"ratings": [{"id": str, "rating": "Strong"|"Good"|'
               '"Stretch"|"No", "reason": str, "pointers": [str, ...], '
               '"undergrad_eligible": "yes"|"no"|"unclear", "starts_before_summer_2027": bool, '
               '"eligibility": "eligible"|"ineligible"|"unclear", "eligibility_note": str, '
               '"why_role": str, "why_company": str, "support": str, "support_evidence": '
               '"verified_policy"|"supporting_evidence"|"tentative_signal"|"unknown", '
               '"development": str, "investigate": [str, ...], "dims": {"learning": 1-5|null, '
               '"support": 1-5|null, "differentiation": 1-5|null, "motivation": 1-5|null}}]}')


def _openai_compatible(system: str, message: str, ids: set[str], url: str, key_env: str,
                       model: str) -> list[Rating]:
    body = json.dumps({
        "model": model, "temperature": 0.2, "max_tokens": 6000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system + "\n\n" + SCHEMA_HINT},
                     {"role": "user", "content": message}],
    }).encode()
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {os.environ[key_env]}", "Content-Type": "application/json",
            "User-Agent": "job-scanner"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                content = json.load(r)["choices"][0]["message"]["content"]
            good, problems = parse_ratings(content, ids)   # tolerates stray prose and bad entries
            for p in problems:
                print(f"rater: {p}", file=sys.stderr)
            return good
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:     # free-tier rate limit: wait it out
                time.sleep(float(e.headers.get("retry-after") or 20))
                continue
            raise RuntimeError(f"{e.code}: {e.read().decode('utf-8', 'replace')[:150]}")
    raise RuntimeError("rate limit persisted")
