"""Keyword rules. Each posting comes out as reject / borderline / clear.

clear       title has both a target function and an early-career level signal
borderline  one of the two, or a location we can't place -> Claude decides
reject      senior, wrong function, non-US, or asks for 3+ years
"""
from __future__ import annotations

import re

US_STATES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY").split()
US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia"]
US_CITIES = [
    "new york", "nyc", "manhattan", "brooklyn", "jersey city", "hoboken", "stamford", "boston",
    "chicago", "san francisco", "los angeles", "seattle", "atlanta", "dallas", "houston", "austin",
    "denver", "miami", "philadelphia", "washington", "arlington", "mclean", "detroit", "minneapolis",
    "charlotte", "phoenix", "tempe", "san diego", "san jose", "pittsburgh", "cleveland", "columbus",
    "nashville", "st. louis", "kansas city", "salt lake city", "portland", "baltimore", "tampa",
    "orlando", "raleigh", "richmond", "hartford", "short hills", "florham park", "morristown",
    "purchase", "plano", "irving", "frisco", "milwaukee", "indianapolis", "cincinnati", "sacramento",
    "silicon valley", "bay area", "palo alto", "menlo park"]
US_WORDS = ["united states", "usa", "u.s.", "(us)", "(us,", " us)", "us -", "us-", "americas - us",
            "north america"]
NON_US = [
    "canada", "toronto", "montreal", "vancouver", "calgary", "ottawa", "united kingdom", "london",
    "england", "ireland", "dublin", "germany", "berlin", "munich", "frankfurt", "france", "paris",
    "india", "bangalore", "bengaluru", "mumbai", "hyderabad", "pune", "chennai", "gurgaon", "delhi",
    "kolkata", "noida", "singapore", "australia", "sydney", "melbourne", "japan", "tokyo", "china",
    "shanghai", "beijing", "hong kong", "brazil", "são paulo", "sao paulo", "mexico", "argentina",
    "buenos aires", "colombia", "bogota", "spain", "madrid", "italy", "milan", "netherlands",
    "amsterdam", "poland", "warsaw", "philippines", "manila", "egypt", "cairo", "uae", "dubai",
    "saudi", "riyadh", "south africa", "switzerland", "zurich", "sweden", "stockholm", "korea",
    "seoul", "malaysia", "kuala lumpur", "thailand", "bangkok", "indonesia", "jakarta", "vietnam",
    "israel", "costa rica", "guadalajara", "medellin", "belgium", "brussels", "austria", "vienna",
    "portugal", "lisbon", "denmark", "norway", "finland", "czech", "romania", "hungary", "greece",
    "turkey", "chile", "peru", "new zealand", "qatar", "doha", "nigeria", "kenya", "azerbaijan",
    "asia pacific", "emea", "europe", "(gb)", "(ca)", "(in)", "(de)", "(fr)", "(au)", "(br)",
    "(mx)", "(sg)", "(jp)", "(cn)", "(ie)", "(nl)", "(es)", "(az)", "(ph)", "(pl)"]


def is_us(location: str) -> bool | None:
    """True = at least one US location, False = clearly only non-US, None = can't tell."""
    loc = f" {location.lower()} "
    if not location.strip() or re.search(r"\b(multiple|various|\d+) locations?\b", loc):
        return None
    # Adapters append the board's own country code(s), e.g. "(GB)" or "(US, GB)". Trust them.
    codes = re.search(r"\(([A-Z]{2}(?:,\s*[A-Z]{2})*)\)\s*$", location)
    if codes:
        return "US" in [c.strip() for c in codes.group(1).split(",")]
    if any(w in loc for w in US_WORDS) or any(c in loc for c in US_CITIES) or \
            any(re.search(rf"\b{re.escape(s)}\b", loc) for s in US_STATE_NAMES):
        return True
    if any(w in loc for w in NON_US):
        return False
    # Bare state codes last: ", IN" is Indiana on one board and India on another.
    if re.search(r",\s*(%s)\b" % "|".join(US_STATES), location) or \
            re.search(r"\b(%s)\s*[-,]" % "|".join(US_STATES), location):
        return True
    if "remote" in loc:
        return None
    return None


