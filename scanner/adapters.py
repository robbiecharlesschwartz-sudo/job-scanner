"""One adapter per job-board platform. Each takes a company config dict and returns Jobs.

Adapters only fetch and normalise. Filtering happens in filters.py.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


@dataclass
class Job:
    company: str
    priority: int
    job_id: str
    title: str
    location: str
    url: str
    posted: str = ""          # ISO date or the board's own text ("Posted 3 Days Ago")
    description: str = ""     # plain text, may be empty
    meta: str = ""            # board-specific level / practice hints
    # filled in later
    verdict: str = ""         # clear | borderline
    rating: str = ""          # Strong | Good | Stretch
    reason: str = ""
    early_start: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.company}|{self.job_id}"


# ---------------------------------------------------------------- http helpers

def _request(url: str, data: bytes | None = None, headers: dict | None = None,
             method: str | None = None, timeout: int = 40, retries: int = 2) -> str:
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404, 410, 422):
                break
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def get_json(url: str, **kw):
    return json.loads(_request(url, headers={"Accept": "application/json"}, **kw))


def post_json(url: str, body: dict, headers: dict | None = None):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    h.update(headers or {})
    return json.loads(_request(url, data=json.dumps(body).encode(), headers=h, method="POST"))


def get_text(url: str) -> str:
    return _request(url)


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)                       # Greenhouse double-escapes its HTML
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def _dedupe(jobs: list[Job]) -> list[Job]:
    seen, out = set(), []
    for j in jobs:
        if j.job_id not in seen:
            seen.add(j.job_id)
            out.append(j)
    return out


def _mk(c: dict, **kw) -> Job:
    kw["title"] = re.sub(r"\s+", " ", html.unescape(kw.get("title") or "")).strip()
    kw["location"] = re.sub(r"\s+", " ", html.unescape(kw.get("location") or "")).strip()
    job = Job(company=c["name"], priority=int(c.get("priority", 9)), **kw)
    job.extra["platform"] = c["platform"]
    if c.get("associate_is_senior"):
        job.extra["associate_is_senior"] = True
    return job


# ---------------------------------------------------------------- JSON platforms

def greenhouse(c: dict) -> list[Job]:
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{c['board']}/jobs?content=true")
    return [_mk(c, job_id=str(j["id"]), title=j["title"],
                location=(j.get("location") or {}).get("name", ""),
                url=j["absolute_url"], posted=(j.get("first_published") or j.get("updated_at") or "")[:10],
                description=strip_html(j.get("content")))
            for j in d.get("jobs", [])]


def lever(c: dict) -> list[Job]:
    d = get_json(f"https://api.lever.co/v0/postings/{c['board']}?mode=json")
    out = []
    for j in d:
        cat = j.get("categories") or {}
        out.append(_mk(c, job_id=j["id"], title=j["text"],
                       location=", ".join(cat.get("allLocations") or [cat.get("location", "")]),
                       url=j["hostedUrl"],
                       posted=time.strftime("%Y-%m-%d", time.gmtime(j.get("createdAt", 0) / 1000)),
                       description=j.get("descriptionPlain", ""), meta=cat.get("commitment", "")))
    return out


def ashby(c: dict) -> list[Job]:
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{c['board']}")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        if j.get("isRemote"):
            locs.append("Remote")
        addr = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
        loc = "; ".join(x for x in locs if x)
        if addr and addr not in loc:
            loc += f" ({addr})"
        out.append(_mk(c, job_id=j["id"], title=j["title"], location=loc, url=j["jobUrl"],
                       posted=(j.get("publishedAt") or "")[:10],
                       description=j.get("descriptionPlain", ""),
                       meta=f"{j.get('department', '')} {j.get('employmentType', '')}".strip()))
    return out


def workday(c: dict) -> list[Job]:
    base = f"https://{c['tenant']}.{c['host']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}"
    # Restrict to the US when the tenant exposes a country facet (most do).
    facets = {}
    first = post_json(f"{api}/jobs", {"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}})
    def walk(param, values):
        for v in values or []:
            if v.get("descriptor") in ("United States of America", "United States", "USA"):
                facets.setdefault(param, []).append(v["id"])
            walk(v.get("facetParameter", param), v.get("values"))
    walk(None, first.get("facets"))
    facets = {k: v for k, v in facets.items() if k and "country" in k.lower()} or {}

    max_pages = int(c.get("max_pages", 60))   # 1,200 per search
    out = []
    for q in c.get("queries") or [""]:
        total = 0
        for page in range(max_pages):
            d = post_json(f"{api}/jobs", {"limit": 20, "offset": page * 20, "searchText": q,
                                          "appliedFacets": facets})
            total = d.get("total") or total     # many tenants send the total on page 1 only
            posts = d.get("jobPostings") or []
            for j in posts:
                if c.get("title_must") and not re.search(c["title_must"], j.get("title", ""), re.I):
                    continue                    # shared parent-company board (e.g. Oliver Wyman on MMC)
                path = j.get("externalPath", "")
                jid = (j.get("bulletFields") or [path])[0]
                out.append(_mk(c, job_id=jid, title=j.get("title", ""),
                               location=j.get("locationsText", ""),
                               url=f"{base}/en-US/{c['site']}{path}", posted=j.get("postedOn", ""),
                               extra={"detail": f"{api}{path}"}))
            if len(posts) < 20 or (page + 1) * 20 >= total:
                break
    return _dedupe(out)


def workday_detail(job: Job) -> None:
    """Fill description + full location list for one Workday posting (called for candidates only)."""
    d = get_json(job.extra["detail"]).get("jobPostingInfo", {})
    job.description = strip_html(d.get("jobDescription"))
    locs = [d.get("location", "")] + list(d.get("additionalLocations") or [])
    if any(locs):
        job.location = "; ".join(x for x in locs if x)
    country = (d.get("country") or {}).get("descriptor", "")
    if country:
        job.location += f" ({country})"
        job.extra["country_us"] = country in ("United States of America", "United States")
    job.posted = d.get("startDate", job.posted)
    job.meta = d.get("timeType", "")


def page_detail(job: Job) -> None:
    """Avature (Deloitte) and Radancy (EY) list "Multiple Locations"; the posting page has the
    real city list and the description. Yello (Kearney), KPMG and Slalom listings carry no
    description at all.
    Called for candidates only."""
    h = get_text(job.url)
    body = h[h.find("ats-description"):] if "ats-description" in h else h
    if job.extra.get("platform") in ("yello", "kpmg", "avature_rss", "successfactors"):   # keep listing location
        job.description = strip_html(re.sub(r"<(nav|header|footer)\b.*?</\1>", " ", h, flags=re.S | re.I))[:12000]
        return
    text = strip_html(re.sub(r"<(nav|header|footer)\b.*?</\1>", " ", body, flags=re.S | re.I))
    job.description = text[:12000]
    flat = strip_html(h).replace("\n", " | ")
    m = re.search(r"Locations?:\s*\|?\s*([^|]{3,600})", flat)
    if m:
        job.location = m.group(1).strip()
    else:
        cities = re.findall(r"[A-Z][A-Za-z .'-]+, [A-Z][A-Za-z ]+, United States", strip_html(h))
        if cities:
            job.location = "; ".join(dict.fromkeys(c.strip() for c in cities))


def phenom_detail(job: Job) -> None:
    """Phenom (BCG) job pages embed the full description as a JSON string."""
    h = get_text(job.url)
    m = re.search(r'"description":"((?:[^"\\]|\\.)*)"', h)
    if m:
        job.description = strip_html(json.loads(f'"{m.group(1)}"'))[:12000]


def oracle_detail(job: Job) -> None:
    host, site, rid = job.extra["oracle"]
    finder = urllib.parse.quote(f'ById;Id="{rid}",siteNumber={site}', safe="=;,")
    d = get_json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                 f"?expand=all&onlyData=true&finder={finder}").get("items") or [{}]
    d = d[0]
    parts = [d.get(k) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                "ExternalQualificationsStr", "CorporateDescriptionStr")]
    text = "\n".join(strip_html(x) for x in parts if x)
    if text:
        job.description = text
    if d.get("StudyLevel"):
        job.meta = f"{job.meta} | study level: {d['StudyLevel']}".strip(" |")


def oracle(c: dict) -> list[Job]:
    host, site = c["host"], c["site"]
    out = []
    for q in c.get("queries") or [""]:
        for page in range(int(c.get("max_pages", 8))):
            parts = [f"siteNumber={site}", "limit=25", f"offset={page * 25}", "sortBy=POSTING_DATES_DESC"]
            if q:
                parts.append(f'keyword="{q}"')
            finder = "findReqs;" + ",".join(parts)
            url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                   f"?onlyData=true&expand=requisitionList.secondaryLocations"
                   f"&finder={urllib.parse.quote(finder, safe='=;,')}")
            items = get_json(url).get("items") or [{}]
            reqs = items[0].get("requisitionList") or []
            for r in reqs:
                locs = [r.get("PrimaryLocation", "")] + [s.get("Name", "") for s in r.get("secondaryLocations") or []]
                out.append(_mk(c, job_id=str(r["Id"]), title=r.get("Title", ""),
                               location="; ".join(x for x in locs if x) + f" ({r.get('PrimaryLocationCountry', '')})",
                               url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}",
                               posted=r.get("PostedDate", ""),
                               description=strip_html(r.get("ShortDescriptionStr")),
                               meta=r.get("JobFamily", "") or "",
                               extra={"oracle": (host, site, str(r["Id"]))}))
            if len(reqs) < 25:
                break
    return _dedupe(out)


def ibm(c: dict) -> list[Job]:
    out, size = [], 100
    for page in range(10):
        must = [{"term": {"field_keyword_05": "United States"}}]
        if c.get("category"):
            must.append({"term": {"field_keyword_08": c["category"]}})
        d = post_json("https://www-api.ibm.com/search/api/v2", {
            "appId": "careers", "scopes": ["careers2"], "query": {"bool": {"must": must}},
            "size": size, "from": page * size, "sort": [{"_id": "desc"}], "lang": "zz",
            "_source": ["_id", "title", "url", "description", "body", "field_keyword_05", "field_keyword_08",
                        "field_keyword_17", "field_keyword_18", "field_keyword_19"]},
            headers={"Origin": "https://www.ibm.com", "Referer": "https://www.ibm.com/careers/search"})
        hits = d.get("hits", {}).get("hits", [])
        for h in hits:
            s = h["_source"]
            jid = re.search(r"jobId=(\d+)", s.get("url", ""))
            out.append(_mk(c, job_id=jid.group(1) if jid else h["_id"], title=s.get("title", ""),
                           location=f"{s.get('field_keyword_19', '')} ({s.get('field_keyword_05', '')})",
                           url=s.get("url", ""), description=s.get("body") or s.get("description", ""),
                           meta=f"{s.get('field_keyword_18', '')} / {s.get('field_keyword_17', '')}"))
        if len(hits) < size:
            break
    return _dedupe(out)


def jibe(c: dict) -> list[Job]:
    base = c["base"].rstrip("/")
    out = []
    for q in c.get("queries") or [""]:
        for page in range(1, int(c.get("max_pages", 5)) + 1):
            qs = urllib.parse.urlencode({"keywords": q, "page": page, "limit": 100,
                                         "sortBy": "posted_date", "descending": "true"})
            d = get_json(f"{base}/api/jobs?{qs}")
            jobs = d.get("jobs") or []
            for w in jobs:
                j = w.get("data", {})
                out.append(_mk(c, job_id=str(j.get("req_id") or j.get("slug")), title=j.get("title", ""),
                               location=f"{j.get('full_location', '')} ({j.get('country_code', '')})",
                               url=f"{base}/main/jobs/{j.get('slug')}?lang=en-us",
                               posted=(j.get("posted_date") or "")[:10],
                               description=strip_html(j.get("description")),
                               meta=" ".join(c2.get("name", "") for c2 in j.get("categories") or [] if isinstance(c2, dict))))
            if len(jobs) < 100:
                break
    return _dedupe(out)


def sourceflow(c: dict) -> list[Job]:
    base = c["base"].rstrip("/")
    out = []
    for page in range(20):
        d = post_json(f"{base}/_sf/api/v1/jobs/search.json", {"job_search": {
            "query": "", "location": {"address": "", "radius": 100, "region": "", "radius_units": "miles"},
            "filters": {}, "commute_filter": {}, "offset": page * 50, "jobs_per_page": 50}})
        res = d.get("results") or []
        for r in res:
            j = r["job"]
            countries = {l.get("postal_address", {}).get("region_code", "")
                         for l in (j.get("derived_info") or {}).get("locations") or []}
            loc = "; ".join(j.get("addresses") or [])
            if countries - {""}:
                loc += f" ({', '.join(sorted(countries - {''}))})"
            out.append(_mk(c, job_id=str(j.get("external_reference") or j["id"]), title=j["title"],
                           location=loc, url=f"{base}/jobs/{j['url_slug']}/",
                           posted=time.strftime("%Y-%m-%d", time.gmtime(j.get("published_at") or 0)),
                           description=strip_html(j.get("description"))))
        if len(res) < 50 or (page + 1) * 50 >= d.get("total_size", 0):
            break
    return out


# ---------------------------------------------------------------- HTML / feed platforms

def avature(c: dict) -> list[Job]:
    """Avature portals (Deloitte). Server-rendered results, 10 per page."""
    base = c["base"].rstrip("/")
    out = []
    for q in c.get("queries") or [""]:
        path = f"/SearchJobs/{urllib.parse.quote(q)}" if q else "/SearchJobs/"
        for page in range(int(c.get("max_pages", 30))):
            h = get_text(f"{base}{path}?jobOffset={page * 10}")
            arts = re.findall(r'<article class="article--result.*?</article>', h, flags=re.S)
            for a in arts:
                m = re.search(r'href="([^"]*JobDetail/[^"]*?/(\d+))"[^>]*>\s*(.*?)\s*</a>', a, flags=re.S)
                if not m:
                    continue
                spans = [strip_html(s) for s in re.findall(r"<span>(.*?)</span>", a, flags=re.S)]
                out.append(_mk(c, job_id=m.group(2), title=strip_html(m.group(3)),
                               location=spans[-1] if spans else "", url=m.group(1),
                               meta=" | ".join(spans[:-1])))
            total = re.search(r'data-total="(\d+)', h)
            if len(arts) < 10 or (total and (page + 1) * 10 >= int(total.group(1))):
                break
    return _dedupe(out)


def avature_rss(c: dict) -> list[Job]:
    """Avature RSS feed. Only exposes ~20 items and no location; better than nothing."""
    x = get_text(c["base"].rstrip("/") + "/SearchJobs/feed/")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", x, flags=re.S):
        t = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item, flags=re.S)
        link = re.search(r"<link>(.*?)</link>", item)
        if not (t and link):
            continue
        jid = link.group(1).rstrip("/").rsplit("/", 1)[-1]
        city = re.search(r"\(([^)]+)\)\s*$", t.group(1))
        out.append(_mk(c, job_id=jid, title=t.group(1), location=city.group(1) if city else "",
                       url=link.group(1)))
    return out


def radancy(c: dict) -> list[Job]:
    base = c["base"].rstrip("/")
    out = []
    for q in c.get("queries") or [""]:
        for page in range(1, 30):
            qs = urllib.parse.urlencode({
                "CurrentPage": page, "RecordsPerPage": 100, "Keywords": q, "SearchType": 5,
                "SearchResultsModuleName": "Search Results", "SearchFiltersModuleName": "Search Filters"})
            h = get_json(f"{base}/search-jobs/results?{qs}").get("results", "")
            lis = re.findall(r"<li>\s*<a href=\"([^\"]+)\" data-job-id=\"(\d+)\">(.*?)</a>", h, flags=re.S)
            for href, jid, inner in lis:
                t = re.search(r"<h2>(.*?)</h2>", inner, flags=re.S)
                loc = re.search(r'class="job-location">(.*?)</p>', inner, flags=re.S)
                out.append(_mk(c, job_id=jid, title=strip_html(t.group(1) if t else ""),
                               location=strip_html(loc.group(1) if loc else "").replace("Location:", "").strip(),
                               url=base + href))
            pages = re.search(r'data-total-pages="(\d+)"', h)
            if not lis or not pages or page >= int(pages.group(1)):
                break
    return _dedupe(out)


def yello(c: dict) -> list[Job]:
    base = c["base"].rstrip("/")
    out = []
    for page in range(1, 30):
        d = get_json(f"{base}/job_boards/{c['board']}/search?query=&filters=&page_number={page}")
        items = re.findall(r'<li class="search-results__item">(.*?)</li>', d.get("html", ""), flags=re.S)
        for it in items:
            m = re.search(r'href="(/jobs/([^"?]+)[^"]*)"[^>]*>(.*?)</a>', it, flags=re.S)
            if not m:
                continue
            spans = [strip_html(s) for s in re.findall(r"<span>(.*?)</span>", it, flags=re.S)]
            posted = re.search(r'post-time[^>]*>(.*?)</div>', it, flags=re.S)
            out.append(_mk(c, job_id=m.group(2), title=strip_html(m.group(3)),
                           location=" / ".join(spans[1:]) if len(spans) > 1 else "",
                           url=base + html.unescape(m.group(1)),
                           posted=strip_html(posted.group(1)) if posted else "",
                           meta=spans[0] if spans else ""))
        if not items or not d.get("more_requisitions"):
            break
    return _dedupe(out)


def kpmg(c: dict) -> list[Job]:
    base = "https://www.kpmguscareers.com"
    out = []
    for page in range(1, int(c.get("max_pages", 80)) + 1):
        h = get_text(f"{base}/job-search/?order=recent&spage={page}")
        items = re.findall(r'<div class="search--item(.*?)<div class="item--arrow">', h, flags=re.S)
        new = 0
        for it in items:
            m = re.search(r'href="(/jobdetail/\?jobId=(\d+))"', it)
            title = re.search(r'<div class="h5[^"]*">(.*?)</div>', it, flags=re.S)
            sub = re.search(r'<div class="list-view.*?<div class="text-xs[^"]*">(.*?)</div>', it, flags=re.S)
            level = re.search(r'eyebrow text-white">(.*?)</div>', it, flags=re.S)
            if not (m and title) or any(j.job_id == m.group(2) for j in out):
                continue
            new += 1
            practice, _, locs = strip_html(sub.group(1) if sub else "").partition("|")
            out.append(_mk(c, job_id=m.group(2), title=strip_html(title.group(1)), location=locs.strip(),
                           url=base + m.group(1),
                           meta=f"{strip_html(level.group(1)) if level else ''} / {practice.strip()}"))
        if not new:
            break
    return out



def mckinsey(c: dict) -> list[Job]:
    """McKinsey's own careers API: every posting with its full text, 100 per page (start = page no.)."""
    api = "https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/jobs/search"
    out = []
    for page in range(1, 20):
        d = json.loads(_request(f"{api}?pageSize=100&start={page}&lang=en",
                                headers={"Origin": "https://www.mckinsey.com", "Accept": "application/json"}))
        docs = d.get("docs") or []
        for j in docs:
            if "United States" not in (j.get("countries") or []):
                continue
            out.append(_mk(c, job_id=str(j["jobID"]), title=j.get("title", ""),
                           location="; ".join(j.get("cities") or []) + " (US)",
                           url=f"https://www.mckinsey.com/careers/search-jobs/jobs/{j.get('friendlyURL', j['jobID'])}",
                           description=strip_html(" ".join(j.get(k) or "" for k in
                                                           ("whoYouWillWorkWith", "whatYouWillDo", "yourBackground"))),
                           meta=" / ".join((j.get("jobSkillGroup") or []) + (j.get("jobSkillCode") or []))))
        if len(docs) < 100:
            break
    return _dedupe(out)


