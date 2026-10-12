"""Daily run:  python -m scanner [--dry-run] [--no-sms] [--only NAME ...]

fetch every company -> keep postings not seen before -> keyword rules -> Claude rates the
survivors -> write site/ + state/ -> text the new matches.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import sys
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from . import adapters, filters, notify, report, scorer

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
SITE = ROOT / "site"
TZ = ZoneInfo("America/New_York")
RANK = {"Strong": 0, "Good": 1, "Stretch": 2}
KEEP_RUNS = 30            # scans shown on the results page
FORGET_AFTER_DAYS = 180   # drop seen-IDs not listed for this long
MAX_CLAUDE = 250          # cap per run (first run sees the whole backlog)
CLOSED_DAYS = 30          # closed postings stay on the page this long
PARTIAL_PLATFORMS = {"avature_rss"}   # feeds too partial to detect a posting closing


def short_code(key: str) -> str:
    n = int(hashlib.sha1(key.encode()).hexdigest(), 16)
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
    out = ""
    for _ in range(6):
        n, r = divmod(n, len(alphabet))
        out += alphabet[r]
    return out


def load_json(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def fetch_all(companies: list[dict]) -> tuple[list, dict]:
    sources, jobs = {}, []

    def run(c):
        return c, adapters.ADAPTERS[c["platform"]](c)

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(run, c) for c in companies]
        for f in cf.as_completed(futs):
            c = companies[futs.index(f)]
            label = c["name"] + (f" ({c['site']})" if c.get("site") and c["platform"] == "workday" else "")
            try:
                _, js = f.result()
                sources[label] = {"fetched": len(js), "new": 0, "company": c["name"]}
                jobs.extend(js)
            except Exception as e:  # one broken board must not sink the run
                sources[label] = {"fetched": 0, "new": 0, "company": c["name"], "error": str(e)}
    return jobs, sources


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no state/site writes, print texts")
    ap.add_argument("--no-sms", action="store_true", help="write state/site but don't text")
    ap.add_argument("--only", nargs="*", help="limit to these company names")
    args = ap.parse_args(argv)

    settings = tomllib.loads((ROOT / "settings.toml").read_text())
    companies = [c for c in tomllib.loads((ROOT / "companies.toml").read_text())["company"]
                 if c.get("enabled", True) and (not args.only or c["name"] in args.only)]
    profile = (ROOT / "profile.md").read_text()
    f = settings["filters"]
    now = dt.datetime.now(TZ)
    today = now.date().isoformat()

    seen: dict = load_json(STATE / "seen.json", {})
    runs: list = load_json(STATE / "runs.json", [])
    first_run = not seen

    jobs, sources = fetch_all(companies)
    print(f"fetched {len(jobs)} postings from {len(sources)} sources", file=sys.stderr)

    # Same posting can surface from two queries or two sites of one company.
    uniq = {}
    for j in jobs:
        uniq.setdefault(j.key, j)
    jobs = list(uniq.values())
    new = [j for j in jobs if j.key not in seen]
    for j in new:
        for s in sources.values():
            if s["company"] == j.company and "error" not in s:
                s["new"] += 1
                break

    # Keyword pass. Workday listings carry no description, so fetch it for survivors and re-check.
    first_pass = [j for j in new if filters.title_ok(j, f)]

    def enrich(j):
        try:
            if "detail" in j.extra:
                adapters.workday_detail(j)
            elif j.extra.get("platform") in ("avature", "radancy"):
                adapters.page_detail(j)
        except Exception:
            pass                                # keep the listing-level data
        return j

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        first_pass = list(ex.map(enrich, first_pass))
    candidates = []
    for j in first_pass:
        verdict, why = filters.classify(j, f)
        if verdict != "reject":
            j.verdict, j.reason = verdict, why
            candidates.append(j)
    print(f"{len(new)} new, {len(candidates)} passed keyword rules", file=sys.stderr)

    notes = []
    to_score = sorted(candidates, key=lambda j: (j.priority, j.verdict != "clear"))[:MAX_CLAUDE]
    ratings, err = scorer.score(to_score, profile, settings["scoring"]["model"],
                                settings["scoring"].get("batch_size", 12))
    if err:
        notes.append(err)
    if len(candidates) > MAX_CLAUDE:
        notes.append(f"{len(candidates) - MAX_CLAUDE} candidates over the Claude cap got keyword ratings")

    matches = []
    for j in candidates:
        r = ratings.get(j.key)
        if r:
            if r.rating == "No" or r.starts_before_summer_2027:
                continue                        # the LLM's "no" is final
            j.rating, j.reason = r.rating, r.reason
        else:
            if j.verdict != "clear":
                continue                        # unrated and level unconfirmed: skip
            j.rating = "Good"
            j.reason = "Entry-level consulting/strategy role in NYC (AI rating unavailable this run)."
        j.early_start = j.early_start or filters.flags_early_start(j, f)
        j.extra["code"] = short_code(j.key)
        matches.append(j)
    matches.sort(key=lambda j: (j.priority, RANK[j.rating], j.company, j.title))

    run = {
        "date": today,
        "label": now.strftime("%a %b %-d, %Y · %-I:%M %p ET") + (" · first scan (full backlog)" if first_run else ""),
        "matches": [{
            "code": j.extra["code"], "company": j.company, "priority": j.priority, "title": j.title,
            "location": j.location, "url": j.url, "posted": j.posted, "rating": j.rating,
            "reason": j.reason, "early_start": j.early_start, "key": j.key} for j in matches],
        "sources": dict(sorted(sources.items())),
        "screened_out": len(new) - len(matches),
        "notes": notes,
    }

    texts = notify.compose(run["matches"], settings["site"]["url"], first_run=first_run,
                           max_listed=settings["sms"]["max_listed"])
    if args.dry_run:
        print(json.dumps(run, indent=1))
        print("\n--- texts that would be sent ---", file=sys.stderr)
        for t in texts:
            print(t, "\n", file=sys.stderr)
        return 0

    # Remember everything listed by a board that answered, so nothing is ever texted twice.
    ok_companies = {s["company"] for s in sources.values() if "error" not in s}
    for j in jobs:
        if j.company in ok_companies:
            seen.setdefault(j.key, [today, today])[1] = today
    cutoff = (now.date() - dt.timedelta(days=FORGET_AFTER_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v[1] >= cutoff}

    # Closed postings: a match counts as closed once its board answers twice without listing it.
    # Feeds that only show a slice of the board (Slalom's RSS) can't tell, so they're skipped.
    tracked: dict = load_json(STATE / "tracked.json", {})
    listed = {j.key for j in jobs}
    partial = {c["name"] for c in companies if c["platform"] in PARTIAL_PLATFORMS}
    for key, t in tracked.items():
        if t["company"] not in ok_companies or t["company"] in partial:
            continue
        if key in listed:
            t.update(misses=0, closed_on=None, last_listed=today)
        elif not t.get("closed_on"):
            t["misses"] = t.get("misses", 0) + 1
            if t["misses"] >= 2:
                t["closed_on"] = today
    for m in run["matches"]:
        tracked.setdefault(m["key"], {**m, "first_seen": today, "last_listed": today,
                                      "misses": 0, "closed_on": None})
    closed_cutoff = (now.date() - dt.timedelta(days=CLOSED_DAYS)).isoformat()
    open_cutoff = (now.date() - dt.timedelta(days=FORGET_AFTER_DAYS)).isoformat()
    tracked = {k: t for k, t in tracked.items()
               if (t["closed_on"] or "9") >= closed_cutoff and t["last_listed"] >= open_cutoff}

    runs = (runs + [run])[-KEEP_RUNS:]
    STATE.mkdir(exist_ok=True)
    (STATE / "seen.json").write_text(json.dumps(seen, indent=0, sort_keys=True))
    (STATE / "runs.json").write_text(json.dumps(runs, indent=1))
    (STATE / "tracked.json").write_text(json.dumps(tracked, indent=1, sort_keys=True))
    closed = sorted((t for t in tracked.values() if t["closed_on"]),
                    key=lambda t: (t["closed_on"], -t["priority"]), reverse=True)
    report.write_site(SITE, runs, settings["site"]["url"], closed)
    print(f"{len(matches)} matches -> site/index.html", file=sys.stderr)

    if texts and not args.no_sms:
        errs = notify.send(texts)
        for e in errs:
            print("SMS:", e, file=sys.stderr)
        if errs:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
