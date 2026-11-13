"""Turn rule results into a scored, tiered, explained shortlist with a next action.

What the numbers mean
  score : the quality score, 0-100: a weighted average of six dimensions (dimensions.py),
          each computed from rule results already explained. Weights are settings.
  range : the score if every unknown turned out "no" (low) or "yes" (high).
  tier  : strong   - every required rule is met, and the score stays at or above the
                     threshold even if every unknown answer turns out "no"
          possible - every required rule is met, but the score depends on unknown
                     answers or falls below the threshold
          clarify  - a required rule is unknown: an answer is needed before going further
          not_fit  - a required rule is not met
Permission rules never change the score. They decide the next action.

How a shortlist is held
  A Shortlist is one job's rows, one per person checked, plus running totals. Every rule
  still runs for every person. For a small pool each row keeps its full rule-by-rule
  result. For a large pool each row is compact (tier, score, next action, the line shown
  on the card) and the full explanation is rebuilt on demand for the one person a
  recruiter opens. Rebuilding is exact, because the rules are deterministic.
  A change to one person replaces that person's row; nothing else is recomputed.

Like the rule engine, this module knows nothing about sources or destinations.
"""
from __future__ import annotations

import gc
import sys
import zlib
from datetime import datetime, timezone
from collections import Counter
from dataclasses import asdict, dataclass, field

from . import config as configuration
from .lexicon import STATE_NAMES
from . import dimensions
from .rules import BLOCKED, CONFIRM, MET, NA, NOT_MET, ROUTE, UNKNOWN, Ctx, Result, evaluate
from .timeutil import WEEKDAY_NAMES, fmt_date, fmt_pay, fmt_years

TIER_ORDER = ["strong", "possible", "clarify", "not_fit"]
TIER_LABEL = {"strong": "Match", "possible": "Possible match", "clarify": "Needs an answer", "not_fit": "Not a fit"}
# One list a recruiter works from, and the people a required rule rules out. Someone who needs an answer is on
# the list at the place their score gives them: near the top, the missing answer is worth chasing; lower down
# it hardly matters. "best" is the name of that one list.
GROUPS = {"best": ("strong", "possible", "clarify"), "not_fit": ("not_fit",)}
ACTION_RANK = {"ready": 0, "route": 1, "clarify": 2, "blocked": 3, "none": 4, "closed": 5}
BREAKER_LABEL = {"action_readiness": "The person above can be acted on sooner.",
                 "fewer_open_questions": "The person above has fewer open questions.",
                 "most_recent_candidate_contact": "The person above was heard from more recently."}
ACTIONABLE = ("ready", "route", "clarify")
GROUP_OF = {tier: i for i, members in enumerate(GROUPS.values()) for tier in members}
JOB_LEVEL_QUESTIONS = ("Ask the account manager", "Confirm the requirement change")    # questions about the job, not the person


@dataclass(slots=True, eq=False)
class Match:
    job: str
    person: str
    name: str
    tier: str
    score: int
    low: int
    high: int
    results: list | None             # None on a compact row: Shortlist.explain() rebuilds them
    gates: list | None
    action: dict                     # status, label, owner, steps (steps is None on a compact row)
    summary: str
    questions: list | None
    flags: list
    signature: object                # the result on every rule; on a compact row, its hash
    band: int | None = None          # group of comparable scores within a tier
    band_size: int = 1
    identical: int = 1               # how many people have exactly the same result on every rule
    decided_by: str = ""             # what placed this person below the one above, within a band
    standout: list = field(default_factory=list)    # what sets this person apart from the others for this job
    typical: bool = False            # True when nothing sets them apart: the line then says what they share
    watch: int = 0                   # how many of the stand-out lines, from the first, are problems or cautions
    # What ranking and the running totals need. Kept on the row so it can be re-ranked without re-running the rules.
    outcomes: tuple = ()             # the outcome of every fit rule, then every permission rule, in order
    briefs: tuple = ()               # (0 problem | 1 caution | 2 strength, position, words, rule) for each rule with something to say
    in_profession: bool = True
    open_questions: int = 0
    last_heard: float = 0.0          # when the person was last heard from, for the tie-breaker
    asks: tuple = ()                 # questions this result raises about the job itself
    blockers: tuple = ()             # the required rules that hold this person back: not met (not a fit) or not known
    dims: tuple = ()                 # (value, low, high) for each quality dimension, None where the job says nothing
    dim_detail: dict | None = None   # full rows only: each dimension with the words and the quote behind it
    key: tuple | None = None         # (evidence id, quote) behind the dimension that adds most to the score


