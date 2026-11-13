"""The quality score: six dimensions for people who pass every required rule.

Stage one, the gates, is the rule engine: a required rule not met means "not a fit", one not known means
"needs an answer". Stage two ranks only those who pass. Its score is a weighted average of six dimensions,
each 0 to 100 and each computed from rule results the page already explains, so every point traces to a
quote. Nothing here reads a source.

  setting    experience in the job's care setting: documented 100, claimed or undated 70, at a target employer 100
  skills     the rule's own credit for the tools and skills the job asks for
  depth      documented years in the setting against a target of twice the minimum (at least three years);
             years that are only claimed count for at most half
  evidence   100, less 30 for a claim the records do not support, 30 for a newer message that could not be
             read, and 15 for each answer older than its freshness window
  readiness  start date, schedule and shifts (70%), and how recently the candidate replied (30%)
  pay        100 at or under the approved rate, 10 points less for each 1% above it

An unknown answer gets `unknown_credit` (half, by default) and widens the range: low as if it were "no",
high as if it were "yes". A dimension the job says nothing about is left out of the average.
"""
from __future__ import annotations

from .rules import MET, NA, NOT_MET, UNKNOWN
from .timeutil import fmt_span, fmt_years

ORDER = ("setting", "skills", "depth", "evidence", "readiness", "pay")
LABELS = {"setting": "Setting", "skills": "Skills", "depth": "Depth", "evidence": "Evidence",
          "readiness": "Readiness", "pay": "Pay"}
DEFAULT_WEIGHTS = {"setting": 25, "skills": 20, "depth": 15, "evidence": 15, "readiness": 15, "pay": 10}
PENALTY = {"disputed": 30, "unread_update": 30, "same_moment": 30, "stale": 15}
MEANS = {"setting": "Dated experience in the job's care setting",
         "skills": "Every skill and tool the job asks for",
         "depth": "Documented years at twice the minimum",
         "evidence": "Nothing contradicts or outdates the facts used",
         "readiness": "Start, schedule and shifts met, and a recent reply",
         "pay": "Asks at or below the approved rate"}


def weights_of(dims_cfg: dict, override: dict | None = None) -> dict:
    w = {k: float(dims_cfg.get(k, DEFAULT_WEIGHTS[k])) for k in ORDER}
    for k, v in (override or {}).items():
        if k in w:
            w[k] = max(0.0, float(v))
    return w


def _first_ref(r):
    refs = [x for x in (r.refs if r else []) if x.get("note") != "replaced"]
    return refs[0] if refs else None


