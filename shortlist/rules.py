"""The rule engine.

Every rule runs for every candidate and job and returns one Result. A rule the
job says nothing about returns "na" with the reason, so no rule is ever skipped
silently. Fit rules give met / not_met / unknown / na. Permission rules ("gates")
give clear / route / blocked / confirm and never change the fit score.

This module knows nothing about where data came from or where results go.
It reads the resolved world and returns plain results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .lexicon import STATE_NAMES
from .timeutil import WEEKDAY_NAMES, fmt_date, fmt_money, fmt_pay, fmt_span, fmt_years

MET, NOT_MET, UNKNOWN, NA = "met", "not_met", "unknown", "na"
CLEAR, ROUTE, BLOCKED, CONFIRM = "clear", "route", "blocked", "confirm"
OUTCOME_WORD = {MET: "met", NOT_MET: "not met", UNKNOWN: "unknown", NA: "not applicable"}


@dataclass(slots=True)
class Result:
    rule: str
    label: str
    level: str                      # hard | soft | gate
    weight: float
    outcome: str
    credit: float | None            # share of the weight earned; None for gates and "na"
    low: float | None               # credit if every unknown turned out "no"
    high: float | None              # credit if every unknown turned out "yes"
    needs: str                      # what the job asks for
    shows: str                      # what the evidence shows about this person
    job_refs: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    flags: list = field(default_factory=list)      # provisional | pending_clarification | unverified | stale | disputed | replaced | alternative_route
    notes: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    items: list = field(default_factory=list)      # sub-checks: each skill, each shift
    alt: dict | None = None                        # the outcome under the other version of the requirement
    brief: str = ""                                # a few words on this person for this rule, used to say what sets them apart
    measure: dict = field(default_factory=dict)    # the numbers behind the outcome (years, pay gap), for the quality dimensions


class Ctx:
    """Everything a rule needs besides the job and the person."""

    def __init__(self, world, division_cfg: dict, policy: str):
        self.w = world
        self.today = world.today
        self.lex = world.lex
        self.rules_cfg = division_cfg["rules"]
        self.gates_cfg = division_cfg["gates"]
        self.scoring = division_cfg["scoring"]
        self.fresh = division_cfg["freshness_days"]
        self.policy = policy
        self.dims_cfg = division_cfg.get("dimensions", {})

    def ref(self, claim, note: str = "") -> dict:
        return {"evidence": claim.evidence_id, "quote": claim.quote, "note": note}

    def refs(self, fact) -> list[dict]:
        if fact is None:
            return []
        return [self.ref(c) for c in fact.claims] + [self.ref(c, "replaced") for c in fact.older]

    def cite(self, claim) -> str:
        ev = self.w.evidence[claim.evidence_id]
        return f"{ev.label}, {fmt_date(ev.observed_at)}"

    def primary(self, req):
        """(claim in force, other version, True if they differ) for a job requirement, or Nones."""
        return req.pick(self.policy) if req is not None else (None, None, False)


# ------------------------------------------------------------------ helpers
def _res(ctx: Ctx, rule: str, outcome: str, needs: str = "", shows: str = "", level: str | None = None,
         credit: float | None = None, low: float | None = None, high: float | None = None, **kw) -> Result:
    rc = ctx.rules_cfg[rule]
    lvl = level or rc["level"]
    if lvl == "from_job":
        lvl = "hard"
    if outcome == NA:
        credit = low = high = None
    elif credit is None:
        credit = {MET: 1.0, NOT_MET: 0.0, UNKNOWN: float(ctx.scoring["unknown_credit"])}[outcome]
        low = {MET: 1.0, NOT_MET: 0.0, UNKNOWN: 0.0}[outcome]
        high = {MET: 1.0, NOT_MET: 0.0, UNKNOWN: 1.0}[outcome]
    return Result(rule, rc["label"], lvl, float(rc.get("weight", 1)), outcome, credit, low, high, needs, shows, **kw)


def _gate(ctx: Ctx, gate: str, outcome: str, shows: str, **kw) -> Result:
    return Result(gate, ctx.gates_cfg[gate]["label"], "gate", 0.0, outcome, None, None, None, "", shows, **kw)


def _a(word: str) -> str:
    """The word with its article: "a Washington", "an Oregon"."""
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _first_name(p) -> str:
    return p.name if p.name_basis == "id" else p.name.split()[0]


def _unanswered(ctx: Ctx, fact) -> str:
    """How an open question is described: asked and not answered, or simply absent from a document."""
    latest = fact.claims[0]
    ev = ctx.w.evidence[latest.evidence_id]
    if ev.kind == "recruiter_note":
        return f"asked, with no answer recorded as of {fmt_date(ev.observed_at)}"
    return f"not described in {ev.label} ({fmt_date(ev.observed_at)})"


def _stale(ctx: Ctx, res: Result, fact, cls: str) -> None:
    """Flag a fact that is older than its freshness window. It is still used."""
    if fact is None:
        return
    limit = ctx.fresh.get(cls)
    age = (ctx.today - fact.observed_at.date()).days
    if limit is not None and age > limit:
        res.flags.append("stale")
        res.notes.append(f"Last recorded {fmt_date(fact.observed_at)}, {age} days before the reference date "
                         f"(freshness window: {limit} days). Reconfirm before relying on it.")


def _replaced(ctx: Ctx, res: Result, fact, describe) -> None:
    """Say so when a newer statement replaced an older one."""
    if fact is not None and fact.older:
        old = fact.older[0]
        res.flags.append("replaced")
        res.notes.append(f"An earlier record said {describe(old)} ({ctx.cite(old)}). The later statement is used.")


def _versions(ctx: Ctx, res: Result, primary, other, differs: bool, alt_outcome: str, alt_text: str,
              changed: bool | None = None) -> None:
    """Record what the other version of a requirement would give, and flag the result if it matters."""
    if not differs:
        return
    using_note = primary is not None and primary.version == "clarification"
    res.alt = {"version": "vendor feed" if using_note else "account-manager note", "outcome": alt_outcome,
               "text": alt_text, "ref": ctx.ref(other) if other is not None else None}
    if changed is None:
        changed = alt_outcome != res.outcome
    if not changed:
        return
    if using_note and ctx.policy == "provisional":
        res.flags.append("provisional")
        feed_says = f"Under the vendor feed: {alt_text}." if other is not None else "The vendor feed does not state this."
        res.notes.append(f"Relies on {ctx.cite(primary)}, which the vendor feed does not show yet. {feed_says}")
    elif not using_note and other is not None:
        res.flags.append("pending_clarification")
        res.notes.append(f"A note not yet in the vendor feed would change this ({ctx.cite(other)}): {alt_text}.")


# -------------------------------------------------------------- fit rules
def r_profession(ctx: Ctx, job, p) -> Result:
    req = job.req("req.profession")
    claim = (req.feed or req.clar) if req else None
    if claim is None:
        return _res(ctx, "profession", NA, needs="The posting does not name a profession")
    needs = ctx.lex.label(claim.value)
    fact = p.fact("profession")
    if fact is None or p.profession is None:
        return _res(ctx, "profession", UNKNOWN, needs, "No profession on record", job_refs=[ctx.ref(claim)],
                    questions=[f"Confirm {_first_name(p)}'s profession"])
    ok = p.profession == claim.value
    return _res(ctx, "profession", MET if ok else NOT_MET, needs, str(fact.value), job_refs=[ctx.ref(claim)], refs=ctx.refs(fact))


def _judge_experience(exp, min_years) -> str:
    if exp is None or exp.state == "unanswered":
        return UNKNOWN
    if exp.state == "neg":
        return NOT_MET
    documented, claimed = exp.value["years_documented"], exp.value["years_claimed"]
    if min_years is None:
        return MET
    if documented is not None:
        if documented >= min_years:
            return MET
        return UNKNOWN if (claimed is not None and claimed >= min_years) else NOT_MET
    upper, lower = exp.value.get("years_max"), exp.value.get("years_min")
    if upper is not None:
        return NOT_MET if upper < min_years or (upper == min_years and not exp.value.get("inclusive")) else UNKNOWN
    if lower is not None:
        return MET if lower >= min_years else UNKNOWN
    if claimed is not None:
        return MET if claimed >= min_years else NOT_MET
    return UNKNOWN


def _experience_text(ctx: Ctx, setting: str, exp) -> str:
    lab = ctx.lex.label(setting)
    if exp is None:
        return f"No {lab} work on record"
    if exp.state == "unanswered":
        return f"{lab.capitalize()} experience not answered"
    if exp.state == "neg":
        return f"Says they have no {lab} experience"
    v = exp.value
    parts = []
    if "years_max" in v or "years_min" in v:
        upper = "years_max" in v
        word = ("at most" if v.get("inclusive") else "less than") if upper else ("at least" if v.get("inclusive") else "more than")
        parts.append(f"Reports {word} {fmt_years(v['years_max' if upper else 'years_min'])} in {lab}")
    if v["years_documented"] is not None:
        span = fmt_span(v["years_documented"])
        if v["precision"] == "year":
            parts.append(f"{lab.capitalize()} since {v['since']}: at least {span}")
        else:
            parts.append(f"{lab.capitalize()} since {v['since']}: {span} as of {fmt_date(ctx.today)}")
    elif v["years_claimed"] is None and not parts:
        parts.append(f"{lab.capitalize()} is mentioned, with no dates")
    if v["years_claimed"] is not None:
        parts.append(f"says {fmt_years(v['years_claimed'])}")
    return "; ".join(parts)


def r_setting_experience(ctx: Ctx, job, p) -> Result:
    sreq = job.req("req.setting")
    sclaim = (sreq.feed or sreq.clar) if sreq else None
    if sclaim is None:
        return _res(ctx, "setting_experience", NA, needs="The posting does not name a care setting")
    setting = sclaim.value
    lab = ctx.lex.label(setting)
    primary, other, differs = ctx.primary(job.req("req.min_years"))
    min_years = primary.value["years"] if primary else None
    level = ctx.rules_cfg["setting_experience"]["level"]
    if level == "from_job":
        level = "soft" if (primary and primary.value.get("level") == "preferred") else "hard"
    exp = p.fact("experience", setting)
    outcome = _judge_experience(exp, min_years)
    if min_years is None:
        needs = f"{lab.capitalize()} experience (no minimum stated)"
    else:
        needs = f"{lab.capitalize()} experience: {fmt_years(min_years)} {'preferred' if level == 'soft' else 'minimum'}"
    res = _res(ctx, "setting_experience", outcome, needs, _experience_text(ctx, setting, exp), level=level,
               job_refs=[ctx.ref(sclaim)] + ([ctx.ref(primary)] if primary else []), refs=ctx.refs(exp))
    res.measure = {"min_years": min_years, "setting": setting,
                   **({"documented": exp.value["years_documented"], "claimed": exp.value["years_claimed"],
                       "upper": exp.value.get("years_max"), "lower": exp.value.get("years_min")} if exp is not None and exp.state == "pos" else {})}
    who = _first_name(p)
    others = [ctx.lex.label(k) for k, f in p.keyed("experience").items() if f.state == "pos" and k != setting]
    if exp is None:
        if others:
            res.notes.append("Other settings on record: " + ", ".join(others) + ".")
        res.questions.append(f"Ask {who} about {lab} work: roles and dates")
    elif exp.state == "unanswered":
        if others:
            res.notes.append("Other settings on record: " + ", ".join(others) + ".")
        res.questions.append(f"Follow up with {who} on {lab} work: {_unanswered(ctx, exp)}")
    elif exp.state == "pos":
        v = exp.value
        if v["years_documented"] is not None and v["years_claimed"] is not None and abs(v["years_documented"] - v["years_claimed"]) > 0.25:
            res.flags.append("disputed")
            res.notes.append(f"Dated history shows {fmt_span(v['years_documented'])}; {who} says {fmt_years(v['years_claimed'])}. Not reconciled.")
            if outcome == UNKNOWN:
                res.questions.append(f"Ask {who} for earlier {lab} employers and dates: the answer decides this rule")
        elif v["years_documented"] is None and v["years_claimed"] is None and min_years is not None:
            res.questions.append(f"Ask {who} for the dates of their {lab} work")
    if differs:
        alt_min = other.value["years"] if other is not None else None
        alt_outcome = _judge_experience(exp, alt_min)
        alt_text = (f"{fmt_years(alt_min)} minimum, so this rule would be {OUTCOME_WORD[alt_outcome]}" if alt_min is not None
                    else f"no minimum, so this rule would be {OUTCOME_WORD[alt_outcome]}")
        _versions(ctx, res, primary, other, differs, alt_outcome, alt_text)
        if "provisional" in res.flags and alt_outcome == UNKNOWN and "disputed" in res.flags:
            res.questions.append(f"Ask {who} for earlier {lab} employers and dates: it matters if the vendor feed's minimum applies")
    _stale(ctx, res, exp if exp and exp.state == "pos" else None, "experience")
    return res


def r_skills(ctx: Ctx, job, p) -> Result:
    reqs = job.reqs_of("req.skill")
    if not reqs:
        return _res(ctx, "skills", NA, needs="The posting lists no skills or tools")
    unknown_credit = float(ctx.scoring["unknown_credit"])
    pref_weight = float(ctx.scoring["preferred_item_weight"])
    who = _first_name(p)

    def items_for(use_primary: bool) -> list[dict]:
        out = []
        for key, req in reqs.items():
            primary, other, differs = ctx.primary(req)
            claim = primary if (use_primary or not differs) else other
            if claim is None:
                continue
            fact = p.fact("skill", key)
            state = fact.state if fact else None
            outcome = MET if state == "pos" else NOT_MET if state == "neg" else UNKNOWN
            out.append({"key": key, "level": claim.value.get("level", "required"), "outcome": outcome,
                        "fact": fact, "claim": claim, "differs": differs, "other": other})
        return out

    def aggregate(items: list[dict]):
        required = [i for i in items if i["level"] == "required"]
        base = required or items
        outcome = NOT_MET if any(i["outcome"] == NOT_MET for i in base) else UNKNOWN if any(i["outcome"] == UNKNOWN for i in base) else MET
        credits = []
        for uc in (unknown_credit, 0.0, 1.0):
            num = den = 0.0
            for i in items:
                w = 1.0 if i["level"] == "required" else pref_weight
                den += w
                num += w * {MET: 1.0, NOT_MET: 0.0, UNKNOWN: uc}[i["outcome"]]
            credits.append(num / den if den else 0.0)
        return outcome, credits, ("hard" if required else "soft")

    items = items_for(True)
    if not items:
        return _res(ctx, "skills", NA, needs="The vendor feed lists no skills or tools")
    outcome, (credit, low, high), level = aggregate(items)
    if ctx.rules_cfg["skills"]["level"] != "from_job":
        level = ctx.rules_cfg["skills"]["level"]
    required = [ctx.lex.label(i["key"]) for i in items if i["level"] == "required"]
    preferred = [ctx.lex.label(i["key"]) for i in items if i["level"] != "required"]
    needs = "; ".join(x for x in ("Required: " + ", ".join(required) if required else "",
                                  "Preferred: " + ", ".join(preferred) if preferred else "") if x)
    words = {MET: "described", NOT_MET: "says not used", UNKNOWN: "not on record"}
    shows = "; ".join(f"{ctx.lex.label(i['key'])}: " + ("not answered" if i["fact"] is not None and i["fact"].state == "unanswered" else words[i["outcome"]])
                      for i in items)
    res = _res(ctx, "skills", outcome, needs, shows, level=level, credit=credit, low=low, high=high)
    for i in items:
        lab, fact = ctx.lex.label(i["key"]), i["fact"]
        res.job_refs.append(ctx.ref(i["claim"]))
        res.refs.extend(ctx.refs(fact))
        res.items.append({"label": lab, "level": i["level"], "outcome": i["outcome"],
                          "shows": "not answered" if fact is not None and fact.state == "unanswered" else words[i["outcome"]]})
        if fact is not None and fact.older:
            old = fact.older[0]
            res.flags.append("replaced")
            res.notes.append(f"{lab}: an earlier record listed it ({ctx.cite(old)}); the later statement says it has not been used ({ctx.cite(fact.claims[0])}). The later statement is used."
                             if fact.state == "neg" else
                             f"{lab}: an earlier record said otherwise ({ctx.cite(old)}). The later statement is used.")
        if i["outcome"] == UNKNOWN and i["level"] == "required":
            if fact is not None and fact.state == "unanswered":
                res.questions.append(f"Follow up with {who} on {lab}: {_unanswered(ctx, fact)}")
            else:
                res.questions.append(f"Ask {who} about {lab}")
    if any(i["differs"] for i in items):
        alt_items = items_for(False)
        alt_outcome, (alt_credit, _, _), alt_level = aggregate(alt_items) if alt_items else (NA, (None, None, None), level)
        changes = [f"{ctx.lex.label(i['key'])} is {i['level']}" for i in alt_items if i["differs"]]
        alt_text = (", ".join(changes) + f", so this rule would be {OUTCOME_WORD[alt_outcome]}") if changes else f"this rule would be {OUTCOME_WORD[alt_outcome]}"
        differing = next(i for i in items if i["differs"])
        changed = alt_outcome != outcome or alt_level != level or (alt_credit is not None and abs(alt_credit - credit) > 1e-9)
        _versions(ctx, res, differing["claim"], differing["other"], True, alt_outcome, alt_text, changed=changed)
    res.flags = list(dict.fromkeys(res.flags))
    return res


def r_license(ctx: Ctx, job, p) -> Result:
    reqs = job.reqs_of("req.license")
    if not reqs:
        return _res(ctx, "license", NA, needs="The posting names no license")
    who = _first_name(p)
    held = [STATE_NAMES.get(k, k) for k, f in p.keyed("license").items() if f.state == "pos"]
    outcomes, needs, shows, refs, job_refs, questions = [], [], [], [], [], []
    for state, req in reqs.items():
        claim = ctx.primary(req)[0] or req.feed or req.clar
        name = STATE_NAMES.get(state, state)
        fact = p.fact("license", state)
        job_refs.append(ctx.ref(claim))
        needs.append(f"{name} license, active before the start")
        if fact is not None and fact.state == "pos":
            outcomes.append(MET)
            shows.append(f"Reports {'an active ' + name if fact.value.get('active') else _a(name)} license")
            refs.extend(ctx.refs(fact))
        else:
            outcomes.append(UNKNOWN)
            shows.append(f"No {name} license on record" + (f" (reports: {', '.join(held)})" if held else ""))
            questions.append(f"Ask {who} whether they hold {_a(name)} license, or can before the start")
    outcome = UNKNOWN if UNKNOWN in outcomes else MET
    res = _res(ctx, "license", outcome, "; ".join(needs), "; ".join(shows), job_refs=job_refs, refs=refs, questions=questions)
    if outcome == MET:
        res.flags.append("unverified")
        res.notes.append("Reported by the candidate. No license number or expiry date is on file: verify before submitting.")
    return res


def r_certifications(ctx: Ctx, job, p) -> Result:
    res = _certifications(ctx, job, p)
    # A record's specialty label and the reported board certification can tell different stories: show both.
    specialty = p.fact("specialty")
    held = [k for k, f in p.keyed("cert").items() if f.state == "pos"]
    mapping = ctx.w.cfg.get("board_specialty", {})
    boards = {k[6:] for k in held if k.startswith("board:")} | {mapping[k] for k in held if k in mapping}
    if res.outcome != NA and specialty is not None and boards:
        label = str(specialty.value).strip().lower().replace(" ", "_").replace("-", "_")
        if label not in boards:
            res.notes.append(f"The record lists the specialty as {specialty.value} ({ctx.cite(specialty.claims[0])}); "
                             f"the reported board certification is {', '.join(sorted(b.replace('_', ' ') for b in boards))}.")
            res.refs.extend(ctx.refs(specialty))
    return res


def _certifications(ctx: Ctx, job, p) -> Result:
    paths = job.reqs_of("req.cert_path")
    if not paths:
        return _res(ctx, "certifications", NA, needs="The posting names no board or certificate")
    who = _first_name(p)
    held = {k: f for k, f in p.keyed("cert").items() if f.state == "pos"}
    pref = paths.get("preferred")
    alt = paths.get("alternative")
    pclaim = (pref.feed or pref.clar) if pref else None
    aclaim = (alt.feed or alt.clar) if alt else None
    needs = []
    if pclaim:
        needs.append(" or ".join(ctx.lex.label(k) for k in pclaim.value["any_of"]) + " preferred")
    if aclaim:
        needs.append("other boards considered with " + ", ".join(ctx.lex.label(k) for k in aclaim.value["all_of"]))
    job_refs = [ctx.ref(c) for c in (pclaim, aclaim) if c]
    if pclaim and any(k in held for k in pclaim.value["any_of"]):
        got = [k for k in pclaim.value["any_of"] if k in held]
        res = _res(ctx, "certifications", MET, "; ".join(needs), "Reports " + ", ".join(ctx.lex.label(k) for k in got) + " certification",
                   job_refs=job_refs, refs=[r for k in got for r in ctx.refs(held[k])])
        res.flags.append("unverified")
        res.notes.append("Reported by the candidate. No certificate number or expiry date is on file: verify before submitting.")
        return res
    if aclaim:
        boards = [k for k in held if k.startswith("board:")]
        missing = [k for k in aclaim.value["all_of"] if k not in held]
        if boards and not missing:
            used = boards + list(aclaim.value["all_of"])
            # Met, but the preferred board is not held. It counts as a preferred item that is missing, by the
            # same weight a preferred skill has, so someone with the preferred board scores higher.
            part = 1.0 / (1.0 + float(ctx.scoring.get("preferred_item_weight", 0.5))) if pclaim else 1.0
            res = _res(ctx, "certifications", MET, "; ".join(needs),
                       "Reports " + ctx.lex.label(boards[0]) + " with " + ", ".join(ctx.lex.label(k) for k in aclaim.value["all_of"]),
                       credit=part, low=part, high=part,
                       job_refs=job_refs, refs=[r for k in used for r in ctx.refs(held[k])][:6])
            res.flags += ["alternative_route", "unverified"]
            res.notes.append("Qualifies by the other-boards route, not the preferred board certification.")
            res.notes.append("Reported by the candidate. No certificate numbers or expiry dates are on file: verify before submitting.")
            approval = job.req("note.alt_path")
            if approval and approval.clar:
                res.notes.append(f"The facility has not approved any individual on this route yet ({ctx.cite(approval.clar)}).")
                res.questions.append("Ask the account manager whether the facility will accept this candidate on the other-boards route")
            return res
        gaps = (["a board certification"] if not boards else []) + [ctx.lex.label(k) for k in missing]
        return _res(ctx, "certifications", UNKNOWN, "; ".join(needs), "Not on record: " + ", ".join(gaps), job_refs=job_refs,
                    refs=[r for f in held.values() for r in ctx.refs(f)][:6],
                    questions=[f"Ask {who} about: " + ", ".join(gaps)])
    return _res(ctx, "certifications", UNKNOWN, "; ".join(needs), "The preferred certification is not on record", job_refs=job_refs,
                questions=[f"Ask {who} about board certification"])


def r_location(ctx: Ctx, job, p) -> Result:
    loc = job.location
    city, state = loc.get("city"), loc.get("state")
    if not city and not state:
        return _res(ctx, "location", NA, needs="The posting gives no location")
    state_name = STATE_NAMES.get(state, state)
    needs = ", ".join(x for x in (city, state_name) if x)
    job_refs = [ctx.ref(job.meta["location"].claims[0])]
    who = _first_name(p)
    home, prefs, travel = p.fact("home"), p.fact("pref_states"), p.fact("travel")
    interest = next((f for k, f in p.keyed("location_interest").items() if city and k.lower() == city.lower()), None)

    def by_record():
        """What the structured home and preference fields say on their own."""
        names = ", ".join(STATE_NAMES.get(s, s) for s in prefs.value) if prefs else ""
        if home and home.value.get("state") == state:
            return MET, f"Lives in {home.value.get('city')}, {state_name}", home
        if prefs and state in prefs.value:
            return MET, f"Prefers {names}", prefs
        if prefs:
            if travel is not None and travel.value is False:
                return NOT_MET, f"Prefers {names} only and is recorded as not willing to travel", prefs
            return UNKNOWN, f"Prefers {names}; nothing recorded about {state_name}", prefs
        if home:
            return UNKNOWN, f"Lives in {home.value.get('city')}, {STATE_NAMES.get(home.value.get('state'), home.value.get('state'))}; nothing recorded about {state_name}", home
        return UNKNOWN, "No location preference on record", None

    record_outcome, record_text, record_fact = by_record()
    if interest is not None:
        said = interest.claims[0]
        if interest.state == "neg":
            res = _res(ctx, "location", NOT_MET, needs, f"Said {city} is no longer a location they would consider", job_refs=job_refs, refs=ctx.refs(interest))
            if interest.older:
                res.flags.append("replaced")
                res.notes.append(f"An earlier statement said they were open to {city} ({ctx.cite(interest.older[0])}). The later statement is used.")
        else:
            res = _res(ctx, "location", MET, needs, f"Said they are open to {city}", job_refs=job_refs, refs=ctx.refs(interest))
            if interest.older:
                res.flags.append("replaced")
                res.notes.append(f"An earlier statement ruled {city} out ({ctx.cite(interest.older[0])}). The later statement is used.")
            if record_outcome != MET and record_fact is not None and record_fact.observed_at < said.observed_at:
                res.flags.append("replaced")
                res.notes.append(f"An older record disagrees: {record_text[0].lower() + record_text[1:]} ({ctx.cite(record_fact.claims[0])}). The candidate's later message is used.")
                res.refs.extend(ctx.ref(c, "replaced") for f in (prefs, travel) if f for c in f.claims)
        _stale(ctx, res, interest, "location")
        return res
    res = _res(ctx, "location", record_outcome, needs, record_text, job_refs=job_refs,
               refs=[r for f in (home, prefs, travel) if f for r in ctx.refs(f)])
    if record_outcome == UNKNOWN:
        res.questions.append(f"Ask {who} whether they would work in {needs}")
    _stale(ctx, res, record_fact, "location")
    return res


def r_start_date(ctx: Ctx, job, p) -> Result:
    claim = ctx.primary(job.req("req.start"))[0]
    if claim is None:
        return _res(ctx, "start_date", NA, needs="The posting has no single start date")
    need = claim.value
    needs = f"Start {fmt_date(need)}"
    who = _first_name(p)
    have = p.fact("available_from")
    if have is None:
        return _res(ctx, "start_date", UNKNOWN, needs, "No start date on record", job_refs=[ctx.ref(claim)],
                    questions=[f"Ask {who} when they can start (the job starts {fmt_date(need)})"])
    late = (have.value - need).days
    if late <= int(ctx.scoring.get("start_tolerance_days", 0)):
        res = _res(ctx, "start_date", MET, needs, f"Can start {fmt_date(have.value)}", job_refs=[ctx.ref(claim)], refs=ctx.refs(have))
    else:
        res = _res(ctx, "start_date", NOT_MET, needs, f"Can start {fmt_date(have.value)}: {late} day{'s' if late != 1 else ''} after the start",
                   job_refs=[ctx.ref(claim)], refs=ctx.refs(have))
        firm = job.req("note.start_firm")
        if firm and firm.clar:
            res.notes.append(f"No exception to the start date is confirmed ({ctx.cite(firm.clar)}).")
        res.questions.append(f"Ask the account manager whether starting on {fmt_date(have.value)} would be accepted")
    _replaced(ctx, res, have, lambda c: fmt_date(c.value))
    _stale(ctx, res, have, "availability")
    return res


def r_schedule(ctx: Ctx, job, p) -> Result:
    wanted = {attr: ctx.primary(job.req(attr))[0] for attr in ("req.schedule", "req.hours", "req.weeks")}
    wanted = {a: c for a, c in wanted.items() if c is not None}
    if not wanted:
        return _res(ctx, "schedule", NA, needs="The posting gives no working pattern or length")
    who = _first_name(p)
    checks = []
    if "req.schedule" in wanted:
        f = p.fact("schedule")
        need = wanted["req.schedule"].value
        checks.append(("working pattern", need.capitalize(), f, None if f is None else str(f.value).lower() == str(need).lower(),
                       None if f is None else str(f.value).capitalize()))
    if "req.hours" in wanted:
        f = p.fact("hours")
        need = wanted["req.hours"].value
        checks.append(("weekly hours", f"{need} hours a week", f, None if f is None else f.value >= need, None if f is None else f"{f.value} hours"))
    if "req.weeks" in wanted:
        f = p.fact("weeks")
        need = wanted["req.weeks"].value
        checks.append(("contract length", f"{need} weeks", f, None if f is None else f.value >= need, None if f is None else f"{f.value} weeks"))
    # The working pattern and the length decide the rule. Weekly hours left unsaid raise a question but do not hold a match back.
    blocking = [ok for name, _, _, ok, _ in checks if name != "weekly hours"]
    outcome = NOT_MET if any(ok is False for *_, ok, _ in checks) else UNKNOWN if any(ok is None for ok in blocking) else MET
    stated = [text for _, _, f, _, text in checks if f is not None]
    missing = [name for name, _, f, _, _ in checks if f is None]
    shows = ", ".join(stated) if stated else "Nothing stated"
    if missing:
        shows += ". Not stated: " + ", ".join(missing)
    res = _res(ctx, "schedule", outcome, ", ".join(n for _, n, _, _, _ in checks), shows,
               job_refs=[ctx.ref(c) for c in wanted.values()], refs=[r for _, _, f, _, _ in checks if f for r in ctx.refs(f)])
    for name, need, f, ok, text in checks:
        res.items.append({"label": name.capitalize(), "level": "required", "outcome": MET if ok else UNKNOWN if ok is None else NOT_MET,
                          "shows": text or "not stated"})
    if missing:
        res.questions.append(f"Ask {who} to confirm: " + ", ".join(missing))
    _stale(ctx, res, next((f for _, _, f, _, _ in checks if f is not None), None), "availability")
    return res


def r_shift_coverage(ctx: Ctx, job, p) -> Result:
    claim = ctx.primary(job.req("req.shifts"))[0]
    if claim is None:
        return _res(ctx, "shift_coverage", NA, needs="The posting lists no individual shifts")
    who = _first_name(p)
    occupied, placement = p.fact("occupied_days"), p.fact("placement")
    placed = placement is not None and placement.value.get("status") == "placed"
    busy_names = "–".join(WEEKDAY_NAMES[d] for d in (occupied.value[0], occupied.value[-1])) if occupied else ""
    res_items, refs, outcomes, facts = [], [], [], []
    for s in claim.value:
        start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
        fact = p.fact("shift_availability", start.date().isoformat())
        weekday = WEEKDAY_NAMES[start.weekday()]
        if fact is not None and fact.state == "neg":
            outcome, text = NOT_MET, "Said they cannot work this date"
        elif fact is not None and fact.state == "pos":
            if occupied and start.weekday() in occupied.value:
                outcome, text = UNKNOWN, f"Offered this date, but the current assignment covers {weekday}s"
            else:
                outcome, text = MET, "Offered to cover this date"
        elif placed and occupied and start.weekday() not in occupied.value:
            outcome, text = UNKNOWN, f"Current assignment is {busy_names}, so {weekday} looks free; not confirmed by the candidate"
        elif placed:
            outcome, text = UNKNOWN, "On a placement; the days worked are not recorded"
        else:
            outcome, text = UNKNOWN, "No answer for this date"
        if fact is not None:
            refs.extend(ctx.refs(fact))
            facts.append(fact)
        outcomes.append(outcome)
        res_items.append({"label": f"{weekday} {fmt_date(start.date())}, {start:%H:%M}–{end:%H:%M} local", "level": "required",
                          "outcome": outcome, "shows": text})
    outcome = NOT_MET if NOT_MET in outcomes else UNKNOWN if UNKNOWN in outcomes else MET
    covered = outcomes.count(MET)
    offered = {1: "Offered to cover the shift", 2: "Offered both shifts"}.get(len(outcomes), f"Offered all {len(outcomes)} shifts")
    summary = {MET: offered, NOT_MET: "Cannot work " + ("these dates" if covered == 0 else "every shift"),
               UNKNOWN: f"{covered} of {len(outcomes)} shifts confirmed"}[outcome]
    seen, unique_refs = set(), []
    for r in refs:
        if (r["evidence"], r["quote"]) not in seen:
            seen.add((r["evidence"], r["quote"]))
            unique_refs.append(r)
    res = _res(ctx, "shift_coverage", outcome, f"{len(outcomes)} shift{'s' if len(outcomes) != 1 else ''}: " + "; ".join(i["label"] for i in res_items),
               summary, job_refs=[ctx.ref(claim)], refs=unique_refs, items=res_items)
    if occupied:
        res.refs.extend(ctx.refs(occupied))
        res.notes.append(f"Current assignment is {busy_names}, as reported by the candidate.")
    if placed:
        until = placement.value.get("until")
        res.refs.extend(ctx.refs(placement))
        res.notes.append("The record shows a placement" + (f" until {fmt_date(until)}" if until else "") +
                         ". A placed status does not say which days are taken.")
    if outcome == MET:
        res.flags.append("unverified")
        res.notes.append("Availability is the candidate's own statement and has not been checked against the placement schedule.")
    if outcome == UNKNOWN:
        res.questions.append(f"Ask {who} whether they can cover: " + "; ".join(i["label"] for i, o in zip(res_items, outcomes) if o == UNKNOWN))
    partial = job.req("note.partial_cover")
    if 0 < covered < len(outcomes) and partial and partial.clar:
        res.notes.append(f"Whether the facility accepts partial cover is not confirmed ({ctx.cite(partial.clar)}).")
        res.questions.append("Ask the account manager whether the facility would accept partial cover")
    _stale(ctx, res, facts[0] if facts else None, "availability")
    return res


def r_pay(ctx: Ctx, job, p) -> Result:
    claim = ctx.primary(job.req("req.pay"))[0]
    if claim is None:
        return _res(ctx, "pay", NA, needs="The posting states no pay")
    offer = claim.value
    needs = f"Approved candidate pay {fmt_pay(offer)}" + (f" for {offer['hours']} hours" if offer.get("hours") else "")
    who = _first_name(p)
    have = p.fact("pay")
    if have is None:
        return _res(ctx, "pay", UNKNOWN, needs, "No pay expectation on record", job_refs=[ctx.ref(claim)],
                    questions=[f"Ask {who} for their pay expectation (the job pays {fmt_pay(offer)})"])
    ask = have.value
    if ask["unit"] != offer["unit"] and {ask["unit"], offer["unit"]} != {"week", "hour"}:
        # Only weekly and hourly pay can be converted into each other. Anything else is a question, not a guess.
        return _res(ctx, "pay", UNKNOWN, needs, f"Asks {fmt_pay(ask)}, which cannot be compared with pay per {offer['unit']}",
                    job_refs=[ctx.ref(claim)], refs=ctx.refs(have), questions=[f"Ask {who} what they need per {offer['unit']}"])
    amount = float(ask["amount"])
    asks = fmt_pay(ask)
    if ask["unit"] != offer["unit"]:                       # compare like with like, in the job's unit
        hours_req = ctx.primary(job.req("req.hours"))[0]
        hours = offer.get("hours") or ask.get("hours") or (hours_req.value if hours_req else 40)
        amount = amount * hours if offer["unit"] == "week" else amount / hours
        asks += f" ({fmt_money(amount)}/{offer['unit']} at {hours} hours a week)"
    gap = amount - float(offer["amount"])
    over = gap / float(offer["amount"])
    word = "Needs at least" if ask.get("qualifier") == "minimum" else "Expects"
    if gap <= 0:
        tail = "the approved rate" if gap == 0 else f"{fmt_money(-gap)}/{offer['unit']} under the approved rate"
        res = _res(ctx, "pay", MET, needs, f"{word} {asks}: {tail}", job_refs=[ctx.ref(claim)], refs=ctx.refs(have))
    else:
        res = _res(ctx, "pay", NOT_MET, needs,
                   f"{word} {asks}: {fmt_money(gap)}/{offer['unit']} ({gap / float(offer['amount']):.1%}) above the approved rate",
                   job_refs=[ctx.ref(claim)], refs=ctx.refs(have))
        res.questions.append(f"Ask whether {who} would accept {fmt_pay(offer)}, or the account manager whether the rate can move")
    res.measure = {"over": over}
    basis = job.req("note.pay_basis")
    if basis and basis.clar:
        res.notes.append(f"The job figure is candidate pay, not the bill rate ({ctx.cite(basis.clar)}).")
    _replaced(ctx, res, have, lambda c: fmt_pay(c.value))
    _stale(ctx, res, have, "pay")
    return res


# -------------------------------------------------- permission rules (gates)
def g_job_open(ctx: Ctx, job, p, results) -> Result:
    fact = job.meta.get("status")
    if job.status == "open":
        return _gate(ctx, "job_open", CLEAR, "The job is open", refs=ctx.refs(fact))
    return _gate(ctx, "job_open", BLOCKED, f"The job is {job.status}: nobody is recommended for it", refs=ctx.refs(fact))


def _job_begins(ctx: Ctx, job):
    """The day the job starts: its start date, or its first shift. None when the posting gives neither."""
    start = ctx.primary(job.req("req.start"))[0]
    if start is not None:
        return start.value
    shifts = ctx.primary(job.req("req.shifts"))[0]
    if shifts is not None and shifts.value:
        return min(datetime.fromisoformat(s["start"]).date() for s in shifts.value)
    return None


def g_contact_permission(ctx: Ctx, job, p, results) -> Result:
    dnc, allowed, none = p.fact("dnc_until"), p.fact("contact_allowed"), p.fact("restriction_none")
    if dnc is not None and dnc.value > ctx.today:
        until = "with no end date given" if dnc.value.year == 9999 else f"until {fmt_date(dnc.value)}"
        res = _gate(ctx, "contact_permission", BLOCKED, f"Asked not to be contacted about jobs {until}", refs=ctx.refs(dnc))
        begins = _job_begins(ctx, job)
        if begins is not None and dnc.value > begins:
            res.notes.append(f"The request lasts past this job's start ({fmt_date(begins)}), so the person cannot be approached for it.")
        if allowed is not None and allowed.value and allowed.observed_at < dnc.observed_at:
            res.flags.append("replaced")
            res.notes.append(f"An older field still says contact is allowed ({ctx.cite(allowed.claims[0])}). The candidate's later request is used.")
            res.refs.extend(ctx.ref(c, "replaced") for c in allowed.claims)
    elif allowed is not None and allowed.value is False:
        res = _gate(ctx, "contact_permission", BLOCKED, "The record says contact is not allowed", refs=ctx.refs(allowed))
    elif allowed is not None and allowed.value:
        res = _gate(ctx, "contact_permission", CLEAR, "The record says contact is allowed", refs=ctx.refs(allowed))
    elif dnc is not None:
        res = _gate(ctx, "contact_permission", CLEAR, f"An earlier no-contact request ended on {fmt_date(dnc.value)}", refs=ctx.refs(dnc))
    elif none is not None:
        res = _gate(ctx, "contact_permission", CLEAR, "No contact restriction is recorded", refs=ctx.refs(none))
    else:
        res = _gate(ctx, "contact_permission", CLEAR, "No contact restriction is on record")
    auto = p.fact("automation")
    if auto is not None:
        res.notes.append(f"No authorization for automated outreach is recorded ({ctx.cite(auto.claims[0])}).")
        res.refs.extend(ctx.refs(auto))
    return res


def g_ownership(ctx: Ctx, job, p, results) -> Result:
    route, owner = p.fact("route_via"), p.fact("owner")
    who = route.value if route is not None else (owner.value if owner is not None else None)
    if who:
        refs = ctx.refs(route) + [r for r in ctx.refs(owner) if r not in ctx.refs(route)]
        return _gate(ctx, "ownership", ROUTE, f"Opportunities go through recruiter {who}", refs=refs)
    if owner is not None:
        return _gate(ctx, "ownership", CLEAR, "Unassigned: no recruiter owns this relationship", refs=ctx.refs(owner))
    return _gate(ctx, "ownership", CLEAR, "No owner is on record")


def g_requirement_source(ctx: Ctx, job, p, results) -> Result:
    dependent = [r for r in results if "provisional" in r.flags]
    if dependent:
        names = ", ".join(r.label.lower() for r in dependent)
        return _gate(ctx, "requirement_source", CONFIRM, f"This result relies on a note the vendor feed does not show yet ({names})",
                     refs=[r.alt["ref"] for r in dependent if r.alt and r.alt.get("ref")] + [ref for r in dependent for ref in r.job_refs][:4],
                     questions=["Confirm the requirement change with the account manager, and have the feed updated, before submitting"])
    pending = [r for r in results if "pending_clarification" in r.flags]
    if pending:
        return _gate(ctx, "requirement_source", CLEAR, "Uses the vendor feed as it stands. A pending note would change: " + ", ".join(r.label.lower() for r in pending),
                     questions=["Ask the account manager to have the vendor feed updated: a facility clarification would change results for this job"])
    return _gate(ctx, "requirement_source", CLEAR, "Nothing in this result depends on a requirement the feed and a note state differently")


# ------------------------------------------------------------------ registry
# name -> (function, every outcome the rule can return). The test suite proves each outcome is reachable.
RULES = {
    "profession": (r_profession, (MET, NOT_MET, UNKNOWN, NA)),
    "setting_experience": (r_setting_experience, (MET, NOT_MET, UNKNOWN, NA)),
    "skills": (r_skills, (MET, NOT_MET, UNKNOWN, NA)),
    "license": (r_license, (MET, UNKNOWN, NA)),
    "certifications": (r_certifications, (MET, UNKNOWN, NA)),
    "location": (r_location, (MET, NOT_MET, UNKNOWN, NA)),
    "start_date": (r_start_date, (MET, NOT_MET, UNKNOWN, NA)),
    "schedule": (r_schedule, (MET, NOT_MET, UNKNOWN, NA)),
    "shift_coverage": (r_shift_coverage, (MET, NOT_MET, UNKNOWN, NA)),
    "pay": (r_pay, (MET, NOT_MET, UNKNOWN, NA)),
}
GATES = {
    "job_open": (g_job_open, (CLEAR, BLOCKED)),
    "contact_permission": (g_contact_permission, (CLEAR, BLOCKED)),
    "ownership": (g_ownership, (CLEAR, ROUTE)),
    "requirement_source": (g_requirement_source, (CLEAR, CONFIRM)),
}


LEVELS = ("hard", "soft", "from_job")
POLICIES = ("provisional", "trusted", "feed_only")
DIMENSION_NAMES = ("setting", "skills", "depth", "evidence", "readiness", "pay")


def check_config(cfg: dict) -> None:
    """Fail early if the configuration names a rule the engine does not have, or leaves one out."""
    for section, registry in (("rules", RULES), ("gates", GATES)):
        configured, known = set(cfg.get(section, {})), set(registry)
        if configured != known:
            raise ValueError(f"[{section}] in the configuration must list exactly {sorted(known)}; "
                             f"unknown: {sorted(configured - known)}, missing: {sorted(known - configured)}")
    # A slip in a setting must stop the tool with a clear message. Left unchecked, a mistyped level turns a
    # required rule off, and a mistyped policy uses a note with no flag.
    def bad(where, value, want):
        raise ValueError(f"{where} in the configuration is {value!r}; it must be {want}")

    def number(where, value, low=None, high=None):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
            bad(where, value, "a number")
        if (low is not None and value < low) or (high is not None and value > high):
            bad(where, value, f"between {low} and {high}" if high is not None else f"{low} or more")

    for name, rc in cfg.get("rules", {}).items():
        if rc.get("level") not in LEVELS:
            bad(f"[rules.{name}] level", rc.get("level"), "one of " + ", ".join(LEVELS))
    policy = cfg.get("requirement_changes", {}).get("policy")
    if policy not in POLICIES:
        bad("[requirement_changes] policy", policy, "one of " + ", ".join(POLICIES))
    scoring = cfg.get("scoring", {})
    number("[scoring] unknown_credit", scoring.get("unknown_credit"), 0, 1)
    number("[scoring] strong_threshold", scoring.get("strong_threshold"), 0, 100)
    number("[scoring] tie_margin", scoring.get("tie_margin"), 0)
    number("[scoring] preferred_item_weight", scoring.get("preferred_item_weight"), 0)
    number("[scoring] start_tolerance_days", scoring.get("start_tolerance_days", 0), 0)
    tables = [("[dimensions]", cfg.get("dimensions", {}))]
    tables += [(f"[divisions.{d}.dimensions]", v["dimensions"]) for d, v in cfg.get("divisions", {}).items() if "dimensions" in v]
    tables += [(f"[presets.{n}]", v) for n, v in cfg.get("presets", {}).items()]
    for where, table in tables:
        weights = {k: v for k, v in table.items() if k in DIMENSION_NAMES}
        for k, v in weights.items():
            number(f"{where} {k}", v, 0)
        if where == "[dimensions]" and weights and not any(weights.values()):
            bad("[dimensions]", weights, "weights that are not all zero")


def evaluate(ctx: Ctx, job, p) -> tuple[list[Result], list[Result]]:
    """Run every fit rule, then every permission rule. Nothing is skipped."""
    results = [RULES[name][0](ctx, job, p) for name in ctx.rules_cfg]
    gates = [GATES[name][0](ctx, job, p, results) for name in ctx.gates_cfg]
    for r in results + gates:                     # one clause can support several facts: cite it once
        r.refs, r.job_refs = _unique(r.refs), _unique(r.job_refs)
    for r in results:
        _unread_update(ctx, job, p, r)
        _same_moment(ctx, p, r)
        _unreviewed_requirement(ctx, job, r)
    for r in results:
        r.brief = _brief(ctx, job, p, r)
    return results, gates


def _topics_of(rule: str, job) -> set:
    """What a candidate's message would have to be about to bear on this rule, for this job."""
    if rule == "skills":
        return {"skill:" + k for k in job.reqs_of("req.skill")}
    if rule == "setting_experience":
        sreq = job.req("req.setting")
        setting = (sreq.feed or sreq.clar) if sreq else None
        return {"setting:" + setting.value} if setting is not None else set()
    if rule == "license":
        return {"license:" + k for k in job.reqs_of("req.license")}
    if rule == "certifications":
        return {"cert:"}
    if rule == "location":
        return {"place:" + job.location["city"]} if job.location.get("city") else set()
    if rule == "shift_coverage":
        req = job.req("req.shifts")
        claim = (req.feed or req.clar) if req else None
        return {"date:" + s["start"][:10] for s in claim.value} if claim is not None else set()
    if rule == "start_date":
        req = job.req("req.start")
        claim = (req.feed or req.clar) if req else None
        return {"start"} | ({"date:" + str(claim.value)[:10]} if claim is not None else set())
    return {"schedule": {"schedule"}, "pay": {"pay"}}.get(rule, set())


