"""Tests for company intelligence, fit scoring, AI-output handling, scan reliability and the page.
Everything external (boards, AI providers, ntfy, Resend, web pages) is mocked: no network, no credits."""
import datetime as dt
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scanner import __main__ as scan, fit, intel, notify, report, scorer
from scanner.adapters import Job

TODAY = dt.date(2026, 10, 15)


def profile(**kw):
    base = {"company": "Acme", "researched_on": "2026-10-01", "refresh_after": "2027-01-01",
            "overview": "Acme does strategy.",
            "differentiators": [{"text": "Known for X.", "evidence": "verified_policy", "sources": ["1"]}],
            "employee_support": [{"text": "Documents 16 weeks leave.", "evidence": "verified_policy", "sources": ["1"]}],
            "sources": [{"id": "1", "url": "https://acme.example/careers", "type": "official"}]}
    base.update(kw)
    return base


def rating(id, **kw):
    d = {"id": id, "rating": "Good", "reason": "fits", "pointers": ["+ a", "- b"],
         "undergrad_eligible": "yes", "starts_before_summer_2027": False, "eligibility": "eligible",
         "eligibility_note": "bachelor's", "why_role": "role", "why_company": "co", "support": "",
         "support_evidence": "unknown", "development": "", "investigate": [],
         "dims": {"learning": 4, "support": None, "differentiation": 4, "motivation": 3}}
    d.update(kw)
    return scorer.Rating.model_validate(d)


# ---------------------------------------------------------------- company intelligence

class Intel(unittest.TestCase):
    def test_valid_profile_loads(self):
        p, probs = intel.validate(profile())
        self.assertEqual(probs, [])
        self.assertEqual(len(p["employee_support"]), 1)

    def test_bad_findings_dropped_not_whole_profile(self):
        p, probs = intel.validate(profile(
            culture=[{"text": "Great culture", "evidence": "marketing", "sources": ["1"]},
                     {"text": "No source", "evidence": "tentative_signal", "sources": []},
                     {"text": "Ghost source", "evidence": "tentative_signal", "sources": ["9"]}]))
        self.assertEqual(p["culture"], [])
        self.assertEqual(len(probs), 3)
        self.assertEqual(len(p["differentiators"]), 1)       # the good finding survives

    def test_rejects_nameless_and_bad_urls(self):
        self.assertIsNone(intel.validate({"sources": []})[0])
        p, probs = intel.validate(profile(sources=[{"id": "1", "url": "javascript:alert(1)", "type": "official"}]))
        self.assertEqual(p["differentiators"], [])            # its only source was dropped

    def test_load_all_skips_corrupt_files(self):
        d = Path(tempfile.mkdtemp())
        (d / "acme.json").write_text(json.dumps(profile()))
        (d / "broken.json").write_text("{not json")
        profiles, problems = intel.load_all(d)
        self.assertEqual(list(profiles), ["acme"])
        self.assertTrue(any("broken.json" in x for x in problems))
        shutil.rmtree(d)

    def test_freshness(self):
        p, _ = intel.validate(profile())
        p["_slug"] = "acme"
        self.assertEqual(intel.freshness(p, TODAY), "fresh")
        self.assertEqual(intel.freshness(p, dt.date(2027, 2, 1)), "stale")
        self.assertEqual(intel.freshness(p, TODAY, {"acme": {"changed": ["u"]}}), "changed")

    def test_prompt_block_bounded_and_honest_about_gaps(self):
        p, _ = intel.validate(profile(differentiators=[
            {"text": "x" * 500, "evidence": "verified_policy", "sources": ["1"]}] * 3))
        block = intel.prompt_block(p, "Acme", max_chars=600)
        self.assertLessEqual(len(block), 700)
        self.assertIn("truncated", block)
        missing = intel.prompt_block(None, "Nobody")
        self.assertIn("do not penalise", missing)
        p2, _ = intel.validate(profile(employee_support=[]))
        self.assertIn("treat as unknown, not as a negative", intel.prompt_block(p2, "Acme"))

    def test_slug_and_override(self):
        self.assertEqual(intel.slug("Strategy& (PwC)"), "strategy-and-pwc")
        self.assertEqual(intel.intel_slug({"name": "X", "intel": "y"}), "y")

    def test_repo_profiles_cover_enabled_companies_without_problems(self):
        import tomllib
        profiles, problems = intel.load_all()
        self.assertEqual(problems, [])
        companies = tomllib.loads((ROOT / "companies.toml").read_text())["company"]
        missing = {c["name"] for c in companies if c.get("enabled", True)} - \
            {p["company"] for p in profiles.values()}
        self.assertEqual(missing, set())


