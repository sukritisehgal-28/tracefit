"""The canonical model every source is translated into.

Evidence is a piece of source material (a record, a résumé, a note, a message).
A Claim is one fact asserted by one piece of evidence. Nothing is merged or
overwritten at this level: resolution happens later and keeps every claim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Where a claim's authority comes from, by kind of evidence. Shown to recruiters as the basis of a fact.
BASIS = {
    "structured_record": "structured", "resume": "self_reported", "profile": "profile",
    "candidate_message": "self_reported", "candidate_statement": "self_reported",
    "recruiter_note": "recruiter", "ops_note": "operations",
    "job_feed": "feed", "job_record": "feed", "job_note": "note",
}


@dataclass(slots=True)
class Evidence:
    id: str                 # "<source>/<local id>", unique across sources
    local_id: str           # the id the source itself uses, e.g. "C1-CV"
    source: str             # source name from configuration
    kind: str               # structured_record | resume | profile | candidate_message | candidate_statement
    #                         recruiter_note | ops_note | job_feed | job_record | job_note
    subject: str            # "person:<source>:<id>" or "job:<requisition>"
    observed_at: datetime   # when the source says this was recorded (UTC)
    date_only: bool         # True when the source gave a date with no time
    author: str             # candidate | recruiter | account_manager | operations | system
    label: str              # short human label for the interface
    text: str | None = None
    lineage: str | None = None   # local id of the evidence this one is a rendition of
    related: tuple = ()          # local ids this note repeats or comments on


@dataclass(slots=True)
class Claim:
    subject: str
    attr: str
    key: str                # for keyed facts: the skill, the state, the setting, the date
    value: object
    polarity: str           # pos | neg | unanswered
    evidence_id: str
    quote: str              # the exact words, or "field = value" for structured fields
    observed_at: datetime
    basis: str              # self_reported | recruiter | operations | structured | feed | note
    rule: str               # which extraction rule produced the claim (for coverage)
    version: str = ""       # job requirements only: feed | clarification | unapproved


@dataclass(slots=True)
class Issue:
    """A data problem the tool worked around. Nothing here stops matching; it is reported so a person can fix the source."""
    severity: str           # error (something was left out) | warning (a fallback was used) | info (worth knowing)
    source: str
    where: str              # the record, file or field concerned
    message: str


@dataclass(slots=True)
class Batch:
    evidence: list = field(default_factory=list)
    claims: list = field(default_factory=list)
    links: list = field(default_factory=list)    # (subject_a, subject_b, reason)
    events: list = field(default_factory=list)
    issues: list = field(default_factory=list)


@dataclass(slots=True)
class Event:
    id: str
    at: datetime
    kind: str               # job.created | candidate.updated
    label: str
    source: str
    batch: Batch