def _action(tier: str, results: list[Result], gates: list[Result], name: str) -> dict:
    g = {x.rule: x for x in gates}
    failed = [r for r in results if r.level == "hard" and r.outcome == NOT_MET]
    candidate_questions = [q for r in results for q in r.questions]
    route = g["ownership"].outcome == ROUTE
    owner = g["ownership"].shows.replace("Opportunities go through ", "") if route else None
    steps: list[str] = []
    if g["job_open"].outcome == BLOCKED:
        status, label = "closed", "No action: the job is not open"
    elif g["contact_permission"].outcome == BLOCKED:
        status, label = "blocked", "Do not contact: " + g["contact_permission"].shows[0].lower() + g["contact_permission"].shows[1:]
    elif tier == "not_fit":
        status, label = "none", "No action as posted: does not meet " + ", ".join(r.label.lower() for r in failed)
        steps.extend(q for r in failed for q in r.questions)
    elif route:
        status, label = "route", f"Send to {owner}, who owns this relationship"
        steps.append(f"Do not contact {name} directly. Pass this match to {owner}.")
        steps.extend(candidate_questions)
    elif tier == "clarify":
        status, label = "clarify", "Ask before going further"
        steps.extend(candidate_questions)
    else:
        status, label = "ready", "A recruiter may reach out"
        steps.extend(candidate_questions)
    if status in ("route", "clarify", "ready"):
        if g["requirement_source"].outcome == CONFIRM:
            steps.extend(g["requirement_source"].questions)
        if any("unverified" in r.flags for r in results):
            steps.append("Verify licenses and certificates before submitting: they are candidate-reported.")
        steps.append("Outreach and submission are a recruiter's decision. This tool sends nothing.")
    return {"status": status, "label": label, "owner": owner, "steps": list(dict.fromkeys(steps))}


def _kind(r) -> int:
    """How a rule's remark should read on a card: 0 a problem, 1 met but worth a second look, 2 a strength."""
    if r.outcome != MET:
        return 0
    if "disputed" in r.flags or (r.credit is not None and r.credit < 1):
        return 1
    return 2


def match_one(ctx: Ctx, job, p) -> Match:
    """Every rule for one person and one job, with the full explanation."""
    results, gates = evaluate(ctx, job, p)
    weights = dimensions.weights_of(ctx.dims_cfg)
    dims = dimensions.compute(ctx, job, p, results)
    score, low, high = dimensions.score(dims, weights)
    failed = [r for r in results if r.level == "hard" and r.outcome == NOT_MET]
    unknown = [r for r in results if r.level == "hard" and r.outcome == UNKNOWN]
    soft = [r for r in results if r.level == "soft" and r.outcome in (NOT_MET, UNKNOWN)]
    if failed:
        tier = "not_fit"
        summary = "Does not meet: " + ", ".join(r.label.lower() for r in failed)
    elif unknown:
        tier = "clarify"
        summary = "Not yet known: " + ", ".join(r.label.lower() for r in unknown)
    elif low >= ctx.scoring["strong_threshold"]:
        tier = "strong"
        summary = "Meets every required rule" + ("; lower on " + ", ".join(r.label.lower() for r in soft) if soft else "")
    else:
        tier = "possible"
        summary = "Meets every required rule; open or lower on " + ", ".join(r.label.lower() for r in soft)
    checked = [r for r in results if r.outcome != NA]
    if tier == "strong" and len(checked) < int(ctx.scoring.get("min_rules_for_strong", 3)):
        # A posting that says almost nothing cannot make anyone a strong match.
        tier = "possible"
        summary = "Too little in the posting to judge: only " + ", ".join(r.label.lower() for r in checked) + " could be checked"
    flags = list(dict.fromkeys(f for r in results + gates for f in r.flags))
    if "provisional" in flags:
        summary += ". Relies on a note not yet in the vendor feed"
    action = _action(tier, results, gates, p.name)
    questions = list(dict.fromkeys([q for r in results for q in r.questions] + [q for g in gates for q in g.questions]))
    signature = tuple((r.rule, r.outcome, tuple(sorted(r.flags)), None if r.credit is None else round(r.credit, 4))
                      for r in results) + tuple((g.rule, g.outcome) for g in gates)
    profession = next((r for r in results if r.rule == "profession"), None)
    heard = p.last_candidate_contact
    return Match(job.key, p.key, p.name, tier, score, low, high, results, gates, action, summary, questions, flags, signature,
                 outcomes=tuple(r.outcome for r in results) + tuple(g.outcome for g in gates),
                 briefs=tuple((_kind(r), i, r.brief, r.rule) for i, r in enumerate(results) if r.brief),
                 in_profession=profession is None or profession.outcome == MET,
                 open_questions=len(questions), last_heard=heard.timestamp() if heard else 0.0,
                 asks=tuple(q for q in dict.fromkeys(questions + action["steps"]) if q.startswith(JOB_LEVEL_QUESTIONS)),
                 blockers=tuple(r.label for r in (failed or unknown)),
                 dims=dimensions.compact(dims), dim_detail=dims, key=_key(dimensions.key_ref(dims, weights)))


def _key(ref):
    return (ref["evidence"], ref["quote"]) if ref else None


