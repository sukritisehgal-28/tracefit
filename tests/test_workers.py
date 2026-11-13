"""Several worker processes must give exactly the list one process gives (shortlist/workers.py)."""
from __future__ import annotations

import multiprocessing
import unittest

from helpers import engine
from shortlist.matching import Shortlist, card_dict
from shortlist.workers import Workers

FORK = "fork" in multiprocessing.get_all_start_methods()


def picture(world, sl) -> tuple:
    """Everything a recruiter or a destination could see of a list: every card in order, and the totals."""
    stats = {k: sl.stats[k] for k in ("people", "evaluated", "excluded_by_profession", "tiers", "actions", "outcomes", "reasons")}
    return [card_dict(world, m) for m in sl.ranked()], stats


@unittest.skipUnless(FORK, "needs an operating system with fork")
class WorkersGiveTheSameList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = engine()
        cls.world = cls.eng.world(1)                       # after the 18:05 job, so four jobs exist
        cls.jobs = list(cls.world.jobs.values())

    def one_process(self, job):
        return Shortlist(self.world, job, self.eng.cfg, compact_rows=True)

    def test_one_job_split_between_workers_is_the_same_list(self):
        for n in (2, 3, 5):                                # 5 workers for 16 people: some shares are tiny
            with Workers(self.world, self.eng.cfg, n) as workers:
                for job in self.jobs:
                    with self.subTest(workers=n, job=job.key):
                        self.assertEqual(picture(self.world, workers.shortlist(job)), picture(self.world, self.one_process(job)))

    def test_several_jobs_each_on_its_own_worker_are_the_same_lists(self):
        with Workers(self.world, self.eng.cfg, 3) as workers:
            built = workers.shortlists(self.jobs)
        self.assertEqual(set(built), {j.key for j in self.jobs})
        for job in self.jobs:
            with self.subTest(job=job.key):
                self.assertEqual(picture(self.world, built[job.key]), picture(self.world, self.one_process(job)))

    def test_a_person_can_still_be_explained_from_a_list_the_workers_built(self):
        job = self.world.jobs["job:VMS-901"]
        with Workers(self.world, self.eng.cfg, 2) as workers:
            sl = workers.shortlist(job)
        a = sl.explain(self.world, "person:ats:C2")
        b = self.one_process(job).explain(self.world, "person:ats:C2")
        self.assertEqual((a.tier, a.score, a.low, a.high, a.outcomes), (b.tier, b.score, b.low, b.high, b.outcomes))


if __name__ == "__main__":
    unittest.main()
