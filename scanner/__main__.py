"""Daily run:  python -m scanner [--dry-run] [--no-sms] [--only NAME ...]

fetch every company -> keep postings not seen before (plus any still waiting for a rating) ->
keyword rules -> AI rates the survivors with cached company context -> write state/ + site/ ->
alert on new matches.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from . import adapters, filters, fit, intel, notify, report, scorer

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
SITE = ROOT / "site"
TZ = ZoneInfo("America/New_York")
RANK = {"Strong": 0, "Good": 1, "Stretch": 2}
LOG_DAYS = 30             # scan audit log (site/log.html) keeps this many days
FORGET_AFTER_DAYS = 180   # drop seen-IDs not listed for this long
MAX_AI = 250              # default cap on postings rated per run (settings: scoring.max_per_run)
MAX_RATING_ATTEMPTS = 4   # scans a posting waits for an AI rating before it's shown unrated
CLOSED_DAYS = 30          # closed postings stay on the page this long
DISMISS_DAYS = 7          # "Not interested" cards disappear from the page after this long
PARTIAL_PLATFORMS = {"avature_rss"}   # feeds too partial to detect a posting closing
DROP_FRACTION = 0.5       # a board listing under half its last count is treated as incomplete
JOB_FIELDS = ("pointers", "undergrad", "eligibility", "eligibility_note", "why_role", "why_company",
              "support", "support_evidence", "development", "investigate", "company_fit",
              "fit_score", "adjustments", "provisional")


def short_code(key: str) -> str:
    n = int(hashlib.sha1(key.encode()).hexdigest(), 16)
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
    out = ""
    for _ in range(6):
        n, r = divmod(n, len(alphabet))
        out += alphabet[r]
    return out


def load_json(p: Path, default, critical: bool = False, notes: list | None = None):
    """Missing file -> default. A corrupt critical file (seen / tracked) stops the run with a clear
    message instead of silently forgetting history (which would re-alert everything); a corrupt
    non-critical file falls back to the default and says so in the scan log."""
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except ValueError as e:
        if critical:
            raise SystemExit(f"{p.relative_to(ROOT)} is not valid JSON ({e}). Restore it with "
                             f"`git checkout HEAD~1 -- {p.relative_to(ROOT)}` before the next scan.")
        msg = f"{p.name} was unreadable ({e}); started it afresh"
        print("STATE:", msg, file=sys.stderr)
        if notes is not None:
            notes.append(msg)
        return default


def write_json(p: Path, data, **kw) -> None:
    """Write-then-rename, so a crash mid-write can never leave half a file behind."""
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, **kw))
    os.replace(tmp, p)


def source_label(c: dict) -> str:
    return c["name"] + (f" ({c['site']})" if c.get("site") and c["platform"] == "workday" else "")


def fetch_all(companies: list[dict]) -> tuple[list, dict]:
    sources, jobs = {}, []

    def run(c):
        return c, adapters.ADAPTERS[c["platform"]](c)

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(run, c) for c in companies]
        for f in cf.as_completed(futs):
            c = companies[futs.index(f)]
            label = source_label(c)
            try:
                _, js = f.result()
                sources[label] = {"fetched": len(js), "new": 0, "company": c["name"]}
                if c.get("_truncated"):
                    sources[label]["incomplete"] = "hit the page limit before the end of the board"
                jobs.extend(js)
            except Exception as e:  # one broken board must not sink the run
                sources[label] = {"fetched": 0, "new": 0, "company": c["name"], "error": str(e)[:300]}
    return jobs, sources


def flag_drops(sources: dict, prev_sources: dict, fraction: float = DROP_FRACTION) -> None:
    """A board that suddenly lists far fewer postings than last scan (broken markup, a partial
    response) is marked incomplete, so its 'missing' postings aren't closed."""
    for label, s in sources.items():
        before = (prev_sources.get(label) or {}).get("fetched", 0)
        if "error" in s or "incomplete" in s or before < 10:
            continue
        if s["fetched"] < before * fraction:
            s["incomplete"] = f"listed {s['fetched']} vs {before} last scan"


def completeness(sources: dict) -> tuple[set, dict]:
    """(companies whose every board answered in full, {company: why not} for the rest)."""
    bad = {}
    for label, s in sources.items():
        why = s.get("error") and "board failed" or s.get("incomplete")
        if why:
            bad.setdefault(s["company"], f"{label}: {why}")
    complete = {s["company"] for s in sources.values()} - set(bad)
    return complete, bad


