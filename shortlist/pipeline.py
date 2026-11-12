"""The engine: load sources, apply events in order, match, report what changed, publish.

Two ways to run the same code

  Engine  replays the sample. Step 0 is the snapshot; each later step applies one event,
          in time order. The state at any step is rebuilt from the sources plus the events
          up to that step, so a replay always gives the same answer and can step backwards.

  Live    is the engine as a running service. There is one world. Each event is applied in
          place: only the person or job it touches is re-resolved, a new job gets its
          shortlist built once, and a candidate update replaces that one person's row in
          each open job's shortlist. A test checks that Live and Engine agree at every step.

Neither sends a whole shortlist to the page. The page asks for one screenful of rows at a
time, and for one person's full explanation when a recruiter opens it.
"""
from __future__ import annotations

import gc
from dataclasses import asdict
from pathlib import Path

from . import config as configuration
from .extract import RULES as READING_RULES
from .matching import (documents_key, found_for, short_ref, GROUPS, TIER_LABEL, TIER_ORDER, Shortlist, card_dict, evidence_dict, job_dict, match_dict,
                       person_dict)
from .model import Issue
from .rules import GATES, NOT_MET, OUTCOME_WORD, RULES, check_config
from .sinks import publish as publish_to_sinks
from . import dimensions
from .resume import background, unrecognised
from .sources import load_source
from .timeutil import fmt_date, parse_when
from .world import World

PAGE_SIZE = 25


# ------------------------------------------------------------ change records
# One event produces a short list of plain records saying what moved and why. Engine and Live build them
# with the same functions, so a notification reads the same whichever way the tool is run.
def _number(key: str) -> str:
    return key.split(":", 1)[1]


def _title(job, key: str) -> str:
    return job.titles[0] if job.titles else key


def _tally(tiers: dict) -> str:
    return ", ".join(f"{tiers[t]} {TIER_LABEL[t].lower()}" for t in TIER_ORDER if tiers[t])


def _job_added(key: str, job, sl: Shortlist, cause: list, reopened: bool = False) -> dict:
    lead = f"{_number(key)} ({_title(job, key)}) has reopened" if reopened else f"New job {_number(key)} ({_title(job, key)})"
    return {"kind": "job_added", "job": key, "title": _title(job, key), "cause": cause, "rules": [],
            "text": f"{lead}: {sl.stats['evaluated']} people checked against every rule. "
                    f"{_tally(sl.stats['tiers'])}."}


def _job_closed(key: str, job, cause: list) -> dict:
    return {"kind": "job_closed", "job": key, "title": _title(job, key), "cause": cause, "rules": [],
            "text": f"{_number(key)} ({_title(job, key)}) is now {job.status}. Its shortlist is withdrawn."}


def _person_added(key: str, job, m, cause: list) -> dict:
    return {"kind": "person_added", "job": key, "title": _title(job, key), "person": m.person, "name": m.name, "cause": cause,
            "tier": m.tier, "not_met": [], "rules": [],
            "text": f"{m.name} is new for {_number(key)}: {TIER_LABEL[m.tier]} ({m.score})."}


def _moved(before, after) -> bool:
    return (before.tier, before.score, before.action["status"]) != (after.tier, after.score, after.action["status"])


def _match_changed(key: str, job, before, after, cause: list) -> dict:
    """`after` is a full match. `before` is too, unless only its compact row survives: then the rules are not listed."""
    rules = [] if before.results is None else [
        f"{r.label}: {OUTCOME_WORD.get(rb.outcome, rb.outcome)} → {OUTCOME_WORD.get(r.outcome, r.outcome)}. {r.shows}"
        for rb, r in zip(before.results + before.gates, after.results + after.gates) if rb.outcome != r.outcome]
    return {"kind": "match_changed", "job": key, "title": _title(job, key), "person": after.person, "name": after.name,
            "cause": cause, "rules": rules, "tier": after.tier, "not_met": [r.rule for r in after.results if r.outcome == NOT_MET],
            "text": f"{after.name} for {_number(key)} ({_title(job, key)}): {TIER_LABEL[before.tier]} ({before.score}) → "
                    f"{TIER_LABEL[after.tier]} ({after.score})."}


# ------------------------------------------------------------- page payloads
def _evidence(world, ids) -> dict:
    """The evidence items named, plus the originals they repeat, as plain data."""
    out, queue = {}, list(ids)
    while queue:
        eid = queue.pop()
        if eid in out or eid not in world.evidence:
            continue
        out[eid] = evidence_dict(world, world.evidence[eid])
        queue.extend(x for x in [out[eid]["same_as"], *out[eid]["repeats"]] if x)
    return out


