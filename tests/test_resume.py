"""Reading a whole résumé for the recruiter (shortlist/resume.py). All résumés here are SYNTHETIC and use
layouts the supplied sample does not, so the reader is not fitted to the sample."""
from __future__ import annotations

import unittest

from helpers import engine
from scenario_base import HH, J, build, cand
from shortlist.resume import background, parse

HEADED = """Jordan Lee | Physical Therapist
Boise, Idaho | jordan.lee@example.invalid | (208) 555-0100

WORK HISTORY
Riverbend Home Care
Home-health physical therapist, 2021 to present
- Independent home visits across three counties
- OASIS start-of-care documentation
St. Mary's Hospital — Inpatient physical therapist
2018 to 2021

CORE SKILLS
Wound care; vestibular rehabilitation, Homecare Homebase and Epic

EDUCATION
Bluefield University
Doctor of Physical Therapy, 2018

LICENSES & CERTIFICATIONS
Active Washington PT license.
BLS certified.

Salary history is not included in this résumé."""

PARAGRAPH = ("DPT, Bluefield University, completed 2018. Riverbend Home Care, home-health PT, 2021-03 to present: independent home "
             "visits, wound care, OASIS start-of-care documentation. Candidate reports active Washington PT license.")


class Sections(unittest.TestCase):
    def test_a_headed_resume_is_read_section_by_section(self):
        r = parse(HEADED)
        self.assertEqual(r["header"], {"name": "Jordan Lee", "home": "Boise, Idaho", "email": "jordan.lee@example.invalid",
                                       "phone": "(208) 555-0100"})
        titles = [(x["title"], x["dates"]) for x in r["experience"]]
        self.assertEqual(titles, [("Riverbend Home Care — Home-health physical therapist", "2021 to present"),
                                  ("St. Mary's Hospital — Inpatient physical therapist", "2018 to 2021")])
        self.assertEqual(r["experience"][0]["duties"], ["Independent home visits across three counties", "OASIS start-of-care documentation"])
        self.assertEqual(r["skills"], ["Wound care", "vestibular rehabilitation", "Homecare Homebase", "Epic"])
        self.assertEqual(r["education"], ["Doctor of Physical Therapy, 2018 — Bluefield University"])
        self.assertEqual(r["credentials"], ["Active Washington PT license", "BLS certified"])
        self.assertEqual(r["not_stated"], ["Salary history is not included in this résumé."])

    def test_a_one_paragraph_resume_is_split_by_what_each_sentence_says(self):
        r = parse(PARAGRAPH)
        self.assertEqual([x["dates"] for x in r["experience"]], ["2021-03 to present"])
        self.assertIn("wound care", r["skills"])
        self.assertEqual(len(r["education"]), 1)
        self.assertEqual(len(r["credentials"]), 1)

    def test_a_job_title_on_the_first_line_is_not_taken_for_a_name(self):
        self.assertNotIn("name", parse("Physical Therapist | Spokane, Washington\n\nEXPERIENCE\nX Clinic, 2020 to present\n\nSKILLS\nOASIS")["header"])


class OnThePersonPanel(unittest.TestCase):
    def test_skills_no_rule_checks_are_shown_and_marked(self):
        eng = build([cand(cv=HEADED)], HH())
        w = eng.world(0)
        skills = {s["text"]: s["checked"] for s in background(w, w.people["person:ats:C1"])["skills"]}
        self.assertEqual(skills["Homecare Homebase"], ["Homecare Homebase"])
        self.assertEqual((skills["Wound care"], skills["Epic"]), ([], []))

    def test_what_a_resume_lists_beyond_the_vocabulary_never_changes_a_result(self):
        plain = build([cand()], HH()).matches(0)["job:" + J][0][0]
        extra = build([cand(cv=cand()["documents"][0]["text"] + " Also: wound care; Epic; pediatric aquatic therapy.")], HH()).matches(0)["job:" + J][0][0]
        self.assertEqual((plain.tier, plain.score, plain.outcomes), (extra.tier, extra.score, extra.outcomes))

    def test_the_sample_shows_blairs_unchecked_skill(self):
        w = engine().world(0)
        b = background(w, w.people["person:ats:C2"])
        self.assertIn("sports injury recovery", [s["text"] for s in b["skills"] if not s["checked"]])
        self.assertEqual(b["header"]["home"], "Spokane, Washington")