def compact(m: Match, shared: dict) -> Match:
    """Drop the rule-by-rule explanation from a row, keeping what a list and the ranking need.

    `shared` belongs to one shortlist. Anything two rows have in common (the next action, the summary, the
    words on the card, the pattern of outcomes) is stored once there and pointed to by both rows.
    """
    def one(value):
        return shared.setdefault(value, value)

    a = m.action
    m.results = m.gates = m.questions = None
    m.action = shared.setdefault(("action", a["status"], a["label"], a["owner"]),
                                 {"status": a["status"], "label": a["label"], "owner": a["owner"], "steps": None})
    m.summary = one(m.summary)
    m.signature = hash(m.signature)
    m.flags = one(tuple(m.flags))
    m.briefs = one(tuple((kind, i, one(text), rule) for kind, i, text, rule in m.briefs))
    m.asks, m.outcomes, m.blockers = one(m.asks), one(m.outcomes), one(m.blockers)
    m.dims, m.key, m.dim_detail = one(m.dims), one(m.key), None
    return m


def rank(matches: list[Match], scoring: dict) -> None:
    """Order the list, group comparable scores, and say what (if anything) separated neighbours.

    Works on compact rows as well as full ones, so a list can be put back in order after one row changes.
    """
    breakers = list(scoring.get("tie_breakers", []))

    def breaker_values(m: Match) -> list:
        out = []
        for b in breakers:
            if b == "action_readiness":
                out.append(ACTION_RANK[m.action["status"]])
            elif b == "fewer_open_questions":
                out.append(m.open_questions)
            elif b == "most_recent_candidate_contact":
                out.append(-m.last_heard)
        return out

    first = bool(scoring.get("actionable_first", True))

    def lead(m: Match) -> int:
        # Among people who meet every required rule, those who must not be contacted are listed last:
        # a recruiter cannot act on them. Reaching out and handing over to the owning recruiter both count
        # as something to do now. Fit is not changed by this: score and tier stay what the fit rules gave.
        return int(first and GROUP_OF[m.tier] == 0 and m.action["status"] not in ACTIONABLE)

    def arbitrary(m: Match) -> int:
        # The last resort when nothing separates two people. It differs from job to job, so the same
        # people are not listed first everywhere, as they would be in alphabetical order.
        return zlib.crc32(f"{m.job}|{m.person}".encode())

    matches.sort(key=lambda m: (GROUP_OF[m.tier], lead(m), -m.score, *breaker_values(m), arbitrary(m), m.person))
    _standouts(matches)
    margin = scoring.get("tie_margin", 0)
    for m in matches:
        m.band, m.band_size, m.identical, m.decided_by = None, 1, 1, ""
    band, i = 0, 0
    while i < len(matches):
        j = i + 1
        while (j < len(matches) and matches[j].tier == matches[i].tier and lead(matches[j]) == lead(matches[i])
               and matches[i].score - matches[j].score <= margin):
            j += 1
        if j - i > 1:
            band += 1
            for m in matches[i:j]:
                m.band, m.band_size = band, j - i
        i = j
    groups: dict[tuple, list[Match]] = {}
    for m in matches:
        groups.setdefault((m.tier, m.signature), []).append(m)
    for group in groups.values():
        for m in group:
            m.identical = len(group)
    for above, m in zip(matches, matches[1:]):
        if m.band is None or m.band != above.band:
            continue
        if above.score != m.score:
            m.decided_by = f"The person above scores higher ({above.score} v {m.score}), within the {margin}-point margin."
            continue
        m.decided_by = "Nothing separates this person from the one above: the evidence is the same, so the order is arbitrary."
        for b, va, vm in zip(breakers, breaker_values(above), breaker_values(m)):
            if va != vm:
                m.decided_by = BREAKER_LABEL.get(b, b)
                break
    for m, below in zip(matches, matches[1:]):          # the first of two people nothing separates says so too
        if not m.decided_by and m.band is not None and below.band == m.band and below.decided_by.startswith("Nothing separates"):
            m.decided_by = "Nothing separates this person from the one below: the evidence is the same, so the order is arbitrary."