def cards_payload(world, sl: Shortlist, group: str, offset: int = 0, limit: int = PAGE_SIZE,
                  weights: dict | None = None, filters=()) -> dict:
    """One screenful of one list. With the settings' weights and no filters the cost does not depend on how
    long the list is. With a recruiter's own weights or filters the list is re-scored first (Shortlist.view)."""
    if not weights and not filters:
        rows, total = sl.page(group, offset, limit)
        return {"job": sl.job.key, "group": group, "offset": offset, "limit": limit, "total": total,
                "cards": [card_dict(world, m) for m in rows]}
    view = sl.view(world, group, weights, filters)
    page = view[offset:offset + limit]
    tiers = {t: sum(1 for x in view if x[4] == t) for t in GROUPS[group]}
    return {"job": sl.job.key, "group": group, "offset": offset, "limit": limit, "total": len(view), "tiers": tiers,
            "cards": [dict(card_dict(world, x[0], x[1:] if weights else None), found=found_for(world, x[0], filters)) for x in page],
            "reweighted": bool(weights)}


def detail_payload(world, sl: Shortlist, person_key: str) -> dict | None:
    """Everything the page shows when a recruiter opens one person: every rule, and the evidence behind each."""
    m = sl.explain(world, person_key)
    if m is None:
        return None
    match = match_dict(world, m)
    person = person_dict(world, world.people[person_key])
    person["background"] = background(world, world.people[person_key])   # everything the résumés list, as written
    cited = [ref["evidence"] for r in match["results"] + match["gates"] for ref in r["refs"] + r["job_refs"]]
    cited += [eid for r in match["results"] + match["gates"] for eid in r["context"]]
    return {"match": match, "person": person, "evidence": _evidence(world, cited + person["evidence_ids"])}


def job_summary(world, job, cfg: dict, sl: Shortlist | None) -> dict:
    """What the job list and the job header show: the posting, and totals. No rows."""
    jd = job_dict(world, job, cfg)
    tiers = sl.stats["tiers"] if sl else {t: 0 for t in TIER_ORDER}
    jd["counts"] = dict(tiers)
    jd["groups"] = {g: sum(tiers[t] for t in members) for g, members in GROUPS.items()}
    jd["need_answer"] = tiers["clarify"]
    actions: dict[str, int] = {}
    for t in GROUPS["best"]:
        for status, n in (sl.stats["actions"][t] if sl else {}).items():
            actions[status] = actions.get(status, 0) + n
    jd["best_actions"] = {k: n for k, n in actions.items() if n}
    rules_cfg = sl.ctx.rules_cfg if sl else {}
    jd["rule_labels"] = [rules_cfg[name].get("label", name) for name in rules_cfg]      # for the strip on each card
    jd["action_first"] = bool(sl and sl.scoring.get("actionable_first", True))
    jd["stats"] = {k: sl.stats[k] for k in ("people", "evaluated", "excluded_by_profession")} if sl else None
    jd["questions"] = sl.job_questions() if sl else []
    jd["reasons"] = sl.stats["reasons"] if sl else {"clarify": {}, "not_fit": {}}
    sc = sl.scoring if sl else {}
    jd["order"] = {"actionable_first": bool(sc.get("actionable_first", True)), "tie_breakers": list(sc.get("tie_breakers", [])),
                   "tie_margin": sc.get("tie_margin", 0)}
    jd["weights"] = dimensions.weights_of(sl.ctx.dims_cfg) if sl else {}
    jd["skills"] = [{"key": k, "label": world.lex.label(k)} for k in job.reqs_of("req.skill")]
    outcomes = sl.stats["outcomes"] if sl else {}
    jd["process"] = {                                     # every step behind the list, for "Show the whole process"
        "rules": [{"name": name, "label": rc.get("label", name), "level": rc.get("level", "hard"),
                   **{k: outcomes.get(name, {}).get(k, 0) for k in ("met", "unknown", "not_met", "na")}}
                  for name, rc in rules_cfg.items()],
        "gates": [{"name": name, "label": gc_.get("label", name), "counts": dict(outcomes.get(name, {}))}
                  for name, gc_ in (sl.ctx.gates_cfg.items() if sl else ())],
    }
    jd["openings"] = None          # no source in the packet states how many people a job needs (HANDOFF.md, question 9)
    return jd


