"""Text-reading rules: turn résumés, notes and messages into claims.

Every claim keeps the exact clause it came from, so a recruiter can see the words
behind each fact. The reader is deliberately rule-based: it is deterministic, it
needs no paid service, and every rule that fires (or never fires) is counted.
It only understands the phrasings below; anything it cannot read is listed as
"unparsed" rather than guessed at.

Three things it is careful about:
  * "No home-health detail supplied" mentions home health but is not experience.
    Clauses that say something is missing or unanswered produce an "unanswered" marker.
  * "I have not used Homecare Homebase" is a denial, not a skill.
  * Who is speaking matters. A recruiter's note cannot assert a candidate's skills
    (see [authority] in the configuration); it can record that a question is unanswered.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from .lexicon import STATE_RE, Lexicon, state_code
from .model import BASIS, Claim, Evidence
from .timeutil import (MONTH_RE, NUM_RE, WEEKDAY_RE, month_of, next_month_day, norm_unit, parse_date_phrase,
                       parse_start_token, to_number, weekday_of)

# ------------------------------------------------------------ rule catalogue
# id -> what the rule reads. The coverage report lists every id, fired or not.
RULES = {
    "exp.dated": "A role in a care setting with dates",
    "exp.mention": "A care setting mentioned without dates",
    "exp.years_claimed": "A stated number of years in a setting",
    "exp.unanswered": "A setting that was asked about or is not described",
    "exp.negated": "A setting the person says they have not worked in",
    "skill.mention": "A skill or tool the person describes using",
    "skill.negated": "A skill or tool the person says they have not used",
    "skill.unanswered": "A skill that was asked about or is not described",
    "license.reported": "A state license the person reports",
    "cert.listed": "A named certificate",
    "cert.board": "A board certification in a named specialty",
    "education": "A degree or residency",
    "avail.start": "A date the person can start",
    "avail.schedule": "A working pattern such as weekdays",
    "avail.weeks": "A contract length in weeks",
    "avail.hours": "Weekly hours",
    "shift.available": "Specific dates the person can cover",
    "shift.unavailable": "Specific dates the person cannot work",
    "avail.occupied_days": "Weekdays taken by a current assignment",
    "pay.ask": "A minimum or expected pay rate",
    "location.interest": "A place the person is open to",
    "location.withdrawn": "A place the person no longer considers",
    "contact.do_not_contact": "A request not to be contacted until a date",
    "contact.route_via": "An instruction to go through a named recruiter",
    "contact.owner": "The recruiter who owns the relationship",
    "contact.unassigned": "No recruiter is assigned",
    "contact.allowed_field": "A contact_allowed field quoted in a note",
    "contact.no_restriction": "No contact restriction is recorded",
    "contact.placement_none": "No placement is recorded",
    "contact.placement_current": "A current placement exists",
    "contact.no_automation": "Automated outreach is not authorized",
    "tag.setting": "A care setting in a structured skills list",
    "tag.skill": "A skill in a structured skills list",
    "title.profession": "The profession named in a job title",
    "title.setting": "The care setting named in a job title",
    "tag.license": "A license in a structured requirements list",
    "tag.required_skill": "A skill in a structured requirements list",
    "job.profession": "The profession named in the job text",
    "job.setting": "The care setting named in the job text",
    "job.min_years": "A minimum or preferred number of years",
    "job.license": "A state license the job requires",
    "job.skill": "A skill the job requires or prefers",
    "job.cert_preferred": "The preferred board certification",
    "job.cert_alternative": "The alternative route for other boards",
    "job.schedule": "The job's working pattern",
    "job.hours": "The job's weekly hours",
    "job.weeks": "The job's length in weeks",
    "job.start": "The job's start date",
    "job.pay": "The approved candidate pay",
    "job.note.start_firm": "No exception to the start date is confirmed",
    "job.note.partial_cover": "Partial shift cover is not confirmed",
    "job.note.alt_path": "Nobody is approved on the alternative route yet",
    "job.note.feed_pending": "The vendor feed has not been updated yet",
    "job.note.pay_basis": "The figure is candidate pay, not the bill rate",
    "job.note.all_shifts": "Every listed shift is requested",
}

ATTR_CLASS = {
    "experience": "experience", "skill": "experience", "education": "experience",
    "license": "credential", "cert": "credential",
    "available_from": "availability", "schedule": "availability", "weeks": "availability", "hours": "availability",
    "shift_availability": "availability", "occupied_days": "availability",
    "pay": "pay", "location_interest": "location",
    "dnc_until": "contact", "route_via": "contact", "owner": "contact", "contact_allowed": "contact",
    "restriction_none": "contact", "placement": "contact", "automation": "contact",
}

# ------------------------------------------------------------------ patterns
ABSENT = re.compile(
    r"\b(?:no|not|neither)\b[^.;]*\b(?:supplied|included|listed|attached|stated|described)\b"
    r"|\bno\b[^.;]*\bdetail\b"
    r"|\bdoes not (?:describe|supply|list|state|include|mention)\b"
    r"|\b(?:has|have) not (?:yet )?(?:been )?answered\b"
    r"|\bno (?:response|reply)\b", re.I)
ASKED = re.compile(r"\b(?:I|we)(?:\s+have)?\s+asked\b|\bquestions?\s+about\b", re.I)
NO_REPLY = re.compile(r"\bno\s+(?:response|reply)\b|\bnot\s+(?:yet\s+)?answered\b|\bnot\s+recorded\s+yet\b", re.I)
# Doubt. A clause that holds a negation the reader cannot place ("OASIS is not something I'm familiar
# with", "my Washington license lapsed"), or that is unsure ("I plan to", "pending", a question), yields no
# yes at all. It is left unread instead: a question for the recruiter, never a false match.
# The negations the reader does understand (the patterns listed in _placed) are taken out before this check.
NEGATION = re.compile(r"\b(?:not|never|no|without|unfamiliar|lacks?|lacking|neither|nor|cannot|unable|lapsed|expired|"
                      r"revoked|suspended|inactive)\b|n['’]t\b|\btoo\s+(?:long|short|early|late|far|much|little)\b", re.I)
UNSURE = re.compile(r"\b(?:plan|plans|planning|hope|hopes|hoping|hopefully|might|maybe|perhaps|possibly|pending|apply|"
                    r"applying|applied\s+for|studying|learning|scheduled|working\s+towards?|in\s+progress|intend|intends|"
                    r"whether|if)\b", re.I)
MAY = re.compile(r"\bmay\b")                        # lower case only, so the month May is not doubt
# Ambition is not experience: "interested in moving into home health" says nothing about having done it.
# Used for experience, skills and credentials only, so "interested in Spokane" still reads as a place.
AMBITION = re.compile(r"\b(?:interested\s+in|wants?\s+to|would\s+like\s+to|looking\s+to|moving\s+into|"
                      r"hope\s+to|aim\s+to|keen\s+to)\b", re.I)
# Not first-hand. A clause can name a skill, a setting or a credential without the person doing it: they
# watched, studied or heard of it, or someone else does it. Like ambition, it yields no yes for experience,
# skills or credentials, and is left unread: a question for the recruiter.
SECOND_HAND = re.compile(
    r"\bin\s+theory\b|\btheoretical(?:ly)?\b|\bexposure\b|\bexposed\s+to\b"
    r"|\b(?:webinar|seminar|workshop|lecture|tutorial|coursework)s?\b|\bcourses?\s+(?:on|in|about)\b"
    r"|\b(?:took|taking|attended|attending|completed)\s+(?:an?\s+|the\s+)?(?:[\w-]+\s+){0,3}(?:course|class|module)\b"
    r"|\b(?:read|reading|heard|hearing|know|knew)\s+(?:about|of)\b|\baware(?:ness)?\b"
    r"|\b(?:basic|limited|minimal|some|passing|general)\s+(?:understanding|knowledge|familiarity|awareness)\b|\bintroductory\b"
    r"|\bshadow(?:ed|ing)?\b|\bobserv(?:ed|ing|ation|er)\b|\breferr(?:ed|ing|als?)\b|\bas\s+a\s+student\b|\brotations?\b", re.I)
SOMEONE_ELSE = re.compile(
    r"\b(?:my|our|the|a)\s+(?:colleagues?|co-?workers?|team(?:mates?)?|manager|supervisor|staff|aides?|assistants?|partner|"
    r"wife|husband|friend|sister|brother|mother|father|students?)\b"
    r"|\b(?:aides?|assistants?|staff|students?|others|people)\s+who\b"
    r"|\bsupervis(?:e|es|ed|ing)\s+(?:the\s+)?(?:aides?|assistants?|staff|students?|others|people)\b", re.I)
# A credential once held, or on its way, is not a credential held now: "held a Washington license until 2023",
# "license application submitted", "eligible by reciprocity". Read for licenses and certificates only.
NOT_NOW = re.compile(
    r"\b(?:formerly|previously|used\s+to)\b|(?<!have\s)(?<!has\s)(?<!ve\s)\bheld\b"
    r"|\b(?:eligible|eligibility|application|awaiting|booked|reciprocity)\b|\bunder\s+review\b", re.I)
ENDED = re.compile(r"\b(?:until|through|thru)\s+(?P<y>(?:19|20)\d{2})\b", re.I)
NEGATED = re.compile(
    r"\b(?:have|has|had)\s+(?:not|never)\s+(?:used|worked|done|performed|practi[cs]ed)\b"
    r"|\bnever\s+(?:used|worked|done)\b|\bno\s+experience\s+(?:with|in|of)\b|\bnot\s+familiar\s+with\b", re.I)

_YM = rf"(?:{MONTH_RE}\s+)?\d{{4}}(?:-\d{{2}})?"
RANGE = re.compile(rf"(?P<s>{_YM})\s*(?:to|through|until|–|—|-)\s*(?P<e>present|current|now|{_YM})", re.I)
SINCE = re.compile(rf"\bsince\s+(?P<s>{_YM})", re.I)
YEARS_OF = re.compile(rf"\b(?P<n>{NUM_RE})\s+years?\s+(?:of|in)\s+(?P<rest>[^,.;]*)", re.I)
LICENSE = re.compile(
    rf"(?P<pre>\bactive\s+)?\b(?P<state>{STATE_RE})\s+(?:(?:PT|physical[- ]therapy|physician|medical|nursing)\s+)?"
    rf"licen[cs]e\b(?P<post>\s*[—–-]\s*active\b)?", re.I)
BOARD = re.compile(r"\b(?P<spec>[A-Za-z]+(?:-[A-Za-z]+)?)\s+board\s+certification\b", re.I)
EDUCATION = re.compile(r"\b(DPT|Doctor of Physical Therapy|residency)\b", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

_DATE = rf"(?:{MONTH_RE}\s+\d{{1,2}}(?:,\s*\d{{4}})?|\d{{4}}-\d{{2}}-\d{{2}})"
CORRECTION = re.compile(rf",?\s*\bnot\s+{_DATE}", re.I)          # "December 8, not December 1"
CANNOT_START = re.compile(rf"\b(?:cannot|can't|can not|unable to)\s+start\s+(?:until|before)\s+(?P<d>{_DATE})", re.I)
CAN_START = re.compile(rf"\b(?:can|could|able to|available to)\s+start\s+(?:on\s+|from\s+)?(?P<d>{_DATE})", re.I)
AVAILABLE = re.compile(rf"\bavailable\s+(?:from\s+|on\s+|starting\s+)?(?P<d>{_DATE})", re.I)
SCHEDULE = re.compile(r"\b(weekdays|weekends|nights|evenings)\b", re.I)
WEEKS = re.compile(rf"\b(?P<n>{NUM_RE})\s+weeks\b", re.I)
HOURS = re.compile(rf"\b(?P<n>{NUM_RE})\s+hours\b", re.I)
PAY = re.compile(r"\$\s?(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?:/|per\s+|a\s+)\s*(?P<unit>week|wk|hour|hr)\b", re.I)
PAY_CUE = re.compile(r"\b(?:need|minimum|at least|expect|require)", re.I)
MIN_CUE = re.compile(r"\b(?:need|minimum|at least)", re.I)

_PLACE = r"(?P<place>[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)"
LOC_POS = re.compile(r"\b(?i:open to|interested in|would consider|willing to (?:work in|relocate to|travel to))\s+" + _PLACE)
LOC_NEG = [
    re.compile(_PLACE + r"\s+(?i:is\s+no\s+longer\s+(?:a\s+|an\s+)?(?:location|place|option|area))"),
    re.compile(r"\b(?i:no\s+longer\s+(?:open\s+to|interested\s+in|considering))\s+" + _PLACE),
    re.compile(r"\b(?i:not\s+(?:open\s+to|interested\s+in))\s+" + _PLACE),
]

TOPIC_START = re.compile(r"\b(?:start|starting|begin|available|availability)\b", re.I)
TOPIC_SCHEDULE = re.compile(r"\b(?:weekdays?|weekends?|nights?|evenings?|shifts?|weeks?|hours?|schedule|contract)\b", re.I)
TOPIC_PAY = re.compile(r"\b(?:pay|rate|salary|wage|compensation)\b", re.I)
_DAYS = (rf"(?P<month>{MONTH_RE})\s+(?P<days>\d{{1,2}}(?:\s*(?:,|and|or|&|–|-|to)\s*\d{{1,2}})*)"
         r"(?:,\s*(?P<year>\d{4}))?")
SHIFT_NEG = re.compile(r"\b(?:cannot|can't|can not|unable to|not able to|not available to)\s+(?:cover|work|take)\b[^.]*?" + _DAYS, re.I)
SHIFT_POS = re.compile(r"\b(?:could|can|able to|available to|happy to)\s+(?:cover|work|take)\b[^.]*?" + _DAYS, re.I)
OCCUPIED = re.compile(rf"\b(?:assignment|placement|contract|schedule)\b[^.]*?\b(?P<a>{WEEKDAY_RE})\s*(?:through|thru|to|–|-)\s*(?P<b>{WEEKDAY_RE})\b", re.I)

# Ways a candidate asks not to be contacted. Read from the candidate's own words only, so a recruiter's
# "did not call back" is never taken as a request. A request with no end date blocks contact until withdrawn.
_STOP = r"(?:do\s+not|don['’]t|dont|not|no\s+more|stop|hold\s+off(?:\s+on)?|refrain\s+from|never)"
_REACH = (r"(?:contact\w*|reach(?:ing)?\s+out|call\w*|e-?mail\w*|messag\w*|texts?|texting|approach\w*|get\s+in\s+touch|"
          r"hear(?:ing)?\s+(?:about|from)|send(?:ing)?\s+me)")
DNC_ANY = re.compile(rf"\b{_STOP}\b[^.;]*?\b{_REACH}", re.I)
NOT_LOOKING = re.compile(r"\bnot\s+(?:looking|available|interested)\s+(?:for|in)\s+(?:any\s+|new\s+)?(?:work|jobs?|roles?|"
                         r"opportunities|assignments?|positions?)\b", re.I)
OPT_OUT = re.compile(r"\b(?:remove\s+me\s+from|unsubscribe|opt\s+out|take\s+me\s+off)\b", re.I)
WELCOME = re.compile(r"\b(?:hesitate|feel\s+free)\b", re.I)      # "don't hesitate to call me" invites contact
UNTIL = re.compile(rf"\b(?:until|till|before)\s+(?P<d>{MONTH_RE}\s+\d{{1,2}}(?:,\s*\d{{4}})?)", re.I)
NO_END = date(9999, 12, 31)                                       # a request with no end date
DNC = re.compile(rf"\b(?:do not|don't|no(?:\s+\w+)?)\s+contact\b[^.]*?\buntil\s+(?P<d>{MONTH_RE}\s+\d{{1,2}}(?:,\s*\d{{4}})?)", re.I)
ROUTE = re.compile(r"\b(?:go|goes|going|routed?)\s+through\s+(?:recruiter\s+)?(?P<r>R\d+)\b", re.I)
OWNER = re.compile(r"\bAssigned\s+recruiter\s+(?P<a>R\d+)\b|\b(?P<b>R\d+)\s+owns\b", re.I)
UNASSIGNED = re.compile(r"\bUnassigned\b", re.I)
CONTACT_FIELD = re.compile(r"\bcontact_allowed\s*=\s*(true|false)\b", re.I)
NO_RESTRICTION = re.compile(r"\bno recorded contact restriction\b", re.I)
PLACEMENT_NONE = re.compile(r"\bno (?:current )?placement recorded\b", re.I)
PLACEMENT_CURRENT = re.compile(r"\bcurrent placement\b", re.I)
NO_AUTOMATION = re.compile(r"\bno authori[sz]ation for automated (?:candidate )?outreach\b", re.I)

# job text
MIN_EXP = re.compile(rf"\bminimum experience:?\s*(?P<n>{NUM_RE})\s+years?", re.I)
YEARS_EXP = re.compile(rf"\b(?P<n>{NUM_RE})\s+years?\s+of\s+(?P<what>[\w\s-]*?)\s*experience\b(?:\s+is)?\s*(?P<lvl>preferred|required|acceptable|needed)?", re.I)
REQ_LIST = re.compile(r"\bRequirements?:", re.I)
REQUIRED = re.compile(r"\brequire|\bneeded\b|\bmust\b|\bmandatory\b", re.I)
PREFERRED = re.compile(r"\bprefer|\bcan (?:be )?train", re.I)
TRAINABLE = re.compile(r"\bcan (?:be )?train", re.I)
CERT_PREF = re.compile(r"(?P<certs>[A-Z]{3,6}(?:/[A-Z]{3,6})*)\s+preferred\b")
OTHER_BOARDS = re.compile(r"\bother boards\b[^.;]*?\bwith\s+(?P<list>[^.;]+)", re.I)
JOB_HOURS = re.compile(r"\b(?P<n>\d+)\s+hours/week\b", re.I)
JOB_WEEKS = re.compile(r"\b(?P<n>\d+)\s+weeks\b", re.I)
JOB_START = re.compile(r"\bstart\s+(?P<d>\d{4}-\d{2}-\d{2})\b", re.I)
JOB_PAY = re.compile(r"(?:USD|\$)\s?(?P<amt>\d[\d,]*)\s*(?:/|per\s+)\s*(?P<unit>week|hour)(?:\s+for\s+(?P<h>\d+)\s+hours)?", re.I)
START_FIRM = re.compile(r"\bno confirmed exception to the [^.;]*\bstart\b", re.I)
PARTIAL_COVER = re.compile(r"\bnot confirmed whether\b[^.;]*\bonly one day\b", re.I)
ALT_UNAPPROVED = re.compile(r"\bno individual candidate has been approved\b", re.I)
FEED_PENDING = re.compile(r"\bfeed has not (?:yet )?been (?:edited|updated)\b", re.I)
PAY_BASIS = re.compile(r"\bnot the (?:facility )?bill rate\b", re.I)
ALL_SHIFTS = re.compile(r"\b(?:request|need|coverage) is for both\b", re.I)

_SENTENCE_GAP = re.compile(r"(?<=[.!?;])\s+")
_LINE = re.compile(r"[^\n]+")


def clauses(text: str):
    """Spans of the clauses in a text: each line, split at sentence ends and semicolons."""
    for line in _LINE.finditer(text):
        start = line.start()
        for gap in _SENTENCE_GAP.finditer(line.group()):
            end = line.start() + gap.start()
            if text[start:end].strip():
                yield start, end
            start = line.start() + gap.end()
        if text[start:line.end()].strip():
            yield start, line.end()


def doubt_in(t: str) -> tuple[bool, bool]:
    """(doubt, not first-hand) in one clause, after the negations the reader understands are taken out.
    The second is ambition ("interested in home health") or a passing mention ("shadowed", "my colleague
    handles it"). Used by the rule reader, by the guard on the model reader, and by the checker."""
    rest = _placed(t)
    doubt = bool(NEGATION.search(rest) or UNSURE.search(rest) or MAY.search(rest) or rest.rstrip().endswith("?"))
    return doubt, bool(AMBITION.search(rest) or SECOND_HAND.search(rest) or SOMEONE_ELSE.search(rest))


def not_held_now(t: str, anchor: date) -> bool:
    """A clause about a credential held in the past or still on its way."""
    if NOT_NOW.search(t):
        return True
    m = ENDED.search(t)
    return bool(m and int(m.group("y")) < anchor.year)


def _placed(t: str) -> str:
    """The clause with every negation the reader understands blanked out, so only unplaced ones remain."""
    for p in (NEGATED, ABSENT, NO_REPLY, CORRECTION, CANNOT_START, SHIFT_NEG, DNC, DNC_ANY, NOT_LOOKING, NO_RESTRICTION,
              PLACEMENT_NONE, NO_AUTOMATION, *LOC_NEG):
        t = p.sub(" ", t)
    return t


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _day_list(m: re.Match, anchor: date) -> list[date]:
    month = month_of(m.group("month"))
    numbers = [int(n) for n in re.findall(r"\d{1,2}", m.group("days"))]
    if len(numbers) == 2 and re.search(r"\d\s*(?:–|-|to)\s*\d", m.group("days")):
        numbers = list(range(numbers[0], numbers[1] + 1))
    if m.group("year"):
        return [date(int(m.group("year")), month, d) for d in numbers]
    return [next_month_day(month, d, anchor) for d in numbers]


class Extractor:
    def __init__(self, cfg: dict, lex: Lexicon, model=None):
        self.lex = lex
        self.model = model                            # an optional second reader (model_reader.py); None by default
        self.authority = {cls: set(kinds) for cls, kinds in cfg["authority"].items()}
        self.approvers = set(cfg["requirement_changes"]["approver_roles"])
        self.fired: Counter = Counter()
        self.unparsed: list[tuple[str, str]] = []     # (evidence id, clause) that produced no usable claim

    # ------------------------------------------------------------- plumbing
    def _claim(self, ev: Evidence, clause: str, rule: str, attr: str, key, value, polarity="pos", version="") -> Claim:
        return Claim(subject=ev.subject, attr=attr, key=key or "", value=value, polarity=polarity, evidence_id=ev.id,
                     quote=clause, observed_at=ev.observed_at, basis=BASIS.get(ev.kind, "structured"), rule=rule,
                     version=version)

    def _allowed(self, ev: Evidence, c: Claim) -> bool:
        if c.attr.startswith(("req.", "note.")):
            cls = "requirement"
        elif c.polarity == "unanswered":
            cls = "unanswered"
        else:
            cls = ATTR_CLASS.get(c.attr, "experience")
        return ev.kind in self.authority.get(cls, ())

    def run(self, ev: Evidence) -> list[Claim]:
        """Read one piece of evidence. Structured records carry no free text to read."""
        if not ev.text or ev.kind == "structured_record":
            return []
        text = ev.text
        is_job = ev.subject.startswith("job:")
        no_reply = bool(NO_REPLY.search(text))
        spans = list(clauses(text))
        out: list[Claim] = []
        for i, (s, e) in enumerate(spans):
            clause = text[s:e].strip(" \t•")
            nxt = text[spans[i + 1][0]:spans[i + 1][1]].strip(" \t•") if i + 1 < len(spans) else ""
            found: list[Claim] = []
            if is_job:
                self._job(ev, clause, found)
            else:
                self._person(ev, clause, nxt, no_reply, found)
            kept = [c for c in found if self._allowed(ev, c)]
            for c in kept:
                if c.polarity != "unclear":
                    self.fired[c.rule] += 1
            if not any(c.polarity != "unclear" for c in kept):
                self.unparsed.append((ev.id, clause))
            out.extend(kept)
        if self.model is not None and not is_job:
            out = self.model.combine(ev, out, self)
        return out

    def derive(self, c: Claim) -> list[Claim]:
        """Read structured values that are really short pieces of text: skill tags, requirement lists, job titles."""
        out: list[Claim] = []

        def mk(rule, attr, key, value):
            out.append(Claim(c.subject, attr, key, value, "pos", c.evidence_id, c.quote, c.observed_at, c.basis, rule, c.version))
            self.fired[rule] += 1

        text = str(c.value)
        if c.attr == "tag":
            for k in self.lex.find("setting", text):
                mk("tag.setting", "experience", k, {})
            for k in self.lex.find("skill", text):
                mk("tag.skill", "skill", k, True)
        elif c.attr == "req.tag":
            for m in LICENSE.finditer(text):
                mk("tag.license", "req.license", state_code(m.group("state")), {"level": "required"})
            for k in self.lex.find("skill", text):
                mk("tag.required_skill", "req.skill", k, {"level": "required", "trainable": False})
        elif c.attr == "title":
            profession = self.lex.first("profession", text)
            if profession:
                mk("title.profession", "req.profession", "", profession)
            setting = self.lex.first("setting", text)
            if setting:
                mk("title.setting", "req.setting", "", setting)
        return out

    # ------------------------------------------------------- people: clauses
    def _person(self, ev: Evidence, t: str, nxt: str, no_reply: bool, out: list) -> None:
        def add(rule, attr, value, key="", pol="pos"):
            out.append(self._claim(ev, t, rule, attr, key, value, pol))

        anchor = ev.observed_at.date()
        absent = bool(ABSENT.search(t))
        asked = ev.kind == "recruiter_note" and no_reply and bool(ASKED.search(t))
        neg = NEGATED.search(t)
        pol = "unanswered" if (absent or asked) else "neg" if neg else "pos"
        doubt, ambition = doubt_in(t)
        doubt = doubt and pol != "unanswered"
        unclear = doubt or (ambition and pol != "unanswered")   # ambition counts for experience, skills and credentials
        past = pol == "pos" and not_held_now(t, anchor)         # a credential once held or on its way: not held now
        if (unclear or past) and ev.author == "candidate":
            topics = self._topics(t, anchor)
            if not doubt:                                       # not first-hand: only experience, skills, credentials
                about = ("skill:", "setting:", "cert:", "license:") if unclear else ("cert:", "license:")
                topics = tuple(x for x in topics if x.startswith(about))
            if topics:
                # Not a fact: a sign that the candidate said something newer about these topics that could
                # not be read. A rule met on older evidence becomes a question instead (rules.evaluate).
                out.append(self._claim(ev, t, "unread", "unclear", "", topics, "unclear"))

        # experience in a care setting
        settings = self.lex.spans("setting", t)
        if settings:
            years = YEARS_OF.search(t)
            years_settings = self.lex.find("setting", years.group("rest")) if years else []
            rng = RANGE.search(t) or SINCE.search(t)
            if not rng and nxt and len(nxt) <= 40:
                rng = RANGE.match(nxt) or SINCE.match(nxt)       # "Role title" on one line, dates on the next
            for pos, k in settings:
                if pol == "unanswered":
                    add("exp.unanswered", "experience", None, k, "unanswered")
                elif pol == "neg" and pos > neg.start():
                    add("exp.negated", "experience", None, k, "neg")
                elif unclear:
                    continue
                elif k in years_settings:
                    add("exp.years_claimed", "experience", {"years_claimed": to_number(years.group("n"))}, k)
                elif rng:
                    start, precision, since = parse_start_token(rng.group("s"))
                    end_token = rng.groupdict().get("e")
                    end = None
                    if end_token and end_token.lower() not in ("present", "current", "now"):
                        end_date, end_precision, _ = parse_start_token(end_token)
                        end = date(end_date.year, 1, 1) if end_precision == "year" else end_date
                    add("exp.dated", "experience", {"start": start, "end": end, "precision": precision, "since": since}, k)
                else:
                    add("exp.mention", "experience", {}, k)

        # skills and tools
        for pos, k in self.lex.spans("skill", t):
            if pol == "unanswered":
                add("skill.unanswered", "skill", None, k, "unanswered")
            elif pol == "neg" and pos > neg.start():
                add("skill.negated", "skill", False, k, "neg")
            elif not unclear:
                add("skill.mention", "skill", True, k)

        if pol == "pos" and not unclear:
            for k in () if past else self.lex.find("cert", t):
                add("cert.listed", "cert", True, k)
            for m in () if past else BOARD.finditer(t):
                add("cert.board", "cert", True, "board:" + _slug(m.group("spec")))
            for m in () if past else LICENSE.finditer(t):
                add("license.reported", "license", {"active": bool(m.group("pre") or m.group("post"))}, state_code(m.group("state")))
            m = EDUCATION.search(t)
            if m:
                year = YEAR.findall(t)
                kind = "residency" if m.group(1).lower() == "residency" else "dpt"
                add("education", "education", {"year": int(year[-1]) if year else None}, kind)

        if pol != "unanswered":
            m = CANNOT_START.search(t) or (None if doubt else CAN_START.search(t) or AVAILABLE.search(t))
            if m:
                add("avail.start", "available_from", parse_date_phrase(m.group("d"), anchor))
            if ev.kind != "resume" and not doubt:
                m = SCHEDULE.search(t)
                if m:
                    add("avail.schedule", "schedule", m.group(1).lower())
                m = WEEKS.search(t)
                if m:
                    add("avail.weeks", "weeks", int(to_number(m.group("n"))))
                m = HOURS.search(t)
                if m:
                    add("avail.hours", "hours", int(to_number(m.group("n"))))
            if "$" in t and not doubt:
                m = PAY.search(t)
                if m and PAY_CUE.search(t):
                    h = HOURS.search(t)
                    add("pay.ask", "pay", {"amount": to_number(m.group("amt")), "unit": norm_unit(m.group("unit")),
                                           "hours": int(to_number(h.group("n"))) if h else None,
                                           "qualifier": "minimum" if MIN_CUE.search(t) else "expects"})
            withdrawn = [m for p in LOC_NEG for m in p.finditer(t)]
            for m in withdrawn:
                add("location.withdrawn", "location_interest", False, m.group("place"), "neg")
            if not withdrawn and not doubt:
                for m in LOC_POS.finditer(t):
                    add("location.interest", "location_interest", True, m.group("place"))
            m = SHIFT_NEG.search(t)
            if m:
                for d in _day_list(m, anchor):
                    add("shift.unavailable", "shift_availability", False, d.isoformat(), "neg")
            elif not doubt:
                m = SHIFT_POS.search(t)
                if m:
                    for d in _day_list(m, anchor):
                        add("shift.available", "shift_availability", True, d.isoformat())
            m = OCCUPIED.search(t)
            if m:
                a, b = weekday_of(m.group("a")), weekday_of(m.group("b"))
                add("avail.occupied_days", "occupied_days", list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1)))

        # contact, ownership and placement
        m = DNC.search(t)
        if m:
            add("contact.do_not_contact", "dnc_until", parse_date_phrase(m.group("d"), anchor))
        elif ev.author == "candidate" and not WELCOME.search(t) and (DNC_ANY.search(t) or NOT_LOOKING.search(t) or OPT_OUT.search(t)):
            until = UNTIL.search(t)
            add("contact.do_not_contact", "dnc_until", parse_date_phrase(until.group("d"), anchor) if until else NO_END)
        for m in ROUTE.finditer(t):
            add("contact.route_via", "route_via", m.group("r"))
        m = OWNER.search(t)
        if m:
            add("contact.owner", "owner", m.group("a") or m.group("b"))
        if UNASSIGNED.search(t):
            add("contact.unassigned", "owner", None)
        m = CONTACT_FIELD.search(t)
        if m:
            add("contact.allowed_field", "contact_allowed", m.group(1).lower() == "true")
        if NO_RESTRICTION.search(t):
            add("contact.no_restriction", "restriction_none", True)
        if PLACEMENT_NONE.search(t):
            add("contact.placement_none", "placement", {"status": "none", "until": None})
        elif PLACEMENT_CURRENT.search(t):
            add("contact.placement_current", "placement", {"status": "placed", "until": None})
        if NO_AUTOMATION.search(t):
            add("contact.no_automation", "automation", "not_authorized")

    def _topics(self, t: str, anchor) -> tuple:
        """What a clause is about, for a clause the reader could not read with confidence."""
        tags = {"skill:" + k for k in self.lex.find("skill", t)} | {"setting:" + k for k in self.lex.find("setting", t)}
        tags |= {"cert:" + k for k in self.lex.find("cert", t)} | ({"cert:board"} if BOARD.search(t) else set())
        tags |= {"license:" + state_code(m.group("state")) for m in LICENSE.finditer(t)}
        if TOPIC_START.search(t):
            tags.add("start")
        if TOPIC_SCHEDULE.search(t):
            tags.add("schedule")
        if "$" in t or TOPIC_PAY.search(t):
            tags.add("pay")
        for m in re.finditer(_DAYS, t, re.I):
            tags |= {"date:" + d.isoformat() for d in _day_list(m, anchor)}
        tags |= {"place:" + w for w in re.findall(r"\b[A-Z][a-z]+\b", t)}
        return tuple(sorted(tags))

    # --------------------------------------------------------- jobs: clauses
    def _job(self, ev: Evidence, t: str, out: list) -> None:
        is_note = ev.kind == "job_note"
        version = "feed" if not is_note else ("clarification" if ev.author in self.approvers else "unapproved")

        def add(rule, attr, value, key=""):
            out.append(self._claim(ev, t, rule, attr, key, value, "pos", version))

        if not is_note:
            profession = self.lex.first("profession", t)
            if profession:
                add("job.profession", "req.profession", profession)
            setting = self.lex.first("setting", t)
            if setting:
                add("job.setting", "req.setting", setting)

        m = MIN_EXP.search(t)
        if m:
            add("job.min_years", "req.min_years", {"years": to_number(m.group("n")), "level": "required"})
        for m in YEARS_EXP.finditer(t):
            level = "preferred" if (m.group("lvl") or "").lower() == "preferred" else "required"
            add("job.min_years", "req.min_years", {"years": to_number(m.group("n")), "level": level,
                                                    "setting": self.lex.first("setting", m.group("what") or "")})

        for m in LICENSE.finditer(t):
            add("job.license", "req.license", {"level": "required"}, state_code(m.group("state")))

        skills = self.lex.find("skill", t)
        if skills and (REQ_LIST.search(t) or REQUIRED.search(t) or PREFERRED.search(t)):
            level = "preferred" if (PREFERRED.search(t) and not REQ_LIST.search(t)) else "required"
            for k in skills:
                add("job.skill", "req.skill", {"level": level, "trainable": bool(TRAINABLE.search(t))}, k)

        m = CERT_PREF.search(t)
        if m:
            keys = [k for part in m.group("certs").split("/") for k in self.lex.find("cert", part)]
            if keys:
                add("job.cert_preferred", "req.cert_path", {"any_of": keys}, "preferred")
        m = OTHER_BOARDS.search(t)
        if m:
            keys = self.lex.find("cert", m.group("list"))
            if keys:
                add("job.cert_alternative", "req.cert_path", {"board": True, "all_of": keys}, "alternative")

        if not is_note:
            m = SCHEDULE.search(t)
            if m:
                add("job.schedule", "req.schedule", m.group(1).lower())
            m = JOB_HOURS.search(t)
            if m:
                add("job.hours", "req.hours", int(m.group("n")))
            m = JOB_WEEKS.search(t)
            if m:
                add("job.weeks", "req.weeks", int(m.group("n")))
            m = JOB_START.search(t)
            if m:
                add("job.start", "req.start", date.fromisoformat(m.group("d")))
            m = JOB_PAY.search(t)
            if m:
                add("job.pay", "req.pay", {"amount": to_number(m.group("amt")), "unit": norm_unit(m.group("unit")),
                                           "hours": int(m.group("h")) if m.group("h") else None})

        if START_FIRM.search(t):
            add("job.note.start_firm", "note.start_firm", True)
        if PARTIAL_COVER.search(t):
            add("job.note.partial_cover", "note.partial_cover", True)
        if ALT_UNAPPROVED.search(t):
            add("job.note.alt_path", "note.alt_path", "none_approved")
        if FEED_PENDING.search(t):
            add("job.note.feed_pending", "note.feed_pending", True)
        if PAY_BASIS.search(t):
            add("job.note.pay_basis", "note.pay_basis", "candidate_pay")
        if ALL_SHIFTS.search(t):
            add("job.note.all_shifts", "note.all_shifts", True)