def _passes(world, m: Match, filters, today) -> bool:
    """A recruiter's filters. Each narrows the list; none changes anyone's result.
        act_now        the next action is to reach out or to hand over to the owning recruiter
        recent:N       the candidate replied within N days of the reference date
        no_questions   nothing left to ask the candidate (questions about the job itself do not count)
        skill:KEY      the person has the skill on record, for example skill:oasis
        min:DIM:N      the quality dimension DIM is at least N, for example min:depth:50
        text:PHRASE    a criterion the recruiter typed: the person's own documents say it, without negation or doubt
        school:PHRASE  the education on their résumé names this school or degree
    """
    for f in filters or ():
        kind, _, rest = f.partition(":")
        if kind == "act_now" and m.action["status"] not in ("ready", "route"):
            return False
        if kind == "recent":
            days = int(rest or 30)
            if not m.last_heard or (today - datetime.fromtimestamp(m.last_heard, timezone.utc).date()).days > days:
                return False
        if kind == "no_questions" and m.open_questions - len(m.asks) > 0:
            return False
        if kind == "skill":
            fact = world.people[m.person].fact("skill", rest)
            if fact is None or fact.state != "pos":
                return False
        if kind == "text":
            from .resume import mention
            if mention(world, world.people[m.person], rest) is None:
                return False
        if kind == "school":
            from .resume import studied_at
            if not studied_at(world, world.people[m.person], rest):
                return False
        if kind == "min":
            name, _, n = rest.partition(":")
            d = dict(zip(dimensions.ORDER, m.dims)).get(name)
            if d is None or d[0] < float(n or 0):
                return False
    return True


def _standouts(matches: list[Match]) -> None:
    """For each person, pick the facts that set them apart from the others considered for the same job.

    A problem (a rule not met or not known) or a caution (met, but with a conflicting claim or only in part)
    is always worth saying. A strength is worth saying when at most
    half of the people in the job's profession share it. Problems come first. Someone with neither gets the
    main facts they share with the others, marked as typical.
    """
    peers = [m for m in matches if m.in_profession] or matches
    peer_ids = {id(m) for m in peers}
    counts: dict[str, Counter] = {}
    for m in peers:
        for _, _, text, rule in m.briefs:
            counts.setdefault(rule, Counter())[text] += 1
    half = len(peers) / 2
    for m in matches:
        if id(m) not in peer_ids:
            chosen = sorted(m.briefs)[:1]
        else:
            rare = sorted(b for b in m.briefs if b[0] < 2 or counts[b[3]][b[2]] <= half)
            m.typical = not rare
            chosen = (rare or sorted(m.briefs, key=lambda b: b[1]))[:2]
        m.standout = [b[2] for b in chosen]
        m.watch = sum(1 for b in chosen if b[0] < 2)


