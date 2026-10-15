# Company intelligence

One JSON file per company (`intel/<slug>.json`, slug = lower-case name with `&` -> `and` and other
punctuation -> `-`, e.g. `strategy-and-pwc.json`). A company in `companies.toml` can point at a
different file with `intel = "<slug>"`. Several boards for one company share one profile.

Scans only **read** these files. Nothing here is fetched or researched during a daily scan, so the
research costs nothing per scan and can't be lost when a provider is down.

## What a profile establishes, and what it doesn't

- It records what the company **documents** about itself (policies, programs, service lines) and
  what credible **independent** sources add. It does not show how a particular team, office or
  manager works.
- A **verified_policy** means the policy or program exists as described, with whatever eligibility
  limits the source states. It is not evidence that the policy is generous or applied consistently.
- Company research is **context** for rating a job. Role fit always comes from the posting itself.

## Evidence labels

| label | use for |
|---|---|
| `verified_policy` | explicitly documented by the company: careers pages, benefits pages, program pages, official reports |
| `supporting_evidence` | credible independent reporting or data about practice (press, rankings with a stated method, regulators) |
| `tentative_signal` | indirect, partial, self-reported, marketing-style or uncorroborated (incl. employee-review themes) |

No reliable evidence? Write it in `unknowns`; never make a finding up.

Rules (from the product brief): no fabricated policies or experiences; marketing language is not
proof; hybrid work alone is not "flexibility"; one employee's account never speaks for the firm;
say when a source is old; separate facts from interpretation; more marketing material must not
mean a better profile.

## Schema

```json
{
  "company": "Deloitte",                      // must match the name in companies.toml
  "researched_on": "2026-10-15",              // when the research was done
  "refresh_after": "2027-04-15",              // after this the page marks it as possibly dated
  "method": "manual web research",            // how it was produced
  "overview": "What the company does, in 1-3 sentences.",
  "business_type": "e.g. Big Four professional services / strategy house / in-house strategy",
  "service_lines": ["..."],
  "watch_urls": ["https://..."],              // official pages `scanner.intel check` re-fetches
  "differentiators":  [FINDING, ...],
  "early_career":     [FINDING, ...],
  "employee_support": [FINDING, ...],         // beyond pay, healthcare and retirement
  "flexibility":      [FINDING, ...],
  "culture":          [FINDING, ...],
  "tradeoffs":        [FINDING, ...],
  "unknowns": ["What could not be established."],
  "sources": [{"id": "1", "url": "https://...", "title": "...",
               "type": "official|independent|employee_reviews|other",
               "accessed": "2026-10-15", "published": "2025-06"}]
}
```

`FINDING` = `{"text": "...", "evidence": "<label>", "sources": ["1"], "significance": "optional:
what it means for a new grad"}`. A finding without a valid label or source is dropped when the
profile loads (and listed by `python -m scanner.intel status`).

## Keeping profiles current

| when | what | cost |
|---|---|---|
| every scheduled scan | `python -m scanner.intel check`: re-fetches the `watch_urls` of up to 6 profiles not checked in 30 days, compares page hashes; a changed page flags the profile on the site | a few HTTP GETs, no AI |
| `refresh_after` passes | the page shows "may be out of date"; re-research the company (below) | - |
| new company added | the scan log lists it under "No company profile yet"; matches are judged on the posting alone until a profile exists | - |
| any time | `python -m scanner.intel status` lists every profile, its freshness and problems | free |

**Re-researching a company (manual or urgent refresh).** Open the repo in Claude Code and ask:
"Re-research the company profile for <Company> following intel/README.md". Web research needs a
tool with web search, which the scheduled GitHub Action does not have, so it is deliberately not
automated. Keep the file's shape, update `researched_on` / `refresh_after`, and add or remove
findings with their sources.

**Drafting from official pages without web search.**
`python -m scanner.intel draft "<Company>" <official-url> [<url> ...]` asks the free AI to extract
findings from those pages and keeps only findings whose quoted text appears word for word on the
page. The draft goes to `intel/drafts/` (ignored by scans) for a person to review and move into
`intel/`. Culture statements from a company's own pages are labelled `tentative_signal`.
