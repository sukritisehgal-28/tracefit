"""Building shortlists on several worker processes of one machine.

Matching one person against one job needs nothing from any other person, so a job's people can be split
between workers, and separate jobs can go to separate workers. Each worker runs the same rules on its share
and sends back finished rows; the parent puts them together and ranks them. The result is identical to the
one-process result (tests/test_workers.py checks every row), so this changes speed, never an answer.

This is the matching step of the designed service (docs/ARCHITECTURE.md), measured for real in
bench/benchmark.py --workers. It is NOT a distributed deployment: there is no queue, no network and no
database here, and the product itself still runs in one process by default.

Workers are forked from the loaded process, so they share its memory until they write to it and need no
second copy of the data. That needs an operating system with fork (macOS, Linux).
"""
from __future__ import annotations

import multiprocessing

from .matching import Shortlist

_STATE: dict = {}                     # what a forked worker inherits: the loaded world and the settings


def _rows(args) -> list:
    """Runs in a worker: the finished rows for one job and one share of its people."""
    job_key, keys = args
    world, cfg = _STATE["world"], _STATE["cfg"]
    pool = [world.people[k] for k in keys]
    return list(Shortlist(world, world.jobs[job_key], cfg, pool=pool, compact_rows=True).by_person.values())


def _whole(job_key) -> tuple:
    """Runs in a worker: one job's whole list, already counted and in order, as a worker in the service
    design would write it to the store."""
    world, cfg = _STATE["world"], _STATE["cfg"]
    sl = Shortlist(world, world.jobs[job_key], cfg, compact_rows=True)
    return sl.ranked(), sl.stats


def _adopt(world, job, cfg, rows, stats) -> Shortlist:
    """Take a list a worker finished: nothing is recounted or re-ordered here."""
    sl = Shortlist(world, job, cfg, pool=[], compact_rows=True)
    sl.by_person = {m.person: m for m in rows}
    sl.rows, sl.stats, sl.dirty = rows, stats, False
    return sl


def _assemble(world, job, cfg, parts) -> Shortlist:
    """Put the workers' rows into one list with the same totals a one-process build keeps."""
    sl = Shortlist(world, job, cfg, pool=[], compact_rows=True)
    sl.stats["blocked"] = Shortlist.candidates(world, job, cfg)[1]
    for rows in parts:
        for m in rows:
            sl._count(m, 1)
            sl.by_person[m.person] = m
    sl._sizes(world)
    sl.dirty = True
    return sl


class Workers:
    """A fixed set of worker processes that share one loaded world.

        with Workers(world, cfg, 4) as workers:
            sl = workers.shortlist(job)             # one job, its people split between the workers
            lists = workers.shortlists(jobs)        # several jobs, each built whole by one worker

    The workers see the world as it was when they started. Start them after loading, and start new ones
    after the world changes.
    """

    def __init__(self, world, cfg: dict, n: int):
        if "fork" not in multiprocessing.get_all_start_methods():
            raise RuntimeError("Worker processes need an operating system with fork (macOS or Linux).")
        self.world, self.cfg, self.n = world, cfg, max(1, int(n))
        _STATE.update(world=world, cfg=cfg)
        self.pool = multiprocessing.get_context("fork").Pool(self.n)

    def shortlist(self, job) -> Shortlist:
        people = [p.key for p in Shortlist.candidates(self.world, job, self.cfg)[0]]
        size = max(1, -(-len(people) // self.n))
        shares = [(job.key, people[i:i + size]) for i in range(0, len(people), size)]
        return _assemble(self.world, job, self.cfg, self.pool.map(_rows, shares))

    def shortlists(self, jobs) -> dict:
        jobs = list(jobs)
        done = self.pool.map(_whole, [job.key for job in jobs], chunksize=1)
        return {job.key: _adopt(self.world, job, self.cfg, rows, stats) for job, (rows, stats) in zip(jobs, done)}

    def close(self) -> None:
        self.pool.close()
        self.pool.join()
        _STATE.clear()

    def __enter__(self) -> "Workers":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