def _unread_update(ctx: Ctx, job, p, r: Result) -> None:
    """A rule met on older evidence becomes a question when the candidate said something newer about it that
    could not be read. Without this, an older yes would stand although the candidate may have withdrawn it."""
    if r.outcome not in (MET, UNKNOWN) or not p.unclear:
        return
    wanted = _topics_of(r.rule, job)
    relied = [ctx.w.evidence[ref["evidence"]].observed_at for ref in r.refs
              if ref.get("note") != "replaced" and ref["evidence"] in ctx.w.evidence]
    if not wanted:
        return
    newest = max(relied) if relied else None
    for c in p.unclear:
        if r.outcome == UNKNOWN and c.rule != "model.verifier":
            continue
        if newest is not None and c.observed_at < newest:
            break
        if newest is not None and c.observed_at == newest and not c.rule.startswith("model."):
            continue
        exact = {f"fact:{attr}:" for attr in RULE_FACTS.get(r.rule, ()) if attr not in ("skill", "license", "cert", "shift_availability", "location_interest")}
        mapping = {"skill": "skill", "setting": "experience", "license": "license", "cert": "cert", "date": "shift_availability", "place": "location_interest"}
        for topic in wanted:
            family, sep, key = topic.partition(":")
            if sep and family in mapping:
                exact.add(f"fact:{mapping[family]}:{key}")
        topics = wanted | exact
        if any(t in topics or any(t.startswith(w) for w in topics if w.endswith(":")) for t in c.value):
            r.outcome = UNKNOWN
            r.credit, r.low, r.high = float(ctx.scoring["unknown_credit"]), 0.0, 1.0
            r.flags.append("unread_update")
            r.refs.append(ctx.ref(c))
            if c.rule == "model.verifier":
                r.notes.append(f"A separate source check did not confirm this interpretation ({ctx.cite(c)}).")
                r.questions.append(f"Ask {_first_name(p)} to clarify {r.label.lower()}: the source check could not confirm “{c.quote}”")
            else:
                r.notes.append(f"A newer message from {_first_name(p)} is about this and could not be read with confidence, "
                               f"so the older answer is not relied on ({ctx.cite(c)}).")
                r.questions.append(f"Ask {_first_name(p)} to confirm {r.label.lower()}: their message “{c.quote}” could not be read")
            return