def compute(ctx, job, p, results) -> dict:
    """{dimension: (value, low, high, why, ref)}, value None when the job says nothing about it."""
    by = {r.rule: r for r in results}
    u = 100 * float(ctx.scoring.get("unknown_credit", 0.5))
    out = {}

    def from_rule(r, met=100.0):
        if r is None or r.outcome == NA:
            return None
        if r.outcome == UNKNOWN:
            return (u, 0.0, 100.0)
        if r.outcome == NOT_MET:
            return (0.0, 0.0, 0.0)
        return (met, met, met)

    # setting
    r = by.get("setting_experience")
    m = r.measure if r else {}
    v = from_rule(r)
    why = r.shows if r and r.outcome != NA else ""
    if v and r.outcome == MET and m.get("documented") is None:
        v, why = (70.0, 70.0, 70.0), why + " (no dates)"
    targets = [t.lower() for t in ctx.dims_cfg.get("target_employers", [])]
    if v is not None and targets and r.outcome != NOT_MET:
        # Searched in the person's own résumés and profiles, not in notes about them.
        texts = " ".join(ctx.w.evidence[i].text.lower() for i in p.evidence_ids
                         if ctx.w.evidence[i].kind in ("resume", "profile") and ctx.w.evidence[i].text)
        hit = next((t for t in targets if t in texts), None)
        if hit:
            v, why = (100.0, 100.0, 100.0), f"Worked at a target employer: {hit}"
    out["setting"] = (*v, why, _first_ref(r)) if v else None

    # skills
    r = by.get("skills")
    out["skills"] = None if r is None or r.outcome == NA else (100 * r.credit, 100 * r.low, 100 * r.high, r.shows, _first_ref(r))

    # depth
    r = by.get("setting_experience")
    if r is None or r.outcome == NA:
        out["depth"] = None
    elif r.outcome == NOT_MET and m.get("documented") is None and m.get("claimed") is None:
        out["depth"] = (0.0, 0.0, 0.0, "No experience in this setting", _first_ref(r))
    else:
        minimum = m.get("min_years") or 0
        target = max(3.0, 2.0 * minimum)
        doc, claimed = m.get("documented"), m.get("claimed")
        if doc is not None:
            val = min(100.0, 100.0 * doc / target)
            out["depth"] = (val, val, val, f"{fmt_span(doc)} documented" + (f" against a {fmt_years(minimum)} minimum" if minimum else ""), _first_ref(r))
        elif claimed is not None:
            val = min(50.0, 100.0 * claimed / target)
            out["depth"] = (val, val, val, f"{fmt_span(claimed)} claimed, no dated history", _first_ref(r))
        else:
            out["depth"] = (u, 0.0, 100.0, "Years in this setting not known", _first_ref(r))

    # evidence
    lost, reasons, worst = 0, [], None
    for r in results:
        if r.outcome == NA:
            continue
        for flag, cost in PENALTY.items():
            if flag in r.flags:
                lost += cost
                reasons.append({"disputed": f"{r.label.lower()}: a claim the records do not support",
                                "unread_update": f"{r.label.lower()}: a newer message could not be read",
                                "same_moment": f"{r.label.lower()}: two statements at the same moment disagree",
                                "stale": f"{r.label.lower()}: answer is old"}[flag])
                worst = worst or _first_ref(r)
    val = max(0.0, 100.0 - lost)
    out["evidence"] = (val, val, val, "; ".join(reasons).capitalize() if reasons else "Nothing contradicts or outdates the facts used", worst)

    # readiness
    avail = [by[k] for k in ("start_date", "schedule", "shift_coverage") if k in by and by[k].outcome != NA]
    heard = p.last_candidate_contact
    days = (ctx.today - heard.date()).days if heard else None
    rec = 25.0 if days is None else 100.0 if days <= 7 else 75.0 if days <= 30 else 50.0 if days <= 90 else 25.0
    rec_text = ("no reply on record" if days is None else "replied on the reference date" if days <= 0
                else f"last replied {days} day{'s' if days != 1 else ''} before the reference date")
    if avail:
        a = sum(100 * r.credit for r in avail) / len(avail)
        lo = sum(100 * r.low for r in avail) / len(avail)
        hi = sum(100 * r.high for r in avail) / len(avail)
        met = sum(1 for r in avail if r.outcome == MET)
        ref = next((_first_ref(r) for r in avail if _first_ref(r)), None)
        out["readiness"] = (0.7 * a + 0.3 * rec, 0.7 * lo + 0.3 * rec, 0.7 * hi + 0.3 * rec,
                            f"{met} of {len(avail)} availability checks met; {rec_text}", ref)
    else:
        out["readiness"] = (rec, rec, rec, rec_text.capitalize(), None)

    # pay
    r = by.get("pay")
    if r is None or r.outcome == NA:
        out["pay"] = None
    elif r.outcome == NOT_MET:
        val = max(0.0, 100.0 - 1000.0 * float(r.measure.get("over", 0.1)))
        out["pay"] = (val, val, val, r.shows, _first_ref(r))
    else:
        v = from_rule(r)
        out["pay"] = (*v, r.shows, _first_ref(r))
    return out


def score(dims, weights: dict) -> tuple[int, int, int]:
    """Weighted average of the dimensions that apply: (score, low, high). `dims` may be the full dict or a
    row's tuple of (value, low, high) in ORDER."""
    rows = dims.items() if isinstance(dims, dict) else zip(ORDER, dims)
    total = s = lo = hi = 0.0
    for name, d in rows:
        if d is None:
            continue
        w = weights.get(name, 0.0)
        total += w
        s, lo, hi = s + w * d[0], lo + w * d[1], hi + w * d[2]
    if not total:
        return 0, 0, 0
    return round(s / total), round(lo / total), round(hi / total)


def compact(dims: dict) -> tuple:
    return tuple(None if dims.get(k) is None else tuple(round(x, 1) for x in dims[k][:3]) for k in ORDER)


def key_ref(dims: dict, weights: dict):
    """The one quote behind the dimension that adds most to this person's score."""
    best = max(((weights.get(k, 0) * d[0], d[4]) for k, d in dims.items() if d is not None and d[4] is not None and d[0] > 0),
               default=(0, None), key=lambda x: x[0])
    return best[1]
