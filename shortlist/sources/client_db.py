"""Adapter for the client's internal database: people, journal entries and job notes."""
from __future__ import annotations

from ..lexicon import state_code
from . import Loader, adapter


@adapter("client_db")
def load(raw: dict, L: Loader) -> None:
    tables = raw.get("tables") or {}
    candidate_source = L.src.get("candidate_source", L.name)
    by_pk: dict = {}

    def person(p: dict) -> None:
        pk = p["person_pk"]
        subj = L.person(pk)
        rec = L.evidence(f"person-{pk}", "structured_record", subj, p.get("last_updated"), "system", f"{L.label} person {pk}")
        by_pk[pk] = subj
        if p.get("ats_candidate_id"):      # an explicit cross-reference: the strongest identity link there is
            L.batch.links.append((subj, L.person(p["ats_candidate_id"], candidate_source), "ats_candidate_id"))
        if p.get("email_address"):
            L.claim(rec, "email", p["email_address"])
        if p.get("owner"):
            L.claim(rec, "owner", p["owner"], quote=f"owner = {p['owner']}")
        if p.get("preferred_region_text"):
            L.claim(rec, "pref_states", [state_code(p["preferred_region_text"])], quote=f'preferred_region_text = "{p["preferred_region_text"]}"')

    def entry(e: dict) -> None:
        if e["person_fk"] not in by_pk:
            L.issue("warning", f"journal entry {e['entry_pk']}", f"It refers to person {e['person_fk']}, who is not in the people table. It is kept on its own.")
        is_message = e.get("entry_type") == "candidate_message"
        L.evidence(e["entry_pk"], "candidate_message" if is_message else "recruiter_note", by_pk.get(e["person_fk"], L.person(e["person_fk"])),
                   e.get("created_on"), "candidate" if is_message else "recruiter",
                   ("Candidate message " if is_message else "Recruiter note ") + str(e["entry_pk"]), text=e.get("body"))

    def note(n: dict) -> None:
        subj = L.job(n["requisition_ref"]) if n.get("requisition_ref") else L.job(f"{candidate_source}:{n['ats_job_id']}")
        L.evidence(n["note_pk"], "job_note", subj, n.get("created_on"), n.get("author_role") or "unknown", f"Job note {n['note_pk']}", text=n.get("body"))

    L.each(tables.get("people"), "person", person, key="person_pk")
    L.each(tables.get("journal_entries"), "journal entry", entry, key="entry_pk")
    L.each(tables.get("job_notes"), "job note", note, key="note_pk")