def same_documents(world) -> list:
    """People whose résumé or profile is word for word the same as someone else's. It may be a template, or one
    person imported more than once: the tool keeps them apart (it joins people only on an explicit reference or
    an email) and says so, so a recruiter does not contact one person several times."""
    stamp = (len(world.people), len(world.evidence))
    cached = world.__dict__.get("_same_documents")
    if cached and cached[0] == stamp:
        return cached[1]
    by_text: dict = {}
    for p in world.people.values():
        for i in p.evidence_ids:
            ev = world.evidence[i]
            if ev.kind in ("resume", "profile") and ev.text and len(ev.text) >= 80:
                by_text.setdefault(" ".join(ev.text.lower().split()), set()).add(p.key)
    groups = sorted((g for g in by_text.values() if len(g) > 1), key=lambda g: (-len(g), sorted(g)))
    out = []
    for g in groups[:5]:
        every = {}
        for key in g:
            every.setdefault(documents_key(world, world.people[key]), []).append(key)
        twins = max(every.values(), key=len)
        refs = sorted(short_ref(world.people[k]) for k in twins)
        named = ", ".join(refs[:4]) + (f" and {len(refs) - 4} more" if len(refs) > 4 else "")
        extra = f" For {len(twins)} of them ({named}) every document is identical." if len(twins) > 1 else ""
        out.append(Issue("info", "all sources", f"{len(g)} people",         # a fixed size, however many people share the text
                         f"{len(g)} people have a résumé or profile that is word for word the same.{extra} They are kept as separate "
                         "people because no reference or email links them. Confirm they are different people before contacting each."))
    if len(groups) > 5:
        out.append(Issue("info", "all sources", f"{len(groups) - 5} more groups", "More groups of people share one résumé or profile text."))
    world.__dict__["_same_documents"] = (stamp, out)
    return out


def _job_evidence_ids(jd: dict) -> list:
    ids = list(jd["evidence_ids"]) + list(jd["note_ids"]) + [n["ref"]["evidence"] for n in jd["notes"]]
    for line in jd["requirements"]:
        ids += [x["evidence"] for x in (line["ref"], (line["other"] or {}).get("ref")) if x]
    return ids


