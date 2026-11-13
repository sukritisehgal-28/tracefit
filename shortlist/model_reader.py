"""An optional second reader that uses a language model, and the guard that decides what it may add.

OFF by default. The rule reader (extract.py) always runs first. The model only proposes facts. Every
proposal passes through Guard before it can count, and the guard is ordinary code, so a model that is
wrong, confused or adversarial cannot put a person on a shortlist. A proposed fact is accepted only if:

  1. its quote appears word for word in the document;
  2. the quote actually mentions the thing claimed (the tool, the date, the place ...), and for a yes,
     outside any negation the rule reader understands ("December 8, not December 1");
  3. the thing is in the tool's own vocabulary and its value can be read;
  4. the kind of document may assert it (a recruiter's note cannot assert a candidate's skill);
  5. for a "yes", the sentence holds no negation, uncertainty or question the rule reader can see;
  6. it does not contradict the rule reader. A contradiction becomes a question, not a winner.

A "no" or "unsure" from the model can only turn something into a question. The model can never lift a
no-contact request. What it read, accepted and refused is counted, for the coverage page and the checker.

Providers
  off        no model (default)
  recorded   replays answers saved by an earlier anthropic run; calls nothing. For demos and repeat runs.
  anthropic  calls Claude through the Anthropic SDK, and saves every answer so later runs replay it. Needs the `anthropic` package, credentials, and
             allow_external_text = true, because candidate text leaves the client's systems.
A test can also pass any function (text, kind, vocabulary) -> list of facts as the provider.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from .extract import (BOARD, DNC, DNC_ANY, LICENSE, NO_END, NOT_LOOKING, OPT_OUT, WEEKS, _placed, clauses, doubt_in)
from .lexicon import STATE_NAMES, state_code
from .model import Claim
from .timeutil import parse_date_phrase, to_number

KINDS = ("skill", "setting", "license", "cert", "start", "location", "schedule", "weeks", "shift", "do_not_contact")
SCHEDULES = ("weekdays", "weekends", "nights", "evenings")

SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "key": {"type": "string"},
            "says": {"type": "string", "enum": ["yes", "no", "unsure"]},
            "value": {"type": "string"},
            "quote": {"type": "string"},
        },
        "required": ["kind", "key", "says", "value", "quote"],
        "additionalProperties": False,
    }}},
    "required": ["facts"],
    "additionalProperties": False,
}

SYSTEM = """You read one document about a healthcare job candidate (a résumé, a message, a note) and list the
facts it states about the candidate. Report only what the text says. Never infer, never fill gaps.

For each fact give:
- kind: skill | setting | license | cert | start | location | schedule | weeks | shift | do_not_contact
- key: for skill, setting and cert, one key from the vocabulary given; for license, a two-letter US state
  code; for location, the place name as written; otherwise an empty string
- says: "yes" if the candidate plainly has it or agrees to it; "no" if they plainly do not; "unsure" if they
  plan, hope, are applying, are learning, are asking, or the text is otherwise uncertain
- value: for start and shift, the date as YYYY-MM-DD; for schedule, one of weekdays, weekends, nights,
  evenings; for weeks, a number; for do_not_contact, the end date as YYYY-MM-DD or an empty string;
  otherwise an empty string
- quote: the exact words from the document that state the fact, copied character for character

