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


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)


def classify(job, f: dict) -> tuple[str, str]:
    """Returns (verdict, why). verdict in reject | borderline | clear."""
    title = job.title
    if _any(f["exclude_title"], title):
        return "reject", "seniority/function in title"
    us = job.extra.get("country_us", is_us(job.location))
    if us is None and job.extra.get("assume_us"):
        us = True
    if us is False:
        return "reject", "non-US location"
    fn = _any(f["function_title"], title)
    early = _any(f["early_career"], title) or _any(f["early_career"], job.meta)
    if not fn and not early:
        return "reject", "no target function or level in title"
    if not fn and job.priority in f.get("require_function_priorities", []):
        return "reject", "no target function in title"
    if job.description and _any(f["exclude_description"], job.description):
        if not (fn and early):
            return "reject", "asks for 3+ years"
        return "borderline", "title fits but description mentions 3+ years"
    if fn and early and us:
        return "clear", "function + early-career level"
    why = []
    if not fn:
        why.append("level fits, function unclear")
    if not early:
        why.append("function fits, level unclear")
    if us is None:
        why.append("location unclear")
    return "borderline", "; ".join(why)


def flags_early_start(job, f: dict) -> bool:
    return _any(f["early_start_title"], job.title) or \
        _any(f["early_start_description"], job.description[:6000])