class Engine:
    def __init__(self, cfg: dict | None = None, overlay: str | None = None, root: str | Path | None = None):
        self.cfg = cfg or configuration.load(overlay, root or configuration.ROOT)
        check_config(self.cfg)
        self.root = Path(self.cfg["_root"])
        ctx = {"system_ids": {s.get("system_id", s["name"]): s["name"] for s in self.cfg["sources"]},
               "snapshot_at": self.cfg["clock"]["snapshot_at"]}
        # A source that fails to load is reported and left out; the rest still load.
        self.batches = [load_source(s, ctx, self.root) for s in self.cfg["sources"]]
        self.load_issues: list[Issue] = [i for b in self.batches for i in b.issues]
        self.events, seen = [], set()
        for e in sorted((e for b in self.batches for e in b.events), key=lambda e: e.at):     # replayed in time order
            if e.id in seen:
                self.load_issues.append(Issue("info", e.source, f"event {e.id}", "This event arrived more than once. It was applied once."))
                continue
            seen.add(e.id)
            self.events.append(e)
        self.snapshot_at = parse_when(self.cfg["clock"]["snapshot_at"])[0]
        self._worlds: dict[int, World] = {}
        self._lists: dict[int, dict[str, Shortlist]] = {}

    # ------------------------------------------------------------------ steps
    @property
    def steps(self) -> list[dict]:
        out = [{"index": 0, "at": self.snapshot_at.isoformat(), "kind": "snapshot", "label": "Snapshot loaded", "id": None, "source": None}]
        for i, e in enumerate(self.events, start=1):
            out.append({"index": i, "at": e.at.isoformat(), "kind": e.kind, "label": e.label, "id": e.id, "source": e.source})
        return out

    def world(self, step: int) -> World:
        if step not in self._worlds:
            as_of = self.snapshot_at if step == 0 else self.events[step - 1].at
            self._worlds[step] = World(self.cfg, self.batches + [e.batch for e in self.events[:step]], as_of)
        return self._worlds[step]

    def shortlist(self, step: int, key: str) -> Shortlist:
        """One job's shortlist at one step, built the first time it is asked for."""
        lists = self._lists.setdefault(step, {})
        if key not in lists:
            w = self.world(step)
            lists[key] = Shortlist(w, w.jobs[key], self.cfg)
        return lists[key]

    def matches(self, step: int) -> dict:
        """job key -> (ranked rows, totals). Every job is evaluated, including closed ones, so no rule is skipped."""
        out = {}
        for key in self.world(step).jobs:
            sl = self.shortlist(step, key)
            out[key] = (sl.ranked(), sl.stats)
        return out

    def explain(self, step: int, key: str, person_key: str):
        """The full rule-by-rule result for one person and one job."""
        return self.shortlist(step, key).explain(self.world(step), person_key)

    # ---------------------------------------------------------------- changes
    def changes(self, step: int) -> list[dict]:
        if step == 0:
            return []
        w, was = self.world(step), self.world(step - 1)
        cause = [ev.id for ev in self.events[step - 1].batch.evidence]
        out = []
        for key, job in w.jobs.items():
            if job.status != "open":          # nobody is recommended for a closed job; say so once, when it closes
                if key in was.jobs and was.jobs[key].status == "open":
                    out.append(_job_closed(key, job, cause))
                continue
            sl = self.shortlist(step, key)
            if key not in was.jobs or was.jobs[key].status != "open":
                out.append(_job_added(key, job, sl, cause, reopened=key in was.jobs))
                continue
            earlier = self.shortlist(step - 1, key)
            for m in sl.ranked():
                b = earlier.by_person.get(m.person)
                if b is None:
                    out.append(_person_added(key, job, m, cause))
                elif _moved(b, m):
                    out.append(_match_changed(key, job, earlier.explain(was, m.person), sl.explain(w, m.person), cause))
        return out

    # --------------------------------------------------------------- coverage
    def coverage(self, step: int) -> dict:
        """Proof that every rule ran for every pair, and which outcomes each rule has produced on this data.

        The counts are kept as each pair is evaluated, so this does not walk the shortlists again.
        """
        w = self.world(step)
        table = {}
        for kind, registry, section in (("fit", RULES, "rules"), ("permission", GATES, "gates")):
            for name, (_, outcomes) in registry.items():
                table[name] = {"label": self.cfg[section][name]["label"], "kind": kind, "declared": list(outcomes),
                               "counts": {o: 0 for o in outcomes}, "evaluations": 0}
        pairs = 0
        for _, stats in self.matches(step).values():
            pairs += stats["evaluated"]
            for name, counts in stats["outcomes"].items():
                for outcome, n in counts.items():
                    table[name]["counts"][outcome] = table[name]["counts"].get(outcome, 0) + n
                    table[name]["evaluations"] += n
        for row in table.values():
            row["not_seen"] = [o for o in row["declared"] if row["counts"][o] == 0]
        reading = [{"id": rid, "what": what, "fired": w.extractor.fired.get(rid, 0)} for rid, what in READING_RULES.items()]
        return {"pairs": pairs, "rules": table, "every_rule_ran": all(row["evaluations"] == pairs for row in table.values()),
                "reading_rules": reading, "reading_never_fired": [r["id"] for r in reading if not r["fired"]],
                "unparsed_clauses": len(w.extractor.unparsed), "claims": w.claim_count, "evidence": len(w.evidence),
                "unrecognised": [{"term": t, "people": n} for t, n in unrecognised(w)[:25]]}

    # ----------------------------------------------------------------- issues
    def issues(self, step: int) -> list[dict]:
        """Data problems the tool worked around, most serious first. Matching carries on regardless."""
        order = {"error": 0, "warning": 1, "info": 2}
        w = self.world(step)
        found = self.load_issues + w.issues + same_documents(w)
        return [asdict(i) for i in sorted(found, key=lambda i: order.get(i.severity, 3))]

    # ----------------------------------------------------------- notifications
    def notifications(self, upto: int) -> list[dict]:
        """What a notification destination would receive, for every step up to this one. Simulated."""
        out = []
        for step in range(upto + 1):
            at = self.steps[step]["at"]
            if step == 0:
                for key, job in self.world(0).jobs.items():
                    if job.status != "open":
                        continue
                    out.append({"simulated": True, "step": 0, "at": at, "event": "shortlist.ready", "job": key,
                                "text": f"Shortlist ready for {_number(key)} ({_title(job, key)}): {_tally(self.shortlist(0, key).stats['tiers'])}."})
            else:
                for c in self.changes(step):
                    out.append({"simulated": True, "step": step, "at": at, "event": c["kind"], "job": c["job"], "text": c["text"]})
        return out

    # ------------------------------------------------------------------ state
    def state(self, step: int) -> dict:
        """What the page needs before anyone is opened: the jobs with their totals, and what changed. No rows."""
        step = max(0, min(step, len(self.events)))
        w = self.world(step)
        previous_jobs = set(self.world(step - 1).jobs) if step else set(w.jobs)
        jobs, cited = [], []
        for key, job in sorted(w.jobs.items(), key=lambda kv: (kv[1].status != "open", kv[0] in previous_jobs, kv[0])):
            jd = job_summary(w, job, self.cfg, self.shortlist(step, key) if job.status == "open" else None)
            jd["new"] = key not in previous_jobs
            cited += _job_evidence_ids(jd)
            jobs.append(jd)
        scoring = self.cfg["scoring"]
        return {
            "meta": {
                "reference_date": self.cfg["clock"]["reference_date"], "reference_label": fmt_date(w.today),
                "as_of": w.as_of.isoformat(), "step": step, "steps": self.steps,
                "overlay": self.cfg.get("_overlay"), "overlays": configuration.list_overlays(self.root),
                "policy": self.cfg["requirement_changes"]["policy"],
                "scoring": {k: scoring[k] for k in ("unknown_credit", "strong_threshold", "tie_margin", "tie_breakers")},
                "sources": [{"name": s["name"], "label": s.get("label", s["name"]), "adapter": s["adapter"], "path": s["path"]} for s in self.cfg["sources"]],
                "sinks": [{"name": s["name"], "adapter": s["adapter"], "path": s["path"]} for s in self.cfg["sinks"]],
                "tiers": TIER_LABEL, "rule_count": len(RULES) + len(GATES), "people": len(w.people), "page_size": PAGE_SIZE,
                "top_n": int(self.cfg.get("matching", {}).get("top_n", 10)),
                "dimensions": [{"name": k, "label": dimensions.LABELS[k], "means": dimensions.MEANS[k]} for k in dimensions.ORDER],
                "presets": [{"name": n, "label": p.get("label", n), "weights": dimensions.weights_of(p)}
                            for n, p in self.cfg.get("presets", {}).items()],
            },
            "jobs": jobs,
            "evidence": _evidence(w, cited),              # only what the job headers cite
            "changes": self.changes(step),
            "issues": self.issues(step),
            "outbox": self.notifications(step),
        }

    def cards(self, step: int, key: str, group: str, offset: int = 0, limit: int = PAGE_SIZE,
              weights: dict | None = None, filters=()) -> dict:
        step = max(0, min(step, len(self.events)))
        return cards_payload(self.world(step), self.shortlist(step, key), group, offset, limit, weights, filters)

    def detail(self, step: int, key: str, person_key: str) -> dict | None:
        step = max(0, min(step, len(self.events)))
        return detail_payload(self.world(step), self.shortlist(step, key), person_key)

    # ---------------------------------------------------------------- publish
    def payload(self, step: int) -> dict:
        """The destination-neutral result handed to every sink. Sinks never see rules or sources.

        A destination gets the top of each list, up to a configured number of people, and the totals.
        """
        w = self.world(step)
        cap = int(self.cfg.get("publish", {}).get("max_people_per_job", 200))
        shortlists = []
        for key, job in w.jobs.items():
            if job.status != "open":              # a closed job has no shortlist to send anywhere
                continue
            sl = self.shortlist(step, key)
            rows = sl.ranked()
            sent = [sl.explain(w, m.person) for m in rows[:cap]]
            shortlists.append({
                "job": _number(key), "title": _title(job, key), "status": job.status,
                "people_checked": sl.stats["evaluated"], "people_listed": len(sent), "counts": dict(sl.stats["tiers"]),
                "matches": [{"person": _number(m.person), "name": m.name, "tier": TIER_LABEL[m.tier], "score": m.score,
                             "score_low": m.low, "score_high": m.high, "next_action": m.action["label"],
                             "action_status": m.action["status"], "summary": m.summary, "open_questions": m.questions}
                            for m in sent],
            })
        return {"simulated": True, "as_of": w.as_of.isoformat(), "reference_date": self.cfg["clock"]["reference_date"],
                "step": step, "configuration": self.cfg.get("_overlay") or "default",
                "shortlists": shortlists, "notifications": self.notifications(step)}

    def publish(self, step: int) -> list[str]:
        return publish_to_sinks(self.cfg, self.payload(step), self.root)