def posting_gone(url: str) -> bool:
    """True only on a definite 404/410 from the posting page; anything else proves nothing."""
    try:
        adapters.get_text(url)
        return False
    except RuntimeError as e:
        return any(f"HTTP Error {c}" in str(e) for c in (404, 410))


def update_tracked(tracked: dict, listed: dict, scanned: set, complete: set, partial: set,
                   today: str, gone=posting_gone) -> list[dict]:
    """Refreshes matches still listed and closes ones missing from a COMPLETE listing twice in a
    row (or whose posting page is gone). Incomplete or failed boards never close anything."""
    closed_now = []
    for key, t in tracked.items():
        if t["company"] not in scanned:
            continue
        if key in listed:
            j = listed[key]
            t.update(misses=0, closed_on=None, last_listed=today, title=j.title or t["title"],
                     url=j.url or t["url"], posted=j.posted or t.get("posted", ""))
            if j.location and not re.fullmatch(r"(?i)\d+ locations?|multiple locations", j.location.strip()):
                t["location"] = j.location      # listing-level "4 Locations" would lose the city list
        elif t["company"] in complete and t["company"] not in partial and not t.get("closed_on"):
            t["misses"] = t.get("misses", 0) + 1
            if t["misses"] >= 2 or gone(t["url"]):
                t["closed_on"] = today
                closed_now.append(t)
    return closed_now


def apply_ratings(candidates: list, ratings: dict, support_known: dict, weights: dict):
    """(matches, unrated jobs, keys the AI excluded). Fills each match's job-specific fields."""
    matches, unrated, excluded = [], [], []
    for j in candidates:
        r = ratings.get(j.key)
        if not r:
            unrated.append(j)
            continue
        rating, notes = fit.final_rating(r, j.verdict)
        if rating is None:
            excluded.append(j.key)              # the AI's "no" is final
            continue
        label, score = fit.company_fit(r.dims.model_dump(), weights, support_known.get(j.company, False))
        j.rating, j.reason = rating, r.reason
        j.extra.update(
            pointers=[p.strip() for p in r.pointers if p.strip()][:6], undergrad=r.undergrad_eligible,
            eligibility=r.eligibility, eligibility_note=r.eligibility_note.strip(),
            why_role=r.why_role.strip(), why_company=r.why_company.strip(), support=r.support.strip(),
            support_evidence=r.support_evidence if r.support.strip() else "unknown",
            development=r.development.strip(), investigate=[x.strip() for x in r.investigate if x.strip()][:3],
            company_fit=label, fit_score=score, adjustments=notes, provisional=False)
        matches.append(j)
    return matches, unrated, excluded


def handle_unrated(unrated: list, pending: dict, attempted: set, today: str) -> list:
    """Postings the AI didn't rate this scan are kept, never dropped:
    - on-lane with early-career evidence (clear) or a known step off-lane (stretch): shown now with
      a provisional keyword rating, and re-rated on later scans
    - level unconfirmed (candidate): wait for a rating; after MAX_RATING_ATTEMPTS failed scans
      they're shown as provisional Stretch so nothing on-lane silently disappears
    Updates `pending` in place and returns the provisional matches."""
    shown = []
    for j in unrated:
        p = pending.setdefault(j.key, {"attempts": 0, "first": today})
        p["attempts"] += 1 if j.key in attempted else 0
        p["last"] = today
        exhausted = p["attempts"] >= MAX_RATING_ATTEMPTS
        if j.verdict in ("clear", "stretch"):
            j.rating = "Good" if j.verdict == "clear" else "Stretch"
            j.reason = ("Entry-level consulting/strategy role in NYC" if j.verdict == "clear"
                        else f"Close fit: {j.reason}") + " (AI rating pending)."
        elif exhausted:
            j.rating = "Stretch"
            j.reason = f"On-lane title in NYC; AI rating failed {p['attempts']} scans - check the level by hand."
        else:
            continue
        j.extra.update(provisional=True, company_fit="Unknown", eligibility="unclear")
        shown.append(j)
        if exhausted:
            pending.pop(j.key, None)            # stop retrying; it stays on the page as provisional
    return shown


def match_record(j) -> dict:
    return {"code": j.extra["code"], "company": j.company, "priority": j.priority, "title": j.title,
            "location": j.location, "url": j.url, "posted": j.posted, "rating": j.rating,
            "reason": j.reason, "early_start": j.early_start, "key": j.key,
            **{k: j.extra[k] for k in JOB_FIELDS if k in j.extra}}


def sort_key(m: dict):
    return (m["priority"], RANK.get(m["rating"], 9), fit.FIT_ORDER.get(m.get("company_fit"), 1),
            m["company"], m["title"])


