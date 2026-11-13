"""Adapter for the vendor management system feed: requisitions and its change feed."""
from __future__ import annotations

from datetime import datetime

from ..lexicon import state_code
from ..model import Batch, Event
from ..timeutil import norm_unit, parse_when
from . import Loader, adapter

EVENT_KINDS = {"requisition.created": "job.created", "requisition.updated": "job.updated"}


def _requisition(L: Loader, r: dict, when, into: Batch | None = None, evidence_id: str | None = None) -> None:
    number = r["requisitionNumber"]
    where = f"requisition {number}"
    ev = L.evidence(evidence_id or number, "job_feed", L.job(number), when, "system", f"{L.label} {number}", text=r.get("details"), into=into)

    def c(attr, value, **kw):
        L.claim(ev, attr, value, into=into, **kw)

    c("job_id", number, key=L.name)
    if r.get("positionName"):
        c("title", r["positionName"])
    fac = r.get("facility") or {}
    if fac.get("name"):
        c("facility", fac["name"])
    if fac.get("city") or fac.get("region"):
        c("location", {"city": fac.get("city"), "state": state_code(fac.get("region"))}, quote=f"facility = {fac.get('city')}, {fac.get('region')}")
    if r.get("state"):
        c("status", str(r["state"]).lower(), quote=f"state = {r['state']}")
    start = L.date(r.get("startDate"), f"{where}, startDate", L.src.get("date_format"))
    if start:
        c("req.start", start, quote=f"startDate = {r['startDate']}")
    if r.get("contractWeeks"):
        c("req.weeks", r["contractWeeks"], quote=f"contractWeeks = {r['contractWeeks']}")
    if r.get("weeklyHours"):
        c("req.hours", r["weeklyHours"], quote=f"weeklyHours = {r['weeklyHours']}")
    if r.get("shiftDescription"):
        c("req.schedule", str(r["shiftDescription"]).lower(), quote=f"shiftDescription = {r['shiftDescription']}")
    shifts = []
    for s in r.get("shifts") or []:
        try:
            if datetime.fromisoformat(s["end"]) <= datetime.fromisoformat(s["start"]):
                raise ValueError("the shift ends before it starts")
            shifts.append({"start": s["start"], "end": s["end"]})
        except Exception as exc:  # noqa: BLE001
            L.issue("warning", f"{where}, shifts", f"A shift could not be read and was ignored ({exc}).")
    if shifts:
        c("req.shifts", shifts, quote="shifts = " + "; ".join(f"{s['start']} to {s['end']}" for s in shifts))
    rate = r.get("candidateRate")
    if rate:
        amount = L.amount(rate.get("value"), f"{where}, candidateRate")
        if amount:
            c("req.pay", {"amount": amount, "unit": norm_unit(rate.get("unit", "hour")), "hours": rate.get("hours")},
              quote=f"candidateRate = {rate.get('currency', '')} {rate.get('value')}/{rate.get('unit')}")


def _event(L: Loader, e: dict) -> None:
    batch = Batch()
    record = e["record"]
    kind = EVENT_KINDS.get(e.get("eventType"))
    state = str((record or {}).get("state") or "").strip().lower()
    if kind is None and state and state != "open" and (record or {}).get("requisitionNumber"):
        # An event type the tool does not know, whose record says the job is no longer open (closed, filled,
        # cancelled): applied as an update, because leaving a closed job open puts people forward for it.
        kind = "job.updated"
        L.issue("info", f"feed event {e.get('eventId')}",
                f"The event type {e.get('eventType')!r} is not one the tool knows. Its record says the job is {state}, so it was applied as an update.")
    if kind is None:
        L.issue("warning", f"feed event {e.get('eventId')}",
                f"The event type {e.get('eventType')!r} is not one the tool knows, so the event was ignored.")
        return
    number = record["requisitionNumber"]
    # A later version of a requisition is new evidence: it gets its own id so the earlier version stays on file.
    _requisition(L, record, e["occurredAt"], into=batch, evidence_id=number if kind == "job.created" else f"{number}@{e['eventId']}")
    verb = "New job" if kind == "job.created" else "Job update"
    L.batch.events.append(Event(id=e["eventId"], at=parse_when(e["occurredAt"])[0], kind=kind,
                                label=f"{verb} {number}: {record.get('positionName', '')}".rstrip(": "), source=L.name, batch=batch))


@adapter("vms")
def load(raw: dict, L: Loader) -> None:
    L.each(raw.get("requisitions"), "requisition", lambda r: _requisition(L, r, r.get("lastModified")), key="requisitionNumber")
    L.each(raw.get("changeFeed"), "feed event", lambda e: _event(L, e), key="eventId")
