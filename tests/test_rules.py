import sys, tomllib, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scanner import filters, notify
from scanner.adapters import Job

F = tomllib.loads((ROOT / "settings.toml").read_text())["filters"]


def job(title, loc="New York, NY", pri=1, desc="", **extra):
    j = Job(company="X", priority=pri, job_id=title, title=title, location=loc, url="u", description=desc)
    j.extra.update(extra)
    return j


class Location(unittest.TestCase):
    def test_nyc(self):
        for loc in ["New York, NY", "New York, New York, United States", "US - NY, New York",
                    "NY-New York", "Boston; New York, NY", "Americas / Atlanta, Boston, Chicago, New York",
                    "United States - New York - New York City", "New York, US (United States)",
                    "New York, NY (HQ); San Francisco, CA; Remote (US)"]:
            self.assertTrue(filters.is_nyc(loc), loc)

    def test_not_nyc(self):
        for loc in ["Purchase, New York (US)", "Rochester, New York", "Chicago, IL", "Remote (US)",
                    "Jersey City, NJ, United States (US)", "London (GB)", "Rosslyn, Virginia, United States"]:
            self.assertFalse(filters.is_nyc(loc), loc)

    def test_unknown(self):
        for loc in ["", "Multiple Locations", "3 Locations", "Remote"]:
            self.assertIsNone(filters.is_nyc(loc), loc)


class Classify(unittest.TestCase):
    def v(self, *a, **k):
        return filters.classify(job(*a, **k), F)[0]

    def test_accepts(self):
        self.assertEqual(self.v("Strategy Analyst", desc="Open to 2027 graduates."), "clear")
        self.assertEqual(self.v("Consultative Offerings - Analyst - Functional & Operations Strategy",
                                desc="This is an entry-level opportunity."), "clear")
        self.assertEqual(self.v("Corporate Strategy Analyst", pri=3), "candidate")
        self.assertEqual(self.v("Leadership Development Program - Strategy", pri=3,
                                desc="For recent graduates."), "clear")
        self.assertEqual(self.v("USA - Assurance - Climate Change and Sustainability Services - Staff",
                                desc="campus hire"), "clear")      # ESG rescues "assurance"
        self.assertEqual(self.v("Consultant, Strategy", pri=1), "candidate")

    def test_rejects(self):
        from scanner.adapters import _mk
        pwc = _mk({"name": "PwC", "priority": 1, "platform": "workday"}, job_id="1",
                  title="Manager_Product Development Excellence (Strategy and Transformation)",
                  location="New York, NY", url="u")
        self.assertFalse(filters.title_ok(pwc, F))
        cases = {
            "Technical Consultant, Mid-Market": dict(pri=3, desc="4+ years in technical consulting"),
            "Senior Consultant, Strategy": {}, "Manager, Corporate Strategy": {},
            "Strategy Intern": {}, "Summer Analyst - Strategy": {},
            "Associate Package Consultant - Salesforce 2027": {},
            "Associate, Corporate Strategy": dict(pri=3, associate_is_senior=True),
            "Financial Analyst": dict(pri=3), "Consultant, Strategy (needs yrs)": dict(desc="3+ years of experience"),
            "Strategy Analyst - January 2028": {}, "Strategy Analyst (MBA)": dict(desc="MBA required."),
            "Analyst, Cyber Strategy": {}, "Consultant, Strategy": dict(pri=3),
        }
        for t, k in cases.items():
            self.assertEqual(self.v(t, **k), "reject", t)
        self.assertEqual(self.v("Strategy Analyst", loc="Chicago, IL"), "reject")
        self.assertEqual(self.v("Strategy Analyst", loc="Remote (US)"), "reject")
        self.assertEqual(self.v("Strategy Analyst", loc="Multiple Locations"), "reject")

    def test_years_ranges(self):
        self.assertEqual(self.v("Strategy Analyst", desc="0-2 years of experience preferred"), "clear")
        self.assertEqual(self.v("Strategy Analyst", desc="2-4 years of consulting experience"), "reject")
        self.assertEqual(self.v("Strategy Analyst", desc="Minimum of two years of experience"), "stretch")
        self.assertEqual(self.v("Strategy Analyst", desc="3+ years of experience"), "reject")
        self.assertEqual(self.v("Business Analyst", desc="Typically, an undergraduate degree with "
                                "less than 4 years of work experience"), "clear")
        self.assertEqual(self.v("Strategy Analyst", desc="up to 5 years of experience"), "candidate")

    def test_stretch_band(self):
        self.assertEqual(self.v("Associate, Financial Planning", pri=3, desc="early career"), "stretch")
        self.assertEqual(self.v("Product Strategy Analyst", desc="new grad"), "stretch")
        self.assertEqual(self.v("Marketing Strategy Analyst", desc="new grad"), "stretch")
        self.assertEqual(self.v("Operations Analyst", pri=3, desc="new grad"), "stretch")
        # hard rules still win
        self.assertEqual(self.v("Product Strategy Analyst", loc="Chicago, IL", desc="new grad"), "reject")
        self.assertEqual(self.v("Senior Product Strategy Analyst", desc="new grad"), "reject")

    def test_graduate_degree(self):
        for d in ["Candidates must have or be pursuing an MBA or similar graduate degree -- such as a "
                  "master's degree in Finance -- along with prior full time work experience.",
                  "Hold an MBA or other advanced degree.", "A graduate degree is required.",
                  "Requires prior full-time work experience."]:
            self.assertEqual(self.v("Strategy Analyst", desc=d), "reject", d)
        self.assertEqual(self.v("Strategy Analyst", desc="Bachelor's or Master's degree in business; new grad."), "clear")
        self.assertEqual(self.v("Kearney Associate, Strategic Operations", pri=2, associate_is_senior=True), "reject")

    def test_location_from_description(self):
        self.assertEqual(self.v("Strategy Analyst", loc="Multiple Locations",
                                desc="Entry-level. Offices: New York, Boston."), "clear")