class IntelCheck(unittest.TestCase):
    def setUp(self):
        p, _ = intel.validate(profile(watch_urls=["https://acme.example/benefits"]))
        p["_slug"] = "acme"
        self.profiles = {"acme": p}

    def test_unchanged_page_confirms_changed_page_flags(self):
        checks = {}
        intel.check(self.profiles, checks, TODAY, 30, 5, fetch=lambda u: "Leave: 16 weeks")
        self.assertEqual(checks["acme"]["confirmed_on"], TODAY.isoformat())
        later = TODAY + dt.timedelta(days=31)
        intel.check(self.profiles, checks, later, 30, 5, fetch=lambda u: "Leave: 16 weeks")
        self.assertNotIn("changed", checks["acme"])
        intel.check(self.profiles, checks, later + dt.timedelta(days=31), 30, 5, fetch=lambda u: "Leave: 8 weeks")
        self.assertEqual(checks["acme"]["changed"], ["https://acme.example/benefits"])

    def test_due_dates_and_cap_bound_requests(self):
        calls = []
        checks = {"acme": {"checked_on": TODAY.isoformat()}}
        intel.check(self.profiles, checks, TODAY, 30, 5, fetch=lambda u: calls.append(u) or "x")
        self.assertEqual(calls, [])                           # checked today: skipped
        intel.check(self.profiles, {}, TODAY, 30, 0, fetch=lambda u: calls.append(u) or "x")
        self.assertEqual(calls, [])                           # cap of 0: nothing fetched

    def test_unreachable_page_changes_nothing(self):
        checks = {"acme": {"hashes": {"https://acme.example/benefits": "abc"}}}
        def boom(u):
            raise RuntimeError("403")
        intel.check(self.profiles, checks, TODAY, 30, 5, fetch=boom)
        self.assertNotIn("changed", checks["acme"])
        self.assertNotIn("confirmed_on", checks["acme"])
        self.assertTrue(checks["acme"]["errors"])

    def test_draft_keeps_only_verified_quotes(self):
        page = "Our parental leave policy gives all parents sixteen weeks of fully paid leave after one year."
        ask = lambda sys_, user: {"findings": [
            {"section": "employee_support", "text": "16 weeks paid parental leave",
             "quote": "gives all parents sixteen weeks of fully paid leave", "url": "https://a.example"},
            {"section": "culture", "text": "Amazing culture", "quote": "we have an amazing culture",
             "url": "https://a.example"}]}
        d = intel.draft("Acme", ["https://a.example"], TODAY, ask=ask, fetch=lambda u: page)
        self.assertEqual(len(d["employee_support"]), 1)
        self.assertNotIn("culture", d)                        # invented quote dropped
        self.assertEqual(d["employee_support"][0]["evidence"], "verified_policy")
        self.assertIsNotNone(intel.validate(d)[0])


# ---------------------------------------------------------------- fit + classification