class Shortlist:
    """One job's rows and running totals. Built once when the job arrives, then changed one person at a time."""

    __slots__ = ("job", "ctx", "scoring", "by_person", "rows", "stats", "dirty", "shared")

    def __init__(self, world, job, cfg: dict, pool=None, compact_rows: bool | None = None):
        dcfg = configuration.for_division(cfg, job.division)
        mcfg = cfg.get("matching", {})
        self.job, self.scoring = job, dcfg["scoring"]
        self.ctx = Ctx(world, dcfg, cfg["requirement_changes"]["policy"])
        # Profession is the one rule an index can answer. Above a configured population, people in other
        # professions are counted and set aside; any one of them can still be explained on request.
        blocked = False
        if pool is None:
            pool, blocked = self.candidates(world, job, cfg)
        if compact_rows is None:
            compact_rows = len(pool) > mcfg.get("keep_detail_below", 2000)
        self.stats = {"people": len(world.people), "excluded_by_profession": 0, "evaluated": 0, "blocked": blocked, "compact": compact_rows,
                      "tiers": {t: 0 for t in TIER_ORDER}, "actions": {t: {} for t in TIER_ORDER},
                      "rule_order": list(self.ctx.rules_cfg) + list(self.ctx.gates_cfg),
                      "outcomes": {name: {} for name in list(self.ctx.rules_cfg) + list(self.ctx.gates_cfg)},
                      "reasons": {"clarify": {}, "not_fit": {}}}      # which required rule holds people back, and how many
        self.by_person: dict[str, Match] = {}
        self.rows: list[Match] = []
        self.shared: dict = {}                    # text and values that several compact rows have in common
        # Rule results hold no reference cycles, so the collector has nothing to find while they are produced.
        # Pausing it here roughly halves the time for a large pool (measured in bench/benchmark.py).
        paused = gc.isenabled()
        gc.disable()
        try:
            for p in pool:
                m = match_one(self.ctx, job, p)
                self._count(m, 1)
                self.by_person[p.key] = compact(m, self.shared) if compact_rows else m
        finally:
            if paused:
                gc.enable()
        self._sizes(world)
        self.dirty = True

    @staticmethod
    def candidates(world, job, cfg: dict) -> tuple[list, bool]:
        """The people a job's list is built from, and whether other professions were set aside by the index."""
        mcfg = cfg.get("matching", {})
        blocked = bool(mcfg.get("block_on_profession") and job.profession
                       and len(world.people) >= mcfg.get("block_min_population", 1000))
        return list((world.by_profession.get(job.profession, {}) if blocked else world.people).values()), blocked

    # ---------------------------------------------------------------- totals
    def _count(self, m: Match, n: int) -> None:
        def bump(counts: dict, key: str) -> None:
            counts[key] = counts.get(key, 0) + n
            if not counts[key]:
                del counts[key]                       # so totals after a change equal totals after a rebuild

        s = self.stats
        s["tiers"][m.tier] += n
        bump(s["actions"][m.tier], m.action["status"])
        for name, outcome in zip(s["rule_order"], m.outcomes):
            bump(s["outcomes"][name], outcome)
        if m.tier in s["reasons"]:
            for label in m.blockers:
                bump(s["reasons"][m.tier], label)

    def _sizes(self, world) -> None:
        s = self.stats
        s["people"], s["evaluated"] = len(world.people), len(self.by_person)
        s["excluded_by_profession"] = s["people"] - s["evaluated"] if s["blocked"] else 0

    # ----------------------------------------------------------------- reads
    def ranked(self) -> list[Match]:
        """The rows in order. Re-ranked only when something has changed since the last read."""
        if self.dirty:
            self.rows = list(self.by_person.values())
            rank(self.rows, self.scoring)
            self.dirty = False
        return self.rows

    def page(self, group: str, offset: int = 0, limit: int = 25) -> tuple[list[Match], int]:
        """One screenful of one list (best | clarify | not_fit), and how many rows that list holds in all."""
        rows, tiers = self.ranked(), self.stats["tiers"]
        first = TIER_ORDER.index(GROUPS[group][0])
        start = sum(tiers[t] for t in TIER_ORDER[:first])
        total = sum(tiers[t] for t in GROUPS[group])
        offset = max(0, min(offset, total))
        return rows[start + offset:start + min(offset + max(limit, 0), total)], total

    def view(self, world, group: str, weights: dict | None = None, filters=()) -> list[tuple]:
        """One list re-scored with a recruiter's own weights and narrowed by their filters, best first.

        Gates are untouched: a person stays in the same list (matches, needs an answer, not a fit) whatever the
        weights. Inside a list, the score and the strong or possible label follow the new weights. Each item is
        (row, score, low, high, tier). Reading every row in the list is the cost: about 30,000 rows for the
        largest list at 100,000 candidates.
        """
        w = dimensions.weights_of(self.ctx.dims_cfg, weights)
        threshold = self.scoring["strong_threshold"]
        today = world.today
        out = []
        for m in self.ranked():
            if m.tier not in GROUPS[group] or not _passes(world, m, filters, today):
                continue
            s, lo, hi = dimensions.score(m.dims, w) if m.dims else (m.score, m.low, m.high)
            tier = m.tier
            if tier in ("strong", "possible") and not m.summary.startswith("Too little"):
                tier = "strong" if lo >= threshold else "possible"
            out.append((m, s, lo, hi, tier))
        out.sort(key=lambda x: (GROUP_OF[x[4]], int(x[0].action["status"] not in ACTIONABLE and GROUP_OF[x[4]] == 0),
                                -x[1], ACTION_RANK[x[0].action["status"]], x[0].open_questions,
                                -x[0].last_heard, zlib.crc32(f"{x[0].job}|{x[0].person}".encode()), x[0].person))
        return out

    def job_questions(self) -> list[str]:
        return list(dict.fromkeys(q for m in self.ranked() for q in m.asks))

    def explain(self, world, person_key: str, placed: bool = True) -> Match | None:
        """The full rule-by-rule result for one person. On a compact list it is rebuilt now; it cannot differ.

        `placed` also brings the list up to date first, so the result carries the person's place in it
        (similar candidates, what sets them apart). Describing a change does not need that, and skips it.
        """
        if placed:
            self.ranked()
        row = self.by_person.get(person_key)
        if row is None or row.results is not None:
            return row
        full = match_one(self.ctx, self.job, world.people[person_key])
        for attr in ("band", "band_size", "identical", "decided_by", "standout", "typical"):
            setattr(full, attr, getattr(row, attr))
        return full

    def footprint(self) -> int:
        """Bytes this shortlist's rows hold, counting text shared between rows once."""
        seen, total, stack = set(), 0, list(self.by_person.values())
        while stack:
            obj = stack.pop()
            if id(obj) in seen or obj is None:
                continue
            seen.add(id(obj))
            total += sys.getsizeof(obj)
            if isinstance(obj, Match):
                stack.extend(getattr(obj, name) for name in obj.__slots__)
            elif isinstance(obj, dict):
                stack.extend(obj.keys())
                stack.extend(obj.values())
            elif isinstance(obj, (list, tuple)):
                stack.extend(obj)
        return total

    # ---------------------------------------------------------------- writes
    def patch(self, world, person_key: str) -> tuple[Match | None, Match | None]:
        """Re-check one person after something about them changed. Returns (their row before, their row now)."""
        old = self.by_person.pop(person_key, None)
        p = world.people.get(person_key)
        new = None
        if p is not None and (not self.stats["blocked"] or p.profession == self.job.profession):
            new = match_one(self.ctx, self.job, p)
            if self.stats["compact"]:
                new = compact(new, self.shared)
            self.by_person[person_key] = new
            self._count(new, 1)
        if old is not None:
            self._count(old, -1)
        self._sizes(world)
        self.dirty = True
        return old, new


