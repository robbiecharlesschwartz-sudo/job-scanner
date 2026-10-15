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
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--link);outline-offset:2px;border-radius:4px}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 0}
.badge{font-size:12px;padding:1px 8px;border-radius:6px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.badge.ok{color:var(--strong-ink);border-color:var(--strong-ink)}
.badge.pend{color:var(--stretch-ink);border-color:var(--stretch-ink);background:var(--stretch-bg)}
.whyco{margin:8px 0 0;font-size:14px}
.whyco b{font-weight:600}
details.more{margin-top:8px;color:var(--ink);font-size:14px}
details.more>summary{cursor:pointer;color:var(--link);font-size:13px}
details.more dl{margin:6px 0 0}
details.more dt{font-weight:600;font-size:13px;margin:8px 0 2px}
details.more dd{margin:0}
details.more ul{margin:2px 0;padding-left:18px}
.ev{display:inline-block;font-size:11px;font-weight:600;padding:0 6px;border-radius:4px;margin-right:4px;vertical-align:1px}
.ev-verified_policy{background:var(--strong-bg);color:var(--strong-ink)}
.ev-supporting_evidence{background:var(--good-bg);color:var(--good-ink)}
.ev-tentative_signal{background:var(--stretch-bg);color:var(--stretch-ink)}
.ev-unknown{border:1px solid var(--line);color:var(--muted)}
.lvl{font-size:12px;color:var(--muted)}
.srcs{font-size:12px;color:var(--muted)}
.srcs a{color:var(--muted)}
#saveMsg{position:sticky;top:8px;z-index:2;margin:0;font-size:14px;padding:0}
#saveMsg:not(:empty){display:flex;gap:10px;align-items:center;justify-content:space-between;background:var(--ink);color:var(--bg);padding:10px 12px;border-radius:10px;margin:0 0 10px;box-shadow:0 2px 10px rgba(0,0,0,.18)}
#saveMsg.err:not(:empty){background:var(--warn-bg);color:var(--warn-ink);box-shadow:none}
#saveMsg button{font:inherit;font-size:13px;font-weight:600;background:none;color:inherit;border:1px solid currentColor;border-radius:8px;padding:5px 12px;cursor:pointer;min-height:36px;flex:none}
.acts button[disabled]{opacity:.6;cursor:progress}
.co-prof{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 14px}
.co-prof h2{border:0;margin:0 0 6px;padding:0}
.co-prof h3{font-size:13px;margin:12px 0 4px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.co-prof ul{margin:0;padding-left:18px;font-size:14px}
.co-prof li{margin:3px 0}
.co-prof p{margin:4px 0;font-size:14px}
.co-prof{scroll-margin-top:12px}
.jump{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px}
.jump a{font-size:13px;color:var(--ink);text-decoration:none;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 10px}
.jump a:hover{border-color:var(--ink)}
.top-link{text-align:right;font-size:12px}
.nw{white-space:nowrap}
.top-link a{color:var(--muted)}
"""

JS = r"""
document.querySelectorAll('.chips button').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.chips button').forEach(x=>x.setAttribute('aria-pressed',x===b));
  const r=b.dataset.r;document.querySelectorAll('.card').forEach(c=>{c.hidden=!!r&&c.dataset.r!==r});
}));

