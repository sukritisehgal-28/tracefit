"""Adapter for the supplemental packet: fuller résumés and dated recruiter and job notes.

The fuller résumés are renditions of résumés already held by the ATS, and the notes
say which sources they repeat. Both links are carried through as lineage so the same
evidence is never counted twice. The discovery notes are context for people, not
evidence about a candidate or a job, so they are not loaded for matching.
"""
from __future__ import annotations

from . import Loader, adapter


@adapter("supplement")
def load(raw: dict, L: Loader) -> None:
    candidate_source = L.src.get("candidate_source", L.name)
    profile_source = L.src.get("profile_source", L.name)

    def resume(d: dict) -> None:
        L.evidence(d["document_id"], "resume", L.person(d["candidate_id"], candidate_source), d.get("received_at"), "candidate",
                   f"Full résumé {d['document_id']}", text=d.get("text"), lineage=d.get("rendition_of"))

    def note(n: dict) -> None:
        if n.get("candidate_id"):
            subj = L.person(n["candidate_id"], candidate_source)
        elif n.get("profile_id"):
            subj = L.person(n["profile_id"], profile_source)
        elif n.get("requisition_ref"):
            subj = L.job(n["requisition_ref"])
        else:
            raise ValueError("the note names no candidate, profile or job")
        is_job_note = n.get("kind") == "job_note"
        L.evidence(n["source_id"], "job_note" if is_job_note else "recruiter_note", subj, n.get("recorded_at"),
                   n.get("author_role") or "recruiter", ("Job note " if is_job_note else "Recruiter note ") + n["source_id"],
                   text=n.get("body"), related=tuple(n.get("related_source_ids") or ()))

    L.each(raw.get("resume_documents"), "résumé", resume, key="document_id")
    L.each(raw.get("notes"), "note", note, key="source_id")