def match_job(world, job, cfg: dict, pool=None) -> tuple[list[Match], dict]:
    """Evaluate one job against the pool and rank the result. Returns (rows in order, totals)."""
    sl = Shortlist(world, job, cfg, pool)
    return sl.ranked(), sl.stats


def match_person(world, person, cfg: dict) -> dict:
    """Re-check one person against every open job: what a candidate update triggers. Returns job key -> Match."""
    out = {}
    for key, job in world.jobs.items():
        if job.status != "open":
            continue
        dcfg = configuration.for_division(cfg, job.division)
        out[key] = match_one(Ctx(world, dcfg, cfg["requirement_changes"]["policy"]), job, person)
    return out


# ------------------------------------------------------------- presentation
def result_dict(world, r: Result) -> dict:
    d = asdict(r)
    cited = [ref["evidence"] for ref in r.refs + r.job_refs]
    d["context"] = world.notes_on(cited)      # notes that repeat or discuss the evidence this rule used
    return d


def card_dict(world, m: Match, override: tuple | None = None) -> dict:
    """What one row of a list shows. Works on a compact row: no rule-by-rule detail is needed.
    `override` is (score, low, high, tier) under a recruiter's own weights."""
    from .resume import background
    p = world.people[m.person]
    who = person_dict(world, p)
    bg = background(world, p)                 # read once for the card: what else they list, their links, what is missing
    standout = " · ".join(m.standout)
    marks = list(m.outcomes)
    for kind, i, _, _ in m.briefs:
        if kind == 1:
            marks[i] = "caution"
    out = {
        "watch": list(m.standout[:m.watch]), "good": list(m.standout[m.watch:]), "marks": marks,
        "dims": [None if d is None else d[0] for d in m.dims],
        "key": _key_dict(world, m.key),
        "same": (hash((m.tier, m.signature)) & 0xffffffff) if m.identical > 1 else None,
        "job": m.job, "person": m.person, "name": m.name, "name_basis": p.name_basis, "tier": m.tier, "tier_label": TIER_LABEL[m.tier],
        "score": m.score, "low": m.low, "high": m.high, "summary": m.summary,
        "action": {"status": m.action["status"], "label": m.action["label"], "owner": m.action["owner"]},
        "flags": list(m.flags), "band": m.band, "band_size": m.band_size, "identical": m.identical, "decided_by": m.decided_by,
        "standout": standout[:1].upper() + standout[1:], "typical": m.typical,
        "headline": who["headline"], "home": who["home"], "also": _also(bg),
        "ref": who["ref"], "namesakes": who["namesakes"], "links": _links(bg),
        # for people level on every rule: what is missing that would separate them, and whether their documents differ
        "apart": open_details(world, world.jobs[m.job], p, bg) if m.identical > 1 else None,
        "docs": documents_key(world, p) if m.identical > 1 else None,
    }
    if override:
        out.update(score=override[0], low=override[1], high=override[2], tier=override[3], tier_label=TIER_LABEL[override[3]],
                   reweighted=True)
    return out


def _key_dict(world, key):
    if not key:
        return None
    ev = world.evidence.get(key[0])
    return {"evidence": key[0], "quote": key[1], "source": ev.label if ev else key[0],
            "at": ev.observed_at.isoformat() if ev else None, "date_only": ev.date_only if ev else True}


def open_details(world, job, p, bg: dict | None = None) -> list:
    """What is not on file for this person and would tell them apart from someone level with them: each a
    question a recruiter can ask. Nothing here changes a score, a tier or the order."""
    out = []
    if p.fact("home") is None:
        if bg is None:
            from .resume import background
            bg = background(world, p)
    if p.fact("home") is None and not bg["header"].get("home"):
        out.append({"key": "home", "text": "where they live"})
    degrees = [f.value["year"] for f in p.keyed("education").values() if isinstance(f.value, dict) and f.value.get("year")]
    spans = [f.value for f in p.keyed("experience").values() if isinstance(f.value, dict) and f.value.get("years_documented")]
    if not spans:
        out.append({"key": "history", "text": "a dated work history"})
    elif degrees:
        longest = max(spans, key=lambda v: v["years_documented"])
        started = world.today.year + world.today.timetuple().tm_yday / 365.25 - longest["years_documented"]
        if started - (max(degrees) + 1) >= 0.5:           # more than half a year after the degree year is unaccounted for
            out.append({"key": "history", "text": f"what they did between {max(degrees)} and {longest.get('since') or int(started)}"})
    if job.reqs_of("req.license") and not any(isinstance(f.value, dict) and (f.value.get("number") or f.value.get("expires"))
                                               for f in p.keyed("license").values()):
        out.append({"key": "license", "text": "their license number and expiry date"})
    return out