def phenom(c: dict) -> list[Job]:
    """Phenom career sites (BCG): the page's own search widget endpoint, filtered to one country."""
    base = c["base"].rstrip("/")
    out, size = [], 100
    for page in range(20):
        d = post_json(f"{base}/widgets", {
            "lang": c.get("lang", "en_global"), "deviceType": "desktop", "country": c.get("site_country", "global"),
            "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "",
            "from": page * size, "jobs": True, "counts": True, "all_fields": ["country"], "size": size,
            "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page20",
            "siteType": "external", "keywords": "", "global": True,
            "selected_fields": {"country": [c.get("country", "United States of America")]}, "locationData": {}})
        jobs = ((d.get("refineSearch") or {}).get("data") or {}).get("jobs") or []
        for j in jobs:
            locs = j.get("multi_location") or [j.get("location", "")]
            out.append(_mk(c, job_id=str(j.get("jobId") or j.get("reqId")), title=j.get("title", ""),
                           location="; ".join(locs),
                           url=f"{base}/{c.get('path', 'global/en')}/job/{j.get('jobId')}",
                           posted=(j.get("postedDate") or "")[:10],
                           description=strip_html(j.get("descriptionTeaser")),
                           meta=f"{j.get('category', '')} {j.get('type', '')}".strip()))
        if len(jobs) < size:
            break
    return _dedupe(out)


