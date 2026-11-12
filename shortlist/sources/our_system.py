"""Adapter for our own system: profiles with dated evidence, workflow fields, and candidate updates."""
from __future__ import annotations

from ..model import Batch, Event
from ..timeutil import parse_when
from . import Loader, adapter


@adapter("our_system")
def load(raw: dict, L: Loader) -> None:
    exported = raw.get("exported_at") or L.fallback_when

    def profile(p: dict) -> None:
        pid = p["profile_id"]
        subj = L.person(pid)
        rec = L.evidence(pid, "structured_record", subj, exported, "system", f"{L.label} profile {pid}")
        email = (p.get("contact") or {}).get("email")
        if email:
            L.claim(rec, "email", email)
        if p.get("name"):
            L.claim(rec, "name", p["name"])
        if p.get("profession"):
            L.claim(rec, "profession", p["profession"])
        # Empty workflow fields say nothing either way, so they produce no claims.
        wf = p.get("workflow") or {}
        if wf.get("owner"):
            L.claim(rec, "owner", wf["owner"], quote=f"workflow.owner = {wf['owner']}")
        if wf.get("placement"):
            L.claim(rec, "placement", {"status": "placed", "until": None}, quote=f"workflow.placement = {wf['placement']}")
        if wf.get("contact_restriction"):
            L.evidence(f"{pid}-WORKFLOW", "ops_note", subj, exported, "operations", f"{L.label} workflow note {pid}", text=wf["contact_restriction"])
        for e in p.get("evidence") or []:
            is_profile = e.get("kind") == "profile"
            L.evidence(e["id"], "profile" if is_profile else "candidate_statement", subj, e.get("observed_at"), "candidate",
                       ("Imported profile " if is_profile else "Candidate statement ") + e["id"], text=e.get("text"))

    def update(u: dict) -> None:
        ref = u["subject"]
        source = L.ctx.get("system_ids", {}).get(ref.get("external_source"), L.name)
        subj = L.person(ref["external_id"], source)   # the update is about a record held in another system
        batch = Batch()
        L.evidence(u["event_id"], "candidate_message", subj, u["occurred_at"], "candidate", f"Candidate message {u['event_id']}", text=u.get("text"), into=batch)
        L.batch.events.append(Event(id=u["event_id"], at=parse_when(u["occurred_at"])[0], kind="candidate.updated",
                                    label=f"Candidate update for {ref['external_id']}", source=L.name, batch=batch))

    L.each(raw.get("profiles"), "profile", profile, key="profile_id")
    L.each(raw.get("updates"), "update", update, key="event_id")
