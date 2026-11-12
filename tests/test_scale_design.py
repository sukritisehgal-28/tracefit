"""The parts of the design that make the tool independent of how many people it holds.

  * A shortlist held as compact rows gives exactly the list, the totals and the explanations
    that a shortlist held in full gives.
  * The page is served a screenful at a time, and the first response holds no people at all.
  * The running service (Live) applies one event at a time and agrees with a full replay,
    on the supplied sample and on every scenario in the synthetic scenario base.
  * A name is a fact with a source like any other, with a general fallback when no source states one.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import asdict

from helpers import engine
from scenario_base import HH_JOB, SCENARIOS, build, cand, job, profile, update

from shortlist.matching import GROUPS, Shortlist
from shortlist.pipeline import Live
from shortlist.world import name_from_email

ROW = ("person", "name", "tier", "score", "low", "high", "summary", "flags", "band", "band_size", "identical",
       "decided_by", "standout", "typical", "outcomes", "open_questions")


def row(m) -> tuple:
    return tuple(list(v) if a == "flags" else v for a, v in ((a, getattr(m, a)) for a in ROW)) \
        + (m.action["status"], m.action["label"], m.action["owner"])


def explanation(m) -> str:
    return json.dumps({"results": [asdict(r) for r in m.results], "gates": [asdict(g) for g in m.gates],
                       "action": m.action, "questions": m.questions}, sort_keys=True, default=str)


def engines():
    """The supplied sample, then every scenario in the synthetic base that loads."""
    yield "sample", engine()
    for s in SCENARIOS:
        if not s.get("refuses"):
            yield s["id"], s["build"]()


class CompactRows(unittest.TestCase):
    def test_a_compact_list_is_the_same_list(self):
        for name, eng in engines():
            for step in range(len(eng.steps)):
                w = eng.world(step)
                for key, j in w.jobs.items():
                    full, small = Shortlist(w, j, eng.cfg, compact_rows=False), Shortlist(w, j, eng.cfg, compact_rows=True)
                    with self.subTest(data=name, step=step, job=key):
                        self.assertEqual([row(m) for m in small.ranked()], [row(m) for m in full.ranked()])
                        self.assertEqual({k: v for k, v in small.stats.items() if k != "compact"},
                                         {k: v for k, v in full.stats.items() if k != "compact"})
                        self.assertTrue(all(m.results is None for m in small.ranked()), "a compact row keeps no rule detail")

    def test_an_explanation_rebuilt_on_demand_is_the_same_explanation(self):
        for name, eng in engines():
            w = eng.world(len(eng.events))
            for key, j in w.jobs.items():
                full, small = Shortlist(w, j, eng.cfg, compact_rows=False), Shortlist(w, j, eng.cfg, compact_rows=True)
                for m in full.ranked():
                    with self.subTest(data=name, job=key, person=m.person):
                        again = small.explain(w, m.person)
                        self.assertEqual(explanation(again), explanation(m))
                        self.assertEqual(row(again), row(m))

    def test_the_totals_are_the_rows_counted(self):
        eng = engine()
        for step in range(len(eng.steps)):
            for rows, stats in eng.matches(step).values():
                self.assertEqual(sum(stats["tiers"].values()), len(rows))
                for i, name in enumerate(stats["rule_order"]):
                    counted = {}
                    for m in rows:
                        counted[m.outcomes[i]] = counted.get(m.outcomes[i], 0) + 1
                    self.assertEqual({k: v for k, v in stats["outcomes"][name].items() if v}, counted)


class Paging(unittest.TestCase):
    def test_the_pages_of_the_three_lists_add_up_to_the_whole_list_in_order(self):
        eng = engine()
        for key in ("job:VMS-901", "job:VMS-902", "job:VMS-904"):
            sl = eng.shortlist(2, key)
            seen = []
            for group in GROUPS:
                offset, total = 0, None
                while total is None or offset < total:
                    rows, total = sl.page(group, offset, 3)
                    self.assertLessEqual(len(rows), 3)
                    self.assertTrue(all(m.tier in GROUPS[group] for m in rows))
                    seen += rows
                    offset += 3
            self.assertEqual([m.person for m in seen], [m.person for m in sl.ranked()])

    def test_a_page_past_the_end_is_empty_not_an_error(self):
        sl = engine().shortlist(0, "job:VMS-901")
        self.assertEqual(sl.page("best", 10_000, 25)[0], [])

    def test_the_first_response_holds_no_people_and_does_not_grow_with_them(self):
        few = build([cand(), cand("C2")], [job()])       # two, so both responses carry the one note on identical résumés
        many = build([cand(f"C{i}") for i in range(1, 301)], [job()])
        a, b = (json.dumps(e.state(0), default=str) for e in (few, many))
        self.assertNotIn('"results"', b)
        self.assertNotIn("C150", b, "no person appears until a list is asked for")
        self.assertLess(abs(len(b) - len(a)), 200, "300 people and 2 people give a first response of the same size")
        page = many.cards(0, "job:VMS-1", "best", 0, 25)
        self.assertEqual((len(page["cards"]), page["total"]), (25, 300))
        self.assertNotIn("results", page["cards"][0])

    def test_one_person_s_explanation_arrives_with_only_the_evidence_it_cites(self):
        eng = engine()
        d = eng.detail(2, "job:VMS-901", "person:ats:C1")
        self.assertEqual(len(d["match"]["results"]) + len(d["match"]["gates"]), 14)
        cited = {ref["evidence"] for r in d["match"]["results"] + d["match"]["gates"] for ref in r["refs"] + r["job_refs"]}
        self.assertTrue(cited <= set(d["evidence"]))
        self.assertFalse(any(eid.startswith("our_system/R") for eid in d["evidence"]), "nobody else's evidence is sent")
        self.assertIsNone(eng.detail(2, "job:VMS-901", "person:nobody:X"))

    def test_a_destination_gets_the_top_of_each_list_and_the_totals(self):
        cfg = dict(engine().cfg, publish={"max_people_per_job": 3})
        from shortlist.pipeline import Engine
        payload = Engine(cfg=cfg).payload(2)
        for s in payload["shortlists"]:
            self.assertEqual((s["people_listed"], len(s["matches"]), s["people_checked"]), (3, 3, 16))
            self.assertEqual(sum(s["counts"].values()), 16)


class RunningService(unittest.TestCase):
    def agree(self, name, eng):
        live = Live.from_engine(eng)
        for step, event in enumerate(eng.events, start=1):
            got = live.ingest(event)
            order = lambda c: (c["job"], c.get("person", ""), c["kind"])      # noqa: E731
            with self.subTest(data=name, step=step, what="changes"):
                self.assertEqual(sorted(got, key=order), sorted(eng.changes(step), key=order))
            w = eng.world(step)
            with self.subTest(data=name, step=step, what="jobs"):
                self.assertEqual(sorted(live.open_jobs()), sorted(k for k, j in w.jobs.items() if j.status == "open"))
            for key in live.open_jobs():
                with self.subTest(data=name, step=step, job=key):
                    mine, theirs = live.shortlist(key), eng.shortlist(step, key)
                    self.assertEqual([row(m) for m in mine.ranked()], [row(m) for m in theirs.ranked()])
                    self.assertEqual(mine.stats, theirs.stats)

    def test_applying_events_one_at_a_time_agrees_with_a_replay_on_the_sample(self):
        self.agree("sample", engine())

    def test_and_on_every_scenario_in_the_synthetic_base(self):
        for name, eng in engines():
            self.agree(name, eng)

    def test_a_candidate_update_replaces_one_row_and_touches_nobody_else(self):
        eng = engine()
        live = Live.from_engine(eng)
        live.ingest(eng.events[0])
        before = {key: dict(live.shortlist(key).by_person) for key in live.open_jobs()}
        changes = live.ingest(eng.events[1])
        self.assertEqual([(c["name"], c["job"], c["tier"], c["not_met"]) for c in changes],
                         [("Alex Sample", "job:VMS-901", "not_fit", ["start_date"])])
        for key, rows in before.items():
            now = live.shortlist(key).by_person
            self.assertEqual(set(now), set(rows))
            for person, m in rows.items():
                if person == "person:ats:C1":
                    self.assertIsNot(now[person], m, "the updated person has a new row")
                else:
                    self.assertIs(now[person], m, "everyone else's row is the very same object: it was not recomputed")

    def test_a_job_that_changes_is_rebuilt_and_only_the_people_who_moved_are_reported(self):
        tighter = job(details=HH_JOB.replace("2 years", "5 years"))
        eng = build([cand("C1"), cand("C2", cv=None, msg=None)], [job()],
                    feeds=[{"eventId": "EV-J", "occurredAt": "2026-11-07T18:05:00Z", "eventType": "requisition.updated", "record": tighter}])
        live = Live.from_engine(eng)
        changes = live.ingest(eng.events[0])
        self.assertEqual([(c["kind"], c["person"]) for c in changes], [("match_changed", "person:ats:C1")])
        self.assertEqual(live.shortlist("job:VMS-1").by_person["person:ats:C1"].tier, "not_fit")


    def test_an_event_delivered_twice_changes_nothing_the_second_time(self):
        eng = engine()
        live = Live.from_engine(eng)
        event = eng.events[0]
        live.ingest(event)
        rows = {key: list(sl.ranked()) for key, sl in live.lists.items()}
        self.assertEqual(live.ingest(event), [])
        for key, sl in live.lists.items():
            self.assertTrue(all(a is b for a, b in zip(rows[key], sl.ranked())))


class Names(unittest.TestCase):
    def test_only_an_address_that_clearly_looks_like_a_name_gives_one(self):
        yes = {"jane.doe@x.org": "Jane Doe", "Jane_Doe+jobs@x.org": "Jane Doe", "mary-ann.smith@x.org": "Mary Ann Smith"}
        no = ["r01@example.invalid", "c1@example.invalid", "jdoe42@x.org", "j.doe@x.org", "jane@x.org", "info@x.org",
              "hr.team@x.org", "a.b.c.d@x.org", "", None]
        for email, name in yes.items():
            self.assertEqual(name_from_email(email), name)
        for email in no:
            self.assertIsNone(name_from_email(email), email)

    def test_the_supplied_profiles_have_no_name_in_any_source_or_in_their_email(self):
        people = engine().world(0).people
        self.assertEqual({p.name_basis for k, p in people.items() if ":ats:" in k}, {"stated"})
        unnamed = [p for k, p in people.items() if ":our_system:" in k]
        self.assertEqual(len(unnamed), 12)
        self.assertEqual({p.name_basis for p in unnamed}, {"id"}, "r01@example.invalid is the record id again, not a name")

    def test_a_name_is_read_from_the_email_only_when_no_source_states_one_and_it_is_labelled(self):
        eng = build([cand("C1")], [job()], profiles=[profile("P7", email="jane.doe@synthetic.invalid"), profile("P8")])
        people = eng.world(0).people
        self.assertEqual((people["person:ats:C1"].name, people["person:ats:C1"].name_basis), ("Synthetic C1", "stated"))
        self.assertEqual((people["person:our_system:P7"].name, people["person:our_system:P7"].name_basis), ("Jane Doe", "email"))
        self.assertEqual((people["person:our_system:P8"].name, people["person:our_system:P8"].name_basis), ("Candidate P8", "id"))
        card = next(c for c in eng.cards(0, "job:VMS-1", "best")["cards"] if c["person"] == "person:our_system:P7")
        self.assertEqual((card["name"], card["name_basis"]), ("Jane Doe", "email"))

    def test_a_guessed_name_never_decides_who_is_the_same_person(self):
        eng = build([], [job()], profiles=[profile("P1", email="jane.doe@a.invalid"), profile("P2", email="jane_doe@b.invalid")])
        people = eng.world(0).people
        self.assertEqual(sorted(p.name for p in people.values()), ["Jane Doe", "Jane Doe"])
        self.assertEqual(len(people), 2, "the same guessed name with different addresses stays two people")

    def test_the_fallback_can_be_switched_off_in_configuration(self):
        eng = build([], [job()], profiles=[profile("P7", email="jane.doe@synthetic.invalid")], settings={"identity": {"name_fallbacks": ["id"]}})
        self.assertEqual(eng.world(0).people["person:our_system:P7"].name, "Candidate P7")


if __name__ == "__main__":
    unittest.main()
