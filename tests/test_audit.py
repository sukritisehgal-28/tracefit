"""The checker (shortlist/audit.py): clean on everything the tool produces, and able to catch mistakes.

A checker that never finds anything proves nothing, so each check is also shown catching a mistake
planted in a copy of a real result. All planted data is SYNTHETIC.
"""
from __future__ import annotations

import copy
import unittest
from datetime import timedelta

from helpers import engine
from scenario_base import SCENARIOS
from shortlist import config
from shortlist.audit import audit, check_list, check_match
from shortlist.model import Evidence
from shortlist.pipeline import Engine
from test_model_reader import liar


def checks(findings):
    return {f["check"] for f in findings}


class CleanOnWhatTheToolProduces(unittest.TestCase):
    def test_the_supplied_sample_at_every_step(self):
        r = audit(engine())
        self.assertGreater(r["checked"], 100)
        self.assertEqual(r["findings"], [])

    def test_every_settings_profile(self):
        for overlay in ("feed_only", "strict_pay"):
            with self.subTest(overlay=overlay):
                self.assertEqual(audit(engine(overlay))["findings"], [])

    def test_every_scenario(self):
        for s in SCENARIOS:
            if s.get("refuses"):
                continue
            with self.subTest(scenario=s["id"]):
                self.assertEqual(audit(s["build"]())["findings"], [])

    def test_with_a_lying_model_reader_switched_on(self):
        cfg = config.load()
        cfg["model_reader"] = dict(cfg["model_reader"], _provider=liar)
        self.assertEqual(audit(Engine(cfg=cfg))["findings"], [])


class CatchesPlantedMistakes(unittest.TestCase):
    def setUp(self):
        self.eng = Engine()                       # a fresh copy: these tests change it
        self.world = self.eng.world(0)
        self.job = self.world.jobs["job:VMS-901"]

    def full(self, person):
        return copy.deepcopy(self.eng.explain(0, "job:VMS-901", person))

    def result(self, m, rule):
        return next(r for r in m.results + m.gates if r.rule == rule)

    def plant(self, kind, text, at=None):
        ev = Evidence(f"test/{kind}", kind, "test", kind, "person:our_system:R01", at or self.world.as_of - timedelta(days=1),
                      False, "candidate", f"Planted {kind}", text)
        self.world.evidence[ev.id] = ev
        return ev

    def caught(self, m, check):
        self.assertIn(check, checks(check_match(self.world, self.eng.cfg, self.job, m)))

    def test_a_tier_that_does_not_follow_from_the_rules(self):
        m = self.full("person:our_system:R12")        # not a fit: location not met
        m.tier = "strong"
        self.caught(m, "tier")

    def test_a_score_that_is_not_the_weighted_average_of_its_dimensions(self):
        m = self.full("person:our_system:R04")
        m.score, m.low, m.high = 99, 99, 99
        self.caught(m, "score")

    def test_a_score_outside_its_range(self):
        m = self.full("person:our_system:R01")
        m.score = m.high + 5
        self.caught(m, "score")

    def test_a_quote_the_evidence_never_recorded(self):
        m = self.full("person:our_system:R01")
        self.result(m, "skills").refs[0]["quote"] = "I have ten years of OASIS work"
        self.caught(m, "evidence")

    def test_a_decision_with_no_evidence(self):
        m = self.full("person:our_system:R01")
        r = self.result(m, "license")
        r.refs, r.job_refs = [], []
        self.caught(m, "evidence")

    def test_reach_out_to_someone_who_asked_not_to_be_contacted(self):
        m = self.full("person:our_system:R11")
        m.action = dict(m.action, status="ready")
        self.caught(m, "action")

    def test_reach_out_to_someone_another_recruiter_owns(self):
        m = self.full("person:our_system:R10")
        m.action = dict(m.action, status="ready")
        self.caught(m, "action")

    def test_a_skill_counted_from_a_recruiters_impression(self):
        ev = self.plant("recruiter_note", "Seems very strong on OASIS.")
        m = self.full("person:our_system:R01")
        self.result(m, "skills").refs = [{"evidence": ev.id, "quote": ev.text, "note": ""}]
        self.caught(m, "authority")

    def test_a_rule_met_on_a_doubtful_sentence(self):
        ev = self.plant("candidate_message", "I might be able to start December 1.")
        m = self.full("person:our_system:R01")
        self.result(m, "start_date").refs = [{"evidence": ev.id, "quote": ev.text, "note": ""}]
        self.caught(m, "doubt")

    def test_a_rule_met_on_evidence_from_the_future(self):
        ev = self.plant("candidate_message", "I can start December 1.", at=self.world.as_of + timedelta(days=3))
        m = self.full("person:our_system:R01")
        self.result(m, "start_date").refs = [{"evidence": ev.id, "quote": ev.text, "note": ""}]
        self.caught(m, "time")

    def test_a_credential_called_verified(self):
        m = self.full("person:our_system:R01")
        self.result(m, "license").shows = "Washington license verified with the board"
        self.caught(m, "wording")

    def test_a_list_out_of_order(self):
        rows = list(self.eng.shortlist(0, "job:VMS-901").ranked())
        i = next(i for i in range(len(rows) - 1) if rows[i].tier == rows[i + 1].tier and rows[i].score > rows[i + 1].score
                 and rows[i].action["status"] != "blocked" and rows[i + 1].action["status"] != "blocked")
        rows[i], rows[i + 1] = rows[i + 1], rows[i]
        self.assertIn("list", checks(check_list(self.job, rows)))


if __name__ == "__main__":
    unittest.main()
