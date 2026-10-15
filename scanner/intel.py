"""Company intelligence: one reusable, evidence-labelled profile per company (intel/<slug>.json).

Profiles are researched once (by hand, or by a Claude Code session following intel/README.md) and
reused by every scan, so a daily run never researches a company from scratch. This module:

- loads and validates profiles (a bad finding is dropped, a bad file is skipped - never a crash)
- works out freshness (fresh / stale / expired) from the profile's own dates
- renders a compact, size-bounded block for the AI rater (only for companies in the batch)
- renders the company-level summary the results page falls back to
- `python -m scanner.intel status|check|draft`: cheap upkeep that runs on the free tier
  (see the bottom of this file and intel/README.md)

Evidence labels (what a finding can claim):
  verified_policy      explicitly documented by the company itself (policy, program, benefit page)
  supporting_evidence  credible independent context about how things work in practice
  tentative_signal     relevant but indirect, partial, self-reported or uncorroborated
Anything with no reliable evidence goes in `unknowns`, never in a finding.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEL = ROOT / "intel"
CHECKS = ROOT / "state" / "intel_checks.json"

EVIDENCE = {"verified_policy": "Verified policy", "supporting_evidence": "Supporting evidence",
            "tentative_signal": "Tentative signal"}
EVIDENCE_RANK = {"verified_policy": 0, "supporting_evidence": 1, "tentative_signal": 2}
SECTIONS = {
    "differentiators": "What sets it apart",
    "early_career": "Early-career development",
    "employee_support": "Employee support beyond standard benefits",
    "flexibility": "Flexibility and working arrangements",
    "culture": "Culture and working environment",
    "tradeoffs": "Trade-offs to weigh",
}
SOURCE_TYPES = ("official", "independent", "employee_reviews", "other")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", " and ")).strip("-")


def intel_slug(company: dict) -> str:
    """companies.toml may set `intel = "<slug>"` to share or rename a profile."""
    return company.get("intel") or slug(company["name"])


# ---------------------------------------------------------------- loading + validation

def validate(d: dict) -> tuple[dict | None, list[str]]:
    """Returns (cleaned profile or None, problems). Bad findings are dropped individually so one
    typo can't hide a whole company; a profile without a name or sources is rejected."""
    problems = []
    if not isinstance(d, dict) or not isinstance(d.get("company"), str) or not d["company"].strip():
        return None, ["missing company name"]
    srcs = {}
    for s in d.get("sources") or []:
        if not isinstance(s, dict) or not str(s.get("url", "")).startswith(("https://", "http://")):
            problems.append(f"source dropped (bad url): {str(s)[:80]}")
            continue
        if s.get("type") not in SOURCE_TYPES:
            problems.append(f"source {s.get('id')} has unknown type {s.get('type')!r}; treated as 'other'")
            s = {**s, "type": "other"}
        srcs[str(s.get("id"))] = s
    for k in ("researched_on", "refresh_after"):
        if not DATE.match(str(d.get(k, ""))):
            problems.append(f"{k} missing or not YYYY-MM-DD")
    out = {k: v for k, v in d.items() if k not in SECTIONS and k != "sources"}
    out["sources"] = list(srcs.values())
    for sec in SECTIONS:
        keep = []
        for f in d.get(sec) or []:
            if not isinstance(f, dict) or not str(f.get("text", "")).strip():
                problems.append(f"{sec}: empty finding dropped")
                continue
            if f.get("evidence") not in EVIDENCE:
                problems.append(f"{sec}: finding with label {f.get('evidence')!r} dropped: {f['text'][:60]}")
                continue
            ids = [str(i) for i in f.get("sources") or []]
            if not ids or any(i not in srcs for i in ids):
                problems.append(f"{sec}: finding without a valid source dropped: {f['text'][:60]}")
                continue
            keep.append({**f, "sources": ids})
        out[sec] = keep
    out["unknowns"] = [u for u in d.get("unknowns") or [] if isinstance(u, str) and u.strip()]
    return out, problems


def load_all(directory: Path = INTEL) -> tuple[dict[str, dict], list[str]]:
    """{slug: profile}, problems. Never raises: a broken file is reported and skipped."""
    profiles, problems = {}, []
    if not directory.exists():
        return profiles, problems
    for p in sorted(directory.glob("*.json")):
        try:
            prof, probs = validate(json.loads(p.read_text()))
        except (OSError, ValueError) as e:
            problems.append(f"{p.name}: unreadable ({e})")
            continue
        problems += [f"{p.name}: {x}" for x in probs]
        if prof:
            prof["_slug"] = p.stem
            profiles[p.stem] = prof
    return profiles, problems


def freshness(prof: dict, today: dt.date, checks: dict | None = None) -> str:
    """fresh   within refresh_after
    stale   past refresh_after (still used, page says the research may be dated)
    changed an official page it relies on has changed since it was researched
    """
    entry = (checks or {}).get(prof.get("_slug", ""), {})
    if entry.get("changed"):
        return "changed"
    try:
        return "fresh" if today <= dt.date.fromisoformat(prof["refresh_after"]) else "stale"
    except (KeyError, ValueError):
        return "stale"


