"""Writes the static results site: site/index.html plus one short-link redirect per match."""
from __future__ import annotations

import datetime as dt
import html
import json
import re
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
.sub a{color:inherit}
.logentry .facts{font-size:14px;margin:0 0 6px}
.logentry .lh{font-size:13px;font-weight:600;margin:8px 0 2px}
.logentry ul{margin:0 0 6px;padding-left:18px;font-size:13px}
.logentry li{margin:2px 0}
.logentry .pill{font-size:11px}
details{margin-top:6px;font-size:13px;color:var(--muted)}
details table{border-collapse:collapse;width:100%;margin-top:6px}
details td{padding:3px 6px;border-bottom:1px solid var(--line)}
details td.n{text-align:right;font-variant-numeric:tabular-nums}
.err{color:var(--warn-ink)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 4px}
.chips button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:4px 12px;cursor:pointer}
.chips button[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.pointers{margin:8px 0 0;padding:0;list-style:none;font-size:14px}
.pointers li{display:flex;gap:8px;padding:3px 0;line-height:1.4}
.pointers .m{flex:0 0 16px;font-weight:700;text-align:center}
.pointers .fit .m{color:var(--strong-ink)}
.pointers .gap .m{color:var(--warn-ink)}
.pointers .neu .m{color:var(--muted)}
details.key{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:0 0 16px}
details.key>summary{font-weight:600;cursor:pointer}
.key dl{margin:10px 0 0}
.key dt{margin:10px 0 4px}
.key dd{margin:0 0 2px;font-size:14px}
.key dd.do{color:var(--muted)}
.key .legend{font-size:13px;color:var(--muted);margin:12px 0 0}
.acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.acts button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;padding:5px 10px;cursor:pointer;min-height:32px}
.acts button:hover{border-color:var(--ink)}
.acts .undo{display:none}
.card[data-s] .acts .mark{display:none}
.card[data-s] .acts .undo{display:inline-block}
.card[data-s="applied"]{border-left:4px solid var(--strong-ink)}
.card[data-s="not_interested"]{opacity:.65}
.stamp{font-size:12px;color:var(--muted);margin-left:auto;align-self:center}
details.done{margin:14px 0 0;font-size:15px}
details.done>summary{font-weight:600;cursor:pointer;padding:10px 0;border-bottom:1px solid var(--line)}
details.done>summary span{color:var(--muted);font-weight:400;font-size:13px}
details.done .card{margin-top:10px}
.card.closed{opacity:.7}
.card.closed .title{color:var(--muted)}
.closedtag{font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
details.closedlist{margin:20px 0 0;font-size:15px;color:var(--ink)}
details.closedlist>summary{font-weight:600;cursor:pointer;padding:10px 0;border-bottom:1px solid var(--line)}
details.closedlist>summary span{color:var(--muted);font-weight:400;font-size:13px}
details.closedlist .card{margin-top:10px}
"""

JS = r"""
document.querySelectorAll('.chips button').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.chips button').forEach(x=>x.setAttribute('aria-pressed',x===b));
  const r=b.dataset.r;document.querySelectorAll('.card').forEach(c=>{c.hidden=!!r&&c.dataset.r!==r});
}));

