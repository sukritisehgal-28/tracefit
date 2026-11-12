"""Adapter for the applicant tracking system export: candidates, their documents, and jobs."""
from __future__ import annotations

from ..lexicon import state_code
from ..timeutil import norm_unit
from . import Loader, adapter


def _candidate(L: Loader, c: dict) -> None:
    cid = c["id"]
    where = f"candidate {cid}"
    subj = L.person(cid)
    rec = L.evidence(cid, "structured_record", subj, c.get("updated_at"), "system", f"{L.label} record {cid}", text=c.get("source_text"))
    name = f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
    if name:
        L.claim(rec, "name", name)
    if c.get("email"):
        L.claim(rec, "email", c["email"])
    if c.get("profession"):
        L.claim(rec, "profession", c["profession"])
    if c.get("specialty"):
        L.claim(rec, "specialty", c["specialty"])
    addr = c.get("address") or {}
    if addr:
        L.claim(rec, "home", {"city": addr.get("city"), "state": state_code(addr.get("state"))},
                quote=f"address = {addr.get('city')}, {addr.get('state')}")
    for skill in c.get("skills") or []:
        L.claim(rec, "tag", str(skill), quote=f'skills: "{skill}"')
    prefs = c.get("preferences") or {}
    if prefs.get("states"):
        L.claim(rec, "pref_states", [state_code(s) for s in prefs["states"]], quote=f"preferences.states = {', '.join(prefs['states'])}")
    if prefs.get("travel") is not None:
        L.claim(rec, "travel", bool(prefs["travel"]), quote=f"preferences.travel = {str(prefs['travel']).lower()}")
    if prefs.get("shifts"):
        L.claim(rec, "schedule", str(prefs["shifts"][0]).lower(), quote=f"preferences.shifts = {', '.join(prefs['shifts'])}")
    if prefs.get("hours_per_week"):
        L.claim(rec, "hours", prefs["hours_per_week"], quote=f"preferences.hours_per_week = {prefs['hours_per_week']}")
    if prefs.get("duration_weeks"):
        L.claim(rec, "weeks", prefs["duration_weeks"], quote=f"preferences.duration_weeks = {prefs['duration_weeks']}")
    available = L.date(c.get("available_from"), f"{where}, available_from")
    if available:
        L.claim(rec, "available_from", available, quote=f"available_from = {c['available_from']}")
    pay = c.get("expected_pay")
    if pay:
        amount = L.amount(pay.get("amount"), f"{where}, expected_pay")
        if amount:
            L.claim(rec, "pay", {"amount": amount, "unit": norm_unit(pay.get("period", "week")), "hours": pay.get("hours"), "qualifier": "expects"},
                    quote=f"expected_pay = {pay.get('currency', '')} {pay.get('amount')}/{pay.get('period')}")
    if c.get("status"):
        L.claim(rec, "status", c["status"])
        if c["status"] == "placed":
            L.claim(rec, "placement", {"status": "placed", "until": L.date(c.get("assignment_end"), f"{where}, assignment_end")},
                    quote=f"status = placed; assignment_end = {c.get('assignment_end')}")
    for doc in c.get("documents") or []:
        L.evidence(doc["id"], "resume", subj, doc.get("received_at"), "candidate", f"Résumé {doc['id']}", text=doc.get("text"))
    ops = c.get("operations")
    if ops:
        L.evidence(ops["source_id"], "ops_note", subj, ops.get("recorded_at"), "operations", f"Operations note {ops['source_id']}", text=ops.get("text"))


def _job(L: Loader, j: dict) -> None:
    jid = j["id"]
    where = f"job {jid}"
    subj = L.job(j["external_requisition"]) if j.get("external_requisition") else L.job(f"{L.name}:{jid}")
    rec = L.evidence(jid, "job_record", subj, j.get("updated_at"), "system", f"{L.label} job {jid}", text=j.get("description"))
    L.claim(rec, "job_id", jid, key=L.name)
    if j.get("title"):
        L.claim(rec, "title", j["title"])
    if j.get("status"):
        L.claim(rec, "status", str(j["status"]).lower())
    loc = j.get("location") or {}
    if loc:
        L.claim(rec, "location", {"city": loc.get("city"), "state": state_code(loc.get("state"))}, quote=f"location = {loc.get('city')}, {loc.get('state')}")
    start = L.date(j.get("start_date"), f"{where}, start_date")
    if start:
        L.claim(rec, "req.start", start, quote=f"start_date = {j['start_date']}")
    if j.get("duration_weeks"):
        L.claim(rec, "req.weeks", j["duration_weeks"], quote=f"duration_weeks = {j['duration_weeks']}")
    if j.get("shift"):
        L.claim(rec, "req.schedule", str(j["shift"]).lower(), quote=f"shift = {j['shift']}")
    if j.get("hours_per_week"):
        L.claim(rec, "req.hours", j["hours_per_week"], quote=f"hours_per_week = {j['hours_per_week']}")
    if j.get("minimum_experience_years") is not None:
        L.claim(rec, "req.min_years", {"years": float(j["minimum_experience_years"]), "level": "required"},
                quote=f"minimum_experience_years = {j['minimum_experience_years']}")
    for item in j.get("requirements") or []:
        L.claim(rec, "req.tag", str(item), quote=f'requirements: "{item}"')
    pay = j.get("candidate_pay")
    if pay:
        amount = L.amount(pay.get("amount"), f"{where}, candidate_pay")
        if amount:
            L.claim(rec, "req.pay", {"amount": amount, "unit": norm_unit(pay.get("basis", "week")), "hours": pay.get("hours")},
                    quote=f"candidate_pay = {pay.get('currency', '')} {pay.get('amount')} {pay.get('basis')}")


@adapter("ats")
def load(raw: dict, L: Loader) -> None:
    data = raw.get("data") or {}
    L.each(data.get("candidates"), "candidate", lambda c: _candidate(L, c))
    L.each(data.get("jobs"), "job", lambda j: _job(L, j))
