"""The language-model reader, run for real once, and what it changes.

    python3 tests/model_run.py              replay the saved answers and print the report (no network, no key)
    python3 tests/model_run.py --live       ask the model, save its answers, then print the report
    python3 tests/model_run.py --live --limit 5     the same for five documents only: a cheap first try

The rule reader always runs. The model is a second reader whose proposals count only if the guard in
shortlist/model_reader.py agrees (the quote is in the document word for word, says the thing claimed, and
holds no doubt). This script asks the model about every candidate document in the supplied sample and about
every sentence of the rewording sweep, saves each answer in recorded/model_reads.json, and then reports,
from the saved answers alone, what the model changed. Anyone can repeat the report without a key.

--live needs `pip install anthropic` and an API credential in the environment (ANTHROPIC_API_KEY). It sends
the sample's candidate text and the test sentences to the model provider. All of that text is fictional.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from shortlist import config  # noqa: E402
from shortlist.config import deep_merge  # noqa: E402
from shortlist.extract import Extractor  # noqa: E402
from shortlist.lexicon import Lexicon  # noqa: E402
from shortlist.model_reader import ModelReader, anthropic_provider, recorded  # noqa: E402
from shortlist.pipeline import Engine  # noqa: E402
import test_rewording as sweep  # noqa: E402

STORE = ROOT / "recorded" / "model_reads.json"

# Sentences the rule reader is known to get wrong or to leave standing (docs/EVALUATION.md, limitations).
# Printed with what each reader made of them. Not scored: they show where a second reader helps and where it does not.
HARD = [
    "I can cover most dates, except December 5.",
    "I can work most weekends, though I am away December 5 and 6.",
    "My earliest start is December 15.",
    "Spokane is off the table for me.",
    "I prefer nights but can do weekdays.",
    "I gave two weeks notice at my current job.",
    "I have less than two years of home health experience.",
    "Outpatient PT 2015 to present, per-diem home health 2023.",
]


def settings(provider=None, store=STORE) -> dict:
    """The default settings with the model reader on: a given provider function, or the saved answers."""
    cfg = config.load()
    reader = {"allow_external_text": True, "recorded_path": str(store.relative_to(ROOT)) if store else ""}
    if provider is not None:
        reader.update(_provider=provider, _store=str(store) if store else None)
    else:
        reader.update(provider="recorded")
    return deep_merge(cfg, {"model_reader": reader})


def sweep_sentences() -> list[str]:
    out = []
    for _, yeses, noes, unsure in sweep.FACTS.values():
        out += yeses + noes + unsure
    for group in sweep.NOT_FIRST_HAND.values():
        out += group
    out += [s for _, s in sweep.STILL_READ] + sweep.DO_NOT_CONTACT + sweep.NOT_A_REQUEST + HARD
    return list(dict.fromkeys(out))


def digest(model_id: str, kind: str, text: str) -> str:
    return hashlib.sha256(f"{model_id}|{kind}|{text}".encode()).hexdigest()


def everything_to_ask(model_id: str) -> dict:
    """Every document the model reader would be asked about: {digest: (text, kind, vocabulary)}."""
    asks = {}

    def capture(text, kind, vocabulary):
        asks[digest(model_id, kind, text)] = (text, kind, vocabulary)
        return []

    engine = Engine(cfg=settings(capture, store=None))
    for step in range(len(engine.steps)):
        engine.world(step)                                    # reads every document on file at that step
    cfg = config.load()
    reader = ModelReader(capture, Lexicon(cfg), model_id)
    for sentence in sweep_sentences():
        sweep.read(sentence, model=reader)
    return asks


def go_live(limit: int | None, stand_in: bool) -> None:
    cfg = config.load()["model_reader"]
    model_id = cfg.get("model", "claude-opus-5-5")
    asks = everything_to_ask(model_id)
    saved = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {}
    todo = [(d, a) for d, a in asks.items() if d not in saved][: limit or None]
    print(f"{len(asks)} documents and sentences in all; {len(asks) - len([d for d in asks if d not in saved])} already saved; asking {len(todo)} now.")
    if not todo:
        return
    if stand_in:                                              # SIMULATED: checks this script, never saved to the tracked file
        def provider(text, kind, vocabulary):
            return []
    else:
        try:
            provider = anthropic_provider(cfg)
        except ImportError:
            sys.exit("The anthropic package is not installed here. Install it first, for example:\n"
                     "  python3 -m venv .venv-model && .venv-model/bin/pip install anthropic\n"
                     "then run this script with .venv-model/bin/python.")
        except Exception as exc:  # noqa: BLE001  most often: no API credential in the environment
            sys.exit(f"The model client could not start ({type(exc).__name__}: {exc}).\n"
                     "Set an API credential in this terminal first: export ANTHROPIC_API_KEY=...")
    failed = 0

    def ask(item):
        d, (text, kind, vocabulary) = item
        try:
            return d, kind, provider(text, kind, vocabulary), None
        except Exception as exc:  # noqa: BLE001  reported and left unsaved, so the next run asks again
            return d, kind, None, f"{type(exc).__name__}: {str(exc)[:160]}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        for n, (d, kind, facts, error) in enumerate(pool.map(ask, todo), start=1):
            if error:
                failed += 1
                print(f"  {n}/{len(todo)} failed: {error}")
                continue
            saved[d] = {"model": "stand-in" if stand_in else model_id, "kind": kind, "evidence": "live run", "facts": facts if isinstance(facts, list) else []}
            if n % 20 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} answered")
    if stand_in:
        print("Stand-in run: nothing was saved.")
        return
    if failed == len(todo):
        sys.exit("Every request failed, so nothing was saved. Check the API credential (ANTHROPIC_API_KEY) and try again.")
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(saved, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Saved {len(saved)} answers to {STORE.relative_to(ROOT)}." + (f" {failed} failed and will be asked again next time." if failed else ""))


# ------------------------------------------------------------------ the report
def results(engine) -> dict:
    out = {}
    for step in range(len(engine.steps)):
        for job, (rows, _) in engine.matches(step).items():
            for m in rows:
                out[(step, job.split(":")[-1], m.person.split(":")[-1])] = (m.tier, m.score, m.low, m.high, m.action["status"])
    return out


def claims_of(sentence: str, reader=None) -> str:
    found = [f"{c.attr}{'[' + str(c.key) + ']' if c.key else ''} {c.polarity}" + (f" = {c.value}" if c.value not in (None, True, {}, "") and c.polarity == "pos" else "")
             for c in sweep.read(sentence, model=reader)]
    return "; ".join(dict.fromkeys(found)) or "nothing"


def report() -> int:
    if not STORE.exists():
        print("No saved answers yet. Run with --live once (it needs the anthropic package and an API key).")
        return 0
    saved = json.loads(STORE.read_text(encoding="utf-8"))
    cfg = config.load()
    model_id = cfg["model_reader"].get("model", "claude-opus-5-5")
    print(f"Language-model reader: {len(saved)} saved answers from {', '.join(sorted({v['model'] for v in saved.values()}))}. "
          "Replayed from recorded/model_reads.json; nothing is called.\n")

    # 1. the supplied sample
    plain, with_model = Engine(), Engine(cfg=settings())
    before, after = results(plain), results(with_model)
    stats = with_model.world(len(with_model.steps) - 1).extractor.model.stats
    print("SUPPLIED SAMPLE")
    print("  the model proposed {proposed} facts: {agreed} the rule reader already had, {accepted} added, {disagreed} disagreed (asked as a question)".format(
        **{k: stats.get(k, 0) for k in ("proposed", "agreed", "accepted", "disagreed")}))
    for reason, n in sorted((k, v) for k, v in stats.items() if k.startswith("refused: ")):
        print(f"  refused by the guard, {reason[9:]}: {n}")
    moved = [(k, before[k], after[k]) for k in before if before[k] != after.get(k)]
    print(f"  results that changed: {len(moved)} of {len(before)}")
    for (step, job, person), b, a in moved:
        print(f"    step {step} {job} {person}: {b[0]} {b[1]} ({b[2]}-{b[3]}) {b[4]}  ->  {a[0]} {a[1]} ({a[2]}-{a[3]}) {a[4]}")
    unread = lambda e: len(e.world(len(e.steps) - 1).extractor.unparsed)   # noqa: E731
    print(f"  clauses left unread: {unread(plain)} by the rules alone, {unread(with_model)} with the model\n")

    # 2. the rewording sweep
    lex = Lexicon(cfg)
    reader = ModelReader(recorded, lex, model_id, STORE)
    yes = lambda test, s, r: any(test(c) for c in sweep.read(s, model=r))     # noqa: E731
    bad = read_rules = read_both = total = 0
    print("REWORDING SWEEP (synthetic sentences)")
    for fact, (test, yeses, noes, unsure) in sweep.FACTS.items():
        total += len(yeses)
        read_rules += sum(yes(test, s, None) for s in yeses)
        read_both += sum(yes(test, s, reader) for s in yeses)
        for s in noes + unsure + sweep.NOT_FIRST_HAND.get(fact, []):
            if yes(test, s, reader):
                bad += 1
                print(f"  FALSE YES with the model on: ({fact}) {s}")
    blocks = lambda s, r: any(c.attr == "dnc_until" for c in sweep.read(s, model=r))     # noqa: E731
    missed = [s for s in sweep.DO_NOT_CONTACT if not blocks(s, reader)]
    wrong = [s for s in sweep.NOT_A_REQUEST if blocks(s, reader)]
    print(f"  plain yes sentences read: {read_rules} of {total} by the rules alone, {read_both} of {total} with the model")
    print(f"  a no, a doubt or a passing mention read as yes, with the model on: {bad}")
    print(f"  requests not to be contacted caught: {len(sweep.DO_NOT_CONTACT) - len(missed)} of {len(sweep.DO_NOT_CONTACT)}; ordinary messages wrongly blocked: {len(wrong)}\n")

    # 3. sentences the rules are known to get wrong
    print("SENTENCES THE RULE READER IS KNOWN TO GET WRONG (what each reader made of them)")
    for s in HARD:
        print(f"  {s}\n      rules alone: {claims_of(s)}\n      with model:  {claims_of(s, reader)}")
    print("\nOne run, one model, fictional text. This shows what the model changed here; it is not a measure of model quality.")
    return 1 if bad or missed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the language-model reader once and report what it changes.")
    parser.add_argument("--live", action="store_true", help="ask the model and save its answers (needs the anthropic package and an API key)")
    parser.add_argument("--limit", type=int, help="with --live: ask about this many documents only")
    parser.add_argument("--stand-in", action="store_true", help="with --live: use a stand-in that answers nothing, to check this script; saves nothing")
    args = parser.parse_args()
    if args.live:
        go_live(args.limit, args.stand_in)
    sys.exit(report())