// Check-offs: saved to the private repo through /api/status, so they sync across devices.
const LABEL={applied:'Applied',not_interested:'Not interested'};
function place(card,st){
  const s=st&&st.status;
  if(s){card.dataset.s=s;}else{delete card.dataset.s;}
  const stamp=card.querySelector('.stamp');
  if(stamp)stamp.textContent=s?`${LABEL[s]} ${st.at||''}`:'';
  const dest=s?document.getElementById('done-'+s):document.getElementById(card.dataset.home);
  if(dest&&card.parentElement!==dest){
    const i=+card.dataset.i, next=!s&&card.dataset.i?[...dest.children].find(c=>+c.dataset.i>i):null;
    dest.insertBefore(card,next||null);
  }
}
function counts(){
  for(const s of ['applied','not_interested']){
    const n=document.querySelectorAll('#done-'+s+' .card').length;
    document.getElementById('n-'+s).textContent=n;
  }
  const o=document.getElementById('n-open');
  if(o)o.textContent=document.querySelectorAll('#list .card').length;
}
async function api(method,body){
  const r=await fetch('/api/status',{method,headers:{'Content-Type':'application/json'},
    body:body&&JSON.stringify(body)});
  if(!r.ok)throw new Error(r.status);
  return r.json();
}
function applyAll(data){
  document.querySelectorAll('.card[data-key]').forEach(c=>place(c,data[c.dataset.key]));
  counts();
}
{
  api('GET').then(applyAll).catch(()=>{});
  document.addEventListener('click',async ev=>{
    const btn=ev.target.closest('.acts button');if(!btn)return;
    const card=btn.closest('.card');const s=btn.dataset.s||null;
    const prev=card.dataset.s?{status:card.dataset.s}:undefined;
    place(card,s?{status:s,at:new Date().toISOString().slice(0,10)}:undefined);counts();
    try{applyAll(await api('POST',{key:card.dataset.key,status:s}));}
    catch(e){place(card,prev);counts();alert('Could not save - check your connection and try again.');}
  });
}
"""

e = html.escape


def _date(iso: str) -> str:
    try:
        return dt.date.fromisoformat(iso).strftime("%b %-d")
    except ValueError:
        return iso


MARKS = {"+": ("fit", "✓", "Fits"), "-": ("gap", "✗", "Gap"), "~": ("neu", "~", "Unclear")}


def _pointers(ps: list[str]) -> str:
    items = []
    for p in ps or []:
        cls, sym, label = MARKS.get(p[:1], MARKS["~"])
        text = p[1:].strip() if p[:1] in MARKS else p
        items.append(f'<li class="{cls}"><span class="m" aria-label="{label}">{sym}</span>'
                     f'<span>{e(text)}</span></li>')
    return f'<ul class="pointers">{"".join(items)}</ul>' if items else ""


def _key() -> str:
    from .scorer import RATING_GUIDE
    rows = "".join(f'<dt><span class="pill {r}">{r}</span></dt><dd>{e(what)}</dd>'
                   f'<dd class="do">What to do: {e(do)}</dd>' for r, (what, do) in RATING_GUIDE.items())
    return ('<details class="key"><summary>What Strong, Good and Stretch mean</summary>'
            f'<dl>{rows}</dl><p class="legend">Under each job: ✓ where you fit, ✗ a gap or risk, '
            '~ something the posting leaves unclear. Every rating comes from the full posting '
            'checked against your profile.</p></details>')


def _short_loc(loc: str, keep: int = 4) -> str:
    """Long office lists (McKinsey lists 100+): New York first, then a few, then "+N more"."""
    parts = [p.strip() for p in re.split(r";", loc) if p.strip()]
    if len(parts) <= keep + 1:
        return loc
    suffix = re.search(r"\s(\([A-Z]{2}(?:,\s*[A-Z]{2})*\))$", parts[-1])
    parts[-1] = parts[-1][:suffix.start()] if suffix else parts[-1]
    ny = [p for p in parts if re.search(r"(?i)new york|\bnyc\b|manhattan|brooklyn", p)]
    rest = [p for p in parts if p not in ny]
    shown = (ny + rest)[:keep]
    return "; ".join(shown) + f" +{len(parts) - len(shown)} more" + (f" {suffix.group(1)}" if suffix else "")


def _card(m: dict, closed_on: str | None = None, home: str = "", st: dict | None = None,
          order: int | None = None) -> str:
    flag = '<span class="flag">Start date may be before summer 2027</span>' if m.get("early_start") else ""
    posted = f" · posted {e(m['posted'])}" if m.get("posted") else ""
    found = f" · found {e(_date(m['first_seen']))}" if m.get("first_seen") else ""
    degree = {"yes": " · ✓ open to bachelor's grads", "unclear": " · degree requirement not stated"
              }.get(m.get("undergrad", ""), "")
    tag = f'<span class="closedtag">Closed {e(_date(closed_on))}</span>' if closed_on else ""
    s_attr = f' data-s="{e(st["status"])}"' if st else ""
    stamp = f'{"Applied" if st["status"] == "applied" else "Not interested"} {st.get("at", "")}' if st else ""
    acts = ('<div class="acts"><button class="mark" data-s="applied">✓ Applied</button>'
            '<button class="mark" data-s="not_interested">✗ Not interested</button>'
            f'<button class="undo" data-s="">Undo</button><span class="stamp">{e(stamp)}</span></div>')
    return (f'<div class="card{" closed" if closed_on else ""}" id="{e(m["code"])}" data-r="{e(m["rating"])}"'
            f' data-key="{e(m.get("key", ""))}" data-home="{e(home)}"'
            f'{f" data-i={order}" if order is not None else ""}{s_attr}>'
            f'<div class="top"><span class="co">P{m["priority"]} · {e(m["company"])}</span>'
            f'<span>{tag} <span class="pill {e(m["rating"])}">{e(m["rating"])}</span></span></div>'
            f'<a class="title" href="{e(m["url"])}" target="_blank" rel="noopener">{e(m["title"])}</a>'
            f'<div class="meta" title="{e(m["location"])}">{e(_short_loc(m["location"]) or "Location not listed")}'
            f'{found}{posted}{degree}</div>'
            f'<p class="reason">{e(m["reason"])}</p>{_pointers(m.get("pointers"))}{flag}{acts}</div>')


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


DISMISS_NOTE = "7 days after you mark them"


def write_site(site_dir: Path, matches: list[dict], runs: list[dict], site_url: str,
               closed: list[dict] | None = None, status: dict | None = None) -> None:
    """index.html: one long list of every open match (newest find first), plus the Applied /
    Not interested / Closed drawers. log.html: the scan-by-scan audit log."""
    closed = closed or []
    status = status or {}
    done = {"applied": [], "not_interested": []}
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "j").mkdir(exist_ok=True)
    latest = runs[-1] if runs else {}
    cards = []
    for i, m in enumerate(matches):
        st = status.get(m.get("key", ""))
        html_ = _card(m, None, "list", st, i)
        (done[st["status"]] if st and st["status"] in done else cards).append(html_)
    body = "".join(cards) or '<p class="empty">No open matches right now.</p>'
    n_open = len(cards)
    listing = (f'<h2>Open matches<span><b id="n-open">{n_open}</b> open · newest first</span></h2>'
               f'<div class="cards" id="list">{body}</div>')
    done_html = "".join(
        f'<details class="done"><summary>{label} <span>· <b id="n-{k}">{len(done[k])}</b></span></summary>'
        f'<div id="done-{k}">{"".join(done[k])}</div></details>'
        for k, label in (("applied", "Applied"), ("not_interested", "Not interested")))
    closed_html = (
        f'<details class="closedlist"><summary>Closed postings <span>· {len(closed)} in the last 30 days, '
        f'no longer listed on the company site</span></summary><div id="closed-list">'
        + ("".join(_card(c, c["closed_on"], "closed-list", status.get(c.get("key", ""))) for c in closed)
           or '<p class="empty">None yet.</p>') + '</div></details>')
    chips = ('<div class="chips"><button aria-pressed="true" data-r="">All</button>'
             '<button aria-pressed="false" data-r="Strong">Strong</button>'
             '<button aria-pressed="false" data-r="Good">Good</button>'
             '<button aria-pressed="false" data-r="Stretch">Stretch</button></div>')
    lg = latest.get("log", {})
    last = (f'Last scan {e(latest.get("label", "never"))}'
            + (f' · {lg["fetched"]:,} postings checked' if lg.get("fetched") else "") + ".")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Job Scanner</title><style>{CSS}</style></head><body><main>
<h1>Job Scanner</h1>
<p class="sub">Early-career consulting &amp; strategy openings at target companies, updated every scan.
{last} <a href="log">Scan log</a><br>
"Not interested" cards are removed {DISMISS_NOTE}.</p>
{_key()}{chips}{listing}{done_html}{closed_html}
</main><script>{JS}</script></body></html>"""
    (site_dir / "index.html").write_text(page)
    write_log(site_dir, runs)

    shown = matches + closed
    live = {m["code"] for m in shown}
    for old in (site_dir / "j").glob("*.html"):
        if old.stem not in live:
            old.unlink()
    for m in shown:
        (site_dir / "j" / f"{m['code']}.html").write_text(
            f'<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
            f'<meta http-equiv="refresh" content="0;url={e(m["url"])}">'
            f'<title>{e(m["company"])} - {e(m["title"])}</title>'
            f'<a href="{e(m["url"])}">{e(m["title"])}</a>')
    (site_dir / "latest.json").write_text(json.dumps(latest, indent=1))


