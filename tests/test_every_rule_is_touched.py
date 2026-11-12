"""Every rule is touched.

Three guarantees, each enforced by a test that fails if someone adds a rule and forgets it:

  1. Every fit rule and permission rule runs for every candidate and job.
  2. Every outcome a rule declares it can return is reached by at least one case below.
  3. Every text-reading rule fires at least once, on the supplied data or on a case below.

The cases are SYNTHETIC: small hand-built situations, one per rule and outcome.
"""
from __future__ import annotations

import unittest
from collections import Counter
from datetime import date

from helpers import engine, run_rule, tiny_world
from shortlist import config
from shortlist.extract import RULES as READING_RULES
from shortlist.rules import BLOCKED, CLEAR, CONFIRM, GATES, MET, NA, NOT_MET, ROUTE, RULES, UNKNOWN, check_config

HOME_HEALTH = {"title": "Home Health PT"}
SHIFT = {"req.shifts": [{"start": "2026-12-05T07:00:00-05:00", "end": "2026-12-05T19:00:00-05:00"}]}
PAY = {"req.pay": {"amount": 2450, "unit": "week", "hours": 40}}
BOARDS = "ABEM/AOBEM preferred. Other boards considered with ATLS, ACLS, PALS and BLS."

# (rule, expected outcome, what the case shows, arguments for tiny_world)
CASES = [
    ("profession", MET, "same profession", {}),
    ("profession", NOT_MET, "a physician against a therapist job", {"person_fields": {"profession": "Physician"}}),
    ("profession", UNKNOWN, "no profession on the record", {"person_fields": {"profession": None}}),
    ("profession", NA, "the posting names no profession", {"job_fields": {"title": None}}),

    ("setting_experience", MET, "dated history longer than the minimum",
     {"job_fields": HOME_HEALTH, "job_text": "Structured minimum experience: 1 years.",
      "texts": [("resume", "2026-10-01", "Home-health PT, 2024-01 to present.")]}),
    ("setting_experience", NOT_MET, "dated history shorter than the minimum",
     {"job_fields": HOME_HEALTH, "job_text": "Structured minimum experience: 2 years.",
      "texts": [("resume", "2026-10-01", "Home-health PT, 2026-01 to present.")]}),
    ("setting_experience", NOT_MET, "the person says they have never worked in the setting",
     {"job_fields": HOME_HEALTH, "texts": [("candidate_message", "2026-11-01", "I have never worked in home health.")]}),
    ("setting_experience", UNKNOWN, "only another setting is on record: missing is not 'no'",
     {"job_fields": HOME_HEALTH, "texts": [("resume", "2026-10-01", "Outpatient PT, 2016 to present.")]}),
    ("setting_experience", UNKNOWN, "a bare claim of years that the dated history does not support",
     {"job_fields": HOME_HEALTH, "job_text": "Structured minimum experience: 2 years.",
      "texts": [("resume", "2026-10-01", "Home-health PT, 2025-05 to present."),
                ("candidate_statement", "2026-11-06", "I have three years of home-health work.")]}),
    ("setting_experience", NA, "the posting names no care setting", {}),

    ("skills", MET, "a required tool described in the résumé",
     {"job_text": "Requirements: OASIS.", "texts": [("resume", "2026-10-01", "Completed OASIS documentation.")]}),
    ("skills", MET, "a required tool in a structured skills list", {"job_text": "Requirements: OASIS.", "tags": ["OASIS"]}),
    ("skills", NOT_MET, "the person says they have not used a required tool",
     {"job_text": "Requirements: OASIS.", "texts": [("candidate_statement", "2026-11-01", "I have not used OASIS.")]}),
    ("skills", UNKNOWN, "nothing on record about a required tool", {"job_text": "Requirements: OASIS."}),
    ("skills", UNKNOWN, "a sentence saying the tool is NOT described must not count as having it",
     {"job_text": "Requirements: OASIS.", "texts": [("resume", "2026-10-01", "No OASIS detail supplied.")]}),
    ("skills", NA, "the posting lists no tools", {}),

    ("license", MET, "the person reports the license",
     {"job_text": "Requirements: Washington PT license.", "texts": [("resume", "2026-10-01", "Candidate reports active Washington PT license.")]}),
    ("license", UNKNOWN, "a license for a different state only",
     {"job_text": "Requirements: Washington PT license.", "texts": [("resume", "2026-10-01", "Candidate reports active Oregon PT license.")]}),
    ("license", NA, "the posting names no license", {}),

    ("certifications", MET, "the preferred board certification",
     {"job_text": BOARDS, "texts": [("resume", "2026-10-01", "Candidate reports ABEM certification.")]}),
    ("certifications", MET, "the other-boards route",
     {"job_text": BOARDS, "texts": [("resume", "2026-10-01", "Candidate reports family-medicine board certification, ATLS, ACLS, PALS and BLS.")]}),
    ("certifications", UNKNOWN, "one certificate, no board", {"job_text": BOARDS, "texts": [("resume", "2026-10-01", "Candidate reports ATLS.")]}),
    ("certifications", NA, "the posting names no certificate", {}),

    ("location", MET, "lives in the job's state", {"person_fields": {"home": {"city": "Spokane", "state": "WA"}}}),
    ("location", MET, "a message opening up to the job's city replaces an older preference",
     {"person_fields": {"pref_states": ["OR"], "travel": False},
      "texts": [("candidate_message", "2026-11-06", "I am open to Spokane now.")]}),
    ("location", NOT_MET, "prefers another state and will not travel", {"person_fields": {"pref_states": ["OR"], "travel": False}}),
    ("location", NOT_MET, "a later statement withdraws the city",
     {"texts": [("candidate_statement", "2026-11-06", "I am interested in Spokane."),
                ("candidate_statement", "2026-11-07", "Spokane is no longer a location I would consider.")]}),
    ("location", UNKNOWN, "nothing on record", {}),
    ("location", NA, "the posting gives no location", {"job_fields": {"location": None}}),

    ("start_date", MET, "can start on the day", {"job_fields": {"req.start": date(2026, 12, 1)},
                                                 "texts": [("candidate_message", "2026-11-05", "I can start December 1.")]}),
    ("start_date", NOT_MET, "can only start a week late", {"job_fields": {"req.start": date(2026, 12, 1)},
                                                           "texts": [("candidate_message", "2026-11-05", "I cannot start until December 8.")]}),
    ("start_date", UNKNOWN, "no start date given", {"job_fields": {"req.start": date(2026, 12, 1)}}),
    ("start_date", NA, "a shift-based job has no single start date", {}),

    ("schedule", MET, "same working pattern", {"job_fields": {"req.schedule": "weekdays"},
                                               "texts": [("candidate_message", "2026-11-05", "I can work weekdays.")]}),
    ("schedule", NOT_MET, "a different working pattern", {"job_fields": {"req.schedule": "weekdays"},
                                                          "texts": [("candidate_message", "2026-11-05", "I am only free on weekends.")]}),
    ("schedule", UNKNOWN, "no pattern given", {"job_fields": {"req.schedule": "weekdays"}}),
    ("schedule", NA, "the posting gives no pattern", {}),

    ("shift_coverage", MET, "offered the date", {"job_fields": SHIFT, "texts": [("candidate_message", "2026-11-06", "I could cover December 5.")]}),
    ("shift_coverage", NOT_MET, "declined the date", {"job_fields": SHIFT, "texts": [("candidate_message", "2026-11-06", "I cannot work December 5.")]}),
    ("shift_coverage", UNKNOWN, "placed, with the days not recorded: placed is not unavailable",
     {"job_fields": SHIFT, "person_fields": {"placement": {"status": "placed", "until": date(2026, 12, 31)}}}),
    ("shift_coverage", UNKNOWN, "offered a date that falls on a day the current assignment covers",
     {"job_fields": SHIFT, "texts": [("candidate_message", "2026-11-06", "My current assignment is Thursday through Saturday. I could cover December 5.")]}),
    ("shift_coverage", NA, "the posting lists no shifts", {}),

    ("pay", MET, "asks less than the approved pay", {"job_fields": PAY, "texts": [("candidate_message", "2026-11-05", "I need at least $2,400 per week.")]}),
    ("pay", MET, "an hourly ask compared with weekly pay",
     {"job_fields": PAY, "texts": [("candidate_message", "2026-11-05", "My minimum is $60/hour.")]}),
    ("pay", NOT_MET, "asks more than the approved pay", {"job_fields": PAY, "texts": [("candidate_message", "2026-11-05", "My minimum is $2,500 per week.")]}),
    ("pay", UNKNOWN, "no pay expectation given", {"job_fields": PAY}),
    ("pay", NA, "the posting states no pay", {}),

    ("job_open", CLEAR, "an open job", {}),
    ("job_open", BLOCKED, "a filled job", {"job_fields": {"status": "filled"}}),

    ("contact_permission", CLEAR, "no restriction", {}),
    ("contact_permission", CLEAR, "a no-contact request that has already ended",
     {"texts": [("candidate_message", "2026-10-01", "Please do not contact me until November 1, 2026.")]}),
    ("contact_permission", BLOCKED, "a no-contact request still in force, despite an older contact_allowed field",
     {"texts": [("ops_note", "2026-10-15", "Unassigned; contact_allowed=true; no placement recorded."),
                ("candidate_message", "2026-11-06", "Please do not contact me about any jobs until January 15, 2027.")]}),

    ("ownership", CLEAR, "unassigned", {"texts": [("ops_note", "2026-11-06", "Unassigned; no recorded contact restriction; no current placement recorded.")]}),
    ("ownership", ROUTE, "owned by a recruiter",
     {"texts": [("ops_note", "2026-11-04", "Assigned recruiter R2; candidate requested that opportunities go through R2.")]}),

    ("requirement_source", CLEAR, "no note changes any requirement", {}),
    ("requirement_source", CONFIRM, "the result depends on a note the feed does not show yet",
     {"job_fields": HOME_HEALTH, "job_text": "Structured minimum experience: 2 years.",
      "note": "Facility confirmed one year of home-health experience is acceptable.",
      "texts": [("resume", "2026-10-01", "Home-health PT, 2025-05 to present.")]}),
]

