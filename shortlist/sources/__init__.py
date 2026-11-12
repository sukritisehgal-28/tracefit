"""Source adapters: the only code that knows what a source's format looks like.

An adapter translates one source into Evidence and Claims. It makes no matching
decisions and the matching code never imports it. To add a source, write one
function here and add one [[sources]] block to the configuration.

Failure handling
  * A source that cannot be read (missing file, bad JSON, unknown adapter) is left out
    and reported. The other sources are still used.
  * A record that cannot be translated is undone and reported. The other records in
    the same source are still used.
  * A date that cannot be read falls back to the export time (for a record) or is
    ignored (for a single field), and is reported.
Every problem becomes an Issue the recruiter-facing page can show. Nothing is guessed silently.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..model import BASIS, Batch, Claim, Evidence, Issue
from ..timeutil import parse_when


class Loader:
    """Helper handed to each adapter so evidence, claims and problems are handled the same way everywhere."""

    def __init__(self, src: dict, ctx: dict):
        self.src = src
        self.name = src["name"]
        self.label = src.get("label", self.name)
        self.ctx = ctx
        self.batch = Batch()
        self.fallback_when = ctx.get("snapshot_at", "2000-01-01T00:00:00Z")
        self.exported_at = None                 # set when the file says when it was exported
        self._seen: dict[str, int] = {}
        self._added: list | None = None      # ids recorded by the record being translated, so a failure can undo them

    def person(self, local_id, source: str | None = None) -> str:
        return f"person:{source or self.name}:{local_id}"

    @staticmethod
    def job(requisition) -> str:
        return f"job:{requisition}"

    # ------------------------------------------------------------ problems
    def issue(self, severity: str, where: str, message: str) -> None:
        self.batch.issues.append(Issue(severity, self.label, where, message))

    def guard(self, fn, where: str) -> bool:
        """Translate one record. If it fails, undo what it added and report it; other records are unaffected."""
        # The cost of guarding a record must not depend on how many records came before it: only the
        # lengths are noted, and the ids this record adds are written down as it goes.
        b = self.batch
        marks = (len(b.evidence), len(b.claims), len(b.links), len(b.events))
        outer, self._added = self._added, []
        try:
            fn()
        except Exception as exc:  # noqa: BLE001  (any failure in one record must not stop the others)
            del b.evidence[marks[0]:], b.claims[marks[1]:], b.links[marks[2]:], b.events[marks[3]:]
            for local in self._added:
                self._seen[local] -= 1
                if not self._seen[local]:
                    del self._seen[local]
            self._added = outer
            reason = f"the field {exc} is missing" if isinstance(exc, KeyError) else f"{type(exc).__name__}: {exc}"
            self.issue("error", where, f"Record left out: {reason}.")
            return False
        if outer is not None:
            outer.extend(self._added)
        self._added = outer
        return True

    def each(self, items, kind: str, fn, key: str = "id") -> None:
        """Run `fn` on every record, each inside its own guard."""
        if items is not None and not isinstance(items, list):
            self.issue("error", kind, f"Expected a list of {kind} records, found {type(items).__name__}. None were loaded.")
            return
        for i, item in enumerate(items or [], start=1):
            ident = item.get(key) if isinstance(item, dict) else None
            self.guard(lambda item=item: fn(item), f"{kind} {ident if ident is not None else 'number ' + str(i)}")

    def date(self, value, where: str, fmt: str | None = None) -> date | None:
        """A date field. If it cannot be read, the field is ignored and the problem is reported."""
        if value in (None, ""):
            return None
        try:
            return parse_when(str(value), fmt)[0].date()
        except Exception:  # noqa: BLE001
            self.issue("warning", where, f"The date {value!r} could not be read, so that field was ignored.")
            return None

    def amount(self, value, where: str) -> float | None:
        """A pay amount. Anything that is not a positive number is ignored and reported."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = -1.0
        if number <= 0:
            self.issue("warning", where, f"The pay amount {value!r} is not a positive number, so it was ignored.")
            return None
        return number

    # ------------------------------------------------- evidence and claims
    def evidence(self, local_id, kind, subject, when, author, label, text=None, lineage=None,
                 related=(), fmt=None, into: Batch | None = None) -> Evidence:
        try:
            at, date_only = parse_when(when, fmt)
        except Exception:  # noqa: BLE001
            at, date_only = parse_when(self.fallback_when)[0], False
            self.issue("warning", label, f"The date {when!r} could not be read, so the export time was used instead.")
        if into is None and self.exported_at:     # part of the export itself, not a later event
            exported = parse_when(self.exported_at)[0]
            if at > exported:
                self.issue("warning", label, f"It is dated {when}, after the reference date and the export it came in. "
                                             "The export time was used instead.")
                at, date_only = exported, False
        local = str(local_id)
        self._seen[local] = self._seen.get(local, 0) + 1
        if self._added is not None:
            self._added.append(local)
        if self._seen[local] > 1:
            self.issue("info", label, f"The id {local} appears more than once in this source. Both entries are kept.")
            local = f"{local}#{self._seen[local]}"
        if text is not None and not isinstance(text, str):
            raise TypeError(f"text must be a string, found {type(text).__name__}")
        ev = Evidence(id=f"{self.name}/{local}", local_id=local, source=self.name, kind=kind, subject=subject,
                      observed_at=at, date_only=date_only, author=author, label=label, text=text or None,
                      lineage=lineage, related=tuple(related or ()))
        (into or self.batch).evidence.append(ev)
        return ev

    def claim(self, ev: Evidence, attr: str, value, key: str = "", quote: str | None = None,
              polarity: str = "pos", into: Batch | None = None) -> Claim:
        if quote is None:
            quote = f"{attr} = {json.dumps(value, default=str, ensure_ascii=False)}"
        version = "feed" if ev.kind in ("job_feed", "job_record") else ""
        c = Claim(subject=ev.subject, attr=attr, key=key, value=value, polarity=polarity, evidence_id=ev.id,
                  quote=quote, observed_at=ev.observed_at, basis=BASIS.get(ev.kind, "structured"),
                  rule=f"field:{attr}", version=version)
        (into or self.batch).claims.append(c)
        return c


ADAPTERS: dict = {}


def adapter(name: str):
    def register(fn):
        ADAPTERS[name] = fn
        return fn
    return register


def load_source(src: dict, ctx: dict, root: Path) -> Batch:
    """Run one configured source through its adapter. A source that fails is reported and left out; it never raises."""
    loader = Loader(src, ctx)
    try:
        if src.get("adapter") not in ADAPTERS:
            raise LookupError(f"there is no adapter named {src.get('adapter')!r}")
        if "data" in src:
            raw = src["data"]
        else:
            raw = json.loads((Path(root) / src["path"]).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("exported_at"):
            loader.fallback_when = loader.exported_at = raw["exported_at"]
        ADAPTERS[src["adapter"]](raw, loader)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, FileNotFoundError):
            reason = f"the file {src.get('path')} was not found"
        elif isinstance(exc, json.JSONDecodeError):
            reason = f"the file is not valid JSON (line {exc.lineno})"
        elif isinstance(exc, (AttributeError, TypeError)):
            reason = f"its contents are not in the shape the {src.get('adapter')} adapter expects ({exc})"
        else:
            reason = str(exc) or type(exc).__name__
        failed = Batch()
        failed.issues = loader.batch.issues + [Issue("error", loader.label, src.get("path", "(inline data)"),
                                                     f"This source could not be loaded: {reason}. The other sources are still used.")]
        return failed
    return loader.batch


from . import ats, client_db, our_system, supplement, vms  # noqa: E402,F401  (registers the built-in adapters)
