"""Configuration changes behaviour without code changes, and nothing depends on the system clock."""
from __future__ import annotations

import json
import unittest

from helpers import engine, match, rule
from shortlist import config
from shortlist.pipeline import Engine

HOME_HEALTH, EMERGENCY = "VMS-901", "VMS-902"


class RequirementPolicy(unittest.TestCase):
    """The same data under 'vendor feed only': the note is shown but not used."""

    def setUp(self):
        self.e = engine("feed_only")

    def test_eighteen_months_no_longer_meets_a_two_year_minimum(self):
        alex = match(self.e, 0, HOME_HEALTH, "C1")
        exp = rule(alex, "setting_experience")
        self.assertEqual((alex.tier, exp.outcome), ("not_fit", "not_met"))
        self.assertIn("pending_clarification", exp.flags)
        self.assertEqual(exp.alt["outcome"], "met")
        self.assertEqual(rule(alex, "requirement_source").outcome, "clear")

    def test_an_unverified_three_year_claim_becomes_the_deciding_question(self):
        r04 = match(self.e, 0, HOME_HEALTH, "R04")
        self.assertEqual((r04.tier, rule(r04, "setting_experience").outcome), ("clarify", "unknown"))
        self.assertTrue(any("earlier home health employers" in q for q in r04.questions))

    def test_a_tool_the_feed_requires_rules_out_someone_who_has_not_used_it(self):
        r05 = match(self.e, 0, HOME_HEALTH, "R05")
        self.assertEqual((r05.tier, rule(r05, "skills").outcome), ("not_fit", "not_met"))

    def test_default_and_feed_only_disagree_on_who_is_strong(self):
        def strong(e):
            return {m.name for m in e.matches(0)["job:" + HOME_HEALTH][0] if m.tier == "strong"}
        self.assertIn("Alex Sample", strong(engine()))
        self.assertEqual(strong(self.e), set(), "under the feed alone, nobody has two documented years")


class OtherSettings(unittest.TestCase):
    def test_pay_can_be_made_a_hard_ceiling(self):
        self.assertEqual(match(engine(), 0, HOME_HEALTH, "R06").tier, "strong")
        self.assertEqual(match(engine("strict_pay"), 0, HOME_HEALTH, "R06").tier, "not_fit")

    def test_unknown_credit_sets_how_much_benefit_of_the_doubt_a_score_gets(self):
        default, strict = match(engine(), 0, HOME_HEALTH, "C2"), match(engine("strict_pay"), 0, HOME_HEALTH, "C2")
        self.assertEqual((default.low, default.high), (strict.low, strict.high), "the range does not depend on the setting")
        self.assertEqual(strict.score, strict.low)
        self.assertGreater(default.score, strict.score)

    def test_a_division_can_weight_rules_differently(self):
        cfg = config.load()
        therapy, locums = config.for_division(cfg, "therapy"), config.for_division(cfg, "locums")
        self.assertEqual((therapy["dimensions"]["pay"], locums["dimensions"]["pay"]), (10, 5))
        self.assertEqual((therapy["freshness_days"]["availability"], locums["freshness_days"]["availability"]), (30, 14))

    def test_an_unknown_overlay_is_refused(self):
        with self.assertRaises(ValueError):
            config.load("../../etc/passwd")


class Clock(unittest.TestCase):
    def test_results_follow_the_configured_reference_date_not_today(self):
        cfg = config.load("feed_only")
        cfg["clock"]["reference_date"] = "2027-06-01"          # seven months later
        later = Engine(cfg=cfg)
        exp = rule(match(later, 0, HOME_HEALTH, "C1"), "setting_experience")
        self.assertEqual(exp.outcome, "met", "by June 2027 the same history is over two years")
        self.assertEqual(rule(match(later, 0, EMERGENCY, "C4"), "contact_permission").outcome, "clear",
                         "the no-contact request ended on 15 January 2027")

    def test_old_facts_are_flagged_stale_but_still_used(self):
        cfg = config.load()
        cfg["clock"]["reference_date"] = "2027-06-01"
        later = Engine(cfg=cfg)
        start = rule(match(later, 0, HOME_HEALTH, "R01"), "start_date")
        self.assertIn("stale", start.flags)
        self.assertEqual(start.outcome, "met")

    def test_the_same_input_always_gives_the_same_output(self):
        a = json.dumps(Engine().state(2), sort_keys=True, default=str)
        b = json.dumps(Engine().state(2), sort_keys=True, default=str)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
