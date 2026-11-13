"""A deliberately simple matcher, to compare against. It is not part of the product.

    python3 -m shortlist baseline        what a field-and-keyword matcher returns on the supplied data, beside Tracefit

It does what a first version of a matcher usually does, on the same files at the 18:00 snapshot:

  filter   the profession field equals the job's; the preferred-state field allows the job's state unless the
           travel field says yes; the status field is not "placed"
  score    how many of the job's requirement words appear anywhere in the person's text
  order    most words first

It reads structured fields as they stand and counts words. It does not read dates, negations, later messages,
notes or who may be contacted. Everything it prints is run, not assumed, so the comparison in
docs/EVALUATION.md can be checked. It shares no code with the matching it is compared with.
"""
from __future__ import annotations

import json
from pathlib import Path

from .matching import TIER_LABEL

DATA = "assessment-data"
EXTRA_WORDS = {"VMS-901": ["home health"], "VMS-902": ["emergency", "ABEM", "AOBEM"]}     # the setting or board the posting names


def people(root: Path) -> list[dict]:
    ats = json.loads((root / DATA / "ats_response.json").read_text(encoding="utf-8"))["data"]
    ours = json.loads((root / DATA / "our_system_response.json").read_text(encoding="utf-8"))
    out = []
    for c in ats["candidates"]:
        text = " ".join([c.get("source_text") or ""] + [d.get("text") or "" for d in c.get("documents") or []] + (c.get("skills") or []))
        prefs = c.get("preferences") or {}
        out.append({"id": c["id"], "name": f"{c['first_name']} {c['last_name']}", "profession": c.get("profession"), "text": text,
                    "states": prefs.get("states"), "travel": prefs.get("travel"), "status": c.get("status")})
    for p in ours["profiles"]:
        text = " ".join(e.get("text") or "" for e in p.get("evidence") or [])
        out.append({"id": p["profile_id"], "name": f"Candidate {p['profile_id']}", "profession": p.get("profession"), "text": text,
                    "states": None, "travel": None, "status": "placed" if (p.get("workflow") or {}).get("placement") else None})
    return out


def jobs(root: Path) -> list[dict]:
    ats = json.loads((root / DATA / "ats_response.json").read_text(encoding="utf-8"))["data"]
    return [{"number": j["external_requisition"], "title": j["title"], "state": j["location"]["state"],
             "words": list(j.get("requirements") or []) + EXTRA_WORDS.get(j["external_requisition"], [])}
            for j in ats["jobs"] if j.get("status") == "open"]


def _plain(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").split())


def match(job: dict, pool: list[dict]) -> tuple[list[dict], list[dict]]:
    """(ranked list, people filtered out with the field that removed them)."""
    kept, out = [], []
    for p in pool:
        if not p["profession"] or p["profession"].lower() not in job["title"].lower():
            out.append(dict(p, why="profession field"))
        elif p["states"] and job["state"] not in p["states"] and not p["travel"]:
            out.append(dict(p, why="preferred-state field"))
        elif p["status"] == "placed":
            out.append(dict(p, why="status field: placed"))
        else:
            found = [w for w in job["words"] if _plain(w) in _plain(p["text"])]
            kept.append(dict(p, hits=len(found), found=found))
    kept.sort(key=lambda p: (-p["hits"], p["id"]))
    return kept, out


def run(engine) -> list[dict]:
    """The baseline's list for each open job, with Tracefit's result for the same person beside it."""
    root, pool, report = Path(engine.root), people(Path(engine.root)), []
    for job in jobs(root):
        ours = {m.person.split(":")[-1]: m for m in engine.matches(0).get("job:" + job["number"], ([], None))[0]}
        kept, out = match(job, pool)
        report.append({"job": job["number"], "title": job["title"], "words": job["words"],
                       "kept": [dict(p, ours=ours.get(p["id"])) for p in kept], "out": [dict(p, ours=ours.get(p["id"])) for p in out]})
    return report


def render(report: list[dict]) -> str:
    def ours(m) -> str:
        return "—" if m is None else f"{TIER_LABEL[m.tier]}{'' if m.tier == 'not_fit' else f' {m.score}'} · {m.action['label']}"

    lines = ["A field-and-keyword matcher on the supplied data at 18:00, beside Tracefit. Run, not assumed.", ""]
    for r in report:
        lines += [f"{r['job']}  {r['title']}   words counted: {', '.join(r['words'])}", f"  {'#':>2} {'person':<15} {'words':>5}  Tracefit says"]
        for i, p in enumerate(r["kept"], start=1):
            lines.append(f"  {i:>2} {p['name']:<15} {p['hits']:>5}  {ours(p['ours'])}")
        for p in r["out"]:
            if p["ours"] is not None and p["ours"].tier != "not_fit":
                lines.append(f"   – {p['name']:<15} {'out':>5}  {ours(p['ours'])}   (removed by the {p['why']})")
        lines.append(f"     … and {sum(1 for p in r['out'] if p['ours'] is None or p['ours'].tier == 'not_fit')} more removed that Tracefit also rules out")
        lines.append("")
    return "\n".join(lines)