# Which of a person's facts each rule reads, so a disagreement about pay never unsettles the start date.
RULE_FACTS = {"skills": ("skill",), "license": ("license",), "certifications": ("cert",), "location": ("location_interest", "pref_states", "travel", "home"),
              "start_date": ("available_from",), "schedule": ("schedule", "weeks", "hours"), "shift_coverage": ("shift_availability", "occupied_days"),
              "pay": ("pay",)}

REQ_FACTS = {"profession": ("req.profession",), "setting_experience": ("req.setting", "req.min_years"),
             "skills": ("req.skill",), "license": ("req.license",), "certifications": ("req.cert_path",),
             "start_date": ("req.start",), "schedule": ("req.schedule", "req.weeks", "req.hours"),
             "shift_coverage": ("req.shifts",), "pay": ("req.pay",)}


def _unreviewed_requirement(ctx, job, r):
    if r.outcome == NOT_MET:
        return
    for claim in job.unclear:
        if claim.version == "unapproved" or (claim.version == "clarification" and ctx.policy == "feed_only"):
            continue
        if not any(attr in claim.value for attr in REQ_FACTS.get(r.rule, ())):
            continue
        r.outcome = UNKNOWN
        r.level = "hard" if r.level == "from_job" else r.level
        r.credit, r.low, r.high = float(ctx.scoring["unknown_credit"]), 0.0, 1.0
        r.flags.append("unread_update")
        r.job_refs.append(ctx.ref(claim))
        r.shows = "The posting's requirement could not be confirmed from its wording"
        r.questions.append("Ask the account manager to clarify " + r.label.lower())
        return


