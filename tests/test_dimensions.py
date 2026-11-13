"""The quality score: two stages, six dimensions, weights a client can change. All data here is SYNTHETIC
except where a test names a supplied person."""
from __future__ import annotations

import unittest

from helpers import engine, match, rule
from scenario_base import HH, HH_CV, J, MSG, build, cand
from shortlist import config
from shortlist.dimensions import ORDER, score, weights_of
from shortlist.pipeline import Engine

HOME_HEALTH = "VMS-901"


def dims(m):
    return {k: (None if d is None else round(d[0])) for k, d in zip(ORDER, m.dims)}


class Gates(unittest.TestCase):
    def test_no_score_lifts_someone_past_a_failed_gate(self):
        r12 = match(engine(), 0, HOME_HEALTH, "R12")            # location not met, every dimension high
        self.assertEqual(r12.tier, "not_fit")
        self.assertGreaterEqual(r12.score, 90)

    def test_people_who_pass_every_gate_are_no_longer_all_tied(self):
        best = [m for m in engine().matches(0)["job:" + HOME_HEALTH][0] if m.tier == "strong"]
        self.assertGreaterEqual(len({m.score for m in best}), 4)


class Dimensions(unittest.TestCase):
    def test_a_claim_the_records_do_not_support_lowers_evidence(self):
        self.assertEqual(dims(match(engine(), 0, HOME_HEALTH, "R04"))["evidence"], 70)
        self.assertEqual(dims(match(engine(), 0, HOME_HEALTH, "R01"))["evidence"], 100)

    def test_asking_two_percent_over_the_rate_costs_twenty_points_on_pay(self):
        self.assertEqual(dims(match(engine(), 0, HOME_HEALTH, "R06"))["pay"], 80)

    def test_depth_is_documented_years_against_twice_the_minimum(self):
        long = build([cand(cv=HH_CV.replace("2023-01", "2020-11"))], HH())       # six years, minimum two -> target four
        self.assertEqual(dims(long.matches(0)["job:" + J][0][0])["depth"], 100)
        short = build([cand(cv=HH_CV.replace("2023-01", "2024-11"))], HH())      # two years -> half
        self.assertEqual(dims(short.matches(0)["job:" + J][0][0])["depth"], 50)

    def test_a_dimension_the_job_says_nothing_about_is_left_out(self):
        m = match(engine(), 1, "VMS-904", "C2")                  # the outpatient job names no skill or certificate
        self.assertIsNone(dims(m)["skills"])
        self.assertEqual(m.score, 96)

    def test_the_preferred_board_scores_above_the_other_boards_route(self):
        # The ED job prefers ABEM or AOBEM and accepts other boards with four certificates. Casey qualifies by
        # the second route, so the rule is met but earns less than Drew's preferred board would.
        casey, drew = match(engine(), 0, "VMS-902", "C3"), match(engine(), 0, "VMS-902", "C4")
        self.assertEqual((rule(casey, "certifications").outcome, rule(drew, "certifications").outcome), ("met", "met"))
        self.assertLess(rule(casey, "certifications").credit, rule(drew, "certifications").credit)
        self.assertEqual((dims(casey)["skills"], dims(drew)["skills"]), (67, 100))
        self.assertEqual((casey.tier, casey.score), ("strong", 94))

    def test_a_target_employer_counts_as_full_setting_experience(self):
        # Experience with no dates scores 70 on setting; the same experience at an employer the client names scores 100.
        cv = ("Evergreen Visiting Care, home-health PT: independent home visits, OASIS start-of-care documentation, "
              "Homecare Homebase. Candidate reports active Washington PT license.")
        msg = MSG + " I have three years of home-health work."
        plain = build([cand(cv=cv, msg=msg)], HH()).matches(0)["job:" + J][0][0]
        target = build([cand(cv=cv, msg=msg)], HH(), settings={"dimensions": {"target_employers": ["Evergreen Visiting Care"]}}).matches(0)["job:" + J][0][0]
        self.assertEqual((dims(plain)["setting"], dims(target)["setting"]), (70, 100))
        self.assertGreater(target.score, plain.score)