def open_matches(tracked: dict) -> list[dict]:
    """The page's one long list: every open match, newest find first, then priority, fit, company fit."""
    rows = [t for t in tracked.values() if not t.get("closed_on")]
    rows.sort(key=sort_key)
    rows.sort(key=lambda t: t.get("first_seen", ""), reverse=True)    # stable: keeps the order above
    return rows


def company_intel(companies: list[dict], today: dt.date) -> tuple[dict, dict, list[str]]:
    """({company name: profile}, {name: summary for the page}, problems)."""
    profiles, problems = intel.load_all()
    checks = load_json(STATE / "intel_checks.json", {})
    by_name, summaries = {}, {}
    for c in companies:
        p = profiles.get(intel.intel_slug(c))
        if p and c["name"] not in by_name:
            by_name[c["name"]] = p
            summaries[c["name"]] = {**intel.summary(p), "freshness": intel.freshness(p, today, checks)}
    return by_name, summaries, problems


def check_ai() -> int:
    settings = tomllib.loads((ROOT / "settings.toml").read_text())
    sample = adapters.Job(company="Deloitte", priority=1, job_id="check", title="Strategy Analyst",
                          location="New York, NY", url="", description="Entry-level, class of 2027.")
    ratings, err = scorer.score([sample], (ROOT / "profile.md").read_text(),
                                settings["scoring"]["model"], 1)
    r = ratings.get(sample.key)
    order = [n for n, k in (("Claude", "ANTHROPIC_API_KEY"), ("NVIDIA", "NVIDIA_API_KEY"),
                            ("Groq", "GROQ_API_KEY")) if os.environ.get(k)]
    failed = {e.split(":")[0] for e in (err or "").split("; ") if e}
    who = next((n for n in order if n not in failed), "nobody") if r else "nobody"
    print(f"AI check: answered by {who}; rating={r.rating if r else '-'}; reason={r.reason if r else '-'}")
    if err:
        print(f"AI check errors: {err}")
    return 0 if r else 1


def test_email() -> int:
    settings = tomllib.loads((ROOT / "settings.toml").read_text())
    best = open_matches(load_json(STATE / "tracked.json", {}))
    best.sort(key=lambda t: RANK.get(t["rating"], 9))
    mail = notify.compose_email(best[:1], settings["site"]["url"])
    if not mail:
        print("no open matches to use as a sample")
        return 1
    mail["subject"] = "[Test] " + mail["subject"]
    errs = notify.send_email(mail, settings["email"]["to"])
    print("email test:", "sent to " + settings["email"]["to"] if not errs else "; ".join(errs))
    return 1 if errs else 0


