"""What a recruiter types into "Add your own": a phrase in the candidate's own words, or an institution.

    python3 tests/test_own_criteria.py        prints every case and whether it held

Every résumé, message and institution here is SYNTHETIC, written in layouts and wording the supplied sample does
not use, so the feature is checked on words it was not built around. The rules being checked:

  - words are matched whole, whatever their case, accents, hyphens or plural ("art" is not in "start")
  - a sentence that denies, doubts, hopes or asks is not a mention, and a later denial beats an earlier mention
  - only the candidate's own documents count, never a note about them
  - any institution can be typed; only education and training lines are searched, never an employer
  - a different word for the same thing is NOT found. Those cases are listed too, as known limits.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_base import HH, build, cand  # noqa: E402
from shortlist.resume import mention, studied_at  # noqa: E402

CV = """Jordan Lee | Physical Therapist
Boise, Idaho | jordan.lee@example.invalid

WORK HISTORY
Riverbend Home Care
Home-health physical therapist, 2021 to present
- Independent home visits across three counties
- OASIS start-of-care documentation
- Pediatric aquatic therapy and recovery following sports injuries
St. Mary's Hospital — Inpatient physical therapist
2018 to 2021

CORE SKILLS
Wound care; vestibular rehabilitation, Homecare Homebase and Epic. Data tools: SQL and C++.

LANGUAGES
I speak Spanish and Tagalog.

EDUCATION
Bluefield University
Doctor of Physical Therapy, 2018

LICENSES & CERTIFICATIONS
Active Washington PT license.

