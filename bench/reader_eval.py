"""Compare readers on labelled SYNTHETIC language cases; replay is network-free.

python3 bench/reader_eval.py --live    ask OpenAI once per case, save and report
python3 bench/reader_eval.py           replay the exact saved readings
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from reader_cases import CASES, holds
from shortlist import config
from shortlist.extract import Extractor
from shortlist.lexicon import Lexicon
from shortlist.model import Evidence
from shortlist.model_reader import ModelReader, make_reader, recorded
from shortlist.timeutil import parse_when


def sample(live=False, checked=False):
    from shortlist.audit import audit
    from shortlist.pipeline import Engine
    cfg = config.load("openai")
    cfg["model_reader"]["verify_claims"] = checked
    cfg["model_reader"]["recorded_path"] = "recorded/semantic_sample_reads.json"
    if not live:
        cfg["model_reader"]["provider"] = "recorded"
    e = Engine(cfg=cfg)
    plain = Engine()
    changed = []
    for step in range(len(e.steps)):
        after = e.matches(step)
        before = plain.matches(step)
        for job, (rows, _) in after.items():
            old = {m.person: m for m in before[job][0]}
            for row in rows:
                b = old[row.person]
                if (row.tier, row.score, row.action["status"]) != (b.tier, b.score, b.action["status"]):
                    changed.append(dict(step=step, job=job, person=row.person, before=[b.tier, b.score, b.action["status"]],
                                        after=[row.tier, row.score, row.action["status"]]))
        print(f"Checked sample step {step}", flush=True)
    audit_report = audit(e)
    errors = sum(w.extractor.model.stats.get("errors", 0) +
                 (w.extractor.model.verifier.stats.get("errors", 0) if w.extractor.model.verifier else 0) for w in e._worlds.values())
    report = dict(supplied_fictional_packet=True, model="gpt-4o", checked=audit_report["checked"],
                  audit_findings=audit_report["findings"], model_errors=errors, changed_results=changed)
    report["separate_checker"] = checked
    (ROOT / "docs" / ("checked_sample_evaluation.json" if checked else "semantic_sample_evaluation.json")).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "changed_results"} | {"changed_results": len(changed)}), flush=True)
    return 1 if audit_report["findings"] or errors else 0


def run(live=False, checked=False):
    cfg = config.load("openai")
    cfg["model_reader"]["verify_claims"] = checked
    store = ROOT / "recorded" / "semantic_reads.json"
    cfg["model_reader"]["recorded_path"] = str(store)
    if not live:
        cfg["model_reader"]["provider"] = "recorded"
    model = make_reader(cfg)
    rows = []
    for name, text, kind, attr, key, expected in CASES:
        job = kind.startswith("job")
        ev = Evidence("evaluation/" + name, name, "synthetic", kind, "job:test" if job else "person:test:1",
                      parse_when("2026-11-06T12:00:00Z")[0], False, "account_manager" if job else "candidate", "Synthetic reader evaluation", text)
        plain = Extractor(cfg, Lexicon(cfg)).run(ev)
        reviewed = Extractor(cfg, Lexicon(cfg), model).run(ev)
        row = dict(case=name, rules=holds(plain, attr, key, expected), reviewed=holds(reviewed, attr, key, expected))
        rows.append(row)
        print(json.dumps(row), flush=True)
    report = dict(synthetic=True, model=model.model_id, cases=len(rows),
                  rules_passed=sum(r["rules"] for r in rows), reviewed_passed=sum(r["reviewed"] for r in rows),
                  stats=dict(model.stats), results=rows)
    report["separate_checker"] = checked
    report["checker_stats"] = dict(model.verifier.stats) if model.verifier else None
    report_path = ROOT / "docs" / ("checker_evaluation.json" if checked else "reader_evaluation.json")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}), flush=True)
    return 1 if report["reviewed_passed"] != len(rows) or model.stats.get("errors") or (model.verifier and model.verifier.stats.get("errors")) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="send these synthetic cases to OpenAI; otherwise replay saved readings")
    parser.add_argument("--sample", action="store_true", help="audit all 176 supplied-packet results instead of the synthetic language cases")
    parser.add_argument("--checked", action="store_true", help="also run the separate source-entailment checker")
    args = parser.parse_args()
    raise SystemExit(sample(args.live, args.checked) if args.sample else run(args.live, args.checked))
