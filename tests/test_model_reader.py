"""The guard on the optional model reader. No test here calls a model.

The providers below are made-up stand-ins: one that says yes to every topic in every sentence, one that
invents quotes, one that quotes true words about something else, one that crashes. If the guard holds
against these, a real model that is sometimes wrong cannot do worse. All text is SYNTHETIC.
"""
from __future__ import annotations

import unittest

from shortlist import config
from shortlist.extract import Extractor, clauses
from shortlist.lexicon import Lexicon
from shortlist.model_reader import ModelReader, make_reader
from shortlist.pipeline import Engine
from test_rewording import DO_NOT_CONTACT, FACTS, is_yes, read

TIER_RANK = {"not_fit": 0, "clarify": 1, "possible": 2, "strong": 3}


def liar(text, kind, vocabulary):
    """Says yes to everything, quoting each sentence word for word: the worst a model could do."""
    out = []
    for s, e in clauses(text):
        q = text[s:e]
        out += [dict(kind="skill", key="oasis", says="yes", value="", quote=q),
                dict(kind="skill", key="homecare_homebase", says="yes", value="", quote=q),
                dict(kind="setting", key="home_health", says="yes", value="", quote=q),
                dict(kind="setting", key="outpatient", says="yes", value="", quote=q),
                dict(kind="license", key="WA", says="yes", value="", quote=q),
                dict(kind="license", key="OH", says="yes", value="", quote=q),
                dict(kind="cert", key="acls", says="yes", value="", quote=q),
                dict(kind="location", key="Spokane", says="yes", value="", quote=q),
                dict(kind="location", key="Dayton", says="yes", value="", quote=q),
                dict(kind="start", key="", says="yes", value="2026-12-01", quote=q),
                dict(kind="shift", key="", says="yes", value="2026-12-05", quote=q),
                dict(kind="shift", key="", says="yes", value="2026-12-06", quote=q),
                dict(kind="schedule", key="", says="yes", value="weekdays", quote=q),
                dict(kind="weeks", key="", says="yes", value="13", quote=q),
                dict(kind="do_not_contact", key="", says="no", value="", quote=q)]
    return out


def reader(provider):
    cfg = config.load()
    return ModelReader(provider, Lexicon(cfg), "test")


