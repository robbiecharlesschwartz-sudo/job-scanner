# Job Scanner

Daily scan of target companies' own career sites for early-career consulting and strategy
openings. New matches arrive as free push notifications (ntfy) and are listed on a results page,
which also keeps closed postings for 30 days. Everything runs on free tiers: GitHub Actions,
Vercel Hobby, Groq, ntfy.

```
companies.toml   who to scan (add/remove/re-prioritise here)
settings.toml    keyword rules, alert limits, results-page URL, LLM settings
profile.md       your background + hard requirements, sent to the LLM when it rates fit
examples.toml    your verdicts on real postings; the LLM follows them as precedent
scanner/         adapters (one per job-board platform), filters, LLM scorer, ntfy alerts, page
state/           seen.json (every posting ever listed) + runs.json (last 30 scans) + tracked.json (open/closed matches)
site/            the generated results page, deployed by Vercel
```

## How a run works

1. Each company is fetched through its board's own endpoint (Workday, Greenhouse, Ashby, Oracle
   HCM, Avature, Radancy, iCIMS Jibe, Sourceflow, Yello, plus IBM's and KPMG's own search).
   No LinkedIn, no aggregators.
2. Anything already in `state/seen.json` (company + job ID) is skipped.
3. Keyword rules in `settings.toml` sort new postings into reject / borderline / clear.
4. An LLM reads each survivor's full posting (Claude if `ANTHROPIC_API_KEY` has credit, else
   NVIDIA's free Kimi K3, else Groq) against `profile.md` and your past verdicts in
   `examples.toml`. It rates Strong / Good / Stretch / No, writes 3-5 pointers, and says whether
   a bachelor's grad can apply. Anything rated No, or not open to undergrads, is dropped.
5. Matches are ordered by company priority, then fit. Each gets its own phone alert (tap opens
   the posting), then one summary alert opens the results page. No matches, no alert.
6. Past matches that vanish from their board on two scans in a row (or whose posting link
   returns "not found") move to "Closed postings" on the page for 30 days.
7. `state/` and `site/` are committed back, which redeploys the page.

## Check-offs (Applied / Not interested)

Each job card has **Applied** and **Not interested** buttons. A click calls `api/status.mjs` (a
Vercel function), which saves it to `state/status.json` in this repo, so marks sync across devices
and survive re-scans. Marked jobs move into the "Applied" / "Not interested" sections; Undo
moves them back. Clicks don't redeploy the site (`ignoreCommand` in `vercel.json`).

The buttons work on the plain URL with no setup (the site is unlisted and marks are undoable).
Vercel env var: `GITHUB_TOKEN` (writes `state/status.json`; currently the `gh` CLI
token - replace it with a fine-grained token scoped to this repo's contents if you prefer).

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scanner --dry-run          # fetch + rate, print alerts, write nothing
.venv/bin/python -m scanner --no-sms           # write state/site, don't send alerts
.venv/bin/python -m scanner --only Ramp EY     # just these companies
```

In production it runs on GitHub Actions (`.github/workflows/daily-scan.yml`) at 7:23 AM ET with a
backup run at 11:47 AM ET; a failed run sends a phone alert.
Trigger a run by hand from the repo's Actions tab, "daily-scan", "Run workflow".

Secrets the workflow reads (repo Settings, Secrets and variables, Actions):

| Secret | Value |
|---|---|
| `NVIDIA_API_KEY` | free key from build.nvidia.com (main rater, Kimi K3) |
| `GROQ_API_KEY` | free key from console.groq.com (backup rater) |
| `NTFY_TOPIC` | private ntfy topic name; subscribe to it in the ntfy phone app |
| `ANTHROPIC_API_KEY` | optional, paid: use Claude for ratings instead of Groq |

## Adding a company

Find the board its careers page links to, then add an entry to `companies.toml` using one of the
platforms listed at the top of that file. Quick checks:

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/<board>/jobs` returns JSON
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/<board>`
- Lever: `https://api.lever.co/v0/postings/<board>`
- Workday: the URL looks like `https://<tenant>.<wdN>.myworkdayjobs.com/<site>`

Then `.venv/bin/python -m scanner --dry-run --only "<Name>"` to see what it finds.

## Known gaps

- **Slalom** only exposes an RSS feed of ~20 postings with no locations; its search page is
  rendered in the browser. Coverage there is partial.
- **EY** is scanned through its US early-careers board, which is where new-grad roles are posted.
  On careers.ey.com only EY-Parthenon roles in New York are scanned.
- **Bain, L.E.K., Alvarez & Marsal** put their job boards behind Cloudflare / bot checks, so they
  can't be scanned automatically. Check them by hand.
- **Persefoni** is disabled: its Greenhouse board returned 404 on 2026-10-11.
- Boards change their markup. When one breaks, the page's "Scan details" shows it as failed
  and the rest of the run carries on.