class NameFromTheResume(unittest.TestCase):
    def test_a_person_with_no_stated_name_takes_it_from_their_resume_and_it_is_labelled(self):
        eng = build([cand(cv=HEADED, first_name=None, last_name=None)], HH())
        p = eng.world(0).people["person:ats:C1"]
        self.assertEqual((p.name, p.name_basis), ("Jordan Lee", "resume"))

    def test_a_stated_name_always_wins(self):
        p = build([cand(cv=HEADED)], HH()).world(0).people["person:ats:C1"]
        self.assertEqual(p.name_basis, "stated")

    def test_the_same_resume_name_never_joins_two_people(self):
        eng = build([cand("C1", cv=HEADED, first_name=None, last_name=None), cand("C2", cv=HEADED, first_name=None, last_name=None)], HH())
        self.assertEqual(len(eng.world(0).people), 2)


class Links(unittest.TestCase):
    """Links a candidate puts on a résumé are shown, never followed. SYNTHETIC."""

    CV = HEADED + "\n\nPORTFOLIO\nCase studies: https://example.invalid/jordan/home-health-outcomes. Talk: http://example.invalid/talk?id=7"

    def test_links_are_read_from_the_resume(self):
        self.assertEqual(parse(self.CV)["links"], ["https://example.invalid/jordan/home-health-outcomes", "http://example.invalid/talk?id=7"])

    def test_links_reach_the_card_and_the_background(self):
        from shortlist.matching import card_dict
        eng = build([cand(cv=self.CV)], HH())
        w = eng.world(0)
        self.assertEqual(len(background(w, w.people["person:ats:C1"])["links"]), 2)
        self.assertEqual(card_dict(w, eng.matches(0)["job:" + J][0][0])["links"][0], "https://example.invalid/jordan/home-health-outcomes")

    def test_the_sample_has_no_links(self):
        w = engine().world(0)
        self.assertTrue(all(not background(w, p)["links"] for p in w.people.values()))


class SameName(unittest.TestCase):
    """Two different people with one name must be told apart on the page, and never joined. SYNTHETIC."""

    def test_two_people_with_one_name_are_both_listed_and_labelled(self):
        from shortlist.matching import card_dict
        eng = build([cand("C1", first_name="Maria", last_name="Lopez"), cand("C2", first_name="Maria", last_name="Lopez")], HH())
        w = eng.world(0)
        cards = [card_dict(w, m) for m in eng.matches(0)["job:" + J][0]]
        self.assertEqual(len(cards), 2)
        self.assertEqual({c["namesakes"] for c in cards}, {1})
        self.assertEqual(len({c["ref"] for c in cards}), 2)          # each card says which record it is

    def test_a_unique_name_carries_no_label(self):
        from shortlist.matching import card_dict
        w = engine().world(0)
        self.assertTrue(all(card_dict(w, m)["namesakes"] == 0 for m in engine().matches(0)["job:VMS-901"][0]))

    def test_an_older_name_stays_visible(self):
        from shortlist.matching import person_dict
        eng = build([cand("C1", first_name="Maria", last_name="Lopez", email="m@synthetic.invalid", updated_at="2026-10-01"),
                     cand("C2", first_name="Maria", last_name="Lopez-Grant", email="m@synthetic.invalid", updated_at="2026-11-01")], HH())
        w = eng.world(0)
        d = person_dict(w, next(iter(w.people.values())))
        self.assertEqual((d["name"], d["other_names"]), ("Maria Lopez-Grant", ["Maria Lopez"]))


if __name__ == "__main__":
    unittest.main()
