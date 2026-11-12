"""Shared test helpers.

`engine()` runs on the supplied sample packet.
`tiny_world()` builds a one-job, one-person world by hand. Everything it builds
is SYNTHETIC test data and is never mixed with the supplied packet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from shortlist import config
from shortlist.config import deep_merge, for_division
from shortlist.pipeline import Engine
from shortlist.rules import Ctx, evaluate
from shortlist.sources import Loader
from shortlist.world import World

AS_OF = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
JOB, PERSON = "job:T-1", "person:test:P1"


@lru_cache(maxsize=None)
def engine(overlay: str | None = None) -> Engine:
    return Engine(overlay=overlay)


def match(eng: Engine, step: int, job: str, person: str):
    for m in eng.matches(step)["job:" + job][0]:
        if m.person.endswith(":" + person):
            return m
    raise KeyError(f"{person} not matched against {job} at step {step}")


def rule(m, name: str):
    return next(r for r in m.results + m.gates if r.rule == name)


def tiny_world(job_fields=None, job_text=None, note=None, person_fields=None, tags=(), texts=(), overrides=None):
    """Build a synthetic world: one job (T-1), one person (P1).

    job_fields / person_fields : structured values, as an adapter would emit them (None removes a default)
    job_text                   : the feed's free text
    note                       : an account-manager job note, dated after the feed
    tags                       : structured skill tags on the person
    texts                      : (kind, date, text) free-text evidence about the person
    """
    cfg = config.load()
    if overrides:
        cfg = deep_merge(cfg, overrides)
    cfg["sources"] = [{"name": "test", "adapter": "none", "label": "Test", "system_id": "test", "path": "(in memory)"}]
    L = Loader(cfg["sources"][0], {})
    fields = {"title": "Physical Therapist", "status": "open", "location": {"city": "Spokane", "state": "WA"}}
    fields.update(job_fields or {})
    feed = L.evidence("JOB-1", "job_feed", JOB, "2026-11-01", "system", "Test feed T-1", text=job_text)
    for attr, value in fields.items():
        if value is not None:
            L.claim(feed, attr, value)
    if note:
        L.evidence("NOTE-1", "job_note", JOB, "2026-11-05", "account_manager", "Test note NOTE-1", text=note)
    person = {"name": "Test Person", "profession": "Physical Therapist"}
    person.update(person_fields or {})
    record = L.evidence("P1", "structured_record", PERSON, "2026-11-01", "system", "Test record P1")
    for attr, value in person.items():
        if value is not None:
            L.claim(record, attr, value)
    for tag in tags:
        L.claim(record, "tag", tag)
    for i, (kind, when, text) in enumerate(texts):
        author = {"recruiter_note": "recruiter", "ops_note": "operations"}.get(kind, "candidate")
        L.evidence(f"T{i}", kind, PERSON, when, author, f"Test {kind} T{i}", text=text)
    return World(cfg, [L.batch], AS_OF), cfg


def run_rule(name: str, world: World, cfg: dict):
    job, person = world.jobs[JOB], world.people[PERSON]
    ctx = Ctx(world, for_division(cfg, job.division), cfg["requirement_changes"]["policy"])
    results, gates = evaluate(ctx, job, person)
    return next(r for r in results + gates if r.rule == name)