class Fit(unittest.TestCase):
    def test_classifications(self):
        self.assertEqual(fit.final_rating(rating("a", rating="Strong"), "clear")[0], "Strong")
        self.assertEqual(fit.final_rating(rating("a", rating="Good"), "clear")[0], "Good")
        self.assertEqual(fit.final_rating(rating("a", rating="Stretch"), "clear")[0], "Stretch")
        self.assertIsNone(fit.final_rating(rating("a", rating="No"), "clear")[0])

    def test_hard_eligibility_wins_over_everything(self):
        great = {"learning": 5, "support": 5, "differentiation": 5, "motivation": 5}
        for r in (rating("a", rating="Strong", eligibility="ineligible", dims=great),
                  rating("a", rating="Strong", undergrad_eligible="no", dims=great),
                  rating("a", rating="Strong", starts_before_summer_2027=True, dims=great)):
            self.assertIsNone(fit.final_rating(r, "clear")[0])

    def test_strong_needs_confirmed_eligibility_and_on_lane(self):
        r, notes = fit.final_rating(rating("a", rating="Strong", eligibility="unclear"), "clear")
        self.assertEqual(r, "Good")
        self.assertTrue(notes)
        self.assertEqual(fit.final_rating(rating("a", rating="Strong"), "stretch")[0], "Good")

    def test_company_fit_separate_and_missing_evidence_not_penalised(self):
        w = {"learning": 1, "support": 1, "differentiation": 1, "motivation": 1}
        self.assertEqual(fit.company_fit({"learning": 5, "differentiation": 4}, w, True), ("High", 4.5))
        # Same known scores with two unknowns: identical result (unknowns aren't zeros).
        self.assertEqual(fit.company_fit({"learning": 5, "differentiation": 4, "support": None,
                                          "motivation": None}, w, True), ("High", 4.5))
        self.assertEqual(fit.company_fit({"learning": 5}, w, True), ("Unknown", None))
        self.assertEqual(fit.company_fit({}, w, False), ("Unknown", None))

    def test_support_score_ignored_without_evidence(self):
        w = {}
        dims = {"learning": 3, "support": 5, "differentiation": 3}
        self.assertEqual(fit.company_fit(dims, w, False)[1], 3.0)
        self.assertGreater(fit.company_fit(dims, w, True)[1], 3.0)

    def test_weights_from_preferences(self):
        dims = {"learning": 5, "differentiation": 1}
        self.assertEqual(fit.company_fit(dims, {"learning": 3, "differentiation": 1}, True)[0], "High")
        self.assertEqual(fit.company_fit(dims, {"learning": 1, "differentiation": 3}, True)[0], "Low")

    def test_company_fit_never_changes_rating(self):
        poor_role = rating("a", rating="Stretch", dims={"learning": 5, "support": 5,
                                                        "differentiation": 5, "motivation": 5})
        self.assertEqual(fit.final_rating(poor_role, "clear")[0], "Stretch")

    def test_preferences_file_parses_and_is_rendered(self):
        import tomllib
        prefs = tomllib.loads((ROOT / "preferences.toml").read_text())
        self.assertEqual(set(prefs["weights"]), set(fit.DIMENSIONS))
        text = scorer.preferences_text(prefs["preferences"])
        self.assertIn("industries: not stated", text)


# ---------------------------------------------------------------- AI output handling

class Parse(unittest.TestCase):
    def test_one_bad_entry_does_not_sink_the_batch(self):
        content = "Sure! " + json.dumps({"ratings": [
            {"id": "X|1", "rating": "strong", "reason": "r", "pointers": [], "undergrad_eligible": "Yes",
             "starts_before_summer_2027": False},                  # old-style fields only
            {"id": "X|2", "rating": "Maybe", "reason": "r"},       # invalid rating
            {"id": "Z|9", "rating": "Good", "reason": "r"},        # unknown id
            "garbage"]}) + " hope that helps"
        good, problems = scorer.parse_ratings(content, {"X|1", "X|2"})
        self.assertEqual([r.id for r in good], ["X|1"])
        self.assertEqual(good[0].rating, "Strong")
        self.assertEqual(good[0].eligibility, "unclear")           # defaulted, not invented
        self.assertIsNone(good[0].dims.support)
        self.assertEqual(len(problems), 3)

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            scorer.parse_ratings("I cannot help with that", {"a"})
        with self.assertRaises(ValueError):
            scorer.parse_ratings('{"something": 1}', {"a"})


def job(i, company="Acme", title="Strategy Analyst", desc="Entry-level, class of 2027.", **kw):
    j = Job(company=company, priority=1, job_id=str(i), title=title, location="New York, NY",
            url=f"https://jobs.example/{i}", description=desc, **kw)
    j.extra["platform"] = "greenhouse"
    return j