def has_evidence(prof: dict | None, *sections: str) -> bool:
    return bool(prof) and any(prof.get(s) for s in sections)


# ---------------------------------------------------------------- rendering

def _best(findings: list[dict], n: int) -> list[dict]:
    return sorted(findings, key=lambda f: EVIDENCE_RANK[f["evidence"]])[:n]


def prompt_block(prof: dict | None, company: str, max_chars: int = 2200) -> str:
    """Compact company context for the AI rater, bounded so prompts stay small."""
    if not prof:
        return (f"<company name=\"{company}\">No researched profile. Judge only from the posting; "
                "do not guess about culture or support, and do not penalise the company for the "
                "missing research.</company>")
    lines = [f"<company name=\"{company}\" researched=\"{prof.get('researched_on', '?')}\">",
             f"Overview: {prof.get('overview', '')}"]
    for sec, n in (("differentiators", 3), ("early_career", 3), ("employee_support", 3),
                   ("flexibility", 2), ("culture", 2), ("tradeoffs", 3)):
        for f in _best(prof.get(sec, []), n):
            lines.append(f"[{sec} | {f['evidence']}] {f['text']}")
    for sec in ("employee_support", "flexibility"):
        if not prof.get(sec):
            lines.append(f"[{sec}] no evidence on file - treat as unknown, not as a negative")
    for u in prof.get("unknowns", [])[:3]:
        lines.append(f"[unknown] {u}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rsplit("\n", 1)[0] + "\n[...truncated]"
    return out + "\n</company>"


def summary(prof: dict | None) -> dict:
    """Company-level facts for the page, used when a job has no job-specific write-up."""
    if not prof:
        return {}
    pick = lambda sec: (_best(prof.get(sec, []), 1) or [None])[0]
    srcs = {s["id"]: s for s in prof.get("sources", [])}
    used = []
    for sec in ("differentiators", "employee_support", "flexibility", "early_career", "tradeoffs"):
        for f in _best(prof.get(sec, []), 2):
            used += [i for i in f["sources"] if i not in used]
    return {
        "slug": prof["_slug"], "overview": prof.get("overview", ""),
        "differentiator": pick("differentiators"), "support": pick("employee_support") or pick("flexibility"),
        "development": pick("early_career"), "tradeoff": pick("tradeoffs"),
        "researched_on": prof.get("researched_on", ""),
        "sources": [srcs[i] for i in used[:4] if i in srcs],
    }


# ---------------------------------------------------------------- upkeep (free tier, no web search)
#
# `check`: re-fetches each profile's official `watch_urls` and compares a hash of the page text with
#   the last check. Unchanged -> the profile's official basis still stands (recorded as confirmed).
#   Changed -> the profile is flagged "changed" on the page until someone re-researches it.
#   Costs one HTTP GET per URL and no AI calls.
# `draft`: for a company with no profile, fetches its configured official URLs and asks the AI to
#   extract findings, keeping only those whose supporting quote appears word for word in the page.
#   The result is written to intel/drafts/ for review; it is never published automatically.

def _page_text(url: str) -> str:
    from .adapters import get_text, strip_html
    h = get_text(url)
    h = re.sub(r"<(nav|header|footer|script|style|noscript)\b.*?</\1>", " ", h, flags=re.S | re.I)
    return re.sub(r"\s+", " ", strip_html(h)).strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def check(profiles: dict, checks: dict, today: dt.date, every_days: int, max_profiles: int,
          only: str | None = None, fetch=_page_text) -> list[str]:
    """Updates `checks` in place; returns log lines. Bounded by max_profiles per run."""
    log, done = [], 0
    for s, prof in sorted(profiles.items()):
        if only and s != only and prof["company"] != only:
            continue
        entry = checks.setdefault(s, {})
        last = entry.get("checked_on", "")
        if not only and last and (today - dt.date.fromisoformat(last)).days < every_days:
            continue
        if done >= max_profiles:
            break
        done += 1
        urls = prof.get("watch_urls") or []
        hashes, changed, errors = entry.setdefault("hashes", {}), [], []
        for u in urls:
            try:
                h = hashlib.sha256(_norm(fetch(u)).encode()).hexdigest()[:16]
            except Exception as e:  # a blocked page proves nothing either way
                errors.append(f"{u}: {str(e)[:80]}")
                continue
            if u in hashes and hashes[u] != h:
                changed.append(u)
            hashes[u] = h
        entry["checked_on"] = today.isoformat()
        if changed:
            entry["changed"] = sorted(set(entry.get("changed", []) + changed))
        elif not errors and urls:
            entry["confirmed_on"] = today.isoformat()
        entry["errors"] = errors
        log.append(f"{prof['company']}: {len(urls)} page(s), {len(changed)} changed, {len(errors)} unreachable")
    return log


DRAFT_INSTRUCTIONS = """You extract facts about one employer from the text of its own official web
pages. Return JSON only: {"findings": [{"section": one of differentiators | early_career |
employee_support | flexibility | culture, "text": a plain, specific statement (max 30 words),
"quote": an EXACT substring of the page text (8-40 words) that supports it, "url": the page}]}.
Rules: only facts the text states; no adjectives the page doesn't support; skip slogans ("people
first", "great culture"); prefer concrete programs, policies, eligibility and numbers; at most 12
findings. If the pages say nothing useful, return {"findings": []}."""


def draft(company: str, urls: list[str], today: dt.date, ask=None, fetch=_page_text) -> dict:
    """Builds a draft profile from official pages. Every kept finding has a verified quote; findings
    about culture are labelled tentative (a company describing itself), the rest verified_policy."""
    pages = {}
    for u in urls[:4]:
        try:
            pages[u] = fetch(u)[:6000]
        except Exception as e:
            pages[u] = ""
            print(f"draft: {u} unreachable: {e}", file=sys.stderr)
    text = "\n\n".join(f"<page url=\"{u}\">\n{t}\n</page>" for u, t in pages.items() if t)
    found = (ask or _ask_json)(DRAFT_INSTRUCTIONS, text) if text else {"findings": []}
    sources, by_url, prof = [], {}, {
        "company": company, "researched_on": today.isoformat(),
        "refresh_after": (today + dt.timedelta(days=90)).isoformat(),
        "method": "automated draft from official pages (quotes verified, NOT reviewed)",
        "overview": "", "watch_urls": list(pages), "unknowns": [
            "Not reviewed by a person; independent evidence about day-to-day practice not gathered."]}
    for f in found.get("findings") or []:
        page = pages.get(f.get("url", ""), "")
        if f.get("section") not in SECTIONS or not f.get("quote") or _norm(f["quote"]) not in _norm(page):
            continue                            # unverifiable quote: drop rather than risk invention
        if f["url"] not in by_url:
            by_url[f["url"]] = str(len(sources) + 1)
            sources.append({"id": by_url[f["url"]], "url": f["url"], "title": f["url"],
                            "type": "official", "accessed": today.isoformat()})
        prof.setdefault(f["section"], []).append({
            "text": f["text"].strip()[:300], "quote": f["quote"].strip()[:400],
            "evidence": "tentative_signal" if f["section"] == "culture" else "verified_policy",
            "sources": [by_url[f["url"]]]})
    prof["sources"] = sources
    return prof


def _ask_json(system: str, user: str) -> dict:
    """One call to the first free OpenAI-compatible provider with a key (same ones the rater uses)."""
    import urllib.request
    from .scorer import OPENAI_COMPATIBLE
    for name, url, key_env, model in OPENAI_COMPATIBLE:
        if not os.environ.get(key_env):
            continue
        body = json.dumps({"model": model, "temperature": 0, "max_tokens": 3000,
                           "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {os.environ[key_env]}", "Content-Type": "application/json",
            "User-Agent": "job-scanner"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                c = json.load(r)["choices"][0]["message"]["content"]
            return json.loads(c[c.find("{"):c.rfind("}") + 1])
        except Exception as e:
            print(f"draft: {name} failed: {str(e)[:120]}", file=sys.stderr)
    return {"findings": []}


def _load_checks() -> dict:
    try:
        return json.loads(CHECKS.read_text()) if CHECKS.exists() else {}
    except ValueError:
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scanner.intel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="list profiles, freshness, problems and companies with no profile")
    c = sub.add_parser("check", help="re-check official pages of due profiles (no AI calls)")
    c.add_argument("--company", help="check this one now, whatever its schedule")
    d = sub.add_parser("draft", help="draft a profile for a company from its official pages")
    d.add_argument("company")
    d.add_argument("urls", nargs="+")
    args = ap.parse_args(argv)

    settings = tomllib.loads((ROOT / "settings.toml").read_text()).get("intel", {})
    today = dt.date.today()
    profiles, problems = load_all()
    checks = _load_checks()
    if args.cmd == "status":
        companies = tomllib.loads((ROOT / "companies.toml").read_text())["company"]
        want = {intel_slug(c): c["name"] for c in companies if c.get("enabled", True)}
        for s, name in sorted(want.items(), key=lambda x: x[1]):
            p = profiles.get(s)
            print(f"{name:32} " + (f"{freshness(p, today, checks):8} researched {p['researched_on']} "
                                   f"refresh after {p['refresh_after']}" if p else "NO PROFILE"))
        for x in problems:
            print("problem:", x)
        return 0
    if args.cmd == "check":
        for line in check(profiles, checks, today, int(settings.get("check_every_days", 30)),
                          int(settings.get("max_checks_per_run", 6)), args.company):
            print(line)
        CHECKS.parent.mkdir(exist_ok=True)
        tmp = CHECKS.with_suffix(".tmp")
        tmp.write_text(json.dumps(checks, indent=1, sort_keys=True))
        os.replace(tmp, CHECKS)
        return 0
    if args.cmd == "draft":
        prof = draft(args.company, args.urls, today)
        out = INTEL / "drafts" / f"{slug(args.company)}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(prof, indent=1) + "\n")
        n = sum(len(prof.get(s, [])) for s in SECTIONS)
        print(f"{n} verified findings -> {out.relative_to(ROOT)} (review, then move to intel/)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