A request not to be contacted, however it is worded, is kind do_not_contact with says "yes".
If the document states none of these facts, return an empty list."""


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


class Guard:
    """Ordinary code that decides whether a fact proposed by the model may count. See the module docstring."""

    def __init__(self, lex):
        self.lex = lex

    def _clause(self, text: str, quote: str) -> str | None:
        """The one clause of the document that holds the quote. None when the quote spans several."""
        q = _norm(quote)
        for s, e in clauses(text):
            if q in _norm(text[s:e]):
                return text[s:e]
        return None

    def mentions(self, kind: str, key: str, value, quote: str, anchor: date) -> bool:
        """Does the quote itself mention the thing claimed? A true quote about something else is refused."""
        q = _norm(quote)
        if kind in ("skill", "setting", "cert"):
            family = {"skill": "skill", "setting": "setting", "cert": "cert"}[kind]
            return key in self.lex.find(family, quote) or (kind == "cert" and key.startswith("board") and bool(BOARD.search(quote)))
        if kind == "license":
            return any(state_code(m.group("state")) == key for m in LICENSE.finditer(quote)) or \
                _norm(STATE_NAMES.get(key, "~")) in q
        if kind == "location":
            return _norm(key) in q
        if kind in ("start", "shift"):
            return value in self._dates(quote, anchor)
        if kind == "schedule":
            return value in q
        if kind == "weeks":
            m = WEEKS.search(quote)
            return bool(m) and int(to_number(m.group("n"))) == value
        if kind == "do_not_contact":
            return bool(DNC.search(quote) or DNC_ANY.search(quote) or NOT_LOOKING.search(quote) or OPT_OUT.search(quote))
        return False

    @staticmethod
    def _dates(quote: str, anchor: date) -> set:
        from .extract import _DATE
        out = set()
        for m in re.finditer(_DATE, quote, re.I):
            try:
                out.add(parse_date_phrase(m.group(0), anchor))
            except Exception:  # noqa: BLE001
                pass
        return out

    def proposal(self, fact: dict, ev) -> tuple[tuple | None, str]:
        """(attr, key, value, polarity) for an acceptable fact, or (None, reason)."""
        kind, says = fact.get("kind"), fact.get("says")
        key, raw, quote = str(fact.get("key") or "").strip(), str(fact.get("value") or "").strip(), str(fact.get("quote") or "")
        text = ev.text or ""
        if kind not in KINDS or says not in ("yes", "no", "unsure"):
            return None, "not a kind of fact the tool knows"
        if len(_norm(quote)) < 3 or _norm(quote) not in _norm(text):
            return None, "the quote is not in the document"
        if self._clause(text, quote) is None:
            return None, "the quote spans several sentences"
        if says == "unsure":
            return None, "unsure"
        value = None
        if kind in ("skill", "setting", "cert"):
            family = {"skill": "skill", "setting": "setting", "cert": "cert"}[kind]
            known = {canon for canon, _ in self.lex.families.get(family, ())}
            if key not in known:
                return None, "outside the vocabulary"
        elif kind == "license":
            key = key.upper()
            if key not in STATE_NAMES:
                return None, "outside the vocabulary"
        elif kind == "location":
            if not re.fullmatch(r"[A-Z][A-Za-z.' -]{1,40}", key):
                return None, "the value could not be read"
        elif kind in ("start", "shift"):
            try:
                value = date.fromisoformat(raw)
            except ValueError:
                return None, "the value could not be read"
        elif kind == "schedule":
            value = raw.lower()
            if value not in SCHEDULES:
                return None, "outside the vocabulary"
        elif kind == "weeks":
            try:
                value = int(float(raw))
            except ValueError:
                return None, "the value could not be read"
            if not 1 <= value <= 104:
                return None, "the value could not be read"
        elif kind == "do_not_contact":
            if says != "yes":
                return None, "a model may not lift a no-contact request"
            if ev.author != "candidate":
                return None, "only the candidate can ask not to be contacted"
            try:
                value = date.fromisoformat(raw) if raw else NO_END
            except ValueError:
                return None, "the value could not be read"
        anchor = ev.observed_at.date()
        # A yes must be anchored outside any negation the rule reader understands: "December 8, not December 1"
        # mentions December 1, but only to rule it out.
        where = _placed(quote) if says == "yes" and kind != "do_not_contact" else quote
        if not self.mentions(kind, key, value, where, anchor):
            return None, "the quote does not mention it"
        if says == "yes" and kind != "do_not_contact":
            doubt, ambition = doubt_in(self._clause(text, quote))
            if doubt or (ambition and kind in ("skill", "setting", "license", "cert")):
                return None, "a yes from a sentence with a negation or doubt"
        pos = says == "yes"
        if kind == "skill":
            return ("skill", key, pos, "pos" if pos else "neg"), "ok"
        if kind == "setting":
            return ("experience", key, {} if pos else None, "pos" if pos else "neg"), "ok"
        if kind == "license":
            return (("license", key, {"active": "active" in _norm(quote)}, "pos"), "ok") if pos else (None, "no")
        if kind == "cert":
            return (("cert", key, True, "pos"), "ok") if pos else (None, "no")
        if kind == "start":
            return (("available_from", "", value, "pos"), "ok") if pos else (None, "no")
        if kind == "location":
            return ("location_interest", key, pos, "pos" if pos else "neg"), "ok"
        if kind == "schedule":
            return (("schedule", "", value, "pos"), "ok") if pos else (None, "no")
        if kind == "weeks":
            return (("weeks", "", value, "pos"), "ok") if pos else (None, "no")
        if kind == "shift":
            return ("shift_availability", value.isoformat(), pos, "pos" if pos else "neg"), "ok"
        return ("dnc_until", "", value, "pos"), "ok"


class ModelReader:
    """Asks the provider once per document, then lets the guard decide.

    Answers are kept by a hash of (model, kind of document, text). With a `store` file they are also saved,
    so a later run replays them instead of asking again: the same input then gives the same output, and
    each document is paid for once, across restarts. A changed document, or a different model, is asked
    afresh. A failed request is not saved, so it is tried again next time.
    """

    def __init__(self, provider, lex, model_id: str = "model", store: Path | None = None):
        self.provider, self.model_id, self.store = provider, model_id, store
        self.guard = Guard(lex)
        self.cache: dict[str, list] = self._load()
        self.stats: Counter = Counter()          # proposed, accepted, agreed, disagreed, refused: <reason>, errors
        self.refused: list[tuple[str, str, str]] = []     # (evidence id, quote, reason), for the coverage page

    def _load(self) -> dict:
        if self.store is None or not self.store.exists():
            return {}
        saved = json.loads(self.store.read_text(encoding="utf-8"))
        return {k: v["facts"] for k, v in saved.items() if isinstance(v, dict) and isinstance(v.get("facts"), list)}

    def _save(self, digest: str, ev, facts: list) -> None:
        if self.store is None:
            return
        saved = json.loads(self.store.read_text(encoding="utf-8")) if self.store.exists() else {}
        saved[digest] = {"model": self.model_id, "kind": ev.kind, "evidence": ev.id, "facts": facts}
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(self.store)                       # written whole, so a crash never leaves half a file

    def _ask(self, ev, vocabulary) -> list:
        digest = hashlib.sha256(f"{self.model_id}|{ev.kind}|{ev.text}".encode()).hexdigest()
        if digest in self.cache:
            self.stats["replayed"] += 1
            return self.cache[digest]
        try:
            facts = self.provider(ev.text, ev.kind, vocabulary)
        except Exception as exc:  # noqa: BLE001  a model that fails leaves the rule reading as it is
            self.stats["errors"] += 1
            self.refused.append((ev.id, "", f"the model could not be asked: {type(exc).__name__}"))
            return []                                 # not kept: asked again next time
        facts = facts if isinstance(facts, list) else []
        self.stats["asked"] += 1
        self.cache[digest] = facts
        self._save(digest, ev, facts)
        return facts

    def combine(self, ev, rule_claims: list, extractor) -> list:
        """The rule reader's claims for one document, with what the guard lets the model add."""
        lex = extractor.lex
        vocabulary = {fam: sorted({canon for canon, _ in lex.families.get(fam, ())}) for fam in ("skill", "setting", "cert")}
        out = list(rule_claims)
        anchor = ev.observed_at.date()
        for fact in self._ask(ev, vocabulary):
            self.stats["proposed"] += 1
            proposal, reason = self.guard.proposal(fact if isinstance(fact, dict) else {}, ev)
            quote = str((fact or {}).get("quote", "")) if isinstance(fact, dict) else ""
            if proposal is None:
                self.stats["refused: " + reason] += 1
                self.refused.append((ev.id, quote, reason))
                if reason in ("unsure", "a yes from a sentence with a negation or doubt", "no") and ev.author == "candidate":
                    out.append(self._question(ev, extractor, quote, anchor))
                continue
            attr, key, value, pol = proposal
            claim = Claim(subject=ev.subject, attr=attr, key=key, value=value, polarity=pol, evidence_id=ev.id,
                          quote=self.guard._clause(ev.text, quote).strip(), observed_at=ev.observed_at,
                          basis=extractor._claim(ev, "", "", attr, key, value).basis, rule=f"model.{fact['kind']}")
            if not extractor._allowed(ev, claim):
                self.stats["refused: this kind of document may not assert it"] += 1
                self.refused.append((ev.id, quote, "this kind of document may not assert it"))
                continue
            same = [c for c in out if c.attr == attr and c.key == key and c.polarity in ("pos", "neg") and not c.rule.startswith("model.")]
            if same:
                if all(c.polarity == pol and (attr not in ("available_from", "schedule", "weeks") or c.value == value) for c in same):
                    self.stats["agreed"] += 1
                    continue
                # The two readers disagree. Neither wins: the rule reading is set aside and a question is asked.
                self.stats["disagreed"] += 1
                self.refused.append((ev.id, quote, "disagrees with the rule reader; asked as a question"))
                out = [c for c in out if c not in same]
                if ev.author == "candidate":
                    out.append(self._question(ev, extractor, quote, anchor))
                continue
            self.stats["accepted"] += 1
            out.append(claim)
        return out

    def _question(self, ev, extractor, quote, anchor) -> Claim:
        clause = (self.guard._clause(ev.text or "", quote) if quote else None) or quote or (ev.text or "")
        topics = extractor._topics(clause, anchor) or ("unread",)
        return Claim(ev.subject, "unclear", "", topics, "unclear", ev.id, clause.strip(), ev.observed_at,
                     "self_reported", "model.unsure")


