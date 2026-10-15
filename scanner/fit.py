"""Turns one AI assessment into the final, explainable result.

Three separate questions, answered in this order:
1. Eligibility (gate)  can the candidate apply at all? A hard miss excludes the role, whatever the
                       company is like.
2. Role fit           Strong / Good / Stretch / No from the AI, capped by the keyword rules.
                       This IS the classification shown on the page.
3. Company fit        High / Medium / Low / Unknown from the AI's 1-5 dimension scores, weighted
                       by preferences.toml. It orders matches within a rating; it never upgrades
                       a role, so a great company can't hide a poor one.
"""
from __future__ import annotations

DIMENSIONS = ("learning", "support", "differentiation", "motivation")
FIT_ORDER = {"High": 0, "Medium": 1, "Unknown": 1, "Low": 2}   # unknown is not ranked below medium
MIN_DIMS = 2   # fewer known dimensions than this -> "Unknown" rather than a shaky number


def final_rating(r, verdict: str) -> tuple[str | None, list[str]]:
    """(rating or None for excluded, notes explaining any adjustment)."""
    if r.eligibility == "ineligible":
        return None, ["fails a hard requirement"]
    if r.rating == "No" or r.undergrad_eligible == "no" or r.starts_before_summer_2027:
        return None, ["rated No"]
    rating, notes = r.rating, []
    if verdict == "stretch" and rating == "Strong":
        rating = "Good"
        notes.append("capped at Good: keyword rules found it a step off your lanes")
    if rating == "Strong" and r.eligibility != "eligible":
        rating = "Good"
        notes.append("capped at Good: eligibility not confirmed by the posting")
    return rating, notes


def company_fit(dims: dict | None, weights: dict, support_evidence: bool) -> tuple[str, float | None]:
    """Weighted mean of the known 1-5 scores. Missing scores are left out, not counted as low.
    A support score without any support evidence on file is ignored (it can only be a guess)."""
    total = wsum = 0.0
    known = 0
    for d in DIMENSIONS:
        v = (dims or {}).get(d)
        if d == "support" and not support_evidence:
            v = None
        w = float(weights.get(d, 1))
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 1 <= v <= 5 or w <= 0:
            continue
        total += v * w
        wsum += w
        known += 1
    if known < MIN_DIMS or not wsum:
        return "Unknown", None
    score = round(total / wsum, 2)
    return ("High" if score >= 4 else "Medium" if score >= 3 else "Low"), score