def successfactors(c: dict) -> list[Job]:
    """SAP SuccessFactors career sites (careers.ey.com): HTML search results, 25 rows a page."""
    base = c["base"].rstrip("/")
    root = re.match(r"https?://[^/]+", base).group(0)
    out = []
    for q in c.get("queries") or [""]:
        for page in range(int(c.get("max_pages", 20))):
            qs = urllib.parse.urlencode({"q": q, "locationsearch": c.get("location", ""), "startrow": page * 25})
            h = get_text(f"{base}/search/?{qs}")
            rows = re.findall(r'<tr class="data-row">(.*?)</tr>', h, re.S)
            for r in rows:
                a = re.search(r'href="([^"]+)"[^>]*class="jobTitle-link">(.*?)</a>', r, re.S) or \
                    re.search(r'class="jobTitle-link"[^>]*href="([^"]+)">(.*?)</a>', r, re.S)
                if not a:
                    continue
                loc = re.search(r'class="jobLocation">(.*?)</span>', r, re.S)
                loc = strip_html(loc.group(1)).replace("…", "").replace("\n", " ") if loc else ""
                href = html.unescape(a.group(1))
                jid = re.search(r"/(\d+)/?$", href)
                out.append(_mk(c, job_id=jid.group(1) if jid else href, title=strip_html(a.group(2)),
                               location=loc, url=root + href))
            if len(rows) < 25:
                break
    return _dedupe(out)

ADAPTERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday,
    "oracle": oracle, "ibm": ibm, "jibe": jibe, "sourceflow": sourceflow,
    "avature": avature, "avature_rss": avature_rss, "radancy": radancy, "yello": yello, "kpmg": kpmg,
    "mckinsey": mckinsey, "phenom": phenom, "successfactors": successfactors,
}