# ------------------------------------------------------------------ providers
def recorded(text, kind, vocabulary):
    """Used with the store only: replays saved answers and calls nothing. A document with no saved answer
    gets no model reading. SIMULATED."""
    return []


def anthropic_provider(cfg: dict):
    """Claude through the Anthropic SDK, with a JSON schema so the answer always parses.

    Not exercised in this repository's tests, which never call a model. Server-side fallbacks are on, so a
    request a safety classifier declines is retried on another model; a request still declined adds nothing.
    """
    import anthropic

    client = anthropic.Anthropic()
    model, effort = cfg.get("model", "claude-opus-5-5"), cfg.get("effort", "medium")

    def provider(text, kind, vocabulary):
        content = (f"Document kind: {kind}\nVocabulary: {json.dumps(vocabulary)}\n\nDocument:\n<<<\n{text}\n>>>")
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            return []
        body = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(body).get("facts", []) if body else []
    return provider


def openai_provider(cfg: dict):
    """An OpenAI model over plain HTTPS (standard library only), with the same JSON schema, so the answer always
    parses. Reads OPENAI_API_KEY from the environment. Not exercised in this repository's tests."""
    import os
    import urllib.request
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    model = os.environ.get("OPENAI_MODEL") or cfg.get("openai_model", "gpt-4o")

    def provider(text, kind, vocabulary):
        content = f"Document kind: {kind}\nVocabulary: {json.dumps(vocabulary)}\n\nDocument:\n<<<\n{text}\n>>>"
        body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "facts", "strict": True, "schema": SCHEMA}}}
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as response:
            message = json.loads(response.read())["choices"][0]["message"]
        return [] if message.get("refusal") or not message.get("content") else json.loads(message["content"]).get("facts", [])
    provider.model = model
    return provider


def make_reader(cfg: dict, provider=None):
    """The configured model reader, or None when it is off."""
    mcfg = cfg.get("model_reader", {})
    name = mcfg.get("provider", "off")
    provider = provider or mcfg.get("_provider")         # a function passed in directly, for tests
    store = mcfg.get("_store")
    if provider is None:
        if name == "off":
            return None
        store = Path(cfg.get("_root", ".")) / mcfg.get("recorded_path", "out/model_reads.json")
        if name == "recorded":
            provider = recorded
        elif name == "anthropic":
            if not mcfg.get("allow_external_text"):
                raise ValueError("model_reader.provider is 'anthropic' but allow_external_text is false. Sending candidate "
                                 "text to a model provider needs the client's agreement first.")
            provider = anthropic_provider(mcfg)
        elif name == "openai":
            if not mcfg.get("allow_external_text"):
                raise ValueError("model_reader.provider is 'openai' but allow_external_text is false.")
            provider = openai_provider(mcfg)
        else:
            raise ValueError(f"model_reader.provider must be off, recorded, anthropic or openai, not {name!r}")
    from .lexicon import Lexicon
    return ModelReader(provider, Lexicon(cfg), mcfg.get("model", name), Path(store) if store else None)