class Guard(unittest.TestCase):
    def test_a_model_that_says_yes_to_everything_adds_no_false_yes(self):
        model = reader(liar)
        for fact, (test, _, noes, unsure) in FACTS.items():
            for s in noes + unsure:
                with self.subTest(fact=fact, sentence=s):
                    self.assertFalse(is_yes(test, s, model))

    def test_nor_can_it_lift_a_request_not_to_be_contacted(self):
        model = reader(liar)
        for s in DO_NOT_CONTACT:
            with self.subTest(sentence=s):
                self.assertTrue(any(c.attr == "dnc_until" for c in read(s, model=model)))

    def test_a_quote_that_is_not_in_the_document_is_refused(self):
        model = reader(lambda t, k, v: [dict(kind="skill", key="oasis", says="yes", value="", quote="I use OASIS daily")])
        self.assertFalse(is_yes(FACTS["uses OASIS"][0], "I use Homecare Homebase.", model))
        self.assertEqual(model.stats["refused: the quote is not in the document"], 1)

    def test_a_true_quote_about_something_else_is_refused(self):
        model = reader(lambda t, k, v: [dict(kind="skill", key="oasis", says="yes", value="", quote=t)])
        self.assertFalse(is_yes(FACTS["uses OASIS"][0], "I use Homecare Homebase.", model))
        self.assertEqual(model.stats["refused: the quote does not mention it"], 1)

    def test_a_recruiters_note_cannot_assert_a_skill_through_the_model_either(self):
        model = reader(lambda t, k, v: [dict(kind="skill", key="oasis", says="yes", value="", quote=t)])
        claims = read("Seems strong on OASIS.", kind="recruiter_note", model=model)
        self.assertFalse(any(c.attr == "skill" and c.polarity == "pos" for c in claims))

    def test_when_the_two_readers_disagree_neither_wins(self):
        model = reader(lambda t, k, v: [dict(kind="skill", key="oasis", says="no", value="", quote=t)])
        claims = read("I use OASIS every day.", model=model)
        self.assertFalse(any(c.attr == "skill" for c in claims))            # the rule reader's yes is set aside
        self.assertTrue(any(c.attr == "unclear" and "skill:oasis" in c.value for c in claims))   # and it is asked
        self.assertEqual(model.stats["disagreed"], 1)

    def test_agreement_adds_nothing_twice(self):
        model = reader(lambda t, k, v: [dict(kind="skill", key="oasis", says="yes", value="", quote=t)])
        claims = read("I use OASIS every day.", model=model)
        self.assertEqual(sum(1 for c in claims if c.attr == "skill" and c.key == "oasis"), 1)
        self.assertEqual(model.stats["agreed"], 1)

    def test_a_correct_model_reads_what_the_rules_miss(self):
        def good(t, k, v):
            return [dict(kind="location", key="Spokane", says="yes", value="", quote=t)] if "Spokane" in t else []
        model = reader(good)
        test, yeses, _, _ = FACTS["open to Spokane"]
        self.assertEqual([s for s in yeses if not is_yes(test, s)], ["Spokane works for me.", "Happy to relocate to Spokane."])
        self.assertTrue(all(is_yes(test, s, model) for s in yeses))
        self.assertTrue(all(c.rule.startswith("model.") for c in read("Spokane works for me.", model=model) if c.attr == "location_interest"))

    def test_a_model_that_fails_leaves_the_rule_reading_alone(self):
        def broken(t, k, v):
            raise TimeoutError("no answer")
        model = reader(broken)
        self.assertEqual(len(read("I use OASIS every day.", model=model)), len(read("I use OASIS every day.")))
        self.assertEqual(model.stats["errors"], 1)

    def test_each_document_is_read_once(self):
        calls = []
        model = reader(lambda t, k, v: calls.append(t) or [])
        cfg = config.load()
        ex = Extractor(cfg, Lexicon(cfg), model)
        from test_rewording import SAID
        from shortlist.model import Evidence
        for _ in range(3):
            ex.run(Evidence("x/1", "1", "x", "candidate_message", "person:x:1", SAID, False, "candidate", "x", "I use OASIS."))
        self.assertEqual(len(calls), 1)

    def test_saved_answers_are_replayed_after_a_restart_without_asking_again(self):
        import tempfile
        from pathlib import Path
        store = Path(tempfile.mkdtemp()) / "reads.json"
        calls = []

        def counting(t, k, v):
            calls.append(t)
            return [dict(kind="location", key="Spokane", says="yes", value="", quote=t)]
        cfg = config.load()
        first = ModelReader(counting, Lexicon(cfg), "m", store)
        before = read("Spokane works for me.", model=first)

        def must_not_be_called(t, k, v):
            raise AssertionError("asked again")
        second = ModelReader(must_not_be_called, Lexicon(cfg), "m", store)      # a new process, same file
        after = read("Spokane works for me.", model=second)
        self.assertEqual([(c.attr, c.key, c.value) for c in before], [(c.attr, c.key, c.value) for c in after])
        self.assertEqual((len(calls), second.stats["replayed"], second.stats["errors"]), (1, 1, 0))
        third = ModelReader(counting, Lexicon(cfg), "another-model", store)      # a different model asks afresh
        read("Spokane works for me.", model=third)
        self.assertEqual(len(calls), 2)

    def test_a_failed_request_is_not_saved_and_is_tried_again(self):
        import tempfile
        from pathlib import Path
        store = Path(tempfile.mkdtemp()) / "reads.json"
        cfg = config.load()

        def broken(t, k, v):
            raise TimeoutError
        read("I use OASIS.", model=ModelReader(broken, Lexicon(cfg), "m", store))
        self.assertFalse(store.exists())

    def test_the_real_provider_will_not_start_without_the_clients_agreement(self):
        cfg = config.load()
        cfg["model_reader"]["provider"] = "anthropic"
        with self.assertRaises(ValueError):
            make_reader(cfg)

    def test_on_the_supplied_sample_a_lying_model_raises_nobody(self):
        plain = Engine()
        cfg = config.load()
        cfg["model_reader"] = dict(cfg["model_reader"], _provider=liar)
        lied = Engine(cfg=cfg)
        for step in range(len(plain.steps)):
            before, after = plain.matches(step), lied.matches(step)
            for job, (rows, _) in before.items():
                was = {m.person: m.tier for m in rows}
                for m in after[job][0]:
                    with self.subTest(step=step, job=job, person=m.person):
                        self.assertLessEqual(TIER_RANK[m.tier], TIER_RANK[was[m.person]])


if __name__ == "__main__":
    unittest.main()
