"""The comparison in docs/EVALUATION.md is run, not assumed: a simple field-and-keyword matcher on the supplied
data, beside the tool's own result. The baseline is not part of the product."""
from __future__ import annotations

import unittest

from helpers import engine
from shortlist.baseline import run


class WhatAFieldAndKeywordMatcherDoes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = {r["job"]: r for r in run(engine())}

    def test_it_cannot_tell_thirteen_people_apart_for_the_home_health_job(self):
        kept = self.report["VMS-901"]["kept"]
        self.assertEqual(len(kept), 13)
        self.assertEqual({p["hits"] for p in kept}, {4})                 # every one of them has all four words

    def test_it_counts_words_from_a_sentence_that_says_they_are_missing(self):
        blair = next(p for p in self.report["VMS-901"]["kept"] if p["id"] == "C2")
        self.assertEqual(blair["hits"], 4)                               # "No home-health, OASIS, or Homecare Homebase detail supplied"
        self.assertEqual(blair["ours"].tier, "clarify")

    def test_it_keeps_people_the_tool_rules_out_or_must_not_contact(self):
        kept = {p["id"]: p["ours"] for p in self.report["VMS-901"]["kept"]}
        self.assertEqual((kept["R07"].tier, kept["R12"].tier), ("not_fit", "not_fit"))      # start a week late; Spokane withdrawn
        self.assertEqual(kept["R11"].action["status"], "blocked")                           # asked for no contact

    def test_it_removes_the_two_people_whose_fields_are_out_of_date(self):
        alex = next(p for p in self.report["VMS-901"]["out"] if p["id"] == "C1")
        casey = next(p for p in self.report["VMS-902"]["out"] if p["id"] == "C3")
        self.assertEqual((alex["why"], alex["ours"].tier), ("preferred-state field", "strong"))
        self.assertEqual((casey["why"], casey["ours"].tier), ("status field: placed", "strong"))

    def test_it_puts_the_person_who_declined_first_for_the_physician_job(self):
        kept = self.report["VMS-902"]["kept"]
        self.assertEqual([p["id"] for p in kept], ["C4"])
        self.assertEqual((kept[0]["ours"].tier, kept[0]["ours"].action["status"]), ("not_fit", "blocked"))