def documents_key(world, p) -> int | None:
    """One number for everything the person wrote or sent, so two people with word-for-word the same documents
    can be recognised. None when they have no documents of their own."""
    from .resume import OWN_WORDS
    texts = sorted(" ".join(world.evidence[i].text.lower().split()) for i in p.evidence_ids
                   if world.evidence[i].kind in OWN_WORDS and world.evidence[i].text)
    return zlib.crc32("\n".join(texts).encode("utf-8")) if texts else None


def _links(bg: dict) -> list:
    return [x["url"] for x in bg["links"]][:3]


def found_for(world, m: Match, filters) -> list:
    """For each criterion the recruiter typed, the words in this person's own documents that meet it."""
    from .resume import mention
    out = []
    for f in filters or ():
        kind, _, rest = f.partition(":")
        if kind == "text":
            hit = mention(world, world.people[m.person], rest)
            if hit:
                out.append({"phrase": rest, "quote": hit[0], "source": hit[1].label})
        if kind == "school":
            from .resume import studied_at
            line = studied_at(world, world.people[m.person], rest)
            if line:
                out.append({"phrase": rest, "quote": line["text"], "source": line["source"]})
    return out


def _also(bg: dict) -> list:
    """Skills on the person's résumés that no rule checks, so the card can say what else they have done."""
    return [s["text"] for s in bg["skills"] if not s["checked"]]


def match_dict(world, m: Match) -> dict:
    """A card plus the full explanation. Needs a full match (see Shortlist.explain)."""
    d = card_dict(world, m)
    d["dim_detail"] = [{"name": k, "label": dimensions.LABELS[k], "value": None if v is None else round(v[0]),
                        "low": None if v is None else round(v[1]), "high": None if v is None else round(v[2]),
                        "why": "" if v is None else v[3], "ref": None if v is None else v[4]}
                       for k, v in (m.dim_detail or {}).items()]
    d.update(action=m.action, questions=m.questions,
             results=[result_dict(world, r) for r in m.results], gates=[result_dict(world, g) for g in m.gates])
    return d


def _req_text(lex, attr: str, key: str, value) -> str:
    if attr == "req.min_years":
        return f"{fmt_years(value['years'])} of experience, {'preferred' if value.get('level') == 'preferred' else 'minimum'}"
    if attr == "req.skill":
        return f"{lex.label(key)}: {value.get('level', 'required')}" + (" (can be trained)" if value.get("trainable") else "")
    if attr == "req.license":
        return f"{STATE_NAMES.get(key, key)} license, active before the start"
    if attr == "req.cert_path":
        if key == "preferred":
            return " or ".join(lex.label(k) for k in value["any_of"]) + " preferred"
        return "Other boards considered with " + ", ".join(lex.label(k) for k in value["all_of"])
    if attr == "req.start":
        return f"Starts {fmt_date(value)}"
    if attr == "req.weeks":
        return f"{value} weeks"
    if attr == "req.hours":
        return f"{value} hours a week"
    if attr == "req.schedule":
        return str(value).capitalize()
    if attr == "req.shifts":
        from datetime import datetime
        parts = []
        for s in value:
            a, b = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
            parts.append(f"{WEEKDAY_NAMES[a.weekday()][:3]} {fmt_date(a.date())} {a:%H:%M}–{b:%H:%M}")
        return "; ".join(parts) + " (local time)"
    if attr == "req.pay":
        return f"Candidate pay {fmt_pay(value)}" + (f" for {value['hours']} hours" if value.get("hours") else "")
    if attr in ("req.setting", "req.profession"):
        return lex.label(value).capitalize()
    return str(value)


REQ_ORDER = ["req.profession", "req.setting", "req.min_years", "req.skill", "req.license", "req.cert_path",
             "req.start", "req.schedule", "req.hours", "req.weeks", "req.shifts", "req.pay"]
REQ_LABEL = {"req.profession": "Profession", "req.setting": "Setting", "req.min_years": "Experience", "req.skill": "Skill",
             "req.license": "License", "req.cert_path": "Certification", "req.start": "Start", "req.schedule": "Pattern",
             "req.hours": "Hours", "req.weeks": "Length", "req.shifts": "Shifts", "req.pay": "Pay"}
NOTE_TEXT = {"note.start_firm": "No exception to the start date is confirmed.",
             "note.partial_cover": "Whether the facility accepts cover for only some shifts is not confirmed.",
             "note.alt_path": "The facility has not approved any individual on the other-boards route.",
             "note.feed_pending": "The vendor feed has not been updated with the facility's clarification.",
             "note.pay_basis": "The pay figure is candidate pay, not the facility bill rate.",
             "note.all_shifts": "The request is for every listed shift."}


