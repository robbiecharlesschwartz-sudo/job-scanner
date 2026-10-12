# Job Scanner

Daily scan of target companies' own career sites for early-career consulting and strategy
openings. New matches are texted via Twilio and listed on a results page.

```
companies.toml   who to scan (add/remove/re-prioritise here)
settings.toml    keyword rules, SMS limits, results-page URL, Claude model
profile.md       your background, sent to Claude when it rates fit
scanner/         adapters (one per job-board platform), filters, Claude scorer, SMS, page
state/           seen.json (every posting ever listed) + runs.json (last 30 scans)
site/            the generated results page, deployed by Vercel
```

## How a run works

1. Each company is fetched through its board's own endpoint (Workday, Greenhouse, Ashby, Oracle
   HCM, Avature, Radancy, iCIMS Jibe, Sourceflow, Yello, plus IBM's and KPMG's own search).
   No LinkedIn, no aggregators.
2. Anything already in `state/seen.json` (company + job ID) is skipped.
3. Keyword rules in `settings.toml` sort new postings into reject / borderline / clear.
4. Claude rates every survivor Strong / Good / Stretch / No with a one-line reason. Borderline
   postings rated No are dropped; keyword-clear ones are kept. Without an API key, clear
   postings get "Good" and borderline ones "Stretch".
5. Matches are ordered by company priority, then fit. Up to 3 matches are texted one per
   message; more than that sends one summary text linking to the page. No matches, no text.
6. `state/` and `site/` are committed back, which redeploys the page.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scanner --dry-run          # fetch + rate, print texts, write nothing
.venv/bin/python -m scanner --no-sms           # write state/site, don't text
.venv/bin/python -m scanner --only Ramp EY     # just these companies
```

In production it runs daily on GitHub Actions (`.github/workflows/daily-scan.yml`, 8 AM ET).
Trigger a run by hand from the repo's Actions tab, "daily-scan", "Run workflow".

Secrets the workflow reads (repo Settings, Secrets and variables, Actions):

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (optional; without it, keyword ratings only) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | from the Twilio console |
| `TWILIO_FROM` | your Twilio number (+1...) or a Messaging Service SID (MG...) |
| `SMS_TO` | your phone, e.g. +1646... |

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
  Experienced-hire roles on careers.ey.com are not scanned.
- **Persefoni** is disabled: its Greenhouse board returned 404 on 2026-10-11.
- Boards change their markup. When one breaks, the page's "Scan details" shows it as failed
  and the rest of the run carries on.