// Check-offs: saved to the repo through /api/status, so they sync across devices.
// If the site owner set STATUS_TOKEN, open the page once as .../#token=<token> on each device;
// the token is kept in this browser and removed from the address bar.
const LABEL={applied:'Applied',not_interested:'Not interested'};
let TOKEN='';
function readToken(){
  const m=location.hash.match(/token=([^&]+)/);
  if(m){TOKEN=decodeURIComponent(m[1]);history.replaceState(null,'',location.pathname+location.search);}
  try{if(m)localStorage.setItem('statusToken',TOKEN);else TOKEN=TOKEN||localStorage.getItem('statusToken')||'';}catch(e){}
  return !!m;
}
readToken();
let msgTimer;
function msg(t,opts){
  const el=document.getElementById('saveMsg');if(!el)return;
  clearTimeout(msgTimer);el.textContent='';el.className=opts&&opts.err?'err':'';
  if(!t)return;
  const span=document.createElement('span');span.textContent=t;el.append(span);
  if(opts&&opts.undo){
    const b=document.createElement('button');b.type='button';b.textContent='Undo';
    b.addEventListener('click',()=>{msg('');opts.undo();});el.append(b);
    if(opts.focus)b.focus();
  }
  if(!(opts&&opts.err))msgTimer=setTimeout(()=>{if(!el.contains(document.activeElement))el.textContent='';},8000);
}
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
    const el=document.getElementById('n-'+s);if(el)el.textContent=n;
  }
  const o=document.getElementById('n-open');
  if(o)o.textContent=document.querySelectorAll('#list .card').length;
}
async function api(method,body){
  const h={'Content-Type':'application/json'};if(TOKEN)h['X-Status-Token']=TOKEN;
  const r=await fetch('/api/status',{method,headers:h,body:body&&JSON.stringify(body)});
  if(!r.ok){const e=new Error(r.status);e.status=r.status;throw e;}
  return r.json();
}
function applyAll(data){
  document.querySelectorAll('.card[data-key]').forEach(c=>place(c,data[c.dataset.key]));
  counts();
}
async function mark(card,s,fromKeyboard){
  const prev=card.dataset.s?{status:card.dataset.s,at:(card.querySelector('.stamp')||{}).textContent}:undefined;
  const btns=card.querySelectorAll('.acts button');btns.forEach(b=>b.disabled=true);
  const title=(card.querySelector('.title')||{}).textContent||'';
  place(card,s?{status:s,at:new Date().toISOString().slice(0,10)}:undefined);counts();msg('');
  try{
    applyAll(await api('POST',{key:card.dataset.key,status:s}));
    // The card has moved into a (possibly collapsed) section: confirm it and offer Undo here.
    msg(s?`Marked ${LABEL[s]}: ${title}`:`Moved back to open matches: ${title}`,
        {undo:()=>mark(card,prev?prev.status:null,true),focus:fromKeyboard});
  }catch(e){place(card,prev);counts();
    msg(e.status===401?'Not saved: this device has no access token. Open your private link (…/#token=…) once, then try again.'
      :'Not saved - check your connection and try again.',{err:true});}
  finally{btns.forEach(b=>b.disabled=false);}
}
function sync(){
  api('GET').then(d=>{applyAll(d);const el=document.getElementById('saveMsg');if(el&&el.className==='err')msg('');})
    .catch(e=>{if(e.status===401)msg('Marks are read-only on this device: open your private link (…/#token=…) once to enable saving.',{err:true});});
}
if(document.querySelector('.acts')){
  sync();
  // The private link pasted into a tab that already shows the page only changes the hash.
  window.addEventListener('hashchange',()=>{if(readToken())sync();});
  document.addEventListener('click',ev=>{
    const btn=ev.target.closest('.acts button');if(!btn||btn.disabled)return;
    mark(btn.closest('.card'),btn.dataset.s||null,ev.detail===0);
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


def _url(u: str) -> str:
    """Only http(s) links reach an href: a board can't inject javascript: or data: URLs."""
    return u if isinstance(u, str) and re.match(r"(?i)https?://", u.strip()) else "#"


EV_LABEL = {"verified_policy": "Verified policy", "supporting_evidence": "Supporting evidence",
            "tentative_signal": "Tentative signal", "unknown": "Unknown"}
FRESH = {"stale": "past its refresh date - may be out of date",
         "changed": "an official page it relies on has changed since - may be out of date"}


def _ev(label: str) -> str:
    label = label if label in EV_LABEL else "unknown"
    return f'<span class="ev ev-{label}">{EV_LABEL[label]}</span>'


def _key() -> str:
    from .scorer import NO_MEANING, RATING_GUIDE
    rows = "".join(f'<dt><span class="pill {r}">{r}</span></dt><dd>{e(what)}</dd>'
                   f'<dd class="do">What to do: {e(do)}</dd>' for r, (what, do) in RATING_GUIDE.items())
    rows += f'<dt><b>No</b></dt><dd>{e(NO_MEANING)}</dd>'
    evs = "".join(f"<li>{_ev(k)} {e(v)}</li>" for k, v in (
        ("verified_policy", "the company documents it (a policy or program exists - not proof every team practises it)"),
        ("supporting_evidence", "credible independent context about how it works in practice"),
        ("tentative_signal", "indirect, partial or self-reported - treat as a lead to check"),
        ("unknown", "no reliable evidence either way; never counted against a company")))
    return ('<details class="key"><summary>How to read a card</summary>'
            f'<dl>{rows}</dl><p class="legend">The rating is about the <b>role</b>. Under each job: ✓ where '
            'you fit, ✗ a gap or risk, ~ something the posting leaves unclear. A role that fails a hard '
            'requirement is never shown, however good the company.</p>'
            '<p class="legend"><b>Company fit</b> (High / Medium / Low / Unknown) is separate: it weighs '
            'learning, employee support, how distinctive the work is and how plausible a genuine interest '
            'is (weights in preferences.toml). It only orders matches within the same rating. Unknown '
            'means too little evidence, not a bad company.</p>'
            f'<p class="legend"><b>Evidence labels</b></p><ul class="legend">{evs}</ul></details>')


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


def _badges(m: dict) -> str:
    out = []
    # One eligibility badge: the strongest thing the posting confirms, else "unclear".
    if m.get("eligibility") == "eligible":
        out.append('<span class="badge ok">✓ Eligible on stated requirements</span>')
    elif m.get("undergrad") == "yes":
        out.append('<span class="badge ok">✓ Open to bachelor\'s grads</span>')
    elif m.get("eligibility") == "unclear" or m.get("undergrad") == "unclear":
        out.append('<span class="badge">Eligibility not stated</span>')
    cf = m.get("company_fit")
    if cf in ("High", "Medium", "Low"):          # "Unknown" adds nothing to a card
        out.append(f'<span class="badge">Company fit: {e(cf)}</span>')
    if m.get("provisional"):
        out.append('<span class="badge pend">Provisional - AI rating pending</span>')
    return f'<div class="badges">{"".join(out)}</div>' if out else ""


def _more(m: dict, co: dict) -> tuple[str, str]:
    """(visible 'Why <company>' line, expandable details). Job-specific write-ups come first;
    company-level research fills gaps and is labelled as such."""
    company = e(m["company"])
    why_co = m.get("why_company") or ""
    whyline = ""
    if why_co:
        whyline = f'<p class="whyco"><b>Why {company}:</b> {e(why_co)}</p>'
    elif co.get("differentiator"):
        f = co["differentiator"]
        whyline = (f'<p class="whyco"><b>About {company}:</b> {e(f["text"])} '
                   f'<span class="lvl">(company-level)</span></p>')
    rows = []
    if m.get("why_role"):
        rows.append(f"<dt>Why this role</dt><dd>{e(m['why_role'])}</dd>")
    if m.get("support"):
        rows.append(f"<dt>Support &amp; working environment</dt><dd>{_ev(m.get('support_evidence', 'unknown'))}"
                    f"{e(m['support'])}</dd>")
    elif co.get("support"):
        f = co["support"]
        rows.append(f"<dt>Support &amp; working environment</dt><dd>{_ev(f['evidence'])}{e(f['text'])} "
                    f'<span class="lvl">(company-level)</span></dd>')
    if m.get("development"):
        rows.append(f"<dt>Development</dt><dd>{e(m['development'])}</dd>")
    elif co.get("development"):
        f = co["development"]
        rows.append(f"<dt>Development</dt><dd>{_ev(f['evidence'])}{e(f['text'])} "
                    f'<span class="lvl">(company-level)</span></dd>')
    inv = list(m.get("investigate") or [])
    if co.get("tradeoff") and not inv:
        inv.append(co["tradeoff"]["text"] + " (company-level)")
    if inv:
        rows.append("<dt>Check before applying</dt><dd><ul>" + "".join(f"<li>{e(x)}</li>" for x in inv) + "</ul></dd>")
    if m.get("eligibility_note"):
        rows.append(f"<dt>Eligibility</dt><dd>{e(m['eligibility_note'])}</dd>")
    for a in m.get("adjustments") or []:
        rows.append(f"<dt>Rating note</dt><dd>{e(a)}</dd>")
    if co:
        srcs = " · ".join(f'<a href="{e(_url(x["url"]))}" target="_blank" rel="noopener noreferrer">{e(x.get("title") or x["url"])[:60]}</a>'
                          for x in co.get("sources", []))
        fresh = FRESH.get(co.get("freshness", ""), "")
        rows.append(f'<dt>Company research</dt><dd class="srcs">Researched {e(co.get("researched_on", "?"))}'
                    f'{" - " + e(fresh) if fresh else ""}. <a href="companies#{e(co["slug"])}">Full {company} profile</a>'
                    f'{" · Sources: " + srcs if srcs else ""}</dd>')
    else:
        rows.append('<dt>Company research</dt><dd class="srcs">No company profile yet - judged on the posting alone.</dd>')
    details = (f'<details class="more"><summary>Role, company and what to check</summary><dl>{"".join(rows)}</dl></details>'
               if rows else "")
    return whyline, details


def _card(m: dict, closed_on: str | None = None, home: str = "", st: dict | None = None,
          order: int | None = None, co: dict | None = None) -> str:
    flag = '<span class="flag">Start date may be before summer 2027</span>' if m.get("early_start") else ""
    posted = f" · posted {e(m['posted'])}" if m.get("posted") else ""
    found = f" · found {e(_date(m['first_seen']))}" if m.get("first_seen") else ""
    tag = f'<span class="closedtag">Closed {e(_date(closed_on))}</span>' if closed_on else ""
    s_attr = f' data-s="{e(st["status"])}"' if st else ""
    stamp = f'{"Applied" if st["status"] == "applied" else "Not interested"} {st.get("at", "")}' if st else ""
    title = e(m["title"])
    acts = (f'<div class="acts"><button type="button" class="mark" data-s="applied" aria-label="Mark {title} as applied">✓ Applied</button>'
            f'<button type="button" class="mark" data-s="not_interested" aria-label="Mark {title} as not interested">✗ Not interested</button>'
            f'<button type="button" class="undo" data-s="" aria-label="Undo mark on {title}">Undo</button>'
            f'<span class="stamp">{e(stamp)}</span></div>')
    whyline, details = _more(m, co or {})
    return (f'<article class="card{" closed" if closed_on else ""}" id="{e(m["code"])}" data-r="{e(m["rating"])}"'
            f' data-key="{e(m.get("key", ""))}" data-home="{e(home)}"'
            f'{f" data-i={order}" if order is not None else ""}{s_attr}>'
            f'<div class="top"><span class="co">P{m["priority"]} · {e(m["company"])}</span>'
            f'<span>{tag} <span class="pill {e(m["rating"])}">{e(m["rating"])}</span></span></div>'
            f'<a class="title" href="{e(_url(m["url"]))}" target="_blank" rel="noopener noreferrer">{title}</a>'
            f'<div class="meta" title="{e(m["location"])}">{e(_short_loc(m["location"]) or "Location not listed")}'
            f'{found}{posted}</div>{_badges(m)}'
            f'<p class="reason">{e(m["reason"])}</p>{_pointers(m.get("pointers"))}{whyline}{details}{flag}{acts}</article>')


def _health(run: dict) -> str:
    rows = []
    for co, s in run.get("sources", {}).items():
        if s.get("error"):
            rows.append(f'<tr><td>{e(co)}</td><td class="err" colspan="2">failed: {e(s["error"][:140])}</td></tr>')
        elif s.get("incomplete"):
            rows.append(f'<tr><td>{e(co)}</td><td class="n">{s["fetched"]} listed</td>'
                        f'<td class="err">incomplete ({e(s["incomplete"][:80])}) - nothing closed</td></tr>')
        else:
            rows.append(f'<tr><td>{e(co)}</td><td class="n">{s["fetched"]} listed</td><td class="n">{s["new"]} new</td></tr>')
    notes = "".join(f"<p>{e(n)}</p>" for n in run.get("notes", []))
    return (f'<details><summary>Scan details · {run.get("screened_out", 0)} new postings screened out</summary>'
            f'{notes}<table>{"".join(rows)}</table></details>')


DISMISS_NOTE = "7 days after you mark them"


def write_site(site_dir: Path, matches: list[dict], runs: list[dict], site_url: str,
               closed: list[dict] | None = None, status: dict | None = None,
               companies: dict | None = None, profiles: dict | None = None) -> None:
    """index.html: one long list of every open match (newest find first), plus the Applied /
    Not interested / Closed drawers. log.html: the scan-by-scan audit log. companies.html: the
    company research behind the cards."""
    closed = closed or []
    status = {k: v for k, v in (status or {}).items() if isinstance(v, dict) and v.get("status") in ("applied", "not_interested")}
    companies = companies or {}
    done = {"applied": [], "not_interested": []}
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "j").mkdir(exist_ok=True)
    latest = runs[-1] if runs else {}
    cards = []
    for i, m in enumerate(matches):
        st = status.get(m.get("key", ""))
        html_ = _card(m, None, "list", st, i, companies.get(m["company"]))
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
        + ("".join(_card(c, c["closed_on"], "closed-list", status.get(c.get("key", "")), None,
                         companies.get(c["company"])) for c in closed)
           or '<p class="empty">None yet.</p>') + '</div></details>')
    chips = ('<div class="chips" role="group" aria-label="Filter by rating">'
             '<button type="button" aria-pressed="true" data-r="">All</button>'
             '<button type="button" aria-pressed="false" data-r="Strong">Strong</button>'
             '<button type="button" aria-pressed="false" data-r="Good">Good</button>'
             '<button type="button" aria-pressed="false" data-r="Stretch">Stretch</button></div>')
    lg = latest.get("log", {})
    last = (f'Last scan {e(latest.get("label", "never"))}'
            + (f' · {lg["fetched"]:,} postings checked' if lg.get("fetched") else "") + ".")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Job Scanner</title><style>{CSS}</style></head><body><main>
<h1>Job Scanner</h1>
<p class="sub">Early-career consulting &amp; strategy openings at target companies, updated every scan.
{last} <a href="log">Scan log</a> · <a href="companies">Company research</a><br>
"Not interested" cards are removed {DISMISS_NOTE}.</p>
<p id="saveMsg" role="status" aria-live="polite"></p>
{_key()}{chips}{listing}{done_html}{closed_html}
</main><script>{JS}</script></body></html>"""
    (site_dir / "index.html").write_text(page)
    write_log(site_dir, runs)
    write_companies(site_dir, profiles or {}, companies)

    shown = matches + closed
    live = {m["code"] for m in shown}
    for old in (site_dir / "j").glob("*.html"):
        if old.stem not in live:
            old.unlink()
    for m in shown:
        (site_dir / "j" / f"{m['code']}.html").write_text(
            f'<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
            f'<meta http-equiv="refresh" content="0;url={e(_url(m["url"]))}">'
            f'<title>{e(m["company"])} - {e(m["title"])}</title>'
            f'<a href="{e(_url(m["url"]))}">{e(m["title"])}</a>')
    (site_dir / "latest.json").write_text(json.dumps(latest, indent=1))


def _jobline(x: dict) -> str:
    rating = f' <span class="pill {e(x["rating"])}">{e(x["rating"])}</span>' if x.get("rating") else ""
    title = (f'<a href="{e(_url(x["url"]))}" target="_blank" rel="noopener noreferrer">{e(x["title"])}</a>'
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
            if lg.get("retried"):
                facts.insert(2, f'{lg["retried"]} re-tried for an AI rating')
            if lg.get("awaiting_rating"):
                facts.append(f'{lg["awaiting_rating"]} waiting for an AI rating')
            inc = lg.get("incomplete") or {}
            if inc:
                facts.append(f'{len(inc)} compan{"ies" if len(inc) != 1 else "y"} scanned incompletely '
                             '(no postings closed there)')
            lists = "".join(
                f'<p class="lh">{label}</p><ul>{"".join(_jobline(x) for x in lg[k])}</ul>'
                for k, label in (("new_matches", "New matches"), ("rerated", "Re-rated"),
                                 ("closed", "Closed this scan"),
                                 ("removed", "Removed after the AI rated them")) if lg.get(k))
            if inc:
                lists += ('<p class="lh">Incomplete scans</p><ul>'
                          + "".join(f"<li>{e(k)}: {e(v)}</li>" for k, v in sorted(inc.items())) + "</ul>")
            if lg.get("no_profile"):
                lists += (f'<p class="lh">No company profile yet</p><p class="facts">{e(", ".join(lg["no_profile"]))} '
                          '- matches there are judged on the posting alone.</p>')
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


def write_companies(site_dir: Path, profiles: dict, summaries: dict) -> None:
    """companies.html: every researched profile with its evidence labels, unknowns and sources."""
    from .intel import SECTIONS
    from collections import Counter
    blocks = []
    first = lambda m: re.split(r"(?<=\.)\s", m or "", maxsplit=1)[0]
    common = Counter(first(p.get("method")) for p in profiles.values()).most_common(1)
    common_method = common[0][0] if common and common[0][1] > 1 else ""
    for name in sorted(profiles, key=str.lower):
        p = profiles[name]
        src = {x["id"]: x for x in p.get("sources", [])}
        ref = lambda ids: " ".join(f'<a href="#{e(p["_slug"])}-s{e(i)}">[{e(i)}]</a>' for i in ids)
        secs = []
        for sec, title in SECTIONS.items():
            items = p.get(sec) or []
            if items:
                secs.append(f"<h3>{e(title)}</h3><ul>" + "".join(
                    f'<li>{_ev(f["evidence"])}{e(f["text"])}'
                    + (f' <span class="lvl">- {e(f["significance"])}</span>' if f.get("significance") else "")
                    + f' <span class="srcs">{ref(f["sources"])}</span></li>' for f in items) + "</ul>")
        if p.get("unknowns"):
            secs.append("<h3>Unknown / not established</h3><ul>" + "".join(f"<li>{e(u)}</li>" for u in p["unknowns"]) + "</ul>")
        sl = "".join(f'<li id="{e(p["_slug"])}-s{e(x["id"])}">[{e(x["id"])}] <a href="{e(_url(x["url"]))}" '
                     f'target="_blank" rel="noopener noreferrer">{e(x.get("title") or x["url"])}</a> '
                     f'<span class="lvl">{e(x.get("type", ""))}'
                     f'{", published " + e(x["published"]) if x.get("published") else ""}'
                     f'{", accessed " + e(x["accessed"]) if x.get("accessed") else ""}</span></li>'
                     for x in src.values())
        fresh = FRESH.get((summaries.get(name) or {}).get("freshness", ""), "")
        method = p.get("method", "")
        note = method[len(common_method):].strip() if common_method and method.startswith(common_method) else method
        blocks.append(
            f'<section class="co-prof" id="{e(p["_slug"])}"><h2>{e(name)}</h2>'
            f'<p class="lvl">Researched <span class="nw">{e(p.get("researched_on", "?"))}</span> · refresh after '
            f'<span class="nw">{e(p.get("refresh_after", "?"))}</span>{" · " + e(fresh) if fresh else ""}'
            f'{" · " + e(note) if note else ""}</p>'
            f'<p>{e(p.get("overview", ""))}</p>{"".join(secs)}<h3>Sources</h3><ul class="srcs">{sl}</ul>'
            '<p class="top-link"><a href="#top">Back to top</a></p></section>')
    jump = "".join(f'<a href="#{e(profiles[n]["_slug"])}">{e(n)}</a>' for n in sorted(profiles, key=str.lower))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Company Research</title><style>{CSS}</style></head><body><main id="top">
<h1>Company research</h1>
<p class="sub">What each company documents about itself, what independent sources add, and what is
still unknown. A verified policy shows that a policy exists, not that every team practises it.
Research is cached and reused by every scan; dates show how current it is. <a href="./">Back to matches</a></p>
{f'<p class="sub">How it was gathered: {e(common_method)}</p>' if common_method else ""}
{f'<nav class="jump" aria-label="Jump to a company">{jump}</nav>' if blocks else ""}
{"".join(blocks) or '<p class="empty">No company profiles yet.</p>'}
</main></body></html>"""
    (site_dir / "companies.html").write_text(page)
