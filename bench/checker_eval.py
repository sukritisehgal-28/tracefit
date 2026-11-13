"""Verify that the separate checker catches deliberately planted SYNTHETIC misreadings.

python3 bench/checker_eval.py --live    ask and save; otherwise replay with no network
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from shortlist import config
from shortlist.extract import Extractor
from shortlist.lexicon import Lexicon
from shortlist.model import Claim, Evidence
from shortlist.model_reader import SemanticVerifier, openai_provider, recorded
from shortlist.timeutil import parse_when

CASES = [
    ("wrong-role-dates", "Outpatient PT 2015 to 2022, home-health PT 2023 to present.", "experience", "home_health",
     {"start": date(2015, 12, 31), "end": date(2022, 1, 1), "precision": "year", "since": "2015"}),
    ("ignored-exception", "I can cover most dates, except December 5.", "shift_availability", "2026-12-05", True),
    ("someone-elses-work", "OASIS access is reserved for someone else in the department.", "skill", "oasis", True),
]


def run(live=False):
    cfg = config.load("openai")
    lex = Lexicon(cfg)
    provider = openai_provider(dict(cfg["model_reader"], _root=str(ROOT))) if live else recorded
    checker = SemanticVerifier(provider, lex, "gpt-4o", ROOT / "recorded" / "checker_fault_reads.json")
    results = []
    for name, text, attr, key, value in CASES:
        at = parse_when("2026-11-06T12:00:00Z")[0]
        ev = Evidence("synthetic/" + name, name, "synthetic", "candidate_message", "person:synthetic:1", at,
                      False, "candidate", "Synthetic planted misreading", text)
        wrong = Claim(ev.subject, attr, key, value, "pos", ev.id, text, at, "self_reported", "synthetic.planted")
        after = checker.check(ev, [wrong], Extractor(cfg, lex))
        results.append({"case": name, "caught": wrong not in after and any(c.rule == "model.verifier" for c in after)})
    report = dict(synthetic=True, planted_misreadings=results, stats=dict(checker.stats))
    (ROOT / "docs" / "checker_fault_evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    return 1 if checker.stats.get("errors") or not all(r["caught"] for r in results) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    raise SystemExit(run(parser.parse_args().live))