def _same_moment(ctx: Ctx, p, r: Result) -> None:
    """Two statements recorded at the same moment that disagree cannot be put in order, so a rule decided on
    either of them becomes a question. The answer is then the same whichever arrived first."""
    if r.outcome not in (MET, NOT_MET) or not p.contested:
        return
    relied = {ref["evidence"] for ref in r.refs}
    for attr, _key, claims in p.contested:
        if attr not in RULE_FACTS.get(r.rule, ()) or not relied & {c.evidence_id for c in claims}:
            continue
        tied = {c.evidence_id for c in claims}
        for ref in r.refs:                                    # neither statement replaced the other
            if ref["evidence"] in tied and ref.get("note") == "replaced":
                ref["note"] = ""
        if "replaced" in r.flags and not any(ref.get("note") == "replaced" for ref in r.refs):
            r.flags.remove("replaced")
            r.notes[:] = [n for n in r.notes if not n.startswith("An earlier record said")]
        said = " and ".join(f"“{c.quote}”" for c in claims[:3])
        r.outcome = UNKNOWN
        r.credit, r.low, r.high = float(ctx.scoring["unknown_credit"]), 0.0, 1.0
        r.flags.append("same_moment")
        r.shows = "Two statements at the same moment disagree"
        r.notes.append(f"{said} were recorded at the same moment ({ctx.cite(claims[0])}), so neither is taken as the later one.")
        r.questions.append(f"Ask {_first_name(p)} which is right: {said}")
        return


