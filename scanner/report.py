"""Writes the static results site: site/index.html plus one short-link redirect per match."""
from __future__ import annotations

import html
import json
from pathlib import Path

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1c1c1a;--muted:#6b6b66;--line:#e4e4df;--link:#1f5fbf;
--strong-bg:#e3f1e6;--strong-ink:#1d5a2c;--good-bg:#e5eef9;--good-ink:#1f4f8f;
--stretch-bg:#f2efe6;--stretch-ink:#5f5532;--warn-bg:#fbeaea;--warn-ink:#8a2222}
@media (prefers-color-scheme:dark){:root{--bg:#151514;--card:#1f1f1d;--ink:#ecece8;--muted:#a3a39c;
--line:#33332f;--link:#8ab4f8;--strong-bg:#1e3524;--strong-ink:#a6dcb3;--good-bg:#1d2a3d;--good-ink:#a9c7ef;
--stretch-bg:#302c1f;--stretch-ink:#ddd2a8;--warn-bg:#3a1f1f;--warn-ink:#f0b0b0}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
h2{font-size:15px;margin:28px 0 10px;display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid var(--line);padding-bottom:6px}
h2 span{color:var(--muted);font-weight:400;font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 10px}
.top{display:flex;gap:8px;align-items:baseline;justify-content:space-between}
.co{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.title{font-weight:600;font-size:16px;margin:2px 0 4px;display:block;color:var(--link);text-decoration:none}
.title:hover{text-decoration:underline}
.meta{font-size:13px;color:var(--muted)}
.reason{margin:6px 0 0;font-size:14px}
.pill{font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;white-space:nowrap}
.Strong{background:var(--strong-bg);color:var(--strong-ink)}
.Good{background:var(--good-bg);color:var(--good-ink)}
.Stretch{background:var(--stretch-bg);color:var(--stretch-ink)}
.flag{display:inline-block;margin-top:6px;font-size:12px;background:var(--warn-bg);color:var(--warn-ink);padding:2px 8px;border-radius:6px}
.empty{color:var(--muted);font-size:14px;margin:0 0 6px}
details{margin-top:6px;font-size:13px;color:var(--muted)}
details table{border-collapse:collapse;width:100%;margin-top:6px}
details td{padding:3px 6px;border-bottom:1px solid var(--line)}
details td.n{text-align:right;font-variant-numeric:tabular-nums}
.err{color:var(--warn-ink)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 4px}
.chips button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:4px 12px;cursor:pointer}
.chips button[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
"""

JS = """
document.querySelectorAll('.chips button').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.chips button').forEach(x=>x.setAttribute('aria-pressed',x===b));
  const r=b.dataset.r;document.querySelectorAll('.card').forEach(c=>{c.hidden=!!r&&c.dataset.r!==r});
}));
"""

e = html.escape


def _card(m: dict) -> str:
    flag = '<span class="flag">Start date may be before summer 2027</span>' if m.get("early_start") else ""
    posted = f" · posted {e(m['posted'])}" if m.get("posted") else ""
    return (f'<div class="card" id="{e(m["code"])}" data-r="{e(m["rating"])}"><div class="top">'
            f'<span class="co">P{m["priority"]} · {e(m["company"])}</span>'
            f'<span class="pill {e(m["rating"])}">{e(m["rating"])}</span></div>'
            f'<a class="title" href="{e(m["url"])}" target="_blank" rel="noopener">{e(m["title"])}</a>'
            f'<div class="meta">{e(m["location"] or "Location not listed")}{posted}</div>'
            f'<p class="reason">{e(m["reason"])}</p>{flag}</div>')


def _health(run: dict) -> str:
    rows = []
    for co, s in run.get("sources", {}).items():
        if s.get("error"):
            rows.append(f'<tr><td>{e(co)}</td><td class="err" colspan="2">failed: {e(s["error"][:140])}</td></tr>')
        else:
            rows.append(f'<tr><td>{e(co)}</td><td class="n">{s["fetched"]} listed</td><td class="n">{s["new"]} new</td></tr>')
    notes = "".join(f"<p>{e(n)}</p>" for n in run.get("notes", []))
    return (f'<details><summary>Scan details · {run.get("screened_out", 0)} new postings screened out</summary>'
            f'{notes}<table>{"".join(rows)}</table></details>')


def write_site(site_dir: Path, runs: list[dict], site_url: str) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "j").mkdir(exist_ok=True)
    latest = runs[-1] if runs else {}
    total = sum(len(r["matches"]) for r in runs)
    sections = []
    for run in reversed(runs):
        ms = run["matches"]
        head = f'<h2>{e(run["label"])}<span>{len(ms)} match{"es" if len(ms) != 1 else ""}</span></h2>'
        body = "".join(_card(m) for m in ms) or '<p class="empty">No new matches.</p>'
        sections.append(head + body + _health(run))
    chips = ('<div class="chips"><button aria-pressed="true" data-r="">All</button>'
             '<button aria-pressed="false" data-r="Strong">Strong</button>'
             '<button aria-pressed="false" data-r="Good">Good</button>'
             '<button aria-pressed="false" data-r="Stretch">Stretch</button></div>')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Job Scanner</title><style>{CSS}</style></head><body><main>
<h1>Job Scanner</h1>
<p class="sub">Early-career consulting &amp; strategy openings at target companies.
Last scan {e(latest.get("label", "never"))}. {total} matches across the last {len(runs)} scans, newest first.</p>
{chips}{"".join(sections)}
</main><script>{JS}</script></body></html>"""
    (site_dir / "index.html").write_text(page)

    live = {m["code"] for run in runs for m in run["matches"]}
    for old in (site_dir / "j").glob("*.html"):
        if old.stem not in live:
            old.unlink()
    for run in runs:
        for m in run["matches"]:
            (site_dir / "j" / f"{m['code']}.html").write_text(
                f'<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
                f'<meta http-equiv="refresh" content="0;url={e(m["url"])}">'
                f'<title>{e(m["company"])} - {e(m["title"])}</title>'
                f'<a href="{e(m["url"])}">{e(m["title"])}</a>')
    (site_dir / "latest.json").write_text(json.dumps(latest, indent=1))