def enrich(j):
    """Workday and HTML boards list no description: fetch the posting page for keyword survivors."""
    try:
        platform = j.extra.get("platform")
        if "detail" in j.extra:
            adapters.workday_detail(j)
        elif "oracle" in j.extra:
            adapters.oracle_detail(j)
        elif platform == "phenom":
            adapters.phenom_detail(j)
        elif platform in ("avature", "radancy", "yello", "kpmg", "avature_rss", "successfactors", "breezy"):
            adapters.page_detail(j)
    except Exception:
        pass                                    # keep the listing-level data
    return j


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no state/site writes, print alerts")
    ap.add_argument("--no-sms", action="store_true", help="write state/site but send no alerts or email")
    ap.add_argument("--only", nargs="*", help="limit to these company names")
    ap.add_argument("--check-ai", action="store_true", help="rate one sample posting and report which AI answered")
    ap.add_argument("--test-email", action="store_true", help="email the best open match as a test")
    args = ap.parse_args(argv)
    if args.check_ai:
        return check_ai()
    if args.test_email:
        return test_email()

    settings = tomllib.loads((ROOT / "settings.toml").read_text())
    all_companies = [c for c in tomllib.loads((ROOT / "companies.toml").read_text())["company"]
                     if c.get("enabled", True)]
    companies = [c for c in all_companies if not args.only or c["name"] in args.only]
    profile = (ROOT / "profile.md").read_text()
    prefs = tomllib.loads((ROOT / "preferences.toml").read_text()) if (ROOT / "preferences.toml").exists() else {}
    f = settings["filters"]
    sc = settings["scoring"]
    now = dt.datetime.now(TZ)
    today = now.date().isoformat()
    notes: list[str] = []

    seen: dict = load_json(STATE / "seen.json", {}, critical=True)
    tracked: dict = load_json(STATE / "tracked.json", {}, critical=True)
    runs: list = load_json(STATE / "runs.json", [], notes=notes)
    pending: dict = load_json(STATE / "pending.json", {}, notes=notes)
    status: dict = load_json(STATE / "status.json", {}, notes=notes)
    first_run = not seen
    by_name, summaries, intel_problems = company_intel(all_companies, now.date())
    if intel_problems:
        notes.append(f"{len(intel_problems)} company-profile problem(s), e.g. {intel_problems[0]}")
    no_profile = sorted({c["name"] for c in companies} - set(by_name))

    jobs, sources = fetch_all(companies)
    prev = next((r.get("sources", {}) for r in reversed(runs) if r.get("sources")), {})
    flag_drops(sources, prev)
    complete, incomplete = completeness(sources)
    scanned = complete | set(incomplete)
    print(f"fetched {len(jobs)} postings from {len(sources)} sources", file=sys.stderr)

    # Same posting can surface from two queries or two sites of one company.
    uniq = {}
    for j in jobs:
        uniq.setdefault(j.key, j)
    jobs = list(uniq.values())
    new = [j for j in jobs if j.key not in seen or j.key in pending]
    retried = sum(1 for j in new if j.key in seen)
    for j in new:
        for s in sources.values():
            if s["company"] == j.company and "error" not in s:
                s["new"] += 1
                break

    # Keyword pass. Workday listings carry no description, so fetch it for survivors and re-check.
    first_pass = [j for j in new if filters.title_ok(j, f)]
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        first_pass = list(ex.map(enrich, first_pass))
    candidates = []
    for j in first_pass:
        verdict, why = filters.classify(j, f)
        if verdict != "reject":
            j.verdict, j.reason = verdict, why
            candidates.append(j)
        else:
            pending.pop(j.key, None)
    print(f"{len(new)} new ({retried} retried), {len(candidates)} passed keyword rules", file=sys.stderr)

    cap = int(sc.get("max_per_run", MAX_AI))
    to_score = sorted(candidates, key=lambda j: (j.priority, j.verdict != "clear"))[:cap]
    context = {c: intel.prompt_block(by_name.get(c), c, int(settings.get("intel", {}).get("prompt_chars", 2200)))
               for c in {j.company for j in to_score}}
    ratings, err = scorer.score(to_score, profile, sc["model"], sc.get("batch_size", 12),
                                company_context=context,
                                preferences=scorer.preferences_text(prefs.get("preferences", {})))
    if err:
        notes.append(err)
    if len(candidates) > cap:
        notes.append(f"{len(candidates) - cap} candidates over the per-run cap wait for the next scan")

    support_known = {n: intel.has_evidence(p, "employee_support", "flexibility") for n, p in by_name.items()}
    matches, unrated, excluded = apply_ratings(candidates, ratings, support_known, prefs.get("weights", {}))
    for k in ratings:
        pending.pop(k, None)
    matches += handle_unrated(unrated, pending, {j.key for j in to_score}, today)
    for j in matches:
        j.early_start = j.early_start or filters.flags_early_start(j, f)
        j.extra["code"] = short_code(j.key)
    run_matches = sorted((match_record(j) for j in matches), key=sort_key)

    # Never alert twice about a posting already on the page (e.g. after a re-scan of the backlog),
    # nor about one marked "Not interested" more than a week ago (it's gone from the page for good).
    dismiss_cutoff = (now.date() - dt.timedelta(days=DISMISS_DAYS)).isoformat()
    dismissed = {k for k, v in status.items()
                 if isinstance(v, dict) and v.get("status") == "not_interested" and v.get("at", "9") <= dismiss_cutoff}
    fresh = [m for m in run_matches if m["key"] not in tracked and m["key"] not in dismissed]
    mail_alerts = [m for m in fresh if m["rating"] in settings.get("email", {}).get("ratings", ["Strong"])]
    texts = notify.compose(fresh, settings["site"]["url"], first_run=first_run and not tracked,
                           max_listed=settings["sms"]["max_listed"])
    run = {
        "date": today,
        "label": now.strftime("%a %b %-d, %Y · %-I:%M %p ET") + (" · first scan (full backlog)" if first_run else ""),
        "matches": run_matches,
        "sources": dict(sorted(sources.items())),
        "screened_out": len(new) - len(run_matches),
        "notes": notes,
    }
    if args.dry_run:
        print(json.dumps(run, indent=1))
        print("\n--- alerts that would be sent ---", file=sys.stderr)
        for t in texts:
            print(t, "\n", file=sys.stderr)
        return 0

    # Remember everything listed by a board that answered, so nothing is ever alerted twice.
    for j in jobs:
        seen.setdefault(j.key, [today, today])[1] = today
    cutoff = (now.date() - dt.timedelta(days=FORGET_AFTER_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v[1] >= cutoff}
    listed = {j.key: j for j in jobs}
    # Pending postings that vanished from a complete listing are dropped; others keep waiting.
    pending = {k: v for k, v in pending.items()
               if k in listed or k.split("|", 1)[0] not in complete}

    partial = {c["name"] for c in companies if c["platform"] in PARTIAL_PLATFORMS}
    closed_now = update_tracked(tracked, listed, scanned, complete, partial, today)
    removed = []
    for k in excluded:                          # a provisional match the AI has now ruled out
        if k in tracked and tracked[k].get("provisional"):
            removed.append(tracked.pop(k))
    rerated = []
    for m in run_matches:
        if m["key"] in tracked:                 # re-rated (rules or AI changed): keep the history
            t = tracked[m["key"]]
            if (t.get("rating"), t.get("provisional", False)) != (m["rating"], m.get("provisional", False)):
                rerated.append(m["key"])
            t.update({k: v for k, v in m.items() if k != "key"})
        else:
            tracked[m["key"]] = {**m, "first_seen": today, "last_listed": today,
                                 "misses": 0, "closed_on": None}
    # Open matches stay until they close, however many scans ago they were found.
    closed_cutoff = (now.date() - dt.timedelta(days=CLOSED_DAYS)).isoformat()
    open_cutoff = (now.date() - dt.timedelta(days=FORGET_AFTER_DAYS)).isoformat()
    tracked = {k: t for k, t in tracked.items()
               if k not in dismissed
               and (t["closed_on"] or "9") >= closed_cutoff and t["last_listed"] >= open_cutoff}

    # Audit log entry for this scan (site/log.html), kept LOG_DAYS days.
    brief = lambda t: {"company": t["company"], "title": t["title"], "rating": t.get("rating", ""),
                       "url": t.get("url", "")}
    fresh_keys = {m["key"] for m in fresh}
    run["log"] = {
        "fetched": sum(s["fetched"] for s in sources.values()),
        "sources_ok": len(complete), "sources_failed": sorted(k for k, v in sources.items() if "error" in v),
        "incomplete": incomplete,
        "new_postings": len(new) - retried, "retried": retried, "passed_rules": len(candidates),
        "new_matches": [brief(tracked[k]) for k in fresh_keys if k in tracked],
        "rerated": [brief(tracked[k]) for k in rerated if k in tracked],
        "closed": [brief(t) for t in closed_now],
        "removed": [brief(t) for t in removed],
        "awaiting_rating": len(pending),
        "dismissed_total": len(dismissed),
        "open_total": sum(1 for t in tracked.values() if not t.get("closed_on")),
        "alerts": len(texts) if not args.no_sms else 0,
        "emails": len(mail_alerts) if not args.no_sms else 0,
        "no_profile": no_profile,
    }
    log_cutoff = (now.date() - dt.timedelta(days=LOG_DAYS)).isoformat()
    runs = [r for r in runs + [run] if r.get("date", "") >= log_cutoff]
    STATE.mkdir(exist_ok=True)
    write_json(STATE / "seen.json", seen, indent=0, sort_keys=True)
    write_json(STATE / "tracked.json", tracked, indent=1, sort_keys=True)
    write_json(STATE / "pending.json", pending, indent=1, sort_keys=True)
    closed = sorted((t for t in tracked.values() if t["closed_on"]),
                    key=lambda t: (t["closed_on"], -t["priority"]), reverse=True)

    # Alerts go out only once the state that prevents repeats is safely on disk.
    if not args.no_sms:
        mail = notify.compose_email(mail_alerts, settings["site"]["url"])
        mail_to = settings.get("email", {}).get("to")
        if mail and mail_to:
            for e in notify.send_email(mail, mail_to):
                print("EMAIL:", e, file=sys.stderr)
                run["notes"].append(f"Email alert problem: {e}")
    write_json(STATE / "runs.json", runs, indent=1)
    report.write_site(SITE, open_matches(tracked), runs, settings["site"]["url"], closed, status,
                      companies=summaries, profiles=by_name)
    print(f"{len(run_matches)} matches -> site/index.html", file=sys.stderr)

    if texts and not args.no_sms:
        errs = notify.send(texts)
        for e in errs:
            print("ALERT:", e, file=sys.stderr)
        if errs:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