def _jobline(x: dict) -> str:
    rating = f' <span class="pill {e(x["rating"])}">{e(x["rating"])}</span>' if x.get("rating") else ""
    title = (f'<a href="{e(x["url"])}" target="_blank" rel="noopener">{e(x["title"])}</a>'
             if x.get("url") else e(x["title"]))
    return f'<li>{e(x["company"])}: {title}{rating}</li>'


def write_log(site_dir: Path, runs: list[dict]) -> None:
    """Audit log: one entry per scan (newest first) for the last 30 days."""
    entries = []
    for run in reversed(runs):
        lg = run.get("log")
        if lg:
            failed = lg.get("sources_failed") or []
            facts = [f'{lg["fetched"]:,} postings from {lg["sources_ok"]} companies'
                     + (f' ({len(failed)} failed)' if failed else ""),
                     f'{lg["new_postings"]:,} new postings, {lg["passed_rules"]} passed the rules',
                     f'{len(lg["new_matches"])} new match{"es" if len(lg["new_matches"]) != 1 else ""}',
                     f'{len(lg["closed"])} closed',
                     f'{lg["open_total"]} tracked matches still open on company sites',
                     f'{lg["alerts"]} phone alert{"s" if lg["alerts"] != 1 else ""} sent',
                     f'{lg.get("emails", 0)} email{"s" if lg.get("emails", 0) != 1 else ""} sent']
            lists = "".join(
                f'<p class="lh">{label}</p><ul>{"".join(_jobline(x) for x in lg[k])}</ul>'
                for k, label in (("new_matches", "New matches"), ("rerated", "Re-rated"),
                                 ("closed", "Closed this scan")) if lg.get(k))
        else:   # scans from before the log existed
            n = len(run.get("matches", []))
            facts = [f'{n} match{"es" if n != 1 else ""}', f'{run.get("screened_out", 0):,} screened out']
            lists = ""
        entries.append(f'<section class="logentry"><h2>{e(run["label"])}</h2>'
                       f'<p class="facts">{" · ".join(facts)}</p>{lists}{_health(run)}</section>')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Scan Log</title><style>{CSS}</style></head><body><main>
<h1>Scan log</h1>
<p class="sub">Every scan from the last 30 days, newest first. <a href="./">Back to matches</a></p>
{"".join(entries) or '<p class="empty">No scans yet.</p>'}
</main></body></html>"""
    (site_dir / "log.html").write_text(page)
