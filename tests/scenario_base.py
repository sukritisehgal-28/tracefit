"""A base of SYNTHETIC scenarios, each run through the real pipeline.

    python3 tests/scenario_base.py            print every scenario and whether it passes
    python3 tests/scenario_base.py F          only one area (M matching, F failures, E events, I identity)

Every scenario builds small source files in the same shapes as the real ones (ATS, VMS,
client database, our system), loads them through the real adapters, and checks what the
tool decided. Nothing here is supplied data: every person, job and message is made up
to exercise one situation. The unit tests run the whole base (tests/test_scenarios.py).

Areas
  M  matching: ordinary and awkward cases a recruiter would meet
  F  failures: broken files, broken records, bad dates, and the fallback used
  E  events:   new jobs, updates, duplicates, out-of-order delivery
  I  identity: who is the same person, and who is not
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shortlist import config  # noqa: E402
from shortlist.config import deep_merge  # noqa: E402
from shortlist.pipeline import Engine  # noqa: E402

# ------------------------------------------------------------ building blocks
HH_CV = ("Home-health PT, 2023-01 to present: independent home visits, OASIS start-of-care documentation, "
         "Homecare Homebase. Candidate reports active Washington PT license.")
MSG = "I am open to Spokane now. I can start December 1, work weekdays for thirteen weeks, and need at least $2,400 per week for forty hours."
UNASSIGNED = "Unassigned; no recorded contact restriction; no current placement recorded."
HH_JOB = ("Open physical therapist role; Spokane, Washington; home health; start 2026-12-01; 13 weeks; weekdays, 40 hours/week; "
          "approved candidate pay USD 2,450/week for 40 hours. Structured minimum experience: 2 years. "
          "Requirements: Washington PT license, OASIS, Homecare Homebase.")
ED_JOB = ("Open emergency-medicine locums physician role; Dayton, Ohio. Requires relevant emergency-department experience and "
          "Ohio license before start. ABEM/AOBEM preferred. Other boards considered with ATLS, ACLS, PALS and BLS.")
ED_CV = "Emergency-medicine residency, completed 2017. Emergency-department attending, 2017 to present. Candidate reports ABEM certification and active Ohio license."
FM_CV = ("Family-medicine residency, completed 2018. Emergency-department attending, 2019 to present. "
         "Candidate reports family-medicine board certification, active Ohio license, ATLS, ACLS, PALS and BLS.")
SHIFTS = [{"start": "2026-12-05T07:00:00-05:00", "end": "2026-12-05T19:00:00-05:00"},
          {"start": "2026-12-06T07:00:00-05:00", "end": "2026-12-06T19:00:00-05:00"}]
BOTH = "I could cover both December 5 and 6 day shifts in Dayton. My minimum is $230/hour."
ONE_YEAR = "Facility confirmed one year of home-health experience is acceptable."
NO_PAY_MSG = "I am open to Spokane now. I can start December 1 and work weekdays for thirteen weeks."
NO_START = "I am open to Spokane now. I can work weekdays for thirteen weeks, and need at least $2,400 per week for forty hours."
PROFILE = ("DPT completed in 2022. Home-health physical therapist since January 2023. Independent home visits, "
           "OASIS start-of-care documentation and Homecare Homebase. Reports active Washington PT license.")


def cand(cid="C1", cv=HH_CV, msg=MSG, ops=UNASSIGNED, profession="Physical Therapist", said="2026-11-06", msgs=None, **over):
    """One synthetic ATS candidate. `msgs` is a list of (text, date) the candidate sent."""
    c = {"id": cid, "first_name": "Synthetic", "last_name": cid, "email": f"{cid.lower()}@synthetic.invalid",
         "updated_at": "2026-11-01", "profession": profession,
         "documents": [{"id": f"{cid}-CV", "type": "resume_text", "received_at": "2026-11-01", "text": cv}] if cv is not None else [],
         "operations": {"source_id": f"{cid}-OPS", "recorded_at": "2026-11-05", "text": ops} if ops else None,
         "_msgs": msgs if msgs is not None else ([(msg, said)] if msg else [])}
    c.update(over)
    return c


def doctor(cid="D1", cv=ED_CV, msg=BOTH, **over):
    return cand(cid, cv=cv, msg=msg, profession="Physician", address={"city": "Columbus", "state": "OH"}, **over)


def job(number="VMS-1", title="Home Health PT", details=HH_JOB, **over):
    r = {"requisitionNumber": number, "lastModified": "2026-10-25T12:00:00Z", "positionName": title,
         "facility": {"name": "Synthetic Facility", "city": "Spokane", "region": "Washington"}, "state": "OPEN",
         "startDate": "12/01/2026", "contractWeeks": 13, "weeklyHours": 40, "shiftDescription": "Weekdays", "details": details}
    r.update(over)
    return {k: v for k, v in r.items() if v is not None}


def ed_job(number="VMS-2", **over):
    base = {"title": "ED Physician", "details": ED_JOB, "facility": {"name": "Synthetic Hospital", "city": "Dayton", "region": "Ohio"},
            "startDate": None, "contractWeeks": None, "weeklyHours": None, "shiftDescription": None, "shifts": SHIFTS,
            "candidateRate": {"value": 240, "currency": "USD", "unit": "HOUR"}}
    base.update(over)
    return job(number, **base)


def profile(pid, email=None, base=PROFILE, said=MSG, owner=None, restriction=None):
    return {"profile_id": pid, "contact": {"email": email or f"{pid.lower()}@synthetic.invalid"}, "profession": "Physical Therapist",
            "evidence": [{"id": f"{pid}-BASE", "observed_at": "2026-11-06T12:00:00Z", "kind": "profile", "text": base},
                         {"id": f"{pid}-SAID", "observed_at": "2026-11-06T12:01:00Z", "kind": "candidate_statement", "text": said}],
            "workflow": {"owner": owner, "placement": None, "contact_restriction": restriction}}


def update(event_id, at, cid, text):
    return {"event_id": event_id, "occurred_at": f"2026-11-07T{at}:00Z", "subject": {"external_source": "fictional_ats", "external_id": cid},
            "kind": "candidate_message", "text": text}


def feed(event_id, at, record, kind="requisition.created"):
    return {"eventId": event_id, "occurredAt": f"2026-11-07T{at}:00Z", "eventType": kind, "record": record}


def build(cands=(), jobs=(), notes=(), profiles=(), updates=(), feeds=(), ats_jobs=(), extra_people=(), extra_journal=(),
          people_over=None, settings=None, overlay=None, broken=None):
    """Assemble the four source shapes and run them through the real engine.

    notes        (requisition, author role, date, text) job notes in the client database
    broken       {source name: {"path": ...} | {"text": raw file text} | {"adapter": ...}} to make a source fail
    settings     configuration overrides, merged over the defaults
    """
    cands = [dict(c) if isinstance(c, dict) else c for c in cands]
    people, journal = [], []
    for i, c in enumerate(cands, start=1):
        if not isinstance(c, dict):
            continue
        msgs = c.pop("_msgs", [])
        if "id" in c:
            row = {"person_pk": i, "ats_candidate_id": c["id"], "email_address": c.get("email"), "last_updated": "2026-11-01"}
            row.update((people_over or {}).get(c["id"], {}))
            people.append(row)
        for n, (text, when) in enumerate(msgs, start=1):
            journal.append({"entry_pk": f"{c.get('id', i)}-MSG{n}", "person_fk": i, "entry_type": "candidate_message", "created_on": when, "body": text})
    data = {
        "ats": {"exported_at": "2026-11-07T18:00:00Z", "data": {"candidates": cands, "jobs": list(ats_jobs)}},
        "vms": {"exported_at": "2026-11-07T18:00:00Z", "requisitions": list(jobs), "changeFeed": list(feeds)},
        "client_db": {"exported_at": "2026-11-07T18:00:00Z", "tables": {
            "people": people + list(extra_people), "journal_entries": journal + list(extra_journal),
            "job_notes": [{"note_pk": f"NOTE-{i}", "requisition_ref": req, "author_role": role, "created_on": when, "body": text}
                          for i, (req, role, when, text) in enumerate(notes, start=1)]}},
        "our_system": {"exported_at": "2026-11-07T18:00:00Z", "profiles": list(profiles), "updates": list(updates)},
    }
    cfg = config.load(overlay)
    if settings:
        cfg = deep_merge(cfg, settings)
    sources = []
    for s in cfg["sources"]:
        if s["name"] not in data:
            continue
        s = dict(s, data=data[s["name"]])
        fault = (broken or {}).get(s["name"])
        if fault:
            del s["data"]
            if "text" in fault:                       # a real file with unreadable contents
                path = Path(tempfile.mkdtemp()) / f"{s['name']}.json"
                path.write_text(fault["text"], encoding="utf-8")
                s["path"] = str(path)
            s.update({k: v for k, v in fault.items() if k != "text"})
        sources.append(s)
    cfg["sources"] = sources
    return Engine(cfg=cfg)


# ------------------------------------------------------------------ checking
def find(engine, step, job_number, person):
    for m in engine.matches(step).get("job:" + job_number, ([], None))[0]:
        if m.person.endswith(":" + person):
            return m
    return None


def check(engine, expectations, step=None) -> list[str]:
    """Return what did not hold. An empty list means the scenario passed."""
    last = len(engine.events)
    step = last if step is None else step
    problems = []
    issues = engine.issues(step)
    for e in expectations:
        kind = e[0]
        if kind == "match":
            _, job_number, person, want = e
            m = find(engine, step, job_number, person)
            if m is None:
                problems.append(f"{person} was not matched against {job_number}")
                continue
            results = {r.rule: r for r in m.results + m.gates}
            for key, value in want.items():
                if key == "tier":
                    got = m.tier
                elif key == "action":
                    got = m.action["status"]
                elif key == "owner":
                    got = m.action["owner"]
                elif key == "identical":
                    got = m.identical
                elif key == "flag":
                    got = value if value in m.flags else f"flags {m.flags}"
                elif key == "question":
                    got = value if any(value in q for q in m.questions + m.action["steps"]) else f"questions {m.questions}"
                elif key in ("summary", "standout", "decided_by"):
                    text = m.summary if key == "summary" else " · ".join(m.standout) if key == "standout" else m.decided_by
                    got = value if value.lower() in text.lower() else text
                elif key.startswith("not_"):                # ("not_skills", "met"): the outcome must be anything but this
                    got = m.tier if key == "not_tier" else results[key[4:]].outcome
                    if got == value:
                        problems.append(f"{person} for {job_number}: {key[4:]} is {got!r}, which it must not be")
                    continue
                else:
                    got = results[key].outcome
                if got != value:
                    problems.append(f"{person} for {job_number}: {key} is {got!r}, expected {value!r}")
        elif kind == "issue":
            _, severity, text = e
            if not any(i["severity"] == severity and text.lower() in (i["message"] + " " + i["where"]).lower() for i in issues):
                problems.append(f"no {severity} mentioning {text!r}; found {[(i['severity'], i['message'][:60]) for i in issues]}")
        elif kind == "clean":
            if issues:
                problems.append(f"expected no data problems, found {[(i['severity'], i['message'][:60]) for i in issues]}")
        elif kind in ("people", "jobs"):
            got = len(getattr(engine.world(step), kind))
            if got != e[1]:
                problems.append(f"{got} {kind}, expected {e[1]}")
        elif kind == "steps":
            if len(engine.steps) != e[1]:
                problems.append(f"{len(engine.steps)} replay steps, expected {e[1]}")
        elif kind == "change":
            _, at_step, text = e
            if not any(text.lower() in (c["text"] + " " + " ".join(c["rules"])).lower() for c in engine.changes(at_step)):
                problems.append(f"no change at step {at_step} mentioning {text!r}; found {[c['text'] for c in engine.changes(at_step)]}")
        elif kind == "quiet":
            if engine.changes(e[1]):
                problems.append(f"expected no change at step {e[1]}, found {[c['text'] for c in engine.changes(e[1])]}")
        elif kind == "silent":                        # nothing is announced about a job
            if any(e[1] in n["text"] for n in engine.notifications(last)):
                problems.append(f"a notification mentions {e[1]}")
        elif kind == "order":                         # these people appear in this order on the job's list
            _, job_number, want = e
            rows = engine.matches(step).get("job:" + job_number, ([], None))[0]
            got = [m.person.split(":")[-1] for m in rows if m.person.split(":")[-1] in want]
            if got != list(want):
                problems.append(f"order on {job_number} is {got}, expected {list(want)}")
        elif kind == "page":                          # the recruiter page's data can still be built
            engine.state(step)
    return problems


def run(scenario) -> list[str]:
    try:
        engine = scenario["build"]()
    except Exception as exc:  # noqa: BLE001
        want = scenario.get("refuses")
        return [] if want and isinstance(exc, want) else [f"building failed: {type(exc).__name__}: {exc}"]
    if scenario.get("refuses"):
        return ["expected the configuration to be refused, but it loaded"]
    return check(engine, scenario["expect"] + [("page",)], scenario.get("step"))


def S(sid, title, build_fn, expect, **kw):
    return dict(id=sid, area=sid[0], title=title, build=build_fn, expect=expect, **kw)


LATE = "I am open to Spokane now. I cannot start until December 8. I can work weekdays for thirteen weeks and need at least $2,400 per week for forty hours."
SHORT_CV = HH_CV.replace("2023-01", "2025-09")          # about 14 months of home health as of the reference date
J, HH = "VMS-1", lambda **kw: [job(**kw)]               # noqa: E731

SCENARIOS = [
    # ------------------------------------------------------------- matching
    S("M01", "A clean, strong match", lambda: build([cand()], HH()),
      [("match", J, "C1", {"tier": "strong", "action": "ready"}), ("clean",)]),
    S("M02", "No license on file is a question, not a rejection", lambda: build([cand(cv=HH_CV.split(" Candidate reports")[0])], HH()),
      [("match", J, "C1", {"tier": "clarify", "license": "unknown", "question": "Washington license"})]),
    S("M03", "Licensed in another state", lambda: build([cand(cv=HH_CV.replace("Washington PT license", "Oregon PT license"))], HH()),
      [("match", J, "C1", {"tier": "clarify", "license": "unknown", "standout": "no Washington license"})]),
    S("M04", "Experience below the minimum", lambda: build([cand(cv=SHORT_CV)], HH()),
      [("match", J, "C1", {"tier": "not_fit", "setting_experience": "not_met"})]),
    S("M05", "Experience exactly at the minimum counts", lambda: build([cand(cv=HH_CV.replace("2023-01", "2024-11"))], HH(), settings={"clock": {"reference_date": "2026-11-01"}}),
      [("match", J, "C1", {"tier": "strong", "setting_experience": "met"})]),
    S("M06", "Asks more than the approved pay: lower score, still shown", lambda: build([cand(msg=MSG.replace("2,400", "2,600"))], HH()),
      [("match", J, "C1", {"tier": "strong", "pay": "not_met", "standout": "above the approved rate"})]),
    S("M07", "The same person when pay is a hard ceiling", lambda: build([cand(msg=MSG.replace("2,400", "2,600"))], HH(), overlay="strict_pay"),
      [("match", J, "C1", {"tier": "not_fit", "pay": "not_met"})]),
    S("M08", "An hourly ask compared with weekly pay", lambda: build([cand(msg=NO_PAY_MSG + " My minimum is $60/hour.")], HH()),
      [("match", J, "C1", {"tier": "strong", "pay": "met"})]),
    S("M09", "Pay in a unit that cannot be compared is a question, not a guess",
      lambda: build([cand(msg=NO_PAY_MSG, expected_pay={"amount": 500, "currency": "USD", "period": "day"})], HH()),
      [("match", J, "C1", {"pay": "unknown", "question": "per week"})]),
    S("M10", "Can only start a week late", lambda: build([cand(msg=LATE)], HH()),
      [("match", J, "C1", {"tier": "not_fit", "start_date": "not_met", "question": "account manager"})]),
    S("M11", "A week late, when the client allows seven days", lambda: build([cand(msg=LATE)], HH(), settings={"scoring": {"start_tolerance_days": 7}}),
      [("match", J, "C1", {"tier": "strong", "start_date": "met"})]),
    S("M12", "Wants nights; the job is weekdays", lambda: build([cand(msg=MSG.replace("work weekdays", "work nights"))], HH()),
      [("match", J, "C1", {"tier": "not_fit", "schedule": "not_met"})]),
    S("M13", "Prefers another state but will travel: ask",
      lambda: build([cand(msg=None, address={"city": "Portland", "state": "OR"}, preferences={"states": ["OR"], "travel": True})], HH()),
      [("match", J, "C1", {"location": "unknown", "question": "Spokane"})]),
    S("M14", "Prefers another state and will not travel",
      lambda: build([cand(msg=None, address={"city": "Portland", "state": "OR"}, preferences={"states": ["OR"], "travel": False})], HH()),
      [("match", J, "C1", {"tier": "not_fit", "location": "not_met"})]),
    S("M15", "Ruled the city out, then came back: the later message wins",
      lambda: build([cand(msgs=[("Spokane is no longer a location I would consider.", "2026-10-20"), (MSG, "2026-11-06")])], HH()),
      [("match", J, "C1", {"location": "met", "flag": "replaced"})]),
    S("M16", "Said the tool was unfamiliar, then a newer résumé lists it",
      lambda: build([cand(msgs=[("I have not used Homecare Homebase.", "2026-09-01"), (MSG, "2026-11-06")])], HH()),
      [("match", J, "C1", {"skills": "met", "flag": "replaced"})]),
    S("M17", "A no-contact request that has ended",
      lambda: build([cand(msgs=[("Please do not contact me until November 1, 2026.", "2026-10-01"), (MSG, "2026-11-06")])], HH()),
      [("match", J, "C1", {"action": "ready", "contact_permission": "clear"})]),
    S("M18", "A no-contact request still in force: strong fit, no contact",
      lambda: build([cand(msgs=[(MSG, "2026-11-05"), ("Please do not contact me about any jobs until January 15, 2027.", "2026-11-06")])], HH()),
      [("match", J, "C1", {"tier": "strong", "action": "blocked"})]),
    S("M19", "Owned by another recruiter", lambda: build([cand(ops="Assigned recruiter R7; candidate requested that opportunities go through R7.")], HH()),
      [("match", J, "C1", {"action": "route", "owner": "recruiter R7"})]),
    S("M20", "A filled job recommends nobody and announces nothing", lambda: build([cand()], HH(state="FILLED")),
      [("match", J, "C1", {"action": "closed"}), ("silent", J)]),
    S("M21", "A posting that says almost nothing cannot make a strong match",
      lambda: build([cand()], [job(title="Physical Therapist", details=None, facility=None, startDate=None, contractWeeks=None, weeklyHours=None, shiftDescription=None)]),
      [("match", J, "C1", {"tier": "possible", "summary": "Too little in the posting"})]),
    S("M22", "The wrong profession", lambda: build([doctor()], HH()),
      [("match", J, "D1", {"tier": "not_fit", "profession": "not_met", "standout": "not a physical therapist"})]),
    S("M23", "A profession the vocabulary does not list", lambda: build([cand(profession="Registered Nurse")], HH()),
      [("match", J, "C1", {"tier": "not_fit"}), ("issue", "info", "vocabulary does not list")]),
    S("M24", "A recruiter's job note cannot change a requirement",
      lambda: build([cand(cv=SHORT_CV)], HH(), notes=[(J, "recruiter", "2026-11-05", ONE_YEAR)]),
      [("match", J, "C1", {"tier": "not_fit", "setting_experience": "not_met"})]),
    S("M25", "An account manager's note relaxes a requirement, and the result says so",
      lambda: build([cand(cv=SHORT_CV)], HH(), notes=[(J, "account_manager", "2026-11-05", ONE_YEAR)]),
      [("match", J, "C1", {"tier": "strong", "setting_experience": "met", "flag": "provisional", "requirement_source": "confirm"})]),
    S("M26", "A note can also tighten a requirement",
      lambda: build([cand()], HH(), notes=[(J, "account_manager", "2026-11-05", "The facility now says five years of home-health experience is required.")]),
      [("match", J, "C1", {"tier": "not_fit", "setting_experience": "not_met", "flag": "provisional"})]),
    S("M27", "The same note, ignored under 'vendor feed only'",
      lambda: build([cand(cv=SHORT_CV)], HH(), notes=[(J, "account_manager", "2026-11-05", ONE_YEAR)], overlay="feed_only"),
      [("match", J, "C1", {"tier": "not_fit", "flag": "pending_clarification", "requirement_source": "clear"})]),
    S("M28", "Two people identical on every rule are grouped, not ranked", lambda: build([cand("C1"), cand("C2")], HH()),
      [("match", J, "C1", {"identical": 2}), ("match", J, "C2", {"identical": 2, "decided_by": "arbitrary"})]),
    S("M29", "A claim of years the dated history does not support",
      lambda: build([cand(cv=HH_CV.replace("2023-01", "2025-05"), msg=MSG + " I have three years of home-health work.")], HH()),
      [("match", J, "C1", {"tier": "clarify", "setting_experience": "unknown", "flag": "disputed"})]),
    S("M30", "Someone we know almost nothing about needs answers, and is not rejected", lambda: build([cand(cv=None, msg=None, ops=None)], HH()),
      [("match", J, "C1", {"tier": "clarify", "setting_experience": "unknown", "start_date": "unknown"})]),
    S("M31", "Availability stated three months ago is flagged as old, and still used", lambda: build([cand(said="2026-08-01")], HH()),
      [("match", J, "C1", {"start_date": "met", "flag": "stale"})]),
    S("M32", "Locums: covers both shifts", lambda: build([doctor()], [ed_job()]),
      [("match", "VMS-2", "D1", {"tier": "strong", "shift_coverage": "met", "certifications": "met"})]),
    S("M33", "Locums: declines one of two shifts", lambda: build([doctor(msg="I could cover December 5. I cannot work December 6. My minimum is $230/hour.")], [ed_job()]),
      [("match", "VMS-2", "D1", {"tier": "not_fit", "shift_coverage": "not_met"})]),
    S("M34", "Locums: offers one shift and is silent on the other", lambda: build([doctor(msg="I could cover December 5. My minimum is $230/hour.")], [ed_job()]),
      [("match", "VMS-2", "D1", {"tier": "clarify", "shift_coverage": "unknown", "question": "6 Dec 2026"})]),
    S("M35", "Locums: placed, with the days not recorded, is not 'unavailable'",
      lambda: build([doctor(msg=None, status="placed", assignment_end="2026-12-31")], [ed_job()]),
      [("match", "VMS-2", "D1", {"tier": "clarify", "shift_coverage": "unknown"})]),
    S("M36", "Locums: offers a date the current assignment already covers",
      lambda: build([doctor(msg="My current assignment is Thursday through Saturday. " + BOTH)], [ed_job()]),
      [("match", "VMS-2", "D1", {"tier": "clarify", "shift_coverage": "unknown"})]),
    S("M37", "Locums: another board with every certificate qualifies by the second route", lambda: build([doctor(cv=FM_CV)], [ed_job()]),
      [("match", "VMS-2", "D1", {"tier": "strong", "certifications": "met", "flag": "alternative_route"})]),
    S("M38", "Locums: another board with one certificate missing", lambda: build([doctor(cv=FM_CV.replace("ACLS, PALS and BLS", "ACLS and BLS"))], [ed_job()]),
      [("match", "VMS-2", "D1", {"tier": "clarify", "certifications": "unknown", "question": "PALS"})]),

    # ------------------------------------------------------------- failures
    S("F01", "A source file is missing: the others still load", lambda: build([cand()], HH(), broken={"vms": {"path": "no_such_file.json"}}),
      [("issue", "error", "was not found"), ("people", 1), ("jobs", 0)]),
    S("F02", "A source file is not valid JSON", lambda: build([cand()], HH(), broken={"client_db": {"text": "{ this is not json"}}),
      [("issue", "error", "not valid JSON"), ("match", J, "C1", {"tier": "clarify"})]),
    S("F03", "The configuration names an adapter that does not exist", lambda: build([cand()], HH(), broken={"our_system": {"adapter": "nope", "path": "x.json"}}),
      [("issue", "error", "no adapter named"), ("match", J, "C1", {"tier": "strong"})]),
    S("F04", "One record has no id: it is left out, the rest load", lambda: build([cand("C1"), {"first_name": "No", "last_name": "Id"}], HH()),
      [("issue", "error", "field 'id' is missing"), ("people", 1), ("match", J, "C1", {"tier": "strong"})]),
    S("F05", "One record has the wrong shape: it is undone, the rest load", lambda: build([cand("C1"), cand("C2", documents="see attached")], HH()),
      [("issue", "error", "Record left out"), ("issue", "warning", "no record of"), ("match", J, "C1", {"tier": "strong"})]),
    S("F06", "A record's date cannot be read: the export time is used", lambda: build([cand(updated_at="last Tuesday")], HH()),
      [("issue", "warning", "export time was used"), ("match", J, "C1", {"tier": "strong"})]),
    S("F07", "A date field cannot be read: the field is dropped and becomes a question",
      lambda: build([cand(available_from="soon", msg=MSG.replace("I can start December 1, work", "I work"))], HH()),
      [("issue", "warning", "field was ignored"), ("match", J, "C1", {"tier": "clarify", "start_date": "unknown"})]),
    S("F08", "The same id twice in one source", lambda: build([cand("C1"), cand("C1", updated_at="2026-11-03")], HH()),
      [("issue", "info", "more than once"), ("people", 1), ("match", J, "C1", {"tier": "strong"})]),
    S("F09", "The client database points at a candidate the ATS does not have",
      lambda: build([cand()], HH(), extra_people=[{"person_pk": 99, "ats_candidate_id": "C99", "email_address": "c99@synthetic.invalid", "last_updated": "2026-11-01"}]),
      [("issue", "warning", "has no record of"), ("people", 2), ("match", J, "C99", {"tier": "clarify"})]),
    S("F10", "A note about a job no feed lists", lambda: build([cand()], HH(), notes=[("VMS-404", "account_manager", "2026-11-05", ONE_YEAR)]),
      [("issue", "warning", "no feed or job record lists it"), ("jobs", 2), ("match", J, "C1", {"tier": "strong"})]),
    S("F11", "No candidates at all", lambda: build([], HH()), [("people", 0), ("jobs", 1), ("clean",)]),
    S("F12", "Every source fails: an empty page, not a crash",
      lambda: build([cand()], HH(), broken={n: {"path": "gone.json"} for n in ("ats", "vms", "client_db", "our_system")}),
      [("issue", "error", "could not be loaded"), ("people", 0), ("jobs", 0)]),
    S("F13", "Empty text and nothing said", lambda: build([cand(cv="", msg=None)], HH()),
      [("match", J, "C1", {"tier": "clarify"})]),
    S("F14", "A shift that ends before it starts is ignored and reported",
      lambda: build([doctor()], [ed_job(shifts=[SHIFTS[0], {"start": "2026-12-06T19:00:00-05:00", "end": "2026-12-06T07:00:00-05:00"}])]),
      [("issue", "warning", "shift could not be read"), ("match", "VMS-2", "D1", {"shift_coverage": "met"})]),
    S("F15", "A pay amount that is not a positive number is ignored and reported",
      lambda: build([cand(msg=NO_PAY_MSG, expected_pay={"amount": -5, "period": "week"})], HH()),
      [("issue", "warning", "not a positive number"), ("match", J, "C1", {"pay": "unknown"})]),
    S("F16", "The configuration names a rule the engine does not have: refused at start",
      lambda: build([cand()], HH(), settings={"rules": {"made_up": {"label": "x", "level": "hard", "weight": 1}}}), [], refuses=ValueError),
    S("F17", "Names and text beyond plain ASCII",
      lambda: build([cand(first_name="José", last_name="Núñez-O’Brien", cv=HH_CV + " Résumé reviewed in a café — naïve formatting ☕.")], HH()),
      [("match", J, "C1", {"tier": "strong"}), ("clean",)]),
    S("F18", "The job's two sources disagree: the newer one is used and it is reported",
      lambda: build([cand()], HH(), ats_jobs=[{"id": "J1", "external_requisition": J, "updated_at": "2026-10-20", "title": "Physical Therapist",
                                               "status": "open", "start_date": "2026-12-15"}]),
      [("issue", "warning", "disagree on start"), ("match", J, "C1", {"start_date": "met"})]),
    S("F19", "A message for someone not in the people table",
      lambda: build([cand()], HH(), extra_journal=[{"entry_pk": "LOST-1", "person_fk": 77, "entry_type": "candidate_message", "created_on": "2026-11-06", "body": MSG}]),
      [("issue", "warning", "not in the people table"), ("issue", "warning", "no source holds a record"), ("people", 2)]),

    # --------------------------------------------------------------- events
    S("E01", "A new job arrives and is matched at once",
      lambda: build([cand()], [], feeds=[feed("EV-J", "18:05", job("VMS-9"))]),
      [("jobs", 1), ("change", 1, "New job VMS-9"), ("match", "VMS-9", "C1", {"tier": "strong"})]),
    S("E02", "A candidate update flips exactly one result",
      lambda: build([cand("C1"), cand("C2")], HH(), updates=[update("EV-1", "18:10", "C1", "My start date has changed. I cannot start until December 8.")]),
      [("change", 1, "Not a fit"), ("match", J, "C1", {"tier": "not_fit", "flag": "replaced"}), ("match", J, "C2", {"tier": "strong"})]),
    S("E03", "An update that changes no result is not announced",
      lambda: build([cand()], HH(), updates=[update("EV-1", "18:10", "C1", "Thank you, looking forward to hearing more.")]),
      [("quiet", 1), ("match", J, "C1", {"tier": "strong"})]),
    S("E04", "The same event delivered twice is applied once",
      lambda: build([cand()], HH(), updates=[update("EV-1", "18:10", "C1", "I cannot start until December 8."), update("EV-1", "18:10", "C1", "I cannot start until December 8.")]),
      [("steps", 2), ("issue", "info", "arrived more than once")]),
    S("E05", "Events listed out of order are replayed in time order",
      lambda: build([cand()], HH(start="x") if False else HH(), updates=[update("EV-B", "18:20", "C1", "I can start December 1 after all."),
                                                                         update("EV-A", "18:10", "C1", "I cannot start until December 8.")]),
      [("steps", 3), ("change", 1, "Not a fit"), ("change", 2, "→ Match"), ("match", J, "C1", {"tier": "strong"})]),
    S("E06", "An update about someone no source knows",
      lambda: build([cand()], HH(), updates=[update("EV-X", "18:10", "C404", MSG)]),
      [("issue", "warning", "no source holds a record"), ("people", 2), ("match", J, "C1", {"tier": "strong"})]),
    S("E07", "A job update raises the pay: the result improves",
      lambda: build([cand(msg=MSG.replace("2,400", "2,600"))], HH(), feeds=[feed("EV-U", "18:05", job(details=HH_JOB.replace("2,450", "2,700")), "requisition.updated")]),
      [("match", J, "C1", {"pay": "met"}), ("change", 1, "Pay: not met → met")]),
    S("E08", "A job is filled while people are shortlisted: the shortlist is withdrawn",
      lambda: build([cand()], HH(), feeds=[feed("EV-F", "18:05", job(state="FILLED"), "requisition.updated")]),
      [("change", 1, "is now filled"), ("match", J, "C1", {"action": "closed"})]),
    S("E09", "A shortlisted candidate asks for no contact",
      lambda: build([cand()], HH(), updates=[update("EV-1", "18:10", "C1", "Please do not contact me about any jobs until January 15, 2027.")]),
      [("change", 1, "Contact permission: clear → blocked"), ("match", J, "C1", {"tier": "strong", "action": "blocked"})]),

    # ------------------------------------------------------------- identity
    S("I01", "The same email in two sources is one person", lambda: build([cand()], HH(), profiles=[profile("R1", email="c1@synthetic.invalid")]),
      [("people", 1), ("match", J, "C1", {"tier": "strong"})]),
    S("I02", "Identical text with different emails stays two people", lambda: build([], HH(), profiles=[profile("R1"), profile("R2")]),
      [("people", 2), ("match", J, "R1", {"identical": 2})]),
    S("I03", "An explicit reference links records even when the emails differ",
      lambda: build([cand()], HH(), people_over={"C1": {"email_address": "typo@synthetic.invalid"}}),
      [("people", 1), ("issue", "info", "email addresses differ")]),
    S("I04", "Two records in one source with the same email are merged, and it is reported",
      lambda: build([cand("C1"), cand("C2", email="c1@synthetic.invalid")], HH()),
      [("people", 1), ("issue", "info", "same email address")]),
    S("I05", "A recruiter's id is never mistaken for a profile's id",
      lambda: build([], HH(), profiles=[profile("R10", owner="R10", restriction="Opportunity recommendations must go through recruiter R10.")]),
      [("people", 1), ("match", J, "R10", {"action": "route", "owner": "recruiter R10"})]),

    # --------------------------------------------- added after an audit of the base
    S("M39", "Outpatient work only, for a home-health job: a question, not proof of absence",
      lambda: build([cand(cv="Outpatient physical therapist, 2016-01 to present. Orthopedic rehabilitation. Candidate reports active Washington PT license.")], HH()),
      [("match", J, "C1", {"tier": "clarify", "setting_experience": "unknown", "not_tier": "strong"})]),
    S("M40", "Says they have not used a required tool", lambda: build([cand(msg=MSG + " I have not used OASIS.", cv=HH_CV.replace("OASIS start-of-care documentation, ", ""))], HH()),
      [("match", J, "C1", {"tier": "not_fit", "skills": "not_met"})]),
    S("M41", "A denial worded in a way the reader does not know is never read as a yes",
      lambda: build([cand(cv=HH_CV.replace("OASIS start-of-care documentation, ", ""), msg=MSG + " Honestly, OASIS is not something I'm familiar with.")], HH()),
      [("match", J, "C1", {"not_skills": "met"})]),
    S("M42", "Asks exactly the approved pay", lambda: build([cand(msg=MSG.replace("2,400", "2,450"))], HH()),
      [("match", J, "C1", {"tier": "strong", "pay": "met"})]),
    S("M43", "Wants a shorter contract than the job", lambda: build([cand(msg=MSG.replace("thirteen weeks", "eight weeks"))], HH()),
      [("match", J, "C1", {"tier": "not_fit", "schedule": "not_met"})]),
    S("M44", "Lives in the job's city and says nothing about moving",
      lambda: build([cand(msg=NO_PAY_MSG.replace("I am open to Spokane now. ", ""), address={"city": "Spokane", "state": "WA"})], HH()),
      [("match", J, "C1", {"location": "met"})]),
    S("M45", "Two people level on every rule: the one heard from more recently scores higher on readiness",
      lambda: build([cand("C1", said="2026-10-20"), cand("C2", said="2026-11-06")], HH()),
      [("order", J, ["C2", "C1"]), ("match", J, "C1", {"decided_by": "scores higher"})]),
    S("M46", "A strong fit who must not be contacted is listed after everyone a recruiter can act on",
      lambda: build([cand("C1", msgs=[(MSG, "2026-11-05"), ("Please do not contact me about any jobs until January 15, 2027.", "2026-11-06")]),
                     cand("C2", msg=MSG.replace("2,400", "2,600"))], HH()),
      [("order", J, ["C2", "C1"]), ("match", J, "C1", {"tier": "strong", "action": "blocked"})]),
    S("M47", "A request routed to another recruiter is still something to act on, and is not listed last",
      lambda: build([cand("C1", ops="Assigned recruiter R7; candidate requested that opportunities go through R7."), cand("C2", msg=MSG.replace("2,400", "2,600"))], HH()),
      [("order", J, ["C1", "C2"])]),
    S("M48", "A newer message about the start date that cannot be read: the older yes becomes a question",
      lambda: build([cand(msgs=[(MSG, "2026-11-01"), ("Actually December 1 won't work for me anymore.", "2026-11-06")])], HH()),
      [("match", J, "C1", {"tier": "clarify", "start_date": "unknown", "flag": "unread_update", "question": "won't work for me"})]),
    S("M49", "A newer message turning the city down in words the reader does not know",
      lambda: build([cand(msgs=[(MSG, "2026-11-01"), ("Spokane doesn't really work for me after all.", "2026-11-06")])], HH()),
      [("match", J, "C1", {"tier": "clarify", "location": "unknown", "flag": "unread_update"})]),
    S("M50", "An older unclear message does not override a newer clear one",
      lambda: build([cand(msgs=[("Spokane might not work for me.", "2026-10-01"), (MSG, "2026-11-06")])], HH()),
      [("match", J, "C1", {"tier": "strong", "location": "met"})]),
    S("M51", "A message unclear about one tool leaves every other rule alone",
      lambda: build([cand(msgs=[(MSG, "2026-11-01"), ("Honestly OASIS is not something I'm familiar with.", "2026-11-06")])], HH()),
      [("match", J, "C1", {"skills": "unknown", "start_date": "met", "location": "met", "pay": "met"})]),
    S("M52", "A request not to be contacted, worded without the word 'contact'",
      lambda: build([cand(msgs=[(MSG, "2026-11-01"), ("Please hold off on reaching out until January 15.", "2026-11-06")])], HH()),
      [("match", J, "C1", {"tier": "strong", "action": "blocked"})]),
    S("M53", "A request with no end date blocks contact until it is withdrawn",
      lambda: build([cand(msgs=[(MSG, "2026-11-01"), ("Please stop contacting me.", "2026-11-06")])], HH()),
      [("match", J, "C1", {"action": "blocked"})]),
    S("F20", "A feed event of a kind the tool does not know is ignored and reported",
      lambda: build([cand()], HH(), feeds=[feed("EV-Q", "18:05", job("VMS-9"), "requisition.archived")]),
      [("issue", "warning", "requisition.archived"), ("jobs", 1)]),
    S("F21", "A job with no requisition number is left out and reported",
      lambda: build([cand()], [job(), {k: v for k, v in job("VMS-8").items() if k != "requisitionNumber"}]),
      [("issue", "error", "requisitionNumber"), ("jobs", 1), ("match", J, "C1", {"tier": "strong"})]),
    S("F22", "A very long résumé still loads", lambda: build([cand(cv=HH_CV + " Further detail." * 20000)], HH()),
      [("match", J, "C1", {"tier": "strong"})]),
    S("F23", "A message dated after its own export: the export time is used, and it is reported",
      lambda: build([cand(msgs=[(MSG, "2026-11-06"), ("I cannot start until December 8.", "2026-11-20")])], HH()),
      [("issue", "warning", "after the reference date"), ("match", J, "C1", {"start_date": "not_met"})]),
    S("E10", "A filled job that reopens gets its shortlist back",
      lambda: build([cand()], HH(state="FILLED"), feeds=[feed("EV-R", "18:05", job(), "requisition.updated")]),
      [("change", 1, "has reopened"), ("match", J, "C1", {"tier": "strong", "action": "ready"})]),
    S("E11", "The feed tightens a requirement: people drop, and it is announced",
      lambda: build([cand()], HH(), feeds=[feed("EV-T", "18:05", job(details=HH_JOB.replace("minimum experience: 2 years", "minimum experience: 5 years")), "requisition.updated")]),
      [("change", 1, "Not a fit"), ("match", J, "C1", {"tier": "not_fit", "setting_experience": "not_met"})]),
    S("E12", "A new job for a profession nobody holds is still listed, with nobody recommended",
      lambda: build([cand()], [], feeds=[feed("EV-N", "18:05", ed_job("VMS-9"))]),
      [("jobs", 1), ("change", 1, "New job VMS-9"), ("match", "VMS-9", "C1", {"tier": "not_fit"})]),
    # ------------------------------------------------- events at the same moment
    S("E13", "A new job and a candidate update at the same moment: both are applied",
      lambda: build([cand("C1"), cand("C2")], HH(), updates=[update("EV-U", "18:05", "C1", "My start date has changed. I cannot start until December 8.")],
                    feeds=[feed("EV-J", "18:05", job("VMS-9"))]),
      [("steps", 3), ("match", "VMS-9", "C1", {"tier": "not_fit"}), ("match", "VMS-9", "C2", {"tier": "strong"}), ("match", J, "C1", {"tier": "not_fit"})]),
    S("E14", "Two messages from one person at the same moment, about different things: both count",
      lambda: build([cand()], HH(), updates=[update("EV-1", "18:05", "C1", "My start date has changed. I cannot start until December 8."),
                                             update("EV-2", "18:05", "C1", "My minimum is now $2,600 per week for forty hours.")]),
      [("match", J, "C1", {"tier": "not_fit", "start_date": "not_met", "pay": "not_met"})]),
    S("E15", "Two messages at the same moment that disagree: neither is taken as the later one, and it becomes a question",
      lambda: build([cand(msg=NO_START)], HH(), updates=[update("EV-1", "18:05", "C1", "I can start December 1."),
                                                         update("EV-2", "18:05", "C1", "I cannot start until December 8.")]),
      [("match", J, "C1", {"tier": "clarify", "start_date": "unknown", "flag": "same_moment", "question": "which is right"})]),
    S("E16", "A disagreement about pay at the same moment does not unsettle the start date",
      lambda: build([cand()], HH(), updates=[update("EV-1", "18:05", "C1", "My minimum is now $2,300 per week for forty hours."),
                                             update("EV-2", "18:05", "C1", "My minimum is now $2,600 per week for forty hours.")]),
      [("match", J, "C1", {"start_date": "met", "schedule": "met", "pay": "unknown", "not_tier": "not_fit"})]),
    S("E17", "A later message settles a disagreement made at the same moment",
      lambda: build([cand(msg=NO_START)], HH(), updates=[update("EV-1", "18:05", "C1", "I can start December 1."),
                                                         update("EV-2", "18:05", "C1", "I cannot start until December 8."),
                                                         update("EV-3", "18:20", "C1", "To confirm, I can start December 1.")]),
      [("match", J, "C1", {"tier": "strong", "start_date": "met"})]),
    S("E18", "A job is updated and filled at the same moment: it is treated as filled, and the clash is reported",
      lambda: build([cand()], HH(), feeds=[feed("EV-A", "18:05", job(details=HH_JOB.replace("2,450", "2,700")), "requisition.updated"),
                                           feed("EV-B", "18:05", job(state="FILLED"), "requisition.updated")]),
      [("match", J, "C1", {"action": "closed"}), ("issue", "warning", "same moment disagree on whether it is open")]),
    S("E19", "A candidate update for a job filled at the same moment is not announced for that job",
      lambda: build([cand("C1"), cand("C2")], HH() + [job("VMS-7", details=HH_JOB.replace("Spokane", "Seattle"))],
                    updates=[update("EV-U", "18:05", "C1", "Please do not contact me about any jobs until January 15, 2027.")],
                    feeds=[feed("EV-F", "18:05", job("VMS-7", details=HH_JOB.replace("Spokane", "Seattle"), state="FILLED"), "requisition.updated")]),
      [("match", J, "C1", {"action": "blocked"}), ("match", J, "C2", {"action": "ready"}), ("match", "VMS-7", "C1", {"action": "closed"})]),
    S("E20", "Six things at once: a new job, a pay change, a job filled and three candidate updates",
      lambda: build([cand("C1"), cand("C2"), cand("C3"), cand("C4")], HH() + [job("VMS-7", details=HH_JOB.replace("Spokane", "Seattle"))],
                    updates=[update("EV-U1", "18:05", "C1", "My start date has changed. I cannot start until December 8."),
                             update("EV-U2", "18:05", "C2", "Please do not contact me about any jobs until January 15, 2027."),
                             update("EV-U3", "18:05", "C3", "My minimum is now $2,600 per week for forty hours.")],
                    feeds=[feed("EV-J1", "18:05", job("VMS-9")),
                           feed("EV-J2", "18:05", job(details=HH_JOB.replace("2,450", "2,700")), "requisition.updated"),
                           feed("EV-J3", "18:05", job("VMS-7", details=HH_JOB.replace("Spokane", "Seattle"), state="FILLED"), "requisition.updated")]),
      [("steps", 7), ("jobs", 3), ("match", J, "C1", {"tier": "not_fit"}), ("match", J, "C2", {"tier": "strong", "action": "blocked"}),
       ("match", J, "C3", {"pay": "met"}), ("match", "VMS-9", "C3", {"pay": "not_met"}), ("match", "VMS-9", "C4", {"tier": "strong", "action": "ready"}),
       ("match", "VMS-7", "C4", {"action": "closed"})]),
    S("F24", "Source precedence: the client names the vendor feed as the authority on start dates",
      lambda: build([cand()], HH(), ats_jobs=[{"id": "J1", "external_requisition": J, "updated_at": "2026-11-01", "title": "Physical Therapist",
                                               "status": "open", "start_date": "2026-12-15"}],
                    settings={"precedence": {"attributes": {"req.start": ["vms", "ats"]}}}),
      [("issue", "warning", "source precedence: vms, ats"), ("match", J, "C1", {"start_date": "met"})]),
    S("F25", "Source precedence: a structured field outranks a newer message when the client says so",
      lambda: build([cand(available_from="2026-12-15", updated_at="2026-11-01")], HH(),
                    settings={"precedence": {"attributes": {"available_from": ["structured"]}}}),
      [("match", J, "C1", {"start_date": "not_met", "flag": "replaced"})]),
    S("I06", "Two people writing from one shared agency mailbox stay two people",
      lambda: build([cand("C1", email="info@synthetic-agency.invalid"), cand("C2", email="info@synthetic-agency.invalid")], HH()),
      [("people", 2), ("issue", "info", "shared mailbox")]),
    S("I07", "Capitals and spaces in an email do not split one person in two",
      lambda: build([cand()], HH(), profiles=[profile("R1", email="  C1@Synthetic.INVALID ")]),
      [("people", 1)]),
    S("I08", "The same name with different emails stays two people",
      lambda: build([cand("C1"), cand("C2", first_name="Synthetic", last_name="C1")], HH()),
      [("people", 2)]),
    S("I10", "The same name and no email on one record: two people, never joined on the name",
      lambda: build([cand("C1", first_name="Maria", last_name="Lopez"), cand("C2", first_name="Maria", last_name="Lopez", email=None)], HH()),
      [("people", 2)]),
    S("I11", "The same name in two different systems with nothing linking them: two people",
      lambda: build([cand("C1", first_name="Maria", last_name="Lopez")], HH(),
                    profiles=[dict(profile("R1"), name="Maria Lopez")]),
      [("people", 2)]),
    S("I12", "One person under two names (a married name), linked by email: one person, the newest name shown",
      lambda: build([cand("C1", first_name="Maria", last_name="Lopez", email="m@synthetic.invalid", updated_at="2026-10-01"),
                     cand("C2", first_name="Maria", last_name="Lopez-Grant", email="m@synthetic.invalid", updated_at="2026-11-01")], HH()),
      [("people", 1), ("issue", "info", "same email address")]),
    S("I09", "A chain of links joins one person across three sources",
      lambda: build([cand()], HH(), profiles=[profile("R1", email="c1@synthetic.invalid")], people_over={"C1": {"email_address": "c1@synthetic.invalid"}}),
      [("people", 1)]),
]


def main() -> None:
    area = sys.argv[1].upper() if len(sys.argv) > 1 else None
    names = {"M": "Matching", "F": "Failures and fallbacks", "E": "Events", "I": "Identity"}
    passed = failed = 0
    current = None
    for s in SCENARIOS:
        if area and s["area"] != area:
            continue
        if s["area"] != current:
            current = s["area"]
            print(f"\n{names[current]}")
        problems = run(s)
        passed, failed = passed + (not problems), failed + bool(problems)
        print(f"  {'PASS' if not problems else 'FAIL'}  {s['id']}  {s['title']}")
        for p in problems:
            print(f"          {p}")
    print(f"\n{passed} passed, {failed} failed. All scenario data is synthetic.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