class Weights(unittest.TestCase):
    def test_the_score_is_the_weighted_average_of_the_dimensions(self):
        for m in engine().matches(0)["job:" + HOME_HEALTH][0]:
            self.assertEqual(score(m.dims, weights_of(config.load()["dimensions"])), (m.score, m.low, m.high))

    def test_changing_a_weight_changes_the_order_and_nothing_else(self):
        cfg = config.load()
        cfg["dimensions"] = dict(cfg["dimensions"], pay=0)
        no_pay = {m.person: m for m in Engine(cfg=cfg).matches(0)["job:" + HOME_HEALTH][0]}
        base = {m.person: m for m in engine().matches(0)["job:" + HOME_HEALTH][0]}
        r06 = "person:our_system:R06"
        self.assertGreater(no_pay[r06].score, base[r06].score)                   # pay no longer counts against R06
        self.assertEqual({p: m.tier for p, m in base.items() if m.tier in ("not_fit", "clarify")},
                         {p: m.tier for p, m in no_pay.items() if m.tier in ("not_fit", "clarify")})   # gates unchanged

    def test_a_division_can_weigh_dimensions_differently(self):
        self.assertEqual(weights_of(config.for_division(config.load(), "locums")["dimensions"])["readiness"], 30)


if __name__ == "__main__":
    unittest.main()


class SourcePrecedence(unittest.TestCase):
    """The client lead's open question: which source wins when two disagree. SYNTHETIC records."""

    def jobs(self, settings=None):
        ats = [{"id": "J1", "external_requisition": J, "updated_at": "2026-11-01", "title": "Physical Therapist",
                "status": "open", "start_date": "2026-12-15"}]
        return build([cand()], HH(), ats_jobs=ats, settings=settings).world(0).jobs["job:" + J]

    def test_by_default_the_newest_statement_wins(self):
        from datetime import date
        self.assertEqual(self.jobs().req("req.start").feed.value, date(2026, 12, 15))      # the ATS record is newer

    def test_a_client_can_name_the_source_that_wins(self):
        from datetime import date
        job = self.jobs({"precedence": {"attributes": {"req.start": ["vms", "ats"]}}})
        self.assertEqual(job.req("req.start").feed.value, date(2026, 12, 1))

    def test_the_sample_is_unchanged_under_the_default(self):
        self.assertEqual(config.load()["precedence"], {"candidate": "newest", "job": "newest", "attributes": {}})


class RecruiterWeightsAndFilters(unittest.TestCase):
    """The page's Rank-by bar: a recruiter's own weights and filters, applied to the whole list on the server."""

    def names(self, payload):
        return [c["person"].split(":")[-1] for c in payload["cards"]]

    def test_a_preset_reorders_the_list_and_says_so(self):
        e = engine()
        base = e.cards(0, "job:" + HOME_HEALTH, "best", 0, 25)
        budget = e.cards(0, "job:" + HOME_HEALTH, "best", 0, 25, {"pay": 35, "setting": 20, "skills": 15, "depth": 10, "evidence": 10, "readiness": 10})
        self.assertNotEqual(self.names(base), self.names(budget))
        self.assertTrue(all(c.get("reweighted") for c in budget["cards"]))
        self.assertEqual(sorted(self.names(base)), sorted(self.names(budget)))       # the same people: gates unchanged

    def test_weights_never_move_anyone_between_lists(self):
        e = engine()
        for group in ("best", "not_fit"):
            plain = {c["person"] for c in e.cards(0, "job:" + HOME_HEALTH, group, 0, 200)["cards"]}
            tilted = {c["person"] for c in e.cards(0, "job:" + HOME_HEALTH, group, 0, 200, {"setting": 0, "skills": 0, "pay": 50})["cards"]}
            self.assertEqual(plain, tilted, group)

    def test_filters_narrow_the_list(self):
        e = engine()
        act = e.cards(0, "job:" + HOME_HEALTH, "best", 0, 25, None, ["act_now"])
        self.assertNotIn("R11", self.names(act))                                      # asked not to be contacted
        tool = e.cards(0, "job:" + HOME_HEALTH, "best", 0, 25, None, ["skill:homecare_homebase"])
        self.assertNotIn("R05", self.names(tool))                                     # says they have not used it
        solid = e.cards(0, "job:" + HOME_HEALTH, "best", 0, 25, None, ["min:evidence:80"])
        self.assertNotIn("R04", self.names(solid))                                    # a claim the records do not support

    def test_presets_are_offered_to_the_page(self):
        presets = engine().state(0)["meta"]["presets"]
        self.assertTrue({"experience", "soonest", "budget"} <= {p["name"] for p in presets})


