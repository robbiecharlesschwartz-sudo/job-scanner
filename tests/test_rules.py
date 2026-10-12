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
    def test_us(self):
        for loc in ["New York, NY", "US - DC, Washington", "Remote (USA)", "Chicago; London",
                    "Dallas, US (United States)", "Atlanta, Georgia (US)", "Purchase, New York (US)"]:
            self.assertTrue(filters.is_us(loc), loc)

    def test_non_us(self):
        for loc in ["London, Greater London (GB)", "Bangalore, IN", "Toronto, Ontario (CA)",
                    "Asia Pacific / Bangkok, Jakarta", "Azerbaijan, Azerbaijan (AZ)", "Cairo - ETIC"]:
            self.assertFalse(filters.is_us(loc), loc)

    def test_unknown(self):
        for loc in ["", "Multiple Locations", "3 Locations", "Remote"]:
            self.assertIsNone(filters.is_us(loc), loc)


class Classify(unittest.TestCase):
    def v(self, *a, **k):
        return filters.classify(job(*a, **k), F)[0]

    def test_clear(self):
        self.assertEqual(self.v("Strategy Analyst"), "clear")
        self.assertEqual(self.v("Entry Level AI-First Transformation – Strategy Consultant 2027"), "clear")
        self.assertEqual(self.v("Associate, Strategy & Operations", pri=3), "clear")

    def test_reject(self):
        for t in ["Senior Consultant, Strategy", "Manager, Corporate Strategy", "Strategy Intern",
                  "Experienced Consultant, Banking", "Summer Analyst - Strategy", "Software Engineer",
                  "Associate Administrative Assistant", "Route Sales Associate"]:
            self.assertEqual(self.v(t), "reject", t)
        self.assertEqual(self.v("Strategy Analyst", loc="London (GB)"), "reject")
        self.assertEqual(self.v("Financial Analyst", pri=3), "reject")   # P3 needs a function

    def test_borderline(self):
        self.assertEqual(self.v("Consultant, Supply Chain"), "borderline")        # level unclear
        self.assertEqual(self.v("Strategy Analyst", loc="Multiple Locations"), "borderline")
        self.assertEqual(self.v("Strategy Analyst", loc="Multiple Locations", assume_us=True), "clear")

    def test_years(self):
        d = "Requires 5+ years of consulting experience."
        self.assertEqual(self.v("Consultant, Strategy", desc=d), "reject")
        self.assertEqual(self.v("Strategy Analyst", desc=d), "borderline")
        self.assertEqual(self.v("Strategy Analyst", desc="0-2 years of experience"), "clear")

    def test_early_start(self):
        self.assertTrue(filters.flags_early_start(job("Analyst - January 2027 start"), F))
        self.assertTrue(filters.flags_early_start(job("Analyst", desc="Start date: Winter 2027."), F))
        self.assertFalse(filters.flags_early_start(job("Analyst", desc="© 2026 Acme. Start summer 2027."), F))


class Texts(unittest.TestCase):
    def m(self, i, rating="Good"):
        return {"company": "Acme", "title": f"Strategy Analyst {i}", "location": "New York, NY",
                "rating": rating, "code": f"c{i}", "early_start": False}

    def test_every_match_linked(self):
        ms = [self.m(i, r) for i, r in enumerate(["Strong", "Good", "Good", "Stretch", "Good"])]
        out = notify.compose(ms, "https://site.example")
        text = "\n".join(out)
        self.assertTrue(out[0].startswith("Job Scanner: 5 new matches (1 Strong, 3 Good, 1 Stretch)"))
        for i in range(5):
            self.assertIn(f"site.example/j/c{i}", text)
        self.assertTrue(out[-1].endswith("All results: site.example/"))
        self.assertTrue(all(len(t) <= 1200 for t in out))

    def test_one(self):
        out = notify.compose([self.m(1)], "https://site.example")
        self.assertEqual(len(out), 1)
        self.assertIn("1 new match (1 Good)", out[0])

    def test_backlog_capped(self):
        ms = [self.m(i) for i in range(60)]
        out = notify.compose(ms, "https://site.example", first_run=True, max_listed=25)
        text = "\n".join(out)
        self.assertIn("first scan: 60 open matches", text)
        self.assertIn("/j/c24", text)
        self.assertNotIn("/j/c25", text)
        self.assertIn("+35 more on the page: site.example/", text)

    def test_none(self):
        self.assertEqual(notify.compose([], "https://s"), [])


if __name__ == "__main__":
    unittest.main()
