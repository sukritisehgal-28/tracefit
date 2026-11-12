"""The checker: an independent pass over every result that confirms the product's own rules hold.

    python3 -m shortlist audit            check every job and person at every replay step

It does not re-run the matching. It reads what the tool decided and asks, for each person and job:

  tier        does the tier follow from the rule outcomes? (a hard rule not met -> not a fit, and so on)
  score       is the score inside its range, the range inside 0 to 100, and the score exactly the weighted
              average of the six quality dimensions shown?
  evidence    does every decision cite evidence, and is every quote really in that evidence?
  authority   is every skill, setting or credential that counts as met backed by a kind of document
              allowed to assert it? (a recruiter's impression is not evidence of a skill)
  doubt       is any rule met on a sentence that holds a negation, uncertainty or question?
  time        is any rule met on evidence dated after the moment being shown?
  action      does the next action respect the permission rules? (no "reach out" for someone blocked,
              owned by another recruiter, or on a closed job; every active no-contact request is honoured)
  wording     is any credential described as verified?
  list        are identical people really identical, and is the list in the order the tool says it uses?

Each finding names the job, the person, the check and what is wrong. An empty list means every check
held. The checks are deliberately simple and separate from the code they check, so a mistake in the
matching shows up here rather than being repeated here.
"""
from __future__ import annotations

import re

from .config import for_division
from .dimensions import score as dim_score, weights_of
from .extract import doubt_in
from .matching import GROUP_OF, TIER_ORDER
from .model_reader import _norm

VERIFIED = re.compile(r"(?<!not )(?<!un)\bverified\b", re.I)
TRUSTED_FOR = {"skills": "experience", "setting_experience": "experience", "license": "credential", "certifications": "credential"}


def _finding(job, person, check, detail):
    return {"job": job, "person": person, "check": check, "detail": detail}


def recorded(world) -> dict:
    """Every (evidence id, quote) the sources and readers actually recorded, for checking citations."""
    if getattr(world, "_audit_quotes", None) is None:
        quotes: dict = {}
        for claims in world._claims.values():
            for c in claims:
                quotes.setdefault(c.evidence_id, set()).add(_norm(c.quote))
        world.__dict__["_audit_quotes"] = quotes
    return world._audit_quotes