class OneList(unittest.TestCase):
    """People who need an answer are on the same list, placed by score; a failed required rule keeps someone off it."""

    def test_someone_who_needs_an_answer_sits_where_their_score_puts_them(self):
        rows = [m for m in engine().matches(0)["job:" + HOME_HEALTH][0] if m.tier != "not_fit"]
        names = [m.person.split(":")[-1] for m in rows]
        r08 = names.index("R08")                                   # needs an answer on the start date, scores 87
        self.assertEqual(rows[r08].tier, "clarify")
        self.assertLess(r08, names.index("R05"))                   # above R05, who passes everything but scores 81
        self.assertGreater(r08, names.index("R01"))
        open_scores = [m.score for m in rows if m.action["status"] != "blocked"]
        self.assertEqual(open_scores, sorted(open_scores, reverse=True))

    def test_the_page_has_one_list_and_the_people_ruled_out(self):
        from shortlist.matching import GROUPS
        self.assertEqual(list(GROUPS), ["best", "not_fit"])
        payload = engine().cards(0, "job:" + HOME_HEALTH, "best", 0, 10)
        self.assertEqual((len(payload["cards"]), payload["total"]), (10, 12))         # the top ten of twelve
        self.assertEqual(len(engine().cards(0, "job:" + HOME_HEALTH, "best", 10, 10)["cards"]), 2)   # "show next 10"

    def test_no_card_says_strong_match(self):
        labels = {c["tier_label"] for c in engine().cards(0, "job:" + HOME_HEALTH, "best", 0, 50)["cards"]}
        self.assertEqual(labels, {"Match", "Needs an answer"})


class RecruitersOwnCriteria(unittest.TestCase):
    """What a recruiter types into "Add your own". SYNTHETIC where a test builds its own people."""

    def names(self, filters):
        return [c["person"].split(":")[-1] for c in engine().cards(0, "job:" + HOME_HEALTH, "best", 0, 50, None, filters)["cards"]]

    def test_a_typed_phrase_finds_who_says_it_and_shows_the_words(self):
        payload = engine().cards(0, "job:" + HOME_HEALTH, "best", 0, 50, None, ["text:sports injury"])
        self.assertEqual([c["person"] for c in payload["cards"]], ["person:ats:C2"])
        self.assertIn("sports injury", payload["cards"][0]["found"][0]["quote"].lower())

    def test_a_sentence_that_says_it_is_missing_does_not_count(self):
        self.assertNotIn("C2", self.names(["text:OASIS"]))         # "No home-health, OASIS ... detail supplied"

    def test_a_later_denial_beats_an_earlier_mention(self):
        self.assertNotIn("R05", self.names(["text:Homecare Homebase"]))   # the profile lists it; R05 later says not used
        self.assertIn("R01", self.names(["text:Homecare Homebase"]))

    def test_a_school_filter_reads_the_education_on_the_resume(self):
        self.assertEqual(len(self.names(["school:Pine Valley"])), 12)
        self.assertEqual(self.names(["school:Stanford"]), [])

    def test_a_school_filter_shows_the_education_line_it_matched(self):
        payload = engine().cards(0, "job:" + HOME_HEALTH, "best", 0, 50, None, ["school:Pine Valley"])
        for card in payload["cards"]:
            self.assertIn("pine valley", card["found"][0]["quote"].lower())
            self.assertTrue(card["found"][0]["source"])

    def test_the_closest_to_our_work_preset_puts_setting_at_forty(self):
        preset = next(p for p in engine().state(0)["meta"]["presets"] if p["name"] == "closest")
        self.assertEqual(preset["weights"]["setting"], 40)
        self.assertEqual(preset["label"], "Closest to our work")


class WorkedExample(unittest.TestCase):
    def test_the_worked_example_in_the_docs_matches_what_the_tool_produces(self):
        from pathlib import Path
        from shortlist.worked import render
        doc = Path(__file__).resolve().parent.parent / "docs" / "WORKED_EXAMPLE.md"
        self.assertEqual(doc.read_text(encoding="utf-8"), render(engine()),
                         "docs/WORKED_EXAMPLE.md is out of date: run python3 -m shortlist worked --write")
