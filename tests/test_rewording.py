"""A sweep of reworded SYNTHETIC sentences through the text reader.

    python3 tests/test_rewording.py        print every sentence, what was read, and the read rate per fact

The reader was written from the phrasings in the sample packet. Real candidates word things in many
other ways, so this sweep checks the one property that must hold whatever the wording:

  1. A sentence that says no, or is unsure (planning, hoping, pending, asking), is never read as a yes.
     A false yes puts someone on a shortlist they should not be on.
  2. A request not to be contacted is never missed, however it is worded.
     A missed request lets a recruiter call someone who asked not to be called.

Sentences that plainly say yes are not required to be read. One that is not read becomes a question
for the recruiter, which is safe. How many are read is printed as a rate, so the reader's reach is
measured rather than claimed.

Every sentence here is made up for this test.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shortlist import config  # noqa: E402
from shortlist.extract import Extractor  # noqa: E402
from shortlist.lexicon import Lexicon  # noqa: E402
from shortlist.model import Evidence  # noqa: E402

SAID = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _is(attr, key=None, value=None, test=None):
    """A test for one kind of yes: a claim on this attribute (and key), read as positive."""
    def check(c):
        if c.attr != attr or c.polarity != "pos" or (key is not None and c.key != key):
            return False
        if test is not None:
            return test(c.value)
        return value is None or c.value == value
    return check


# fact: (what counts as a yes, sentences that say yes, sentences that say no, sentences that are unsure)
FACTS = {
    "uses OASIS": (
        _is("skill", "oasis", True),
        ["I use OASIS every day.", "I have completed OASIS start-of-care documentation for three years.",
         "OASIS documentation is part of my daily work.", "Experienced with OASIS.",
         "I'm comfortable with OASIS assessments.", "I've done OASIS for every admission."],
        ["I have not used OASIS.", "I've never used OASIS.", "I have no experience with OASIS.",
         "OASIS is not something I'm familiar with.", "I don't know OASIS.", "I haven't done OASIS documentation.",
         "No OASIS experience.", "I am unfamiliar with OASIS.", "OASIS? Never touched it.", "Never worked with OASIS.",
         "I lack OASIS experience.", "I have not been trained on OASIS yet."],
        ["I am currently learning OASIS.", "I plan to get trained on OASIS.", "Is OASIS required for this role?",
         "I'm hoping to learn OASIS on the job.", "My manager asked whether I had used OASIS.",
         "OASIS training is scheduled for next month.", "I might have used OASIS once, years ago."],
    ),
    "home-health experience": (
        _is("experience", "home_health"),
        ["I have worked in home health since 2023.", "Home health PT, 2023 to present.",
         "Three years of home health visits.", "I do home health."],
        ["I have never worked in home health.", "I haven't done home health.", "Home health is not something I've done.",
         "No home health experience.", "I've only worked outpatient, never home health.",
         "I don't have home-health experience."],
        ["I'm interested in moving into home health.", "I hope to start in home health.",
         "Would home health suit someone like me?", "I want to try home health next."],
    ),
    "Washington license": (
        _is("license", "WA"),
        ["Active Washington PT license.", "I hold an active Washington license.", "Washington license — active."],
        ["I do not have a Washington license.", "My Washington license lapsed.", "I don't hold a Washington license yet.",
         "No Washington license.", "My Washington license has expired.", "Washington license not active."],
        ["I am applying for a Washington license.", "My Washington license is pending.",
         "I plan to get a Washington license.", "Do you need a Washington license?"],
    ),
    "ACLS": (
        _is("cert", "acls"),
        ["ACLS certified.", "I hold ACLS and BLS.", "Current ACLS."],
        ["I don't have ACLS.", "My ACLS expired.", "ACLS lapsed in 2024.", "No ACLS."],
        ["ACLS renewal is pending.", "I'm scheduled to take ACLS next month.", "Is ACLS required?"],
    ),
    "open to Spokane": (
        _is("location_interest", "Spokane", True),
        ["I am open to Spokane.", "I would consider Spokane.", "Spokane works for me.", "Happy to relocate to Spokane."],
        ["I'm not open to Spokane.", "Spokane is no longer an option.", "I won't relocate to Spokane.",
         "I'm not interested in Spokane anymore.", "Spokane doesn't work for me.", "I would not consider Spokane.",
         "I'm no longer considering Spokane."],
        ["Might be open to Spokane.", "Is Spokane the only location?", "I'd be open to Spokane if the pay is right."],
    ),
    "can start 1 December": (
        _is("available_from", value=date(2026, 12, 1)),
        ["I can start December 1.", "Available from December 1.", "I could start on December 1."],
        ["I can't start December 1.", "I'm not able to start December 1.", "I won't be available December 1.",
         "I am unable to start December 1.", "December 1 is too early for me to start.", "I cannot start until December 8."],
        ["I might be able to start December 1.", "I hope to start December 1.", "Could I start December 1?"],
    ),
    "covers 5 December": (
        _is("shift_availability", "2026-12-05", True),
        ["I could cover December 5.", "I can work December 5.", "Happy to take December 5."],
        ["I can't cover December 5.", "I won't be able to work December 5.", "I'm not available to cover December 5.",
         "I am unable to work December 5.", "December 5 doesn't work for me."],
        ["I may be able to cover December 5.", "Could I cover December 5 only?"],
    ),
    "works weekdays": (
        _is("schedule", value="weekdays"),
        ["I can work weekdays.", "Weekdays suit me."],
        ["I can't do weekdays.", "I don't work weekdays.", "Weekdays are not possible for me.", "No weekdays, please."],
        ["Maybe weekdays.", "Are weekdays required?"],
    ),
    "13 weeks": (
        _is("weeks", value=13),
        ["I can commit to 13 weeks.", "Thirteen weeks works."],
        ["I can't commit to 13 weeks.", "13 weeks is too long for me.", "I won't do 13 weeks."],
        ["Possibly 13 weeks.", "Is 13 weeks fixed?"],
    ),
}

# Every one of these asks not to be contacted. Each must be read as a restriction.
DO_NOT_CONTACT = [
    "Please do not contact me about any jobs until January 15, 2027.",
    "Please don't reach out to me until January 15.",
    "Do not call me about roles until January 15.",
    "Please hold off on contacting me until January 15.",
    "I don't want to hear about jobs until January 15.",
    "Until January 15, please don't contact me.",
    "Don't email me about new jobs before January 15.",
    "I'm not looking for work until January 15.",
    "Please stop contacting me.",
    "Please remove me from your list.",
    "No more job emails, please.",
]


def read(sentence: str, kind: str = "candidate_message", model=None):
    cfg = config.load()
    ex = Extractor(cfg, Lexicon(cfg), model)
    author = {"recruiter_note": "recruiter", "ops_note": "operations"}.get(kind, "candidate")
    ev = Evidence(id="sweep/1", local_id="1", source="sweep", kind=kind, subject="person:sweep:P1", observed_at=SAID,
                  date_only=False, author=author, label="Sweep sentence", text=sentence)
    return ex.run(ev)


def is_yes(test, sentence: str, model=None) -> bool:
    return any(test(c) for c in read(sentence, model=model))


def blocks(sentence: str) -> bool:
    return any(c.attr == "dnc_until" for c in read(sentence))


class Rewording(unittest.TestCase):
    def test_a_no_is_never_read_as_a_yes(self):
        for fact, (test, _, noes, _) in FACTS.items():
            for s in noes:
                with self.subTest(fact=fact, sentence=s):
                    self.assertFalse(is_yes(test, s))

    def test_an_unsure_sentence_is_never_read_as_a_yes(self):
        for fact, (test, _, _, unsure) in FACTS.items():
            for s in unsure:
                with self.subTest(fact=fact, sentence=s):
                    self.assertFalse(is_yes(test, s))

    def test_a_request_not_to_be_contacted_is_never_missed(self):
        for s in DO_NOT_CONTACT:
            with self.subTest(sentence=s):
                self.assertTrue(blocks(s))

    def test_a_recruiters_note_about_a_missed_call_is_not_a_request_from_the_candidate(self):
        for s in ["I did not call her back yet.", "Could not reach the candidate; no contact until we hear back."]:
            with self.subTest(sentence=s):
                self.assertFalse(any(c.attr == "dnc_until" for c in read(s, kind="recruiter_note")))

    def test_the_sample_phrasings_are_still_read(self):
        # The wording the sample packet uses must keep working after any change to the reader.
        for fact, sentence in [("uses OASIS", "Home-health PT, 2023-01 to present: independent home visits, OASIS start-of-care documentation, Homecare Homebase."),
                               ("Washington license", "Candidate reports active Washington PT license."),
                               ("open to Spokane", "I am open to Spokane now."),
                               ("can start 1 December", "I can start December 1, work weekdays for thirteen weeks."),
                               ("works weekdays", "I can start December 1, work weekdays for thirteen weeks."),
                               ("13 weeks", "I can start December 1, work weekdays for thirteen weeks."),
                               ("covers 5 December", "I could cover both December 5 and 6 day shifts in Dayton.")]:
            with self.subTest(fact=fact):
                self.assertTrue(is_yes(FACTS[fact][0], sentence))


def report() -> int:
    bad = 0
    total_yes = read_yes = 0
    for fact, (test, yeses, noes, unsure) in FACTS.items():
        got = [is_yes(test, s) for s in yeses]
        total_yes, read_yes = total_yes + len(got), read_yes + sum(got)
        print(f"\n{fact}: {sum(got)} of {len(got)} plain yes sentences read")
        for s, ok in zip(yeses, got):
            print(f"   {'read  ' if ok else 'unread'}  {s}")
        for label, group in (("no", noes), ("unsure", unsure)):
            for s in group:
                wrong = is_yes(test, s)
                bad += wrong
                print(f"   {'FALSE YES' if wrong else 'safe     '}  ({label}) {s}")
    missed = [s for s in DO_NOT_CONTACT if not blocks(s)]
    bad += len(missed)
    print(f"\nDo not contact: {len(DO_NOT_CONTACT) - len(missed)} of {len(DO_NOT_CONTACT)} read as a restriction")
    for s in DO_NOT_CONTACT:
        print(f"   {'MISSED   ' if s in missed else 'blocked  '}  {s}")
    print(f"\nPlain yes sentences read: {read_yes} of {total_yes}. Unsafe readings: {bad}. All sentences are synthetic.")
    return bad


if __name__ == "__main__":
    sys.exit(1 if report() else 0)