def is_nyc(location: str) -> bool | None:
    """True = at least one location is New York City, False = located but not NYC,
    None = no usable location ("Multiple Locations", blank)."""
    loc = location.strip()
    if not loc or re.fullmatch(r"(?i)(multiple|various|\d+)\s+locations?.*|remote", loc):
        return None
    low = loc.lower()
    if re.search(r"new york city|\bnyc\b|manhattan|brooklyn", low):
        return True
    for seg in re.split(r"[;/|]", low):
        seg = seg.strip()
        if re.match(r"(us\s*-\s*)?(ny\s*[-,]\s*)?new york\b", seg):       # "New York, NY", "NY-New York"
            return True
        if re.search(r"\bny\s*[-,]\s*new york\b|new york,\s*(ny|new york)\b", seg):
            return True
        parts = [x.strip() for x in seg.split(",")]
        if len(parts) >= 3 and "new york" in parts and parts.index("new york") != 1:
            return True                                                    # city list "Boston, Chicago, New York"
    return False


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)


def title_ok(job, f: dict) -> bool:
    """Cheap first pass on title + listed location, before fetching detail pages."""
    consulting = job.priority in f["consulting_priorities"]
    t = job.title
    return not (_any(f["exclude_level"], t)
                or (_any(f["exclude_domain"], t) and not _any(f["domain_rescue"], t))
                or not (_any(f["function_any"], t) or _any(f["function_adjacent"], t)
                        or (consulting and _any(f["function_consulting"], t)))
                or _any(f["bad_start_title"], t)
                or is_nyc(job.location) is False)


def classify(job, f: dict) -> tuple[str, str]:
    """Returns (verdict, why). verdict:
    reject    misses a hard requirement (NYC, level, start date, 3+ yrs/MBA, off-target work)
    clear     on-lane, NYC, and the posting itself says early-career
    candidate on-lane and NYC, level rests on the title -> the AI decides
    stretch   passes the hard rules but is a step off (adjacent work, ~2 yrs) -> the AI may
              rate it Good or Stretch, never Strong"""
    title, desc = job.title, job.description or ""
    consulting = job.priority in f["consulting_priorities"]
    if _any(f["exclude_level"], title):
        return "reject", "senior / intern / wrong-stage title"
    if _any(f["exclude_domain"], title) and not _any(f["domain_rescue"], title):
        return "reject", "off-target function"
    on_lane = _any(f["function_any"], title) or (consulting and _any(f["function_consulting"], title))
    if not on_lane and not _any(f["function_adjacent"], title):
        return "reject", "not consulting / strategy / rotational"
    level = _any(f["level_title"], title) or (consulting and _any(f["level_title_consulting"], title))
    early_desc = _any(f["early_markers"], f"{title}\n{job.meta}\n{desc}")
    if not level and not early_desc:
        return "reject", "no early-career level"
    if job.extra.get("associate_is_senior") and re.search(r"(?i)\bassociate\b", title) and not early_desc:
        return "reject", "bank 'Associate' = post-MBA"
    if _any(f["bad_start_title"], title) or _any(f["bad_start_description"], desc[:8000]):
        return "reject", "start date / cohort doesn't fit an April 2027 grad"
    desc_years = re.sub(r"(?i)\b[01]\s*(-|–|to)\s*\d+\s*\+?\s*years?", " ", desc)
    # A ceiling isn't a requirement: "less than 4 years of work experience" (McKinsey BA).
    desc_years = re.sub(r"(?i)\b(less than|fewer than|under|up to|no more than|maximum of|max\.?)\s+"
                        r"(\d+|one|two|three|four|five|six)\s*\+?\s*years?", " ", desc_years)
    if _any(f["exclude_description"], desc_years):
        return "reject", "asks for 3+ years or an MBA"
    nyc = is_nyc(job.location)
    if nyc is None and re.search(r"New York", desc):
        nyc = True
    if not nyc:
        return "reject", "not based in NYC" if nyc is False else "location unknown"
    stretch = []
    if not on_lane:
        stretch.append("adjacent to your lanes")
    if _any(f["stretch_domain"], title) and not _any(f["domain_rescue"], title):
        stretch.append("specialised domain")
    if _any(f["stretch_description"], desc_years):
        stretch.append("asks for ~2 years")
    if stretch:
        return "stretch", "; ".join(stretch)
    return ("clear", "early-career consulting/strategy role in NYC") if early_desc else \
        ("candidate", "title fits; level not stated in posting")


def flags_early_start(job, f: dict) -> bool:
    return False   # bad start dates are now rejected outright in classify()
