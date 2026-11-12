"""Everything a résumé or profile lists, section by section, for the recruiter to read.

The rule reader (extract.py) turns only what a rule can check into facts. This module keeps the rest:
every role, skill, degree and credential as the candidate wrote it, so the person's panel can show what
they have done even when no job asks for it. Each item says whether a rule understands it.

Nothing here feeds a rule. A résumé is the candidate's own account, often written for many jobs, so it is
shown as written and labelled as such. A name read from a résumé header is used for display only, never to
decide that two records are one person.

It works on any layout: headed sections (EXPERIENCE, EDUCATION, SKILLS, LICENSURE ...) or a single
paragraph, as short extracts and imported profiles are written.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .extract import ABSENT, BOARD, EDUCATION, LICENSE, RANGE, SINCE
from .lexicon import STATE_RE

SECTIONS = (                                   # first match wins; checked against the heading in lower case
    ("credentials", ("licen", "certif", "credential", "registration")),
    ("education", ("education", "training", "qualification", "degree", "academic")),
    ("skills", ("skill", "competenc", "expertise", "tools")),
    ("experience", ("experience", "employment", "work history", "career", "practice", "positions", "history")),
    ("summary", ("summary", "profile", "about", "objective")),
)
BULLET = re.compile(r"^\s*[•\-*·▪]\s+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\+?\(?\d[\d\s().-]{7,}\d")
PLACE = re.compile(rf"\b(?P<city>[A-Z][a-z]+(?:\s[A-Z][a-z]+)?),\s*(?P<state>{STATE_RE}|[A-Z]{{2}})\b")
NAME = re.compile(r"^[A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’.-]+){1,3}$")
NOT_A_NAME = re.compile(r"\b(?:therapist|physician|nurse|résumé|resume|curriculum|vitae|profile|candidate|doctor|medicine)\b", re.I)
ABOUT_DOC = re.compile(r"\bthis (?:document|résumé|resume|cv)\b|\bseparately dated\b|\bsee separately\b", re.I)
NOT_STATED = re.compile(r"\bnot\s+(?:listed|included|supplied|stated|attached|described|answered)\b|\bare not\b.*\b(?:included|supplied|listed)\b", re.I)
def _date_only(s: str) -> bool:
    """A line that is only a date range, such as "May 2025–present"."""
    m = RANGE.search(s) or SINCE.search(s)
    return bool(m) and not s.replace(m.group(0), "").strip(" .,—–-")


def _heading(line: str) -> str | None:
    """The section a line opens, or None. A heading is short and in capitals, or short and ends with a colon."""
    s = line.strip().rstrip(":").strip()
    letters = [c for c in s if c.isalpha()]
    if not letters or len(s) > 70:
        return None
    capitals = sum(c.isupper() for c in letters) / len(letters) >= 0.8 and len(letters) >= 4
    if not (capitals or (line.strip().endswith(":") and len(s.split()) <= 4)):
        return None
    low = s.lower()
    for name, words in SECTIONS:
        if any(w in low for w in words):
            return name
    return "other"


def _place_line(text: str) -> bool:
    """A short line that only names a school or hospital: no year, no degree words."""
    return len(text) < 60 and not re.search(r"\d", text) and not EDUCATION.search(text) and \
        not re.search(r"\b(?:degree|doctor|bachelor|master|diploma|residency|fellowship)\b", text, re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


def _split_list(text: str) -> list[str]:
    parts = re.split(r";|,(?![^()]*\))|\s+and\s+", text.strip().rstrip("."))
    return [p.strip().rstrip(".") for p in parts if len(p.strip()) > 1]


def _header(lines: list[str]) -> dict:
    out: dict = {}
    for i, line in enumerate(lines[:3]):
        parts = [p.strip() for p in re.split(r"\s*[|—–]\s*|\s+-\s+", line) if p.strip()]
        for p in parts:
            if i == 0 and "name" not in out and NAME.match(p) and not NOT_A_NAME.search(p) and not PLACE.search(p):
                out["name"] = p
            m = EMAIL.search(p)
            if m:
                out["email"] = m.group(0)
            m = PLACE.search(p)
            if m and "home" not in out:
                out["home"] = m.group(0)
            if PHONE.fullmatch(p):
                out["phone"] = p
    return out


@lru_cache(maxsize=20000)
def parse(text: str) -> dict:
    """Sections of one document: header, experience (roles), education, skills, credentials, summary, other,
    and what the document itself says it does not state. Items are kept as written."""
    out = {"header": {}, "experience": [], "education": [], "skills": [], "credentials": [], "summary": [], "other": [], "not_stated": []}
    lines = [l.rstrip() for l in text.splitlines()]
    headed = sum(1 for l in lines if l.strip() and _heading(l)) >= 2
    if not headed:
        _paragraph(text, out)
        return out
    first = next((i for i, l in enumerate(lines) if l.strip() and _heading(l)), len(lines))
    out["header"] = _header([l for l in lines[:first] if l.strip()])
    section, role = "other", None
    for line in lines[first:]:
        s = line.strip()
        if not s:
            role = None if section != "experience" else role
            continue
        h = _heading(s)
        if h:
            section, role = h, None
            continue
        bullet = bool(BULLET.match(s))
        s = BULLET.sub("", s)
        if section == "experience":
            role = _experience_line(s, bullet, role, out)
            continue
        for sentence in _sentences(s) if not bullet else [s]:
            if NOT_STATED.search(sentence) or ABSENT.search(sentence) or ABOUT_DOC.search(sentence):
                out["not_stated"].append(sentence)
            elif section == "skills":
                out["skills"] += _split_list(sentence)
            elif section == "education" and out["education"] and _place_line(out["education"][-1]):
                out["education"][-1] = f"{sentence.rstrip('.')} — {out['education'][-1]}"
            elif section in ("education", "credentials", "summary"):
                out[section].append(sentence.rstrip("."))
            else:
                out["other"].append(sentence.rstrip("."))
    return out


def _experience_line(s: str, bullet: bool, role: dict | None, out: dict) -> dict | None:
    """One line of a work-history section. Returns the role that later lines belong to.

    Dates alone on a line belong to the role above. A line with a date range completes an employer named on
    the line before, or starts a new role. A bullet, or a full sentence under a role, is a duty. A short line
    with no full stop starts a new role (an employer, or "Employer — Title").
    """
    def new(title, dates=None):
        r = {"title": title.strip(" ,—–-."), "dates": dates, "duties": []}
        out["experience"].append(r)
        return r

    if NOT_STATED.search(s) or ABSENT.search(s) or ABOUT_DOC.search(s):
        out["not_stated"].append(s)
        return role
    m = RANGE.search(s) or SINCE.search(s)
    if bullet:
        if role is None:
            return new(s)
        role["duties"].append(s.rstrip("."))
        return role
    if m and _date_only(s):
        if role is not None and not role["dates"]:
            role["dates"] = m.group(0)
            return role
        return new("Role not named", m.group(0))
    if m:
        title = s.replace(m.group(0), "").strip(" ,—–-.")
        if role is not None and not role["dates"] and not role["duties"] and "—" not in role["title"]:
            role["title"], role["dates"] = f"{role['title']} — {title}", m.group(0)      # employer above, title and dates here
            return role
        return new(title, m.group(0))
    if role is not None and s.rstrip().endswith("."):
        for sentence in _sentences(s):
            if NOT_STATED.search(sentence) or ABSENT.search(sentence) or ABOUT_DOC.search(sentence):
                out["not_stated"].append(sentence)
            else:
                role["duties"].append(sentence.rstrip("."))
        return role
    if role is not None and not role["dates"] and not role["duties"] and "—" not in role["title"]:
        role["title"] = f"{role['title']} — {s.rstrip('.')}"
        return role
    return new(s)


def _paragraph(text: str, out: dict) -> None:
    """A document with no headings: each sentence goes to the section it reads like."""
    for sentence in _sentences(" ".join(text.split())):
        s = sentence.rstrip(".")
        if NOT_STATED.search(s) or ABSENT.search(s) or ABOUT_DOC.search(s):
            out["not_stated"].append(s)
        elif EDUCATION.search(s) or re.search(r"\b(?:university|degree|college|completed)\b", s, re.I) and not RANGE.search(s):
            out["education"].append(s)
        elif LICENSE.search(s) or re.search(r"\bcertif|\b(?:ATLS|ACLS|PALS|BLS|ABEM|AOBEM)\b", s):
            out["credentials"].append(s)
        elif RANGE.search(s) or SINCE.search(s):
            role, _, duties = s.partition(":")
            m = RANGE.search(role) or SINCE.search(role)
            out["experience"].append({"title": role.replace(m.group(0), "").strip(" ,—–-") if m else role, "dates": m.group(0) if m else None,
                                      "duties": []})
            if duties:
                out["skills"] += _split_list(duties)
        else:
            out["skills"] += _split_list(s)


def background(world, person) -> dict:
    """Everything the person's résumés and profiles list, merged across documents, with each item marked
    as checked by a rule or only listed. Renditions of one document are read once, using the fullest."""
    lex = world.lex
    chosen: dict[str, object] = {}
    for eid in person.evidence_ids:
        ev = world.evidence[eid]
        if ev.kind not in ("resume", "profile") or not ev.text:
            continue
        root = world.root(eid)
        if root not in chosen or len(ev.text) > len(chosen[root].text):
            chosen[root] = ev
    docs = sorted(chosen.values(), key=lambda e: e.observed_at, reverse=True)

    def understood(text: str) -> list[str]:
        found = [lex.label(k) for fam in ("skill", "setting", "cert") for k in lex.find(fam, text)]
        found += [f"{m.group('state')} license" for m in LICENSE.finditer(text)]
        if EDUCATION.search(text):
            found.append("education")
        if BOARD.search(text):
            found.append("board certification")
        return list(dict.fromkeys(found))

    def item(text: str, ev) -> dict:
        return {"text": text, "checked": understood(text), "source": ev.label}

    out = {"header": {}, "experience": [], "skills": [], "education": [], "credentials": [], "summary": [], "not_stated": [],
           "sources": [{"label": e.label, "at": e.observed_at.isoformat(), "kind": e.kind} for e in docs]}
    seen: set = set()
    for ev in docs:
        parsed = parse(ev.text)
        for k, v in parsed["header"].items():
            out["header"].setdefault(k, v)
        for role in parsed["experience"]:
            key = ("role", role["title"].lower())
            if key not in seen:
                seen.add(key)
                out["experience"].append(dict(item(role["title"], ev), dates=role["dates"],
                                              duties=[item(d, ev) for d in role["duties"]]))
        for section in ("skills", "education", "credentials", "summary", "not_stated"):
            for text in parsed[section] + (parsed["other"] if section == "summary" else []):
                key = (section, text.lower())
                if key not in seen and text:
                    seen.add(key)
                    out[section].append(item(text, ev))
    return out


def unrecognised(world) -> list[tuple[str, int]]:
    """Skills listed on résumés that no rule understands, most common first: the terms worth adding to the
    vocabulary. Each person counts once per term."""
    counts: dict[str, int] = {}
    for p in world.people.values():
        terms = {s["text"].lower() for s in background(world, p)["skills"] if not s["checked"]}
        for t in terms:
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
