"""Several events at the same moment, shown on the supplied data.

    python3 -m shortlist burst

The supplied files hold two events, five minutes apart. A running service gets many at once, so this command
adds eight SYNTHETIC events that all carry the same timestamp (18:15:00) to the supplied snapshot, delivers
them, and prints what changed. It then delivers the same eight in many other orders and checks that the lists
come out identical, and identical to rebuilding everything from scratch.

The eight events are made up for this demonstration and are labelled as such. They go through the same
adapters as real ones: the feed events are added to the VMS feed's change list and the messages to our own
system's updates, exactly as those files would carry them.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

from . import config as configuration
from .matching import TIER_LABEL
from .pipeline import Engine, Live

AT = "2026-11-07T18:15:00Z"


def _feed_events(vms: dict) -> list:
    by_number = {r["requisitionNumber"]: r for r in vms["requisitions"]}
    raised = copy.deepcopy(by_number["VMS-901"])
    raised["details"] = raised["details"].replace("2,450", "2,550")
    filled = dict(copy.deepcopy(by_number["VMS-902"]), state="FILLED")
    new = {"requisitionNumber": "VMS-905", "positionName": "Home Health PT", "state": "OPEN",
           "facility": {"name": "Synthetic Home Care", "city": "Spokane", "region": "Washington"},
           "startDate": "12/08/2026", "contractWeeks": 13, "weeklyHours": 40, "shiftDescription": "Weekdays",
           "candidateRate": {"value": 2500, "currency": "USD", "unit": "WEEK", "hours": 40},
           "details": "SYNTHETIC. Open physical therapist role; Spokane, Washington; home health; start 2026-12-08; 13 weeks; weekdays, "
                      "40 hours/week. Structured minimum experience: 1 year. Requirements: Washington PT license, OASIS."}
    return [{"eventId": "EV-S1", "occurredAt": AT, "eventType": "requisition.created", "record": new},
            {"eventId": "EV-S2", "occurredAt": AT, "eventType": "requisition.updated", "record": raised},
            {"eventId": "EV-S3", "occurredAt": AT, "eventType": "requisition.updated", "record": filled}]


MESSAGES = [("EV-S4", "R07", "I can start December 1 after all."),
            ("EV-S5", "R09", "I need at least $2,400 per week for forty hours."),
            ("EV-S6", "R03", "Please do not contact me about any jobs until February 1, 2027."),
            ("EV-S7", "R02", "My minimum is now $2,300 per week for forty hours."),
            ("EV-S8", "R02", "My minimum is now $2,600 per week for forty hours.")]
WHAT = {"EV-S1": "a new job arrives: VMS-905, Home Health PT in Spokane, starting 8 December",
        "EV-S2": "the feed raises the pay on VMS-901 from $2,450 to $2,550 a week",
        "EV-S3": "the feed marks VMS-902 (ED Physician) as filled",
        "EV-S4": "R07 writes: “I can start December 1 after all.”",
        "EV-S5": "R09 writes: “I need at least $2,400 per week for forty hours.”",
        "EV-S6": "R03 writes: “Please do not contact me about any jobs until February 1, 2027.”",
        "EV-S7": "R02 writes: “My minimum is now $2,300 per week for forty hours.”",
        "EV-S8": "R02 also writes, at the same moment: “My minimum is now $2,600 per week for forty hours.”"}


def engine(overlay: str | None = None) -> Engine:
    """The supplied data with the eight synthetic events added to the two files that would carry them."""
    cfg = configuration.load(overlay)
    sources = []
    for s in cfg["sources"]:
        if s["name"] in ("vms", "our_system"):
            raw = json.loads((Path(configuration.ROOT) / s["path"]).read_text(encoding="utf-8"))
            if s["name"] == "vms":
                raw["changeFeed"] = list(raw.get("changeFeed") or []) + _feed_events(raw)
            else:
                raw["updates"] = list(raw.get("updates") or []) + [
                    {"event_id": eid, "occurred_at": AT, "subject": {"external_source": "fictional_our_system", "external_id": who},
                     "kind": "candidate_message", "text": text} for eid, who, text in MESSAGES]
            s = dict(s, data=raw)
        sources.append(s)
    cfg["sources"] = sources
    return Engine(cfg=cfg)


def _lists(world, shortlist_of) -> dict:
    out = {}
    for key in sorted(world.jobs):
        job = world.jobs[key]
        out[key] = job.status if job.status != "open" else [
            (m.person, m.tier, m.score, m.low, m.high, m.action["status"]) for m in shortlist_of(key).ranked()]
    return out


def run(overlay: str | None = None, orders: int = 30, seed: int = 7) -> dict:
    eng = engine(overlay)
    burst = [i for i, e in enumerate(eng.events) if e.id in WHAT]
    before = [i for i in range(len(eng.events)) if i not in burst]
    live = Live.from_engine(eng)
    for i in before:
        live.ingest(eng.events[i])
    changes = [(eng.events[i].id, live.ingest(eng.events[i])) for i in burst]
    final = _lists(live.world, live.shortlist)
    last = len(eng.events)
    rebuilt = _lists(eng.world(last), lambda key: eng.shortlist(last, key))
    rng, same = random.Random(seed), 0
    for _ in range(orders):
        other = Live.from_engine(eng)
        for i in before:
            other.ingest(eng.events[i])
        shuffled = burst[:]
        rng.shuffle(shuffled)
        for i in shuffled:
            other.ingest(eng.events[i])
        same += _lists(other.world, other.shortlist) == final
    r02 = live.shortlist("job:VMS-901").explain(live.world, "person:our_system:R02")
    pay = next(r for r in r02.results if r.rule == "pay")
    return {"events": [eng.events[i] for i in burst], "changes": changes, "final": final, "world": live.world,
            "orders": orders, "orders_identical": same, "equals_rebuild": final == rebuilt, "clash": pay}


def render(report: dict) -> str:
    w = report["world"]
    out = ["SEVERAL EVENTS AT THE SAME MOMENT",
           "The eight events below are SYNTHETIC, made up for this demonstration. Everything else is the supplied data,",
           "after its own two events (the 18:05 job and the 18:10 update).", "",
           "At 18:15:00, all at once:"]
    out += [f"  {e.id}  {WHAT[e.id]}" for e in report["events"]]
    out += ["", "What each one changed (delivered in the order listed):"]
    for eid, changes in report["changes"]:
        if not changes:
            out.append(f"  {eid}  no result changed")
        for c in changes:
            out.append(f"  {eid}  {c['text']}")
            out += [f"           {line}" for line in c.get("rules", [])[:3]]
    out += ["", "The lists afterwards:"]
    for key, rows in report["final"].items():
        title = w.jobs[key].titles[0] if w.jobs[key].titles else ""
        if isinstance(rows, str):
            out.append(f"  {key[4:]} {title}: {rows}, no list")
            continue
        on_list = [r for r in rows if r[1] != "not_fit"]
        top = ", ".join(f"{w.people[p].name.replace('Candidate ', '')} {score}" + (" (needs an answer)" if tier == "clarify" else "")
                        for p, tier, score, *_ in on_list[:6])
        out.append(f"  {key[4:]} {title}: {len(on_list)} on the list, {len(rows) - len(on_list)} not a fit. Top: {top}")
    pay = report["clash"]
    out += ["", "Checked:",
            f"  the same eight events delivered in {report['orders']} other orders gave identical lists: "
            f"{'yes' if report['orders_identical'] == report['orders'] else 'NO, ' + str(report['orders'] - report['orders_identical']) + ' differed'}",
            f"  identical to rebuilding everything from scratch: {'yes' if report['equals_rebuild'] else 'NO'}",
            f"  R02's two pay figures at the same moment: pay is “{pay.outcome}”, not a guess. {pay.questions[-1] if pay.questions else ''}"]
    return "\n".join(out)
