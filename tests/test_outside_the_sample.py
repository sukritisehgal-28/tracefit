"""Inputs worded or shaped differently from the supplied sample. All data here is SYNTHETIC.

These came from an independent bug hunt against the finished tool: each case below gave a wrong contact
decision, a wrong "yes", or a crash before it was fixed. The rule they share: when the tool cannot read
something, it must fail towards a question or a block, never towards "free to reach out".
"""
from __future__ import annotations

import unittest

from scenario_base import HH, J, build, cand, doctor, ed_job, feed, find, job, profile, update
from shortlist.pipeline import Live


def gate(engine, person, name="contact_permission", job_number=J, step=0):
    m = find(engine, step, job_number, person)
    return next(r for r in m.results + m.gates if r.rule == name), m


class ContactRestrictions(unittest.TestCase):
    def test_a_restriction_field_blocks_however_it_is_worded(self):
        for text in ["Do not contact", "DNC", "No contact - candidate request", "Do not contact until 2027-01-15",
                     "Do not contact until 15 January 2027", "No contact until Jan. 15, 2027", "Do not contact until further notice",
                     "Candidate opted out of all outreach", "Candidate asked us to stop contacting her.",
                     "Sensitive: see manager before any approach"]:
            with self.subTest(text=text):
                g, m = gate(build([], HH(), profiles=[profile("R01", restriction=text)]), "R01")
                self.assertEqual((g.outcome, m.action["status"]), ("blocked", "blocked"))

    def test_a_restriction_field_that_names_a_recruiter_routes_instead(self):
        g, m = gate(build([], HH(), profiles=[profile("R01", restriction="Opportunity recommendations must go through recruiter R10.")]), "R01")
        self.assertEqual((g.outcome, m.action["status"]), ("clear", "route"))

    def test_an_empty_restriction_field_blocks_nobody(self):
        for text in [None, "", "None", "n/a"]:
            with self.subTest(text=text):
                self.assertEqual(gate(build([], HH(), profiles=[profile("R01", restriction=text)]), "R01")[0].outcome, "clear")

    def test_a_date_in_any_common_form_is_read(self):
        for text in ["Do not contact until 2027-01-15", "Do not contact until 15 January 2027", "No contact until Jan. 15, 2027"]:
            with self.subTest(text=text):
                self.assertIn("15 Jan 2027", gate(build([], HH(), profiles=[profile("R01", restriction=text)]), "R01")[0].shows)

    def test_staff_notes_that_say_do_not_contact_block(self):
        for ops in ["Unassigned. Do not contact.", "Unassigned; contact_allowed=false.", "Candidate opted out of outreach."]:
            with self.subTest(ops=ops):
                self.assertEqual(gate(build([cand(ops=ops)], HH()), "C1")[1].action["status"], "blocked")

    def test_a_recruiter_missing_a_call_is_still_not_a_request(self):
        eng = build([cand(ops="Unassigned. Could not reach the candidate; no contact until we hear back.")], HH())
        self.assertEqual(gate(eng, "C1")[0].outcome, "clear")


class BadValuesDoNotStopTheTool(unittest.TestCase):
    def test_an_impossible_date_is_left_unread(self):
        for text in ["I can start June 31.", "I can cover November 31.", "Please do not contact me until February 30."]:
            with self.subTest(text=text):
                eng = build([cand(msgs=[(text, "2026-11-06")])], HH())
                self.assertIsNotNone(find(eng, 0, J, "C1"))          # a result, not an exception

    def test_an_impossible_date_in_a_live_update_is_not_fatal(self):
        eng = build([cand()], HH(), updates=[update("EV-1", "18:05", "C1", "I can start June 31.")])
        live = Live.from_engine(eng)
        live.ingest(eng.events[0])
        self.assertIn("job:" + J, live.open_jobs())


class Events(unittest.TestCase):
    def test_a_job_closed_by_an_unknown_event_type_is_closed(self):
        for kind in ("requisition.closed", "requisition.filled", "requisition.cancelled"):
            with self.subTest(kind=kind):
                eng = build([cand()], HH(), feeds=[feed("EV-1", "18:05", job(state="CLOSED"), kind)])
                self.assertEqual(find(eng, 1, J, "C1").action["status"], "closed")

    def test_an_unknown_event_type_for_an_open_job_is_still_ignored(self):
        eng = build([cand()], HH(), feeds=[feed("EV-1", "18:05", job(title="Something else"), "requisition.audited")])
        self.assertEqual(len(eng.events), 0)

    def test_two_sources_may_use_the_same_event_id(self):
        eng = build([cand()], HH(), feeds=[feed("1001", "18:05", job("VMS-9"))],
                    updates=[update("1001", "18:06", "C1", "My start date has changed. I cannot start until December 8.")])
        self.assertEqual(len(eng.events), 2)
        self.assertEqual(find(eng, 2, J, "C1").tier, "not_fit")