No ventilator experience. I have never worked in a burn unit. I might be open to hand therapy.
I would like to learn dry needling. Salary history is not included in this résumé."""

# (what the recruiter types, is it found, why)
PHRASES = [
    ("wound care", True, "as written"),
    ("WOUND CARE", True, "case does not matter"),
    ("Wound-Care", True, "a hyphen is a space"),
    ("woundcare", False, "LIMIT: two words typed as one are not split"),
    ("home health", True, "the résumé writes home-health"),
    ("home visit", True, "singular finds the plural"),
    ("sports injury", True, "injury finds injuries"),
    ("sports injuries", True, "as written"),
    ("pediatrics", True, "pediatrics finds pediatric"),
    ("aquatic therapy", True, "two words inside a longer phrase"),
    ("epic", True, "one word, lower case"),
    ("OASIS", True, "an acronym"),
    ("c++", True, "symbols in a name are kept"),
    ("SQL", True, "a short acronym"),
    ("St Marys", True, "punctuation and apostrophes do not matter"),
    ("st. mary's hospital", True, "as written"),
    ("three counties", True, "plain words"),
    ("spanish", True, "from another section"),
    ("tagalog", True, "from another section"),
    ("inpatient", True, "from a job title"),
    ("art", False, "not a word here, only letters inside 'start' and 'therapy'"),
    ("oas", False, "part of a word is not the word"),
    ("ep", False, "part of a word is not the word"),
    ("care wound", False, "the words must be in the order typed"),
    ("rehab", False, "LIMIT: a shorter form of 'rehabilitation' is not found"),
    ("bilingual", False, "LIMIT: a different word for the same thing is not found"),
    ("kids", False, "LIMIT: a different word for the same thing is not found"),
    ("ventilator", False, "'No ventilator experience' is a denial"),
    ("burn unit", False, "'never worked in' is a denial"),
    ("hand therapy", False, "'might be open to' is a doubt"),
    ("dry needling", False, "'would like to learn' is a hope, not experience"),
    ("salary history", False, "'is not included' says it is missing"),
    ("oncology", False, "not on the résumé at all"),
    ("a", False, "too short to mean anything"),
    ("", False, "empty"),
    (".*", False, "symbols only"),
    ("(wound", True, "stray punctuation is ignored"),
]

# (the education on a résumé, what the recruiter types, is it found, why)
HEADED = "Sam Park | Physical Therapist\n\nEXPERIENCE\nRiverbend Home Care — Home-health PT\n2021 to present\n\nEDUCATION\n{}\n"
INSTITUTIONS = [
    (HEADED.format("Bluefield University\nDoctor of Physical Therapy, 2018"), "Bluefield University", True, "school on its own line"),
    (HEADED.format("Bluefield University\nDoctor of Physical Therapy, 2018"), "bluefield", True, "part of the name, lower case"),
    (HEADED.format("Bluefield University\nDoctor of Physical Therapy, 2018"), "Bluefield Univ.", True, "a common abbreviation"),
    (HEADED.format("Bluefield University\nDoctor of Physical Therapy, 2018"), "Doctor of Physical Therapy", True, "a degree can be typed too"),
    (HEADED.format("Bluefield University\nDoctor of Physical Therapy, 2018"), "Riverbend", False, "an employer is not where they studied"),
    (HEADED.format("Bluefield University\nDoctor of Physical Therapy, 2018"), "Blue", False, "part of a word is not the word"),
    (HEADED.format("MSN — Johns Hopkins School of Nursing, 2015\nBSN — Howard University, 2011"), "Johns Hopkins", True, "degree first, school after"),
    (HEADED.format("MSN — Johns Hopkins School of Nursing, 2015\nBSN — Howard University, 2011"), "john hopkins", True, "a common misspelling of the plural"),
    (HEADED.format("MSN — Johns Hopkins School of Nursing, 2015\nBSN — Howard University, 2011"), "Howard", True, "the second school listed"),
    (HEADED.format("B.S. Kinesiology, Texas A&M University, 2012"), "Texas A and M", True, "'&' and 'and' are the same"),
    (HEADED.format("B.S. Kinesiology, Texas A&M University, 2012"), "texas a&m", True, "as written, lower case"),
    (HEADED.format("B.S. Kinesiology, Texas A&M University, 2012"), "Texas Tech", False, "a different school in the same state"),
    (HEADED.format("Massachusetts Inst. of Technology — B.S. Biology, 2010"), "Massachusetts Institute of Technology", True, "abbreviated on the résumé"),
    (HEADED.format("A.S., Spokane Falls Community College, 2012"), "Spokane Falls", True, "a community college"),
    (HEADED.format("École de Kinésithérapie de Lyon — Diplôme d'État, 2014"), "Ecole de Kinesitherapie", True, "accents do not matter"),
    (HEADED.format("University of Texas at Austin, B.S. 2011"), "University of Texas", True, "as written"),
    (HEADED.format("University of Texas at Austin, B.S. 2011"), "Texas University", False, "word order matters: that is another school"),
    (HEADED.format("University of Texas at Austin, B.S. 2011"), "UT Austin", False, "LIMIT: initials are not expanded"),
    ("DPT, Cascade State College, completed 2019. Home-health PT, 2021-03 to present: independent home visits, OASIS.",
     "Cascade State", True, "a one-paragraph extract with no headings"),
    ("Graduated from Northgate College of Health Sciences. Home-health PT, 2021-03 to present: independent home visits.",
     "Northgate College", True, "a sentence, no heading, no year"),
    ("Sam Park\n\nTRAINING\nEmergency-medicine residency at Lakeview Medical Center, completed 2017.\n\nWORK HISTORY\nEmergency-department attending — 2017–present.",
     "Lakeview Medical Center", True, "a training hospital under a Training heading"),
    ("Home-health PT, 2021-03 to present: independent home visits, OASIS. Candidate reports active Washington PT license.",
     "Bluefield University", False, "no education on the résumé at all"),
]


def person_with(cv, msgs=None, **kw):
    eng = build([cand(cv=cv, msgs=msgs if msgs is not None else [], **kw)], HH())
    world = eng.world(0)
    return world, world.people["person:ats:C1"]


class TypedPhrases(unittest.TestCase):
    def test_every_phrase(self):
        world, p = person_with(CV)
        for phrase, want, why in PHRASES:
            with self.subTest(phrase=phrase, why=why):
                self.assertEqual(mention(world, p, phrase) is not None, want)

    def test_the_sentence_shown_is_the_candidates_own(self):
        world, p = person_with(CV)
        clause, ev = mention(world, p, "sports injury")
        self.assertIn("sports injuries", clause)
        self.assertEqual(ev.kind, "resume")

    def test_a_later_denial_beats_the_resume(self):
        world, p = person_with(CV, msgs=[("I have not used Epic since 2019 and would need retraining.", "2026-11-05")])
        self.assertIsNone(mention(world, p, "Epic"))
        self.assertIsNotNone(mention(world, p, "wound care"))          # nothing else is taken back

    def test_a_later_yes_beats_an_earlier_no(self):
        world, p = person_with("Home-health PT, 2021-03 to present: independent home visits, OASIS.",
                               msgs=[("I have not done lymphedema therapy.", "2026-10-01"), ("I now run the lymphedema therapy clinic.", "2026-11-05")])
        self.assertIsNotNone(mention(world, p, "lymphedema therapy"))

    def test_a_question_is_not_a_mention(self):
        world, p = person_with("Home-health PT, 2021-03 to present: independent home visits, OASIS.",
                               msgs=[("Does the role involve lymphedema therapy?", "2026-11-05")])
        self.assertIsNone(mention(world, p, "lymphedema therapy"))

    def test_a_recruiters_note_about_them_does_not_count(self):
        eng = build([cand(cv="Home-health PT, 2021-03 to present: independent home visits, OASIS.", msgs=[])], HH(),
                    extra_journal=[{"entry_pk": "N1", "person_fk": 1, "entry_type": "recruiter_note", "created_on": "2026-11-05",
                                    "body": "Excellent at lymphedema therapy, I am told."}])
        world = eng.world(0)
        self.assertIsNone(mention(world, world.people["person:ats:C1"], "lymphedema therapy"))


class Institutions(unittest.TestCase):
    def test_every_institution(self):
        for cv, typed, want, why in INSTITUTIONS:
            with self.subTest(typed=typed, why=why):
                world, p = person_with(cv)
                self.assertEqual(studied_at(world, p, typed) is not None, want)

    def test_the_line_shown_names_the_school_and_its_source(self):
        world, p = person_with(INSTITUTIONS[6][0])
        line = studied_at(world, p, "Howard")
        self.assertIn("Howard University", line["text"])
        self.assertTrue(line["source"])

    def test_the_filter_narrows_a_list_to_the_people_who_studied_there(self):
        eng = build([cand("C1", cv=INSTITUTIONS[0][0] + "OASIS start-of-care documentation, Homecare Homebase. Active Washington PT license."),
                     cand("C2", cv=INSTITUTIONS[6][0] + "OASIS start-of-care documentation, Homecare Homebase. Active Washington PT license.")], HH())
        job = next(iter(eng.world(0).jobs))
        got = lambda school: [c["person"] for c in eng.cards(0, job, "best", 0, 50, None, [f"school:{school}"])["cards"]]   # noqa: E731
        everyone = {c["person"] for g in ("best", "not_fit") for c in eng.cards(0, job, g, 0, 50)["cards"]}
        on_list = {c["person"] for c in eng.cards(0, job, "best", 0, 50)["cards"]}
        self.assertEqual(everyone, {"person:ats:C1", "person:ats:C2"})
        self.assertEqual(set(got("Bluefield")), {"person:ats:C1"} & on_list)
        self.assertEqual(set(got("Howard University")), {"person:ats:C2"} & on_list)
        self.assertEqual(got("Oxford"), [])


def main() -> None:
    world, p = person_with(CV)
    bad = 0
    print("Typed phrases, against one synthetic résumé:")
    for phrase, want, why in PHRASES:
        got = mention(world, p, phrase) is not None
        bad += got != want
        print(f"  {'ok ' if got == want else 'BAD'} {'found    ' if got else 'not found'} {phrase!r:26} {why}")
    print("\nInstitutions, against synthetic résumés in different layouts:")
    for cv, typed, want, why in INSTITUTIONS:
        w, q = person_with(cv)
        got = studied_at(w, q, typed) is not None
        bad += got != want
        print(f"  {'ok ' if got == want else 'BAD'} {'found    ' if got else 'not found'} {typed!r:40} {why}")
    limits = sum(1 for row in PHRASES + [r[1:] for r in INSTITUTIONS] if row[2].startswith("LIMIT"))
    print(f"\n{len(PHRASES)} phrases and {len(INSTITUTIONS)} institutions checked; {bad} wrong; {limits} are known limits. All data is synthetic.")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