class Alerts(unittest.TestCase):
    def m(self, i, rating="Good"):
        return {"company": "Acme", "title": f"Strategy Analyst {i}", "location": "New York, NY",
                "rating": rating, "code": f"c{i}", "early_start": False, "reason": "fits",
                "url": f"https://jobs.example/{i}"}

    def test_every_match_alerted_then_summary(self):
        ms = [self.m(i, r) for i, r in enumerate(["Strong", "Good", "Good", "Stretch", "Good"])]
        out = notify.compose(ms, "https://site.example")
        self.assertEqual(len(out), 6)
        self.assertEqual([a["click"] for a in out[:5]], [f"https://jobs.example/{i}" for i in range(5)])
        self.assertEqual(out[0]["priority"], "high")
        self.assertEqual(out[-1]["click"], "https://site.example/")
        self.assertIn("5 new matches today", out[-1]["title"])
        self.assertIn("1 Strong, 3 Good, 1 Stretch", out[-1]["body"])

    def test_backlog_capped(self):
        out = notify.compose([self.m(i) for i in range(60)], "https://site.example",
                             first_run=True, max_listed=25)
        self.assertEqual(len(out), 26)
        self.assertIn("First scan: 60 open matches", out[-1]["title"])
        self.assertIn("35 more only on the results page", out[-1]["body"])

    def test_none(self):
        self.assertEqual(notify.compose([], "https://s"), [])


if __name__ == "__main__":
    unittest.main()


class Page(unittest.TestCase):
    def test_one_list_open_only_newest_first(self):
        from scanner.__main__ import open_matches
        m = lambda k, seen, **x: {"key": k, "code": k, "company": "X", "priority": 1, "rating": "Good",
                                  "title": k, "closed_on": None, "first_seen": seen, **x}
        tracked = {"old": m("old", "2026-09-01"), "new": m("new", "2026-10-14"),
                   "shut": m("shut", "2026-10-10", closed_on="2026-10-12")}
        self.assertEqual([t["key"] for t in open_matches(tracked)], ["new", "old"])