def job_dict(world, job, cfg: dict) -> dict:
    """Everything the interface shows about a job, including both versions of any requirement that differs."""
    policy = cfg["requirement_changes"]["policy"]
    lex = world.lex

    def ref(claim):
        return {"evidence": claim.evidence_id, "quote": claim.quote, "note": ""}

    lines = []
    for attr in REQ_ORDER:
        for key, req in sorted(job.reqs_of(attr).items()):
            primary, other, differs = req.pick(policy)
            if primary is None and other is None:
                continue
            line = {"label": REQ_LABEL[attr], "text": None, "ref": None, "status": "agreed", "other": None}
            if primary is not None:
                line["text"], line["ref"] = _req_text(lex, attr, key, primary.value), ref(primary)
            if differs:
                using_note = primary is not None and primary.version == "clarification"
                line["status"] = ("note_in_use" if policy == "trusted" else "provisional") if using_note else "pending"
                if other is not None:
                    line["other"] = {"text": _req_text(lex, attr, key, other.value), "ref": ref(other),
                                     "role": "Vendor feed" if using_note else "Account-manager note"}
                if primary is None:
                    line["text"] = "Not stated in the vendor feed"
            lines.append(line)
    notes = [{"text": NOTE_TEXT[attr], "ref": ref(req.clar or req.feed)}
             for (attr, _), req in sorted(job.reqs.items()) if attr in NOTE_TEXT and (req.clar or req.feed)]
    loc = job.location
    start, weeks, pay = (job.req(a) for a in ("req.start", "req.weeks", "req.pay"))
    shifts = job.req("req.shifts")
    when = []
    if start and start.feed:
        when.append(f"From {fmt_date(start.feed.value)}")
    if weeks and weeks.feed:
        when.append(f"{weeks.feed.value} weeks")
    if shifts and shifts.feed:
        when.append(_req_text(lex, "req.shifts", "", shifts.feed.value))
    facility = job.meta.get("facility")
    return {
        "key": job.key, "ids": job.ids, "title": job.titles[0] if job.titles else job.key,
        "other_titles": job.titles[1:], "facility": facility.value if facility else None,
        "place": ", ".join(x for x in (loc.get("city"), STATE_NAMES.get(loc.get("state"), loc.get("state"))) if x),
        "status": job.status, "division": cfg.get("divisions", {}).get(job.division or "", {}).get("label", "Unassigned"),
        "when": " · ".join(when), "pay": fmt_pay(pay.feed.value) if pay and pay.feed else None,
        "requirements": lines, "notes": notes, "note_ids": job.note_ids, "evidence_ids": job.evidence_ids,
        "first_seen": job.first_seen.isoformat() if job.first_seen else None,
    }


def namesakes(world, name: str) -> int:
    """How many other people on file show the same name. Kept per world and rebuilt when people change."""
    key = (len(world.people), world.claim_count)
    cache = world.__dict__.get("_namesakes")
    if cache is None or cache[0] != key:
        counts: dict[str, int] = {}
        for other in world.people.values():
            counts[other.name.lower()] = counts.get(other.name.lower(), 0) + 1
        cache = (key, counts)
        world.__dict__["_namesakes"] = cache
    return cache[1].get(name.lower(), 1) - 1


def short_ref(p) -> str:
    """The person's first record, short enough for a card: "ATS C2", "Our system R04"."""
    first = p.records[0] if p.records else p.key.split(":", 1)[1].replace(":", " ")
    return first.replace(" record ", " ").replace(" profile ", " ").replace("Client database person", "Client DB")


def person_dict(world, p) -> dict:
    home = p.fact("home")
    profession = str(p.fact("profession").value) if p.fact("profession") else None
    settings = sorted(((f.value.get("since") is None, k, f) for k, f in p.keyed("experience").items() if f.state == "pos"),
                      key=lambda x: (x[0], x[1]))
    experience = [world.lex.label(k).capitalize() + (f" since {f.value['since']}" if f.value.get("since") else "") for _, k, f in settings]
    return {
        "key": p.key, "name": p.name, "named": p.name_basis == "stated", "name_basis": p.name_basis, "records": p.records,
        "profession": profession,
        "headline": " · ".join(x for x in (profession, experience[0] if experience else None) if x),
        "home": (f"{home.value.get('city')}, {STATE_NAMES.get(home.value.get('state'), home.value.get('state'))}" if home else None),
        "evidence_ids": p.evidence_ids,
        "linked_by": world.link_reasons.get(p.key, []),
        "ref": short_ref(p), "namesakes": namesakes(world, p.name),
        "other_names": sorted({str(c.value) for c in (p.fact("name").older if p.fact("name") else [])} - {p.name}),
    }


def evidence_dict(world, ev) -> dict:
    return {
        "id": ev.id, "local_id": ev.local_id, "source": world.source_labels.get(ev.source, ev.source), "kind": ev.kind,
        "label": ev.label, "author": ev.author, "at": ev.observed_at.isoformat(), "date_only": ev.date_only,
        "text": ev.text, "same_as": world.root(ev.id) if world.root(ev.id) != ev.id else None,
        "repeats": [world.alias[r] for r in ev.related if r in world.alias],
    }
