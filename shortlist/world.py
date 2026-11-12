"""The world as of one moment: all evidence, who is who, and what is currently believed.

Resolution never deletes anything. Each fact keeps the claims behind its current
value and the older or disagreeing claims it replaced, so both can be shown.

Rules of resolution
  * Identity: records are the same person only on an explicit cross-reference or an
    identical email address. Identical profile text never merges two people.
  * Recency: for things a person controls (availability, pay, location, contact
    wishes) the newest statement wins and older ones are kept as "replaced".
  * Lineage: a fuller rendition of a résumé, or a note repeating a message, is the
    same evidence as its original and is not counted as a second source.
  * Experience: dated work history gives documented years, measured to the reference
    date. A bare "I have three years" is kept separately as a claim.
  * Job requirements: the vendor feed and an approver's note are kept as two versions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .extract import Extractor
from .lexicon import Lexicon
from collections import Counter

from .model import Batch, Claim, Evidence, Issue
from .timeutil import calendar_years, years_between

LATEST_ATTRS = ("name", "email", "profession", "specialty", "home", "pref_states", "travel", "status",
                "available_from", "schedule", "weeks", "hours", "pay", "dnc_until", "contact_allowed",
                "restriction_none", "owner", "route_via", "automation", "occupied_days")
KEYED_ATTRS = ("skill", "license", "cert", "location_interest", "shift_availability", "education")
JOB_META = ("title", "facility", "location", "status")
# Mailboxes that belong to a role or a team, not to one person. No name is ever taken from these.
ROLE_MAILBOXES = {"info", "admin", "office", "hr", "jobs", "careers", "contact", "hello", "mail", "sales", "support",
                  "team", "recruiting", "recruitment", "noreply", "no", "reply", "donotreply", "billing", "accounts", "test"}


def _shared_mailbox(email: str) -> bool:
    """An address a team or an office reads, such as info@ or jobs@. Two people can share one."""
    words = [w for w in re.split(r"[._+-]+", email.split("@")[0]) if w]
    return bool(words) and all(w in ROLE_MAILBOXES for w in words)


def name_from_email(email) -> str | None:
    """A likely name from an address such as jane.doe@example.org, or None when the address does not look like one.

    Only the clear case is accepted: two or three words made of letters, separated by a dot, underscore or hyphen.
    An id (r01@), initials (jd@), a single word (jane@), digits (jdoe42@) or a shared mailbox (info@) give nothing.
    The result is a guess for display. It is never used to decide that two records are the same person.
    """
    local = str(email or "").split("@")[0].split("+")[0].strip().lower()
    words = [w for w in re.split(r"[._-]+", local) if w]
    if not 2 <= len(words) <= 3 or any(not w.isalpha() for w in words):
        return None
    if len(words[0]) < 2 or len(words[-1]) < 2 or any(w in ROLE_MAILBOXES for w in words):
        return None
    return " ".join(w.capitalize() for w in words)


@dataclass(slots=True)
class Fact:
    attr: str
    key: str
    state: str            # pos | neg | unanswered
    value: object
    claims: list          # behind the current value: one per lineage, originals first
    older: list           # claims that said something different and were replaced
    observed_at: datetime
    origins: tuple        # distinct bases behind the current value


@dataclass(slots=True)
class Requirement:
    attr: str
    key: str
    feed: Claim | None            # what the vendor feed / ATS job record says
    clar: Claim | None            # what an approver's note says, if anything
    unapproved: list              # notes by roles that may not change requirements: shown, never used

    def pick(self, policy: str) -> tuple[Claim | None, Claim | None, bool]:
        """(version in force, the other version, whether the two say different things)."""
        if self.clar is None:
            return self.feed, None, False
        differs = self.feed is None or _norm(self.attr, self.feed.value) != _norm(self.attr, self.clar.value)
        if policy == "feed_only":
            return self.feed, self.clar, differs
        return self.clar, self.feed, differs


def _norm(attr: str, value):
    """A comparable form of a value, ignoring detail that does not change its meaning."""
    if isinstance(value, dict):
        if attr == "pay" or attr == "req.pay":
            return (float(value["amount"]), value["unit"])
        if attr == "placement":
            return value.get("status")
        if attr == "req.min_years":
            return (float(value["years"]), value.get("level"))
        if attr in ("req.skill", "req.license"):
            return value.get("level")
        if attr == "license":
            return True
        return tuple(sorted((k, _norm("", v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_norm("", v) if isinstance(v, (dict, list)) else v for v in value)
    if isinstance(value, str):
        return value.strip().lower()
    return value


class Person:
    __slots__ = ("key", "name", "name_basis", "profession", "records", "facts", "evidence_ids", "last_candidate_contact", "unclear")

    def __init__(self, key: str):
        self.key = key
        self.name = key
        self.name_basis = "id"            # stated | email | id : where the displayed name comes from
        self.unclear: list = []           # the candidate's own words the reader could not read with confidence, newest first
        self.profession = None
        self.records: list[str] = []
        self.facts: dict[tuple[str, str], Fact] = {}
        self.evidence_ids: list[str] = []
        self.last_candidate_contact: datetime | None = None

    def fact(self, attr: str, key: str = "") -> Fact | None:
        return self.facts.get((attr, key))

    def keyed(self, attr: str) -> dict[str, Fact]:
        return {k: f for (a, k), f in self.facts.items() if a == attr}


class Job:
    __slots__ = ("key", "ids", "titles", "meta", "reqs", "evidence_ids", "note_ids", "profession", "division", "first_seen")

    def __init__(self, key: str):
        self.key = key
        self.ids: dict[str, str] = {}
        self.titles: list[str] = []
        self.meta: dict[str, Fact] = {}
        self.reqs: dict[tuple[str, str], Requirement] = {}
        self.evidence_ids: list[str] = []
        self.note_ids: list[str] = []
        self.profession = None
        self.division = None
        self.first_seen: datetime | None = None

    def req(self, attr: str, key: str = "") -> Requirement | None:
        return self.reqs.get((attr, key))

    def reqs_of(self, attr: str) -> dict[str, Requirement]:
        return {k: r for (a, k), r in self.reqs.items() if a == attr}

    @property
    def status(self) -> str:
        f = self.meta.get("status")
        return f.value if f else "unknown"

    @property
    def location(self) -> dict:
        f = self.meta.get("location")
        return f.value if f else {}


class World:
    def __init__(self, cfg: dict, batches: list[Batch], as_of: datetime):
        self.cfg = cfg
        self.as_of = as_of
        self.today: date = date.fromisoformat(cfg["clock"]["reference_date"])
        self.lex = Lexicon(cfg)
        from .model_reader import make_reader      # optional second reader; None unless switched on in settings
        self.extractor = Extractor(cfg, self.lex, make_reader(cfg))
        self.source_order = {s["name"]: i for i, s in enumerate(cfg["sources"])}
        self.source_labels = {s["name"]: s.get("label", s["name"]) for s in cfg["sources"]}

        self.evidence: dict[str, Evidence] = {}
        self.alias: dict[str, str] = {}
        claims: list[Claim] = []
        links: list[tuple] = []
        for batch in batches:
            for ev in batch.evidence:
                self.evidence[ev.id] = ev
                self.alias.setdefault(ev.local_id, ev.id)
            claims.extend(batch.claims)
            links.extend(batch.links)

        derived: list[Claim] = []
        for c in claims:
            if c.attr in ("tag", "req.tag", "title"):
                derived.extend(self.extractor.derive(c))
        claims.extend(derived)
        for ev in self.evidence.values():
            claims.extend(self.extractor.run(ev))
        self.claim_count = len(claims)

        self.last_removed: set[str] = set()
        self._issues: dict[str, list[Issue]] = {}          # problems found while resolving each person or job
        self._global_issues: list[Issue] = []
        self._link_identities(claims, links)
        self._build_lineage()
        self.people: dict[str, Person] = {}
        self.jobs: dict[str, Job] = {}
        self.by_profession: dict[str, dict[str, Person]] = {}
        self._claims: dict[str, list[Claim]] = {}
        self._evs: dict[str, list[Evidence]] = {}
        for c in claims:
            self._claims.setdefault(self.canon(c.subject), []).append(c)
        for ev in self.evidence.values():
            self._evs.setdefault(self.canon(ev.subject), []).append(ev)
        for subject in sorted(set(self._claims) | set(self._evs)):
            self._resolve_subject(subject)

    # ------------------------------------------------------------ incremental
    def apply(self, batch: Batch, as_of: datetime | None = None) -> set[str]:
        """Apply one event without rebuilding the world. Only the people and jobs it touches are re-resolved.

        Returns the subjects that changed. The test suite checks that applying events this way
        gives exactly the same results as rebuilding from scratch.
        """
        for ev in batch.evidence:
            self.evidence[ev.id] = ev
            self.alias.setdefault(ev.local_id, ev.id)
        claims = list(batch.claims)
        for c in batch.claims:
            if c.attr in ("tag", "req.tag", "title"):
                claims.extend(self.extractor.derive(c))
        for ev in batch.evidence:
            claims.extend(self.extractor.run(ev))
        self.claim_count += len(claims)
        touched: set[str] = set()
        self.last_removed: set[str] = set()                   # people merged into someone else by this event
        merges = [(a, b) for a, b, _ in batch.links]
        if self.cfg.get("identity", {}).get("link_by_email", True):
            for c in claims:
                if c.attr == "email":
                    email = str(c.value).strip().lower()
                    merges.append((self._by_email.setdefault(email, c.subject), c.subject))
        for a, b in merges:                                   # a merge moves one person's material under the other
            before = {self.canon(a), self.canon(b)}
            self._union(a, b)
            keep = self.canon(a)
            for old in before - {keep}:
                self._claims.setdefault(keep, []).extend(self._claims.pop(old, []))
                self._evs.setdefault(keep, []).extend(self._evs.pop(old, []))
                gone = self.people.pop(old, None)
                if gone is not None:
                    self.by_profession.get(gone.profession, {}).pop(old, None)
                    self.last_removed.add(old)
                touched.add(keep)
        for ev in batch.evidence:
            self._add_lineage(ev)
            subject = self.canon(ev.subject)
            self._evs.setdefault(subject, []).append(ev)
            touched.add(subject)
        for c in claims:
            subject = self.canon(c.subject)
            self._claims.setdefault(subject, []).append(c)
            touched.add(subject)
        for subject in touched:
            self._resolve_subject(subject)
        if as_of is not None:
            self.as_of = as_of
        return touched

    def _resolve_subject(self, subject: str) -> None:
        evs = sorted(self._evs.get(subject, []), key=lambda e: e.observed_at, reverse=True)
        if subject.startswith("person:"):
            old = self.people.get(subject)
            if old is not None:
                self.by_profession.get(old.profession, {}).pop(subject, None)
            person = self._resolve_person(subject, self._claims.get(subject, []), evs)
            self.people[subject] = person
            self.by_profession.setdefault(person.profession, {})[subject] = person
        elif subject.startswith("job:"):
            self.jobs[subject] = self._resolve_job(subject, self._claims.get(subject, []), evs)

    # ---------------------------------------------------------------- identity
    def _link_identities(self, claims: list[Claim], links: list[tuple]) -> None:
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def rank(subject: str) -> tuple:
            source = subject.split(":")[1] if subject.count(":") >= 2 else ""
            return (self.source_order.get(source, 99), subject)

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                keep, drop = (ra, rb) if rank(ra) <= rank(rb) else (rb, ra)
                parent[drop] = keep

        self._union = union
        self._by_email: dict[str, str] = {}

        self.link_reasons: dict[str, list[str]] = {}
        recorded = {ev.subject for ev in self.evidence.values() if ev.kind == "structured_record"}
        emails = {c.subject: str(c.value).strip().lower() for c in claims if c.attr == "email"}
        short = lambda subject: subject.split(":", 1)[1].replace(":", " ")       # noqa: E731
        for a, b, reason in links:
            if b not in recorded:      # a cross-reference to a record its own source does not hold
                self._global_issues.append(Issue("warning", "all sources", short(a),
                                                 f"It refers to {short(b)}, which that source has no record of. The link is kept; the person is incomplete."))
            elif a in emails and b in emails and emails[a] != emails[b]:
                self._global_issues.append(Issue("info", "all sources", short(a),
                                                 f"It is linked to {short(b)} by an explicit reference, although the email addresses differ."))
            union(a, b)
            self.link_reasons.setdefault(find(a), []).append(f"{a} = {b} ({reason})")
        if self.cfg.get("identity", {}).get("link_by_email", True):
            by_email, shared = self._by_email, {}
            for c in claims:
                if c.attr == "email" and c.subject.startswith("person:"):
                    email = str(c.value).strip().lower()
                    if _shared_mailbox(email):
                        if email in shared and shared[email] != c.subject:
                            self._global_issues.append(Issue("info", "all sources", short(c.subject),
                                f"It uses {email}, a shared mailbox, as does {short(shared[email])}. A shared mailbox does not show "
                                "that two records are one person, so they are kept apart."))
                        shared.setdefault(email, c.subject)
                        continue
                    if email in by_email and find(by_email[email]) != find(c.subject):
                        first = by_email[email]
                        if first.split(":")[1] == c.subject.split(":")[1]:      # two records in ONE source share an email
                            self._global_issues.append(Issue("info", "all sources", short(c.subject),
                                                             f"It has the same email address as {short(first)} in the same source. They are treated as one person."))
                        union(first, c.subject)
                        self.link_reasons.setdefault(find(c.subject), []).append(f"{first} = {c.subject} (same email)")
                    by_email.setdefault(email, c.subject)
        self._find = find

    def canon(self, subject: str) -> str:
        return self._find(subject) if subject.startswith("person:") else subject

    # ----------------------------------------------------------------- lineage
    def _build_lineage(self) -> None:
        """Group evidence that is really the same material: renditions, and identical text for one subject."""
        self._root: dict[str, str] = {}
        self._by_text: dict[tuple, str] = {}
        self.notes_about: dict[str, list[str]] = {}        # original evidence id -> notes that repeat or discuss it
        for ev in self.evidence.values():
            self._add_lineage(ev, link_notes=False)
        for ev in self.evidence.values():                  # second pass: a note may arrive before what it repeats
            self._link_notes(ev)

    def _add_lineage(self, ev: Evidence, link_notes: bool = True) -> None:
        root = ev.id
        if ev.lineage and ev.lineage in self.alias:
            root = self.alias[ev.lineage]
        elif ev.text:
            key = (self.canon(ev.subject), " ".join(ev.text.split()).lower())
            root = self._by_text.setdefault(key, ev.id)
        seen = {ev.id}
        while root in self._root and self._root[root] != root and root not in seen:     # follow chains to the original
            seen.add(root)
            root = self._root[root]
        self._root[ev.id] = root
        if link_notes:
            self._link_notes(ev)

    def _link_notes(self, ev: Evidence) -> None:
        for local in ev.related:
            if local in self.alias:
                self.notes_about.setdefault(self.root(self.alias[local]), []).append(ev.id)

    def root(self, evidence_id: str) -> str:
        return self._root.get(evidence_id, evidence_id)

    def is_derived(self, c: Claim) -> bool:
        """True when the claim comes from a note that repeats other sources, or from a rendition."""
        ev = self.evidence[c.evidence_id]
        return bool(ev.related) or self.root(ev.id) != ev.id

    def _one_per_lineage(self, claims: list[Claim]) -> list[Claim]:
        ordered = sorted(claims, key=self.is_derived)       # originals first; stable
        seen, out = set(), []
        for c in ordered:
            ev = self.evidence[c.evidence_id]
            roots = {self.root(self.alias[r]) for r in ev.related if r in self.alias} or {self.root(ev.id)}
            if roots & seen and self.is_derived(c):
                continue
            if self.root(ev.id) in seen:
                continue
            seen.add(self.root(ev.id))
            out.append(c)
        return out

    def _markers(self, attr: str, key: str, claims: list[Claim]) -> Fact:
        """A question nobody has answered. Keep every marker, newest first, so the latest follow-up is visible."""
        seen, kept = set(), []
        for c in sorted(claims, key=lambda c: c.observed_at, reverse=True):
            if self.root(c.evidence_id) not in seen:
                seen.add(self.root(c.evidence_id))
                kept.append(c)
        return Fact(attr, key, "unanswered", None, kept, [], kept[0].observed_at, tuple(sorted({c.basis for c in kept})))

    def _fact(self, attr: str, key: str, state: str, value, same: list[Claim], older: list[Claim]) -> Fact:
        kept = self._one_per_lineage(same)
        return Fact(attr, key, state, value, kept, older, kept[0].observed_at,
                    tuple(sorted({c.basis for c in kept})))

    # -------------------------------------------------------------- resolution
    def _ordered(self, attr: str, claims: list[Claim], kind: str = "candidate") -> list[Claim]:
        """Claims in the order that decides which one counts: the configured source precedence for this fact,
        and the newest first within it. The default is newest first, whatever the source."""
        prec = self.cfg.get("precedence", {})
        rule = prec.get("attributes", {}).get(attr) or prec.get(kind, "newest")
        if not isinstance(rule, list):
            return sorted(claims, key=lambda c: c.observed_at, reverse=True)

        def rank(c: Claim) -> int:
            src = self.evidence[c.evidence_id].source
            return next((i for i, entry in enumerate(rule) if entry in (src, c.basis)), len(rule))
        return sorted(claims, key=lambda c: (rank(c), -c.observed_at.timestamp()))

    def _latest(self, attr: str, key: str, claims: list[Claim]) -> Fact:
        ordered = self._ordered(attr, claims, "job" if claims and claims[0].subject.startswith("job:") else "candidate")
        current = _norm(attr, ordered[0].value)
        same = [c for c in ordered if _norm(attr, c.value) == current]
        older = [c for c in ordered if _norm(attr, c.value) != current]
        return self._fact(attr, key, "pos", ordered[0].value, same, older)

    def _keyed(self, attr: str, key: str, claims: list[Claim]) -> Fact:
        decided = self._ordered(attr, [c for c in claims if c.polarity in ("pos", "neg")])
        if not decided:
            return self._markers(attr, key, claims)
        state = decided[0].polarity
        same = [c for c in decided if c.polarity == state]
        value = same[0].value
        if attr == "license":
            value = {"active": any(c.value.get("active") for c in same)}
        return self._fact(attr, key, state, value, same, [c for c in decided if c.polarity != state])

    def _experience(self, key: str, claims: list[Claim]) -> Fact:
        pos = [c for c in claims if c.polarity == "pos"]
        neg = [c for c in claims if c.polarity == "neg"]
        if neg and (not pos or max(c.observed_at for c in neg) > max(c.observed_at for c in pos)):
            return self._fact("experience", key, "neg", None, sorted(neg, key=lambda c: c.observed_at, reverse=True), pos)
        if not pos:
            return self._markers("experience", key, claims)
        dated = [c for c in pos if c.value.get("start")]
        claimed = sorted((c for c in pos if c.value.get("years_claimed") is not None), key=lambda c: c.observed_at, reverse=True)
        mentions = [c for c in pos if not c.value.get("start") and c.value.get("years_claimed") is None]
        value = {"years_documented": None, "since": None, "precision": None, "ongoing": False,
                 "years_claimed": claimed[0].value["years_claimed"] if claimed else None}
        if dated:
            spans = {(self.root(c.evidence_id), c.value["start"], c.value.get("end")) for c in dated}
            intervals = sorted((start, min(end or self.today, self.today)) for _, start, end in spans)
            years, cur_start, cur_end = 0.0, intervals[0][0], intervals[0][1]
            for start, end in intervals[1:]:           # overlapping roles are merged, so no month is counted twice
                if start <= cur_end:
                    cur_end = max(cur_end, end)
                else:
                    years += calendar_years(cur_start, cur_end)
                    cur_start, cur_end = start, end
            years += calendar_years(cur_start, cur_end)
            first = min(dated, key=lambda c: c.value["start"])
            value.update(years_documented=years, since=first.value.get("since"),
                         precision=first.value.get("precision"), ongoing=any(c.value.get("end") is None for c in dated))
        ordered = sorted(dated, key=lambda c: c.observed_at, reverse=True) + claimed + mentions
        return self._fact("experience", key, "pos", value, ordered, [])

    def _placement(self, claims: list[Claim]) -> Fact:
        ordered = sorted(claims, key=lambda c: c.observed_at, reverse=True)
        status = ordered[0].value.get("status")
        same = [c for c in ordered if c.value.get("status") == status]
        until = next((c.value["until"] for c in same if c.value.get("until")), None)
        return self._fact("placement", "", "pos", {"status": status, "until": until}, same,
                          [c for c in ordered if c.value.get("status") != status])

    def _resolve_person(self, subject: str, claims: list[Claim], evs: list[Evidence]) -> Person:
        p = Person(subject)
        p.evidence_ids = [e.id for e in evs]
        p.records = [e.label for e in sorted((e for e in evs if e.kind == "structured_record"),
                                             key=lambda e: self.source_order.get(e.source, 99))]
        said = [e.observed_at for e in evs if e.author == "candidate" and e.kind in ("candidate_message", "candidate_statement")]
        p.last_candidate_contact = max(said) if said else None
        grouped: dict[tuple[str, str], list[Claim]] = {}
        for c in claims:
            grouped.setdefault((c.attr, c.key), []).append(c)
        for (attr, key), group in grouped.items():
            if attr == "unclear":
                p.unclear = sorted(group, key=lambda c: c.observed_at, reverse=True)
            elif attr == "experience":
                p.facts[(attr, key)] = self._experience(key, group)
            elif attr == "placement":
                p.facts[(attr, key)] = self._placement(group)
            elif attr in KEYED_ATTRS:
                p.facts[(attr, key)] = self._keyed(attr, key, group)
            elif attr in LATEST_ATTRS:
                p.facts[(attr, key)] = self._latest(attr, key, group)
        local = subject.split(":")[-1]
        p.name, p.name_basis = self._display_name(p, local)
        self._issues[subject] = [] if any(e.kind == "structured_record" for e in evs) else [Issue(
            "warning", "all sources", f"person {local}",
            "There are notes or messages about this person, but no source holds a record of them. They are kept as a separate, incomplete person.")]
        prof = p.fact("profession")
        if prof:
            raw = str(prof.value)
            p.profession = self.lex.first("profession", raw) or raw.strip().lower().replace(" ", "_")
        return p

    def _display_name(self, p: Person, local: str) -> tuple[str, str]:
        """The name to show, and where it came from. A name a source states always wins.

        When no source states one, the configured fallbacks are tried in order. This is the same for every
        source and every person: nothing here knows which records happen to lack a name.
        """
        stated = p.fact("name")
        if stated is not None and str(stated.value).strip():
            return str(stated.value).strip(), "stated"
        for how in self.cfg.get("identity", {}).get("name_fallbacks", ["email", "id"]):
            if how == "resume":
                from .resume import parse
                docs = sorted((self.evidence[i] for i in p.evidence_ids if self.evidence[i].kind == "resume" and self.evidence[i].text),
                              key=lambda e: e.observed_at, reverse=True)
                found = next((parse(e.text)["header"].get("name") for e in docs if parse(e.text)["header"].get("name")), None)
                if found:
                    return found, "resume"
            elif how == "email" and p.fact("email") is not None:
                guess = name_from_email(p.fact("email").value)
                if guess:
                    return guess, "email"
            elif how == "id":
                break
        return f"Candidate {local}", "id"

    def _resolve_job(self, subject: str, claims: list[Claim], evs: list[Evidence]) -> Job:
        j = Job(subject)
        j.evidence_ids = [e.id for e in evs if e.kind != "job_note"]
        j.note_ids = [e.id for e in evs if e.kind == "job_note"]
        feeds = [e.observed_at for e in evs if e.kind in ("job_feed", "job_record")]
        j.first_seen = min(feeds) if feeds else None
        number = subject.split(":", 1)[1]
        problems = [] if feeds else [Issue("warning", "all sources", f"job {number}",
                                           "There are notes about this job, but no feed or job record lists it. Nobody is matched to it.")]
        grouped: dict[tuple[str, str], list[Claim]] = {}
        for c in claims:
            grouped.setdefault((c.attr, c.key), []).append(c)
        for (attr, key), group in grouped.items():
            if attr == "job_id":
                j.ids[key] = str(group[0].value)
            elif attr == "title":
                for c in sorted(group, key=lambda c: self.source_order.get(self.evidence[c.evidence_id].source, 99), reverse=True):
                    if c.value not in j.titles:
                        j.titles.append(c.value)
            elif attr in JOB_META:
                j.meta[attr] = self._latest(attr, key, group)
            elif attr.startswith(("req.", "note.")) and attr != "req.tag":
                ordered = sorted(group, key=lambda c: (c.observed_at, not self.is_derived(c)), reverse=True)
                feed = next((c for c in self._ordered(attr, ordered, "job") if c.version == "feed"), None)
                clar = next((c for c in ordered if c.version == "clarification"), None)
                if clar is not None:      # show the note that first made the change, not one that repeats it
                    originals = [c for c in ordered if c.version == "clarification" and not self.is_derived(c)
                                 and _norm(attr, c.value) == _norm(attr, clar.value)]
                    clar = originals[-1] if originals else clar
                j.reqs[(attr, key)] = Requirement(attr, key, feed, clar, [c for c in ordered if c.version == "unapproved"])
        for (attr, key), group in grouped.items():         # do the job's own sources agree with each other?
            if not (attr.startswith("req.") or attr == "status") or attr == "req.tag":
                continue
            latest: dict[str, Claim] = {}
            for c in sorted((c for c in group if c.version == "feed"), key=lambda c: c.observed_at):
                latest[self.evidence[c.evidence_id].source] = c
            if len({repr(_norm(attr, c.value)) for c in latest.values()}) > 1:
                what = attr.replace("req.", "").replace("_", " ") + (f" ({key})" if key else "")
                rule = self.cfg.get("precedence", {}).get("attributes", {}).get(attr) or self.cfg.get("precedence", {}).get("job", "newest")
                how = "The most recent value is used." if not isinstance(rule, list) else \
                    f"The value from {self.evidence[j.reqs[(attr, key)].feed.evidence_id].source if (attr, key) in j.reqs and j.reqs[(attr, key)].feed else 'the first source in the precedence order'} is used (source precedence: {', '.join(rule)})."
                problems.append(Issue("warning", "all sources", f"job {number}", f"The job's sources disagree on {what}. {how}"))
        self._issues[subject] = problems
        prof = j.req("req.profession")
        if prof and (prof.feed or prof.clar):
            j.profession = (prof.feed or prof.clar).value
        for name, division in self.cfg.get("divisions", {}).items():
            if j.profession in division.get("professions", ()):
                j.division = name
        return j

    @property
    def issues(self) -> list[Issue]:
        """Every data problem the tool worked around, as of now."""
        out = list(self._global_issues)
        for subject in sorted(self._issues):
            out.extend(self._issues[subject])
        known = {canon for canon, _ in self.lex.families.get("profession", ())}
        odd = Counter(str(p.fact("profession").value) for p in self.people.values() if p.profession and p.profession not in known)
        if odd:
            out.append(Issue("info", "all sources", "professions",
                             f"{sum(odd.values())} {'person has' if sum(odd.values()) == 1 else 'people have'} a profession the vocabulary does not list "
                             f"({', '.join(sorted(odd)[:3])}). They are not matched to any job until it is added to the configuration."))
        return out

    # ------------------------------------------------------------- conveniences
    def notes_on(self, evidence_ids) -> list[str]:
        """Notes that repeat or discuss any of these pieces of evidence."""
        out = []
        for eid in evidence_ids:
            for note in self.notes_about.get(self.root(eid), []):
                if note not in out and note not in evidence_ids:
                    out.append(note)
        return out

    def years_to_today(self, start: date) -> float:
        return years_between(start, self.today)