class Live:
    """The engine as a running service: one world, changed in place, one event at a time.

    Work done per event
      a new job           its shortlist is built once (every rule for everyone in its profession)
      a job changes       that one shortlist is rebuilt, and the people whose result moved are reported
      a job closes        its shortlist is withdrawn
      a candidate update  that person is re-resolved and re-checked against the open jobs; their row in
                          each shortlist is replaced; nobody else is touched
    """

    def __init__(self, cfg: dict, batches: list, as_of, freeze: bool = True):
        self.cfg = cfg
        self.world = World(cfg, batches, as_of)
        self.lists: dict[str, Shortlist] = {}
        self.applied: set[str] = set()                  # event ids already applied; a repeat changes nothing
        if freeze:
            # The loaded data lives as long as the service does. Telling the collector so stops it
            # re-scanning millions of long-lived objects every time a shortlist is built.
            gc.collect()
            gc.freeze()

    @classmethod
    def from_engine(cls, engine: Engine, freeze: bool = False) -> "Live":
        return cls(engine.cfg, engine.batches, engine.snapshot_at, freeze)

    def open_jobs(self) -> list[str]:
        return [key for key, job in self.world.jobs.items() if job.status == "open"]

    def shortlist(self, key: str) -> Shortlist:
        if key not in self.lists:
            self.lists[key] = Shortlist(self.world, self.world.jobs[key], self.cfg)
        return self.lists[key]

    def ingest(self, event) -> list[dict]:
        """Apply one event and return what changed, in the same form Engine.changes() gives."""
        w = self.world
        if event.id in self.applied:
            return []
        self.applied.add(event.id)
        for key in self.open_jobs():                    # every open job has a shortlist before anything changes
            self.shortlist(key)
        cause = [ev.id for ev in event.batch.evidence]
        was_open = {key: job.status == "open" for key, job in w.jobs.items()}
        # The people this event is about, as known before it is applied: their current results are kept
        # in full so the change can be described rule by rule.
        about = {w.canon(x.subject) for x in list(event.batch.evidence) + list(event.batch.claims)}
        about |= {w.canon(x) for a, b, _ in event.batch.links for x in (a, b)}
        about |= {w.canon(w._by_email[e]) for c in event.batch.claims if c.attr == "email"
                  for e in [str(c.value).strip().lower()] if e in w._by_email}
        before = {(key, s): sl.explain(w, s, placed=False) for s in about if s in w.people
                  for key, sl in self.lists.items() if s in sl.by_person}

        touched = w.apply(event.batch, event.at)
        for gone in w.last_removed:                     # a record merged into another person has no row of its own
            for sl in self.lists.values():
                sl.patch(w, gone)

        out = []
        for subject in sorted(touched):
            if subject.startswith("job:"):
                out += self._job_touched(subject, was_open, cause)
            elif subject.startswith("person:"):
                out += self._person_touched(subject, before, cause)
        order = {key: i for i, key in enumerate(w.jobs)}
        return sorted(out, key=lambda c: order.get(c["job"], len(order)))

    def _job_touched(self, key: str, was_open: dict, cause: list) -> list[dict]:
        w = self.world
        job = w.jobs[key]
        earlier = self.lists.pop(key, None)
        if job.status != "open":
            return [_job_closed(key, job, cause)] if was_open.get(key) else []
        sl = self.shortlist(key)
        if not was_open.get(key):
            return [_job_added(key, job, sl, cause, reopened=key in was_open)]
        if earlier is None:
            return []
        out = []
        for m in sl.ranked():                           # the job itself changed: report everyone whose result moved
            b = earlier.by_person.get(m.person)
            if b is None:
                out.append(_person_added(key, job, m, cause))
            elif _moved(b, m):
                # The earlier explanation is rebuilt against the job as it now is, so only the outcome words are exact.
                out.append(_match_changed(key, job, earlier.explain(w, m.person), sl.explain(w, m.person), cause))
        return out

    def _person_touched(self, subject: str, before: dict, cause: list) -> list[dict]:
        w = self.world
        out = []
        for key, sl in self.lists.items():
            old, new = sl.patch(w, subject)
            if new is None:
                continue
            job = w.jobs[key]
            if old is None:
                out.append(_person_added(key, job, new, cause))
            elif _moved(old, new):
                out.append(_match_changed(key, job, before.get((key, subject), old), sl.explain(w, subject, placed=False), cause))
        return out
