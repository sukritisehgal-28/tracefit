"""The text reader on individual sentences, including the ones that are easy to misread. All SYNTHETIC."""
from __future__ import annotations

import unittest
from datetime import date

from helpers import PERSON, engine, tiny_world


def person(**kwargs):
    world, _ = tiny_world(**kwargs)
    return world, world.people[PERSON]


class Reading(unittest.TestCase):
    def test_not_december_1_is_not_read_as_the_start_date(self):
        _, p = person(texts=[("candidate_statement", "2026-11-07", "I can start December 8, not December 1.")])
        self.assertEqual(p.fact("available_from").value, date(2026, 12, 8))

    def test_a_date_without_a_year_is_the_next_one_after_it_was_said(self):
        _, p = person(texts=[("candidate_message", "2026-11-06", "Please do not contact me until January 15.")])
        self.assertEqual(p.fact("dnc_until").value, date(2027, 1, 15))

    def test_a_newer_statement_replaces_an_older_one_and_keeps_it(self):
        _, p = person(texts=[("candidate_statement", "2026-11-06", "I need at least $2,400/week for forty hours."),
                             ("candidate_statement", "2026-11-07", "My minimum is now $2,500 per week for forty hours.")])
        pay = p.fact("pay")
        self.assertEqual(pay.value["amount"], 2500)
        self.assertEqual([c.value["amount"] for c in pay.older], [2400])

    def test_a_recruiter_note_cannot_assert_a_skill(self):
        world, p = person(texts=[("recruiter_note", "2026-11-06", "Strong clinician with OASIS experience.")])
        self.assertIsNone(p.fact("skill", "oasis"))
        self.assertIn(("test/T0", "Strong clinician with OASIS experience."), world.extractor.unparsed,
                      "the sentence is kept as context, not turned into a fact")

    def test_a_recruiter_note_can_record_an_unanswered_question(self):
        _, p = person(texts=[("recruiter_note", "2026-11-06", "I asked about OASIS. No response is recorded yet.")])
        self.assertEqual(p.fact("skill", "oasis").state, "unanswered")

    def test_only_the_skill_after_the_denial_is_negated(self):
        _, p = person(texts=[("candidate_statement", "2026-11-07", "I have OASIS experience but have not used Homecare Homebase.")])
        self.assertEqual(p.fact("skill", "oasis").state, "pos")
        self.assertEqual(p.fact("skill", "homecare_homebase").state, "neg")

    def test_a_university_in_washington_is_not_a_washington_license(self):
        _, p = person(texts=[("resume", "2026-10-30", "DPT, Pine Valley University, Washington, completed 2022.")])
        self.assertEqual(p.keyed("license"), {})

    def test_a_bare_year_is_read_conservatively(self):
        _, p = person(texts=[("resume", "2026-11-01", "Outpatient PT, 2016 to present.")])
        years = p.fact("experience", "outpatient").value["years_documented"]
        self.assertTrue(9.8 < years < 9.9, "counted from the end of 2016, not the start")

    def test_a_role_title_and_its_dates_on_separate_lines_are_joined(self):
        _, p = person(texts=[("resume", "2026-10-30", "Evergreen Visiting Care — Home-health Physical Therapist\nMay 2025–present")])
        self.assertEqual(p.fact("experience", "home_health").value["since"], "May 2025")

    def test_separate_roles_in_one_setting_add_up_without_double_counting_overlap(self):
        _, p = person(texts=[("resume", "2026-10-30", "Home-health PT, 2020-01 to 2021-01. Home-health PT, 2020-07 to 2022-01.")])
        self.assertAlmostEqual(p.fact("experience", "home_health").value["years_documented"], 2.0, places=1)

    def test_weekly_and_hourly_pay_are_both_read(self):
        _, weekly = person(texts=[("candidate_message", "2026-11-06", "I need at least $2,400 per week for forty hours.")])
        _, hourly = person(texts=[("candidate_message", "2026-11-06", "My minimum is $230/hour.")])
        self.assertEqual((weekly.fact("pay").value["unit"], weekly.fact("pay").value["hours"]), ("week", 40))
        self.assertEqual((hourly.fact("pay").value["unit"], hourly.fact("pay").value["amount"]), ("hour", 230))


class SuppliedFormats(unittest.TestCase):
    def test_the_feeds_us_style_date_is_read_as_month_first(self):
        w = engine().world(1)
        self.assertEqual(w.jobs["job:VMS-901"].req("req.start").feed.value, date(2026, 12, 1))
        self.assertEqual(w.jobs["job:VMS-904"].req("req.start").feed.value, date(2026, 12, 8))

    def test_shift_times_keep_their_local_offset(self):
        shifts = engine().world(0).jobs["job:VMS-902"].req("req.shifts").feed.value
        self.assertEqual(shifts[0]["start"], "2026-12-05T07:00:00-05:00")

    def test_the_job_setting_is_found_even_when_the_ats_title_hides_it(self):
        job = engine().world(0).jobs["job:VMS-901"]
        self.assertIn("Physical Therapist", job.titles)
        self.assertEqual(job.req("req.setting").feed.value, "home_health")


if __name__ == "__main__":
    unittest.main()