# Extra synthetic sentences so that every text-reading rule is exercised at least once.
READING_ONLY = [
    {"texts": [("recruiter_note", "2026-11-06", "Casey says the current placement runs Monday through Wednesday. R3 owns the placement. "
                                                "Recruiter R3 owns the current placement; no authorization for automated candidate outreach recorded.")]},
    {"job_text": "Open physical therapist role; home health; start 2026-12-01; 13 weeks; weekdays, 40 hours/week; approved candidate pay USD 2,450/week for 40 hours.",
     "note": "The feed has not yet been edited. The figure is candidate pay, not the facility bill rate. I have no confirmed exception to the December 1 start. "
             "The request is for both days. I have not confirmed whether the facility would accept someone for only one day. "
             "No individual candidate has been approved by the facility."},
    {"texts": [("resume", "2026-10-01", "DPT completed 2022. Outpatient clinic experience."),
               ("candidate_statement", "2026-11-06", "I am available December 1 for thirteen weeks, forty hours.")]},
]


class EveryRuleIsTouched(unittest.TestCase):
    def test_1_every_rule_runs_for_every_candidate_and_job(self):
        eng = engine()
        for step in range(len(eng.steps)):
            cov = eng.coverage(step)
            self.assertTrue(cov["every_rule_ran"])
            for matches, _ in eng.matches(step).values():
                for m in matches:
                    self.assertEqual([r.rule for r in m.results], list(RULES), "a fit rule was skipped")
                    self.assertEqual([g.rule for g in m.gates], list(GATES), "a permission rule was skipped")

    def test_2_every_case_gives_the_expected_outcome(self):
        for name, expected, what, kwargs in CASES:
            with self.subTest(rule=name, outcome=expected, case=what):
                world, cfg = tiny_world(**kwargs)
                self.assertEqual(run_rule(name, world, cfg).outcome, expected)

    def test_3_every_outcome_of_every_rule_has_a_case(self):
        covered = {(name, outcome) for name, outcome, _, _ in CASES}
        for registry in (RULES, GATES):
            for name, (_, declared) in registry.items():
                for outcome in declared:
                    self.assertIn((name, outcome), covered, f"no case reaches {name} -> {outcome}")
        for name, outcome in covered:
            declared = (RULES.get(name) or GATES.get(name))[1]
            self.assertIn(outcome, declared, f"{name} returned {outcome}, which it does not declare")

    def test_4_every_text_reading_rule_fires_somewhere(self):
        eng = engine()
        fired = Counter(eng.world(len(eng.events)).extractor.fired)
        for kwargs in [k for _, _, _, k in CASES] + READING_ONLY:
            world, _ = tiny_world(**kwargs)
            fired.update(world.extractor.fired)
        never = sorted(rid for rid in READING_RULES if not fired[rid])
        self.assertEqual(never, [], "text-reading rules that no data or case exercises")
        self.assertEqual(sorted(set(fired) - set(READING_RULES)), [], "a rule fired that is missing from the catalogue")

    def test_5_configuration_and_engine_list_the_same_rules(self):
        cfg = config.load()
        check_config(cfg)
        self.assertEqual(list(cfg["rules"]), list(RULES))
        self.assertEqual(list(cfg["gates"]), list(GATES))
        broken = dict(cfg, rules=dict(cfg["rules"], made_up_rule={"label": "x", "level": "hard", "weight": 1}))
        with self.assertRaises(ValueError):
            check_config(broken)


if __name__ == "__main__":
    unittest.main()
