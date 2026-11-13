"""Several events at the same moment: the result must not depend on which one happened to arrive first.

All people, jobs and events here are SYNTHETIC. The supplied sample has two events, five minutes apart; these
tests cover what it cannot: a burst of different events sharing one timestamp, delivered in many orders.
"""
from __future__ import annotations

import random
import unittest

from helpers import engine
from scenario_base import HH, HH_JOB, J, NO_START, build, cand, feed, job, update
from shortlist.pipeline import Live

SEATTLE = HH_JOB.replace("Spokane", "Seattle")


def burst():
    """Seven events at 18:05:00: a new job, a pay change, a job filled, and four candidate updates (two from one person)."""
    return build([cand("C1"), cand("C2"), cand("C3"), cand("C4"), cand("C5")], HH() + [job("VMS-7", details=SEATTLE)],
                 updates=[update("EV-U1", "18:05", "C1", "My start date has changed. I cannot start until December 8."),
                          update("EV-U2", "18:05", "C2", "Please do not contact me about any jobs until January 15, 2027."),
                          update("EV-U3", "18:05", "C3", "My minimum is now $2,600 per week for forty hours."),
                          update("EV-U4", "18:05", "C1", "My minimum is now $2,500 per week for forty hours.")],
                 feeds=[feed("EV-J1", "18:05", job("VMS-9")),
                        feed("EV-J2", "18:05", job(details=HH_JOB.replace("2,450", "2,700")), "requisition.updated"),
                        feed("EV-J3", "18:05", job("VMS-7", details=SEATTLE, state="FILLED"), "requisition.updated")])


def lists(world, shortlist_of) -> dict:
    out = {}
    for key in sorted(world.jobs):
        if world.jobs[key].status != "open":
            out[key] = world.jobs[key].status
        else:
            out[key] = [(m.person, m.tier, m.score, m.low, m.high, m.action["status"]) for m in shortlist_of(key).ranked()]
    return out


def replayed(eng) -> dict:
    step = len(eng.events)
    return lists(eng.world(step), lambda key: eng.shortlist(step, key))


def delivered(eng, order) -> dict:
    live = Live.from_engine(eng)
    for i in order:
        live.ingest(eng.events[i])
    return lists(live.world, live.shortlist)


class ABurstInAnyOrder(unittest.TestCase):
    def test_seven_events_at_one_moment_give_the_same_lists_in_any_order(self):
        want = replayed(burst())
        rng = random.Random(11)
        for _ in range(40):
            eng = burst()
            order = list(range(len(eng.events)))
            rng.shuffle(order)
            with self.subTest(order=[eng.events[i].id for i in order]):
                self.assertEqual(delivered(eng, order), want)

    def test_the_burst_did_what_each_event_says(self):
        got = replayed(burst())
        home = {p.split(":")[-1]: (tier, action) for p, tier, _s, _lo, _hi, action in got["job:" + J]}
        self.assertEqual(home["C1"], ("not_fit", "none"))             # start date moved, and the pay ask went up
        self.assertEqual(home["C2"], ("strong", "blocked"))           # asked not to be contacted
        self.assertEqual(home["C3"][0], "strong")                     # $2,600 is under the new $2,700 rate
        self.assertEqual(got["job:VMS-7"], "filled")
        self.assertEqual(len(got["job:VMS-9"]), 5)                    # the new job was matched against everyone

    def test_the_same_event_delivered_twice_in_a_burst_counts_once(self):
        eng = burst()
        order = list(range(len(eng.events)))
        self.assertEqual(delivered(eng, order + order), replayed(eng))


class StatementsThatClashAtOneMoment(unittest.TestCase):
    def clash(self, first="EV-A", second="EV-B"):
        return build([cand(msg=NO_START)], HH(), updates=[update(first, "18:05", "C1", "I can start December 1."),
                                                          update(second, "18:05", "C1", "I cannot start until December 8.")])

    def test_neither_is_taken_whichever_arrives_first(self):
        for ids in (("EV-A", "EV-B"), ("EV-B", "EV-A")):
            eng = self.clash(*ids)
            for order in ([0, 1], [1, 0]):
                with self.subTest(ids=ids, order=order):
                    (row,) = delivered(eng, order)["job:" + J]
                    self.assertEqual(row[1], "clarify")
            self.assertEqual(replayed(eng)["job:" + J][0][1], "clarify")

    def test_the_card_shows_both_statements_and_asks_which_is_right(self):
        eng = self.clash()
        m = eng.explain(2, "job:" + J, "person:ats:C1")
        start = next(r for r in m.results if r.rule == "start_date")
        self.assertEqual((start.outcome, "same_moment" in start.flags, "replaced" in start.flags), ("unknown", True, False))
        self.assertEqual({ref["quote"] for ref in start.refs}, {"I can start December 1.", "I cannot start until December 8."})
        self.assertTrue(any("which is right" in q for q in start.questions))

    def test_only_the_rule_about_that_fact_is_unsettled(self):
        eng = self.clash()
        m = eng.explain(2, "job:" + J, "person:ats:C1")
        others = {r.rule: r.outcome for r in m.results if r.rule not in ("start_date", "certifications", "shift_coverage")}
        self.assertTrue(all(o == "met" for o in others.values()), others)

    def test_the_supplied_sample_has_no_such_clash(self):
        eng = engine()
        for step in range(3):
            self.assertEqual([p.key for p in eng.world(step).people.values() if p.contested], [])


class AJobUpdatedAndFilledAtOneMoment(unittest.TestCase):
    def test_it_is_treated_as_filled_in_either_order_and_reported(self):
        raise_pay = feed("EV-A", "18:05", job(details=HH_JOB.replace("2,450", "2,700")), "requisition.updated")
        fill = feed("EV-B", "18:05", job(state="FILLED"), "requisition.updated")
        for feeds in ([raise_pay, fill], [fill, raise_pay]):
            eng = build([cand()], HH(), feeds=feeds)
            for order in ([0, 1], [1, 0]):
                with self.subTest(first=feeds[0]["eventId"], order=order):
                    self.assertEqual(delivered(eng, order)["job:" + J], "filled")
            self.assertEqual(replayed(eng)["job:" + J], "filled")
            self.assertTrue(any("same moment disagree on whether it is open" in i["message"] for i in eng.issues(2)))



class TheBurstCommand(unittest.TestCase):
    """python3 -m shortlist burst: eight SYNTHETIC events at one moment, on the supplied data."""

    @classmethod
    def setUpClass(cls):
        from shortlist.burst import render, run
        cls.report = run(orders=12)
        cls.text = render(cls.report)

    def test_every_order_gives_the_same_lists_and_equals_a_rebuild(self):
        self.assertEqual(self.report["orders_identical"], self.report["orders"])
        self.assertTrue(self.report["equals_rebuild"])

    def test_each_event_did_what_it_says(self):
        final = self.report["final"]
        home = {p.split(":")[-1]: (tier, action) for p, tier, _s, _lo, _hi, action in final["job:VMS-901"]}
        self.assertEqual(final["job:VMS-902"], "filled")
        self.assertEqual(home["R07"][0], "strong")                    # can start 1 December after all
        self.assertEqual(home["R03"], ("strong", "blocked"))          # asked not to be contacted
        self.assertEqual(len(final["job:VMS-905"]), 16)               # the new job was checked against everyone
        self.assertEqual(self.report["clash"].outcome, "unknown")     # R02's two pay figures at the same moment

    def test_the_output_says_the_events_are_synthetic(self):
        self.assertIn("SYNTHETIC", self.text)

if __name__ == "__main__":
    unittest.main()
