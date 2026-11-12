"""Updates are applied without rebuilding everything, and give exactly the same answer as a rebuild."""
from __future__ import annotations

import unittest

from shortlist.matching import match_job, match_person
from shortlist.pipeline import Engine
from shortlist.world import World


def fingerprint(world, cfg):
    out = {}
    for key, job in world.jobs.items():
        out[key] = [(m.person, m.tier, m.score, m.low, m.high, m.action["status"], m.signature)
                    for m in match_job(world, job, cfg)[0]]
    return out


class Incremental(unittest.TestCase):
    def test_applying_events_one_at_a_time_equals_rebuilding_from_scratch(self):
        eng = Engine()
        live = World(eng.cfg, eng.batches, eng.snapshot_at)
        touched_by_step = []
        for step, event in enumerate(eng.events, start=1):
            touched_by_step.append(live.apply(event.batch, event.at))
            rebuilt = eng.world(step)
            self.assertEqual(sorted(live.people), sorted(rebuilt.people))
            self.assertEqual(sorted(live.jobs), sorted(rebuilt.jobs))
            self.assertEqual(fingerprint(live, eng.cfg), fingerprint(rebuilt, eng.cfg), f"step {step}")
        self.assertEqual(touched_by_step, [{"job:VMS-904"}, {"person:ats:C1"}], "only what the event touched was re-resolved")

    def test_a_candidate_update_only_rechecks_that_candidate_against_open_jobs(self):
        eng = Engine()
        live = World(eng.cfg, eng.batches + [eng.events[0].batch], eng.events[0].at)
        (subject,) = live.apply(eng.events[1].batch, eng.events[1].at)
        rechecked = match_person(live, live.people[subject], eng.cfg)
        self.assertEqual(sorted(rechecked), ["job:VMS-901", "job:VMS-902", "job:VMS-904"], "open jobs only")
        self.assertEqual(rechecked["job:VMS-901"].tier, "not_fit")
        full = {m.person: m for m in eng.matches(2)["job:VMS-901"][0]}[subject]
        self.assertEqual((rechecked["job:VMS-901"].score, rechecked["job:VMS-901"].signature), (full.score, full.signature))


if __name__ == "__main__":
    unittest.main()