class Providers(unittest.TestCase):
    def test_no_keys_returns_error_not_crash(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            out, err = scorer.score([job(1)], "profile", "m")
        self.assertEqual(out, {})
        self.assertIn("No AI key", err)

    def test_fallback_to_next_provider_and_dead_provider_skipped(self):
        calls = []

        def nvidia(system, msg, ids, url, key_env, model):
            calls.append(key_env)
            if key_env == "NVIDIA_API_KEY":
                raise RuntimeError("401: unauthorized")
            return [rating(i) for i in ids]
        env = {"NVIDIA_API_KEY": "x", "GROQ_API_KEY": "y"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(scorer, "_openai_compatible", nvidia), \
                mock.patch.object(scorer, "_examples", lambda: ""):
            out, err = scorer.score([job(1), job(2), job(3)], "p", "m", batch_size=1)
        self.assertEqual(len(out), 3)
        self.assertEqual(calls.count("NVIDIA_API_KEY"), 1)         # 401 -> not retried per batch
        self.assertIn("NVIDIA", err)

    def test_company_context_sent_once_per_batch(self):
        jobs = [job(1, "Acme"), job(2, "Beta"), job(3, "Acme")]
        bs = scorer.batches(jobs, 2)
        self.assertEqual([[j.company for j in b] for b in bs], [["Acme", "Acme"], ["Beta"]])
        msg = scorer.user_message(bs[0], {"Acme": "<company name=\"Acme\">ctx</company>", "Beta": "B"})
        self.assertEqual(msg.count("ctx"), 1)
        self.assertNotIn("B\n", msg.split("POSTINGS")[0])


# ---------------------------------------------------------------- scan reliability (unit)

class Reliability(unittest.TestCase):
    def test_completeness(self):
        sources = {"A (s1)": {"company": "A", "fetched": 5}, "A (s2)": {"company": "A", "error": "boom", "fetched": 0},
                   "B": {"company": "B", "fetched": 3, "incomplete": "page cap"}, "C": {"company": "C", "fetched": 9}}
        complete, bad = scan.completeness(sources)
        self.assertEqual(complete, {"C"})                          # one failed board taints company A
        self.assertEqual(set(bad), {"A", "B"})

    def test_sudden_drop_is_incomplete(self):
        s = {"A": {"company": "A", "fetched": 3}, "B": {"company": "B", "fetched": 95}, "C": {"company": "C", "fetched": 0}}
        scan.flag_drops(s, {"A": {"fetched": 40}, "B": {"fetched": 100}, "C": {"fetched": 4}})
        self.assertIn("incomplete", s["A"])
        self.assertNotIn("incomplete", s["B"])
        self.assertNotIn("incomplete", s["C"])                     # too small to judge

    def test_truncated_adapter_marks_company(self):
        from scanner import adapters
        c = {"name": "Big", "platform": "greenhouse", "priority": 1}
        def fake(cfg):
            cfg["_truncated"] = True
            return [job(1, "Big")]
        with mock.patch.dict(adapters.ADAPTERS, {"greenhouse": fake}):
            jobs, sources = scan.fetch_all([c])
        self.assertIn("incomplete", sources["Big"])

    def test_closure_rules(self):
        t = lambda co: {"company": co, "title": "T", "url": "u", "misses": 0, "closed_on": None}
        tracked = {"A|1": t("A"), "B|1": t("B"), "C|1": t("C")}
        never_gone = lambda u: False
        for _ in range(2):
            closed = scan.update_tracked(tracked, {}, {"A", "B"}, {"A"}, set(), "2026-10-15", gone=never_gone)
        self.assertEqual(tracked["A|1"]["closed_on"], "2026-10-15")   # complete board, missed twice
        self.assertIsNone(tracked["B|1"]["closed_on"])                 # incomplete board: never closed
        self.assertEqual(tracked["B|1"]["misses"], 0)
        self.assertIsNone(tracked["C|1"]["closed_on"])                 # not scanned at all
        tracked2 = {"A|1": t("A")}
        scan.update_tracked(tracked2, {}, {"A"}, {"A"}, set(), "d", gone=lambda u: True)
        self.assertEqual(tracked2["A|1"]["closed_on"], "d")            # posting page 404 closes at once

    def test_corrupt_state(self):
        d = Path(tempfile.mkdtemp())
        (d / "seen.json").write_text("{oops")
        with mock.patch.object(scan, "ROOT", d):
            with self.assertRaises(SystemExit):
                scan.load_json(d / "seen.json", {}, critical=True)
            notes = []
            self.assertEqual(scan.load_json(d / "seen.json", {}, notes=notes), {})
            self.assertTrue(notes)
        scan.write_json(d / "x.json", {"a": 1})
        self.assertEqual(json.loads((d / "x.json").read_text()), {"a": 1})
        self.assertFalse((d / "x.json.tmp").exists())
        shutil.rmtree(d)


# ---------------------------------------------------------------- end-to-end scans (all mocked)

class Scans(unittest.TestCase):
    """Runs scanner.main() repeatedly against fake boards, a fake AI and fake alert channels."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state, self.site = self.tmp / "state", self.tmp / "site"
        self.state.mkdir()
        self.listing = [job(1), job(2, title="Corporate Strategy Analyst"), job(3)]
        self.sources_error = False
        self.rate = {}                 # key -> Rating or None (unrated this scan)
        self.scored = []               # keys sent to the AI per scan
        self.alerts, self.emails = [], []
        self.patches = [
            mock.patch.object(scan, "STATE", self.state), mock.patch.object(scan, "SITE", self.site),
            mock.patch.object(scan, "fetch_all", self.fake_fetch),
            mock.patch.object(scan.scorer, "score", self.fake_score),
            mock.patch.object(scan, "enrich", lambda j: j),
            mock.patch.object(scan.notify, "send", lambda a: self.alerts.append(a) or []),
            mock.patch.object(scan.notify, "send_email", lambda m, to: self.emails.append(m) or []),
            mock.patch.object(scan, "posting_gone", lambda u: False),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def fake_fetch(self, companies):
        if self.sources_error:
            return [], {"Acme": {"fetched": 0, "new": 0, "company": "Acme", "error": "HTTP 500"}}
        js = [Job(**{k: getattr(j, k) for k in ("company", "priority", "job_id", "title", "location", "url", "description")})
              for j in self.listing]
        for j in js:
            j.extra["platform"] = "greenhouse"
        return js, {"Acme": {"fetched": len(js), "new": 0, "company": "Acme"}}

    def fake_score(self, jobs, *a, **k):
        self.scored.append(sorted(j.key for j in jobs))
        out = {j.key: self.rate[j.key] for j in jobs if self.rate.get(j.key)}
        return out, (None if len(out) == len(jobs) else "provider down")

    def run_scan(self, *args):
        return scan.main(list(args) + ["--only", "Acme"])

    def state_json(self, name):
        return json.loads((self.state / name).read_text())

    def test_full_lifecycle(self):
        # Scan 1: AI rates job 1 only; job 3 is "clear" (early-career words) -> provisional; job 2 waits.
        self.listing[1].description = "Strategy role."                    # no early-career words -> candidate
        self.rate = {"Acme|1": rating("Acme|1", rating="Strong")}
        self.assertEqual(self.run_scan(), 0)
        tracked, pending = self.state_json("tracked.json"), self.state_json("pending.json")
        self.assertEqual(set(tracked), {"Acme|1", "Acme|3"})
        self.assertTrue(tracked["Acme|3"]["provisional"])
        self.assertEqual(set(pending), {"Acme|2", "Acme|3"})            # nothing silently lost
        self.assertEqual(len(self.alerts[0]), 3)                         # 2 matches + summary
        self.assertEqual(len(self.emails), 1)                            # the Strong one

        # Scan 2: AI back. Only the two waiting postings are re-sent (no rescoring of job 1).
        self.rate = {"Acme|2": rating("Acme|2"), "Acme|3": rating("Acme|3", rating="Stretch")}
        self.assertEqual(self.run_scan(), 0)
        self.assertEqual(self.scored[-1], ["Acme|2", "Acme|3"])
        tracked = self.state_json("tracked.json")
        self.assertFalse(tracked["Acme|3"]["provisional"])
        self.assertEqual(tracked["Acme|3"]["rating"], "Stretch")
        self.assertEqual(self.state_json("pending.json"), {})
        self.assertEqual(len(self.alerts[-1]), 2)                        # only job 2 is new: no repeat alerts

        # Scan 3: nothing new -> no AI calls, no alerts.
        n_alerts = len(self.alerts)
        self.run_scan()
        self.assertEqual(self.scored[-1], [])
        self.assertEqual(len(self.alerts), n_alerts)

        # Board fails twice: nothing closes. Then job 1 vanishes from two complete listings: closed.
        self.sources_error = True
        self.run_scan(); self.run_scan()
        self.assertTrue(all(t["closed_on"] is None for t in self.state_json("tracked.json").values()))
        self.sources_error = False
        self.listing = self.listing[1:]
        self.run_scan(); self.run_scan()
        self.assertEqual(self.state_json("tracked.json")["Acme|1"]["closed_on"], dt.datetime.now(scan.TZ).date().isoformat())

    def test_status_marks_survive_rescans_and_dismissed_never_realert(self):
        self.rate = {k: rating(k) for k in ("Acme|1", "Acme|2", "Acme|3")}
        self.run_scan()
        old = (dt.datetime.now(scan.TZ).date() - dt.timedelta(days=10)).isoformat()
        (self.state / "status.json").write_text(json.dumps({
            "Acme|1": {"status": "applied", "at": old}, "Acme|2": {"status": "not_interested", "at": old}}))
        self.run_scan()
        tracked = self.state_json("tracked.json")
        self.assertIn("Acme|1", tracked)                                  # applied stays
        self.assertNotIn("Acme|2", tracked)                               # dismissed > 7 days: dropped
        html = (self.site / "index.html").read_text()
        self.assertIn('<div id="done-applied"><article', html)
        # Undo (mark removed) moves it back to the open list on the next build.
        (self.state / "status.json").write_text("{}")
        self.run_scan()
        self.assertIn('<div id="done-applied"></div>', (self.site / "index.html").read_text())

    def test_unrated_candidate_shown_after_max_attempts(self):
        self.listing = [job(5, desc="Strategy role.")]
        for _ in range(scan.MAX_RATING_ATTEMPTS):
            self.run_scan()
        tracked = self.state_json("tracked.json")
        self.assertEqual(tracked["Acme|5"]["rating"], "Stretch")
        self.assertTrue(tracked["Acme|5"]["provisional"])
        self.assertNotIn("Acme|5", self.state_json("pending.json"))

    def test_provisional_removed_when_ai_says_no(self):
        self.listing = [job(7)]
        self.run_scan()
        self.assertIn("Acme|7", self.state_json("tracked.json"))
        self.rate = {"Acme|7": rating("Acme|7", rating="No")}
        self.run_scan()
        self.assertNotIn("Acme|7", self.state_json("tracked.json"))
        log = self.state_json("runs.json")[-1]["log"]
        self.assertEqual([x["title"] for x in log["removed"]], ["Strategy Analyst"])

    def test_dry_run_writes_nothing_and_sends_nothing(self):
        self.rate = {"Acme|1": rating("Acme|1", rating="Strong")}
        with mock.patch("sys.stdout"):
            self.run_scan("--dry-run")
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertEqual(self.alerts + self.emails, [])

    def test_no_sms_writes_but_sends_nothing(self):
        self.rate = {"Acme|1": rating("Acme|1", rating="Strong")}
        self.run_scan("--no-sms")
        self.assertTrue((self.state / "tracked.json").exists())
        self.assertEqual(self.alerts + self.emails, [])


# ---------------------------------------------------------------- results page

class Page(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def m(self, **kw):
        base = {"key": "Acme|1", "code": "abc234", "company": "Acme", "priority": 1, "rating": "Good",
                "title": "Strategy <Analyst>", "location": "New York, NY", "url": "https://jobs.example/1",
                "reason": "fits", "first_seen": "2026-10-15", "closed_on": None}
        base.update(kw)
        return base

    def test_old_entries_render_and_links_are_safe(self):
        old = self.m(url="javascript:alert(1)")                           # pre-upgrade shape, hostile URL
        report.write_site(self.d, [old], [], "https://s", [], {"Acme|1": "junk"})
        html = (self.d / "index.html").read_text()
        self.assertNotIn("javascript:alert", html)
        self.assertIn("Strategy &lt;Analyst&gt;", html)
        self.assertIn("No company profile yet", html)
        self.assertTrue((self.d / "companies.html").exists())
        self.assertNotIn("javascript", (self.d / "j" / "abc234.html").read_text())

    def test_new_fields_and_company_fallback(self):
        p, _ = intel.validate(profile())
        p["_slug"] = "acme"
        summ = {"Acme": {**intel.summary(p), "freshness": "stale"}}
        full = self.m(eligibility="eligible", undergrad="yes", company_fit="High", why_company="Specific edge",
                      support="Documents a leave policy", support_evidence="verified_policy",
                      investigate=["Travel?"], why_role="Matches ESG work")
        bare = self.m(key="Acme|2", code="def567", provisional=True)
        report.write_site(self.d, [full, bare], [], "https://s", [], {}, companies=summ, profiles={"Acme": p})
        html = (self.d / "index.html").read_text()
        for s in ("Eligible on stated requirements", "Company fit: High", "Specific edge", "Verified policy",
                  "Travel?", "Provisional: AI rating pending", "Known for X.", "(company-level)",
                  "may be out of date", 'href="companies#acme"', 'role="status"'):
            self.assertIn(s, html, s)
        comp = (self.d / "companies.html").read_text()
        self.assertIn("Documents 16 weeks leave.", comp)
        self.assertIn('id="acme-s1"', comp)

    def test_log_shows_incomplete_and_pending(self):
        run = {"label": "Thu", "date": "2026-10-15", "sources": {"A": {"company": "A", "fetched": 2, "new": 0,
               "incomplete": "page cap"}}, "log": {"fetched": 2, "sources_ok": 0, "sources_failed": [],
               "new_postings": 1, "retried": 2, "passed_rules": 1, "new_matches": [], "rerated": [], "closed": [],
               "removed": [], "awaiting_rating": 3, "open_total": 0, "alerts": 0, "emails": 0,
               "incomplete": {"A": "A: page cap"}, "no_profile": ["Newco"]}}
        report.write_log(self.d, [run])
        html = (self.d / "log.html").read_text()
        for s in ("3 waiting for an AI rating", "2 re-tried", "scanned incompletely", "nothing closed", "Newco"):
            self.assertIn(s, html, s)


class Alerts(unittest.TestCase):
    def test_ntfy_retries_transient_errors_and_hides_topic(self):
        import urllib.error
        calls = []

        def urlopen(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b"busy"))
            if len(calls) == 2:
                return mock.Mock(read=lambda: b"")
            raise urllib.error.URLError("dns")
        alerts = [{"title": "t", "body": "b", "click": "https://p", "tags": "x", "priority": "default"},
                  {"title": "s", "body": "b", "click": "https://site/", "tags": "x", "priority": "default"}]
        with mock.patch.dict("os.environ", {"NTFY_TOPIC": "secret-topic"}), \
                mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep"):
            errs = notify.send(alerts)
        self.assertEqual(len(errs), 1)                                    # first alert retried OK
        self.assertNotIn("secret-topic", errs[0])

    def test_email_includes_company_reason(self):
        mail = notify.compose_email([{"company": "Acme", "title": "T", "location": "NY", "rating": "Strong",
                                      "reason": "r", "url": "https://x", "why_company": "Edge"}], "https://s")
        self.assertIn("Why Acme: Edge", mail["text"])


if __name__ == "__main__":
    unittest.main()