def _lower(text: str) -> str:
    return text[0].lower() + text[1:] if len(text) > 1 and text[1].islower() else text


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _brief(ctx: Ctx, job, p, r: Result) -> str:
    """A few words on what this rule found for this person. Empty when there is nothing worth remarking on."""
    if r.outcome == NA:
        return ""
    if "unread_update" in r.flags:
        return f"newer message about {r.label.lower()} not read"
    if r.rule == "profession":
        return "" if r.outcome == MET else "profession not on record" if r.outcome == UNKNOWN else f"{_lower(r.shows)}, not a {_lower(r.needs)}"
    if r.rule == "setting_experience":
        sreq = job.req("req.setting")
        setting = (sreq.feed or sreq.clar).value
        lab, exp = ctx.lex.label(setting), p.fact("experience", setting)
        if exp is None:
            return f"no {lab} work on record"
        if exp.state != "pos":
            return f"{lab} experience not answered" if exp.state == "unanswered" else f"no {lab} experience"
        documented, claimed = exp.value["years_documented"], exp.value["years_claimed"]
        if documented is None:
            return f"says {fmt_years(claimed)} of {lab} work" if claimed is not None else f"{lab} work mentioned, with no dates"
        if claimed is not None and abs(documented - claimed) > 0.25:
            return f"says {fmt_years(claimed)} of {lab} work; records show {fmt_span(documented)}"
        return ("at least " if exp.value["precision"] == "year" else "") + f"{fmt_span(documented)} of {lab} work"
    if r.rule == "skills":
        denied = [i["label"] for i in r.items if i["outcome"] == NOT_MET]
        if denied:
            return "has not used " + _and(denied)
        missing = [i["label"] for i in r.items if i["outcome"] == UNKNOWN and i["level"] == "required"]
        if missing:
            return _and(missing) + " not on record"
        held = [i["label"] for i in r.items if i["outcome"] == MET]
        return "uses " + _and(held) if held else ""
    if r.rule == "license":
        return "" if r.outcome == MET else _lower(r.shows.split(" (")[0])
    if r.rule == "certifications":
        if r.outcome != MET:
            return "board certification not on record"
        return "qualifies by the other-boards route" if "alternative_route" in r.flags else r.shows.replace("Reports ", "").replace(" certification", " certified")
    if r.rule == "location":
        city = job.location.get("city")
        if r.outcome == NOT_MET:
            return f"{city} no longer considered" if "no longer" in r.shows else _lower(r.shows)
        if r.outcome == UNKNOWN:
            return "location preference not on record"
        home = p.fact("home")
        if r.shows.startswith("Said they are open") and home is not None and home.value.get("city") and home.value["city"] != city:
            return f"open to {city}, lives in {home.value['city']}"
        return _lower(r.shows.replace("Said they are open", "open"))
    if r.rule == "start_date":
        return "" if r.outcome == MET else "no start date given" if r.outcome == UNKNOWN else _lower(r.shows)
    if r.rule == "schedule":
        return "" if r.outcome == MET else "schedule not confirmed" if r.outcome == UNKNOWN else "schedule does not fit: " + _lower(r.shows)
    if r.rule == "shift_coverage":
        return "shift dates not confirmed" if r.outcome == UNKNOWN else _lower(r.shows)
    if r.rule == "pay":
        if r.outcome == UNKNOWN:
            return "no pay expectation given" if r.shows.startswith("No pay") else _lower(r.shows)
        return "asks " + r.shows.split(": ", 1)[-1]
    return ""


def _unique(refs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for ref in refs:
        if ref is None:
            continue
        key = (ref["evidence"], ref["quote"])
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out