class WhoAndWhat(unittest.TestCase):
    def test_a_different_job_that_contains_the_word_is_not_the_profession(self):
        for profession, the_job in (("Physician Assistant", ed_job()), ("Physical Therapist Assistant", None), ("Pharmacy Technician (PT)", None)):
            with self.subTest(profession=profession):
                person = dict(doctor(), profession=profession) if the_job else cand(profession=profession)
                eng = build([person], [the_job] if the_job else HH())
                m = find(eng, 0, "VMS-2" if the_job else J, person["id"])
                self.assertEqual(next(r for r in m.results if r.rule == "profession").outcome, "not_met")
                self.assertEqual(m.tier, "not_fit")

    def test_a_placeholder_email_joins_nobody(self):
        for emails in (("N/A", "N/A", "n/a"), ("  ", "  "), ("none@none.com", "none@none.com"), ("frontdesk@lakeside.invalid",) * 2):
            with self.subTest(emails=emails):
                eng = build([cand(f"C{i}", email=e) for i, e in enumerate(emails, start=1)], HH(),
                            people_over={f"C{i}": {"email_address": e} for i, e in enumerate(emails, start=1)})
                self.assertEqual(len(eng.world(0).people), len(emails))


class NotesAndTheFeed(unittest.TestCase):
    NOTE = ("VMS-1", "account_manager", "2026-11-05", "Facility confirmed one year of home-health experience is acceptable.")
    SHORT = "Home-health PT, 2025-05 to present: independent home visits, OASIS start-of-care documentation, Homecare Homebase. " \
            "Candidate reports active Washington PT license."

    def setting(self, feeds):
        eng = build([cand(cv=self.SHORT)], HH(), notes=[self.NOTE], feeds=feeds)
        return next(r for r in find(eng, len(eng.events), J, "C1").results if r.rule == "setting_experience")

    def test_a_note_stands_when_a_later_feed_version_changes_something_else(self):
        from scenario_base import HH_JOB
        later = feed("EV-1", "18:05", job(details=HH_JOB.replace("2,450", "2,550")), "requisition.updated")
        self.assertEqual(self.setting([later]).outcome, "met")            # 18 months against the note's one year

    def test_a_feed_that_changes_the_value_after_the_note_is_the_newer_word(self):
        from scenario_base import HH_JOB
        later = feed("EV-1", "18:05", job(details=HH_JOB.replace("minimum experience: 2 years", "minimum experience: 3 years")), "requisition.updated")
        self.assertEqual(self.setting([later]).outcome, "not_met")        # the feed moved to three years after the note


class SettingsSlips(unittest.TestCase):
    """A mistyped setting stops the tool with a message. It never quietly turns a rule off."""

    def refused(self, settings, word):
        with self.assertRaises(ValueError) as raised:
            build([cand()], HH(), settings=settings)
        self.assertIn(word, str(raised.exception))

    def test_a_mistyped_rule_level_is_refused(self):
        for level in ("required", "Hard", ""):
            self.refused({"rules": {"license": {"level": level}}}, "level")

    def test_a_mistyped_policy_is_refused(self):
        for policy in ("feed-only", "Feed_Only", ""):
            self.refused({"requirement_changes": {"policy": policy}}, "policy")

    def test_weights_and_thresholds_must_be_sensible_numbers(self):
        self.refused({"dimensions": {"pay": -10}}, "pay")
        self.refused({"dimensions": {"pay": "ten"}}, "pay")
        self.refused({"scoring": {"unknown_credit": 5}}, "unknown_credit")
        self.refused({"scoring": {"strong_threshold": "80"}}, "strong_threshold")
        self.refused({"dimensions": dict.fromkeys(("setting", "skills", "depth", "evidence", "readiness", "pay"), 0)}, "all zero")


if __name__ == "__main__":
    unittest.main()