def check_match(world, cfg: dict, job, m) -> list[dict]:
    """Every check that concerns one person and one job. `m` must be a full match (results and gates)."""
    out = []
    jk, pk = job.key, m.person

    def bad(check, detail):
        out.append(_finding(jk, pk, check, detail))

    scoring = for_division(cfg, job.division)["scoring"]
    results, gates = m.results, m.gates
    g = {x.rule: x.outcome for x in gates}
    hard = [r for r in results if r.level == "hard"]

    # tier follows from the outcomes
    if any(r.outcome == "not_met" for r in hard):
        want = {"not_fit"}
    elif any(r.outcome == "unknown" for r in hard):
        want = {"clarify"}
    else:
        want = {"strong", "possible"}
    if m.tier not in want:
        bad("tier", f"tier is {m.tier}, but the hard rules give {' or '.join(sorted(want))}")
    if m.tier == "strong" and m.low < scoring["strong_threshold"]:
        bad("tier", f"strong match with a lowest possible score of {m.low}, under the threshold {scoring['strong_threshold']}")

    # score: inside its range, and exactly the weighted average of the dimensions shown
    if not 0 <= m.low <= m.score <= m.high <= 100:
        bad("score", f"score {m.score} is outside its range {m.low} to {m.high}, or the range is outside 0 to 100")
    weights = weights_of(for_division(cfg, job.division)["dimensions"])
    if m.dims and dim_score(m.dims, weights) != (m.score, m.low, m.high):
        bad("score", f"score {m.score} ({m.low} to {m.high}) is not the weighted average of its dimensions, {dim_score(m.dims, weights)}")
    if m.dim_detail and any(d is not None and not 0 <= d[1] <= d[0] <= d[2] <= 100 for d in m.dim_detail.values()):
        bad("score", "a dimension is outside 0 to 100, or outside its own range")

    # evidence: decisions cite something, and every quote is really there
    for r in results:
        if r.outcome in ("met", "not_met") and not (r.refs or r.job_refs):
            bad("evidence", f"{r.label}: {r.outcome.replace('_', ' ')} with no evidence cited")
    said = recorded(world)
    for r in results + gates:
        for ref in r.refs + r.job_refs:
            ev = world.evidence.get(ref["evidence"])
            q = _norm(ref["quote"] or "")
            if ev is None:
                bad("evidence", f"{r.label}: cites {ref['evidence']}, which is not on file")
            elif q not in said.get(ev.id, ()) and not (ev.text and q in _norm(ev.text)):
                # A quote must be something that evidence recorded: a field it holds, or words in its text.
                bad("evidence", f"{r.label}: the quote “{ref['quote'][:60]}” was not recorded by {ev.label}")
            elif ev.text and q in said.get(ev.id, ()) and " = " not in ref["quote"] and q not in _norm(ev.text) \
                    and not ref["quote"].startswith(("skills: ", "requirements: ")):
                bad("evidence", f"{r.label}: the quote “{ref['quote'][:60]}” is not in the text of {ev.label}")

    # authority, doubt and time, for every fit rule counted as met
    authority = cfg.get("authority", {})
    for r in results:
        if r.outcome != "met":
            continue
        for ref in r.refs:
            if ref.get("note") == "replaced":
                continue
            ev = world.evidence.get(ref["evidence"])
            if ev is None:
                continue
            cls = TRUSTED_FOR.get(r.rule)
            if cls and ev.subject.startswith("person:") and ev.kind not in authority.get(cls, ()):
                bad("authority", f"{r.label} is met on a {ev.kind.replace('_', ' ')}, which may not assert it")
            if ev.text and ev.subject.startswith("person:") and doubt_in(ref["quote"])[0] and not any(i.get("outcome") == "not_met" for i in r.items):
                bad("doubt", f"{r.label} is met on a sentence with a negation or doubt: “{ref['quote'][:60]}”")
            if ev.observed_at > world.as_of:
                bad("time", f"{r.label} is met on {ev.label}, dated after the moment shown")

    # next action respects the permission rules
    status = m.action["status"]
    if g.get("job_open") == "blocked" and status != "closed":
        bad("action", f"the job is not open, but the action is {status}")
    if g.get("contact_permission") == "blocked" and status not in ("blocked", "closed"):
        bad("action", f"contact is blocked, but the action is {status}")
    if status == "ready" and (g.get("ownership") == "route" or g.get("contact_permission") == "blocked"):
        bad("action", "marked as free to reach out although another recruiter owns the relationship or contact is blocked")
    if status == "ready" and m.tier not in ("strong", "possible"):
        bad("action", f"marked as free to reach out with tier {m.tier}")
    dnc = world.people[pk].fact("dnc_until")
    if dnc is not None and dnc.value > world.today and job.status == "open" and status != "blocked":
        bad("action", f"asked not to be contacted until {dnc.value}, but the action is {status}")

    # wording
    texts = [m.summary, m.action["label"], *m.action["steps"]] + [t for r in results + gates for t in (r.shows, *r.notes, *r.questions)]
    for t in texts:
        if t and VERIFIED.search(t):
            bad("wording", f"describes something as verified: “{t[:70]}”")
    return out


def check_list(job, rows) -> list[dict]:
    """Checks on a whole ranked list."""
    out = []
    groups: dict = {}
    for m in rows:
        if m.identical > 1:
            groups.setdefault((m.tier, m.signature if not isinstance(m.signature, tuple) else hash(m.signature)), []).append(m)
    for members in groups.values():
        if len({m.outcomes for m in members}) > 1:
            out.append(_finding(job.key, members[0].person, "list", "marked identical to others whose rule outcomes differ"))
        if any(m.identical != len(members) for m in members):
            out.append(_finding(job.key, members[0].person, "list", "the count of identical people does not match the group"))
    for above, m in zip(rows, rows[1:]):
        if GROUP_OF[m.tier] < GROUP_OF[above.tier]:
            out.append(_finding(job.key, m.person, "list", f"listed below {above.person} although its tier group comes first"))
        held = lambda x: GROUP_OF[x.tier] == 0 and x.action["status"] not in ("ready", "route", "clarify")   # noqa: E731
        same_place = GROUP_OF[m.tier] == GROUP_OF[above.tier] and held(m) == held(above)
        if same_place and m.score > above.score:
            out.append(_finding(job.key, m.person, "list", f"scores {m.score} but is listed below a score of {above.score}"))
    return out


def audit(engine, steps=None) -> dict:
    """Run every check at the given replay steps (all of them by default)."""
    steps = range(len(engine.steps)) if steps is None else steps
    findings, checked = [], 0
    for step in steps:
        world = engine.world(step)
        for key, job in world.jobs.items():
            sl = engine.shortlist(step, key)
            rows = sl.ranked()
            findings += [dict(f, step=step) for f in check_list(job, rows)]
            for row in rows:
                m = engine.explain(step, key, row.person)
                findings += [dict(f, step=step) for f in check_match(world, engine.cfg, job, m)]
                checked += 1
    return {"checked": checked, "findings": findings, "tiers": list(TIER_ORDER)}
