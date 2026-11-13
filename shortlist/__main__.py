"""Command line entry point.

    python3 -m shortlist                 start the recruiter interface
    python3 -m shortlist replay          print the demo: snapshot, new job, candidate update
    python3 -m shortlist coverage        show that every rule ran, and which outcomes it produced
    python3 -m shortlist explain JOB PERSON   every rule for one pair, e.g. explain VMS-901 C2
    python3 -m shortlist publish         write the shortlists to the configured (simulated) destinations
    python3 -m shortlist health          list the data problems the tool worked around
    python3 -m shortlist audit           check every result against the product's own rules (audit.py)
    python3 -m shortlist worked          the worked four-candidate evaluation (--write updates docs/WORKED_EXAMPLE.md)
    python3 -m shortlist burst           eight SYNTHETIC events at the same moment, delivered in many orders
"""
from __future__ import annotations

import argparse

from .matching import TIER_LABEL, TIER_ORDER
from .pipeline import Engine

MARK = {"met": "✓", "not_met": "✗", "unknown": "?", "na": "–", "clear": "✓", "route": "→", "blocked": "✗", "confirm": "!"}


def _print_job(jkey, job, matches, limit=None):
    number = jkey.split(":", 1)[1]
    print(f"\n  {number}  {job.titles[0] if job.titles else ''}  [{job.status}]")
    print(f"  {'#':>2} {'name':<14} {'tier':<16} {'score':>5} {'range':>9}  next action")
    for i, m in enumerate(matches[:limit] if limit else matches, start=1):
        tie = f"  = {m.identical} identical" if m.identical > 1 else ""
        score, span = ("—", "") if m.tier == "not_fit" else (m.score, f"{m.low}–{m.high}")      # ruled out: not ranked, no score
        print(f"  {i:>2} {m.name:<14} {TIER_LABEL[m.tier]:<16} {score:>5} {span:>9}  {m.action['label']}{tie}")
    if limit and len(matches) > limit:
        print(f"     … and {len(matches) - limit:,} more")


def replay(engine: Engine) -> None:
    for step in engine.steps:
        i = step["index"]
        w = engine.world(i)
        print("\n" + "=" * 100)
        print(f"{step['at'][11:16]} UTC  {step['label']}" + (f"  [{step['id']} from {step['source']}]" if step["id"] else ""))
        print("=" * 100)
        for c in engine.changes(i):
            print(f"  CHANGE  {c['text']}")
            for r in c["rules"]:
                print(f"            {r}")
        for jkey, (matches, _) in sorted(engine.matches(i).items()):
            job = w.jobs[jkey]
            if job.status == "open":
                _print_job(jkey, job, matches, limit=40)
            else:
                print(f"\n  {jkey.split(':', 1)[1]}  {job.titles[0]}  [{job.status}]  not recommended to anyone")


def coverage(engine: Engine) -> None:
    last = len(engine.events)
    cov = engine.coverage(last)
    print(f"Pairs evaluated at the final step: {cov['pairs']}  |  every rule ran for every pair: {cov['every_rule_ran']}")
    print(f"\n{'rule':<22} {'kind':<11} {'runs':>5}  outcomes on this data")
    for name, row in cov["rules"].items():
        counts = "  ".join(f"{o}={n}" for o, n in row["counts"].items())
        missing = f"   (not seen here: {', '.join(row['not_seen'])})" if row["not_seen"] else ""
        print(f"{name:<22} {row['kind']:<11} {row['evaluations']:>5}  {counts}{missing}")
    print(f"\nText-reading rules: {len(cov['reading_rules'])} defined, {len(cov['reading_never_fired'])} never fired on this data")
    for rid in cov["reading_never_fired"]:
        print(f"  never fired: {rid}")
    print(f"Clauses the reader could not use: {cov['unparsed_clauses']} (shown to recruiters as context, never guessed at)")
    if cov["unrecognised"]:
        print("\nSkills listed on résumés that no rule checks (worth adding to the vocabulary):")
        for row in cov["unrecognised"]:
            print(f"  {row['people']:>5}  {row['term']}")


def explain(engine: Engine, job_id: str, person_id: str, step: int) -> None:
    w = engine.world(step)
    jkey = next(k for k in w.jobs if k.endswith(":" + job_id) or job_id in w.jobs[k].ids.values())
    row = next(x for x in engine.shortlist(step, jkey).ranked() if x.person.endswith(":" + person_id))
    m = engine.explain(step, jkey, row.person)
    print(f"{m.name} for {jkey.split(':', 1)[1]}: {TIER_LABEL[m.tier]}"
          + ("" if m.tier == "not_fit" else f", score {m.score} (range {m.low}–{m.high})"))
    print(f"Next action: {m.action['label']}")
    for r in m.results + m.gates:
        print(f"\n {MARK[r.outcome]} {r.label} [{r.level}] — {r.outcome}")
        if r.needs:
            print(f"     job:    {r.needs}")
        print(f"     person: {r.shows}")
        for ref in r.refs:
            ev = w.evidence[ref['evidence']]
            print(f"       “{ref['quote']}” — {ev.label}, {ev.observed_at.date()}" + (" (replaced)" if ref["note"] else ""))
        for n in r.notes:
            print(f"     note:   {n}")
        for q in r.questions:
            print(f"     ask:    {q}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="shortlist", description="Rule-based candidate-job matching for recruiters.")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "replay", "coverage", "explain", "publish", "health", "audit", "worked", "burst", "baseline"])
    parser.add_argument("args", nargs="*")
    parser.add_argument("--overlay", help="configuration overlay from config/overlays, e.g. feed_only")
    parser.add_argument("--step", type=int, help="replay step (0 = snapshot)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--write", action="store_true", help="worked: rewrite docs/WORKED_EXAMPLE.md")
    opts = parser.parse_args()

    if opts.command == "burst":                  # several SYNTHETIC events at the same moment, on the supplied data
        from .burst import render, run
        print(render(run(opts.overlay)))
        return
    if opts.command == "serve":
        from .server import serve
        serve(opts.port)
        return
    try:
        engine = Engine(overlay=opts.overlay)
    except ValueError as exc:                      # an unknown settings profile, or a setting that cannot be right
        from .config import list_overlays
        raise SystemExit(f"{exc}\nSettings profiles available: {', '.join(o['name'] for o in list_overlays())}") from None
    last = len(engine.events)
    if opts.command == "baseline":               # a simple field-and-keyword matcher, run for comparison
        from .baseline import render, run
        print(render(run(engine)))
    elif opts.command == "replay":
        replay(engine)
    elif opts.command == "coverage":
        coverage(engine)
    elif opts.command == "explain":
        if len(opts.args) != 2:
            raise SystemExit("explain needs a job and a person, for example: python3 -m shortlist explain VMS-901 C2")
        try:
            explain(engine, opts.args[0], opts.args[1], last if opts.step is None else opts.step)
        except StopIteration:
            raise SystemExit(f"No job {opts.args[0]!r} with a person {opts.args[1]!r} at this step. "
                             "Run python3 -m shortlist replay to see the jobs and people on file.") from None
    elif opts.command == "health":
        found = engine.issues(last if opts.step is None else opts.step)
        print(f"{len(found)} data problem{'s' if len(found) != 1 else ''} worked around." if found else "No data problems found.")
        for i in found:
            print(f"  [{i['severity']}] {i['source']} · {i['where']}: {i['message']}")
    elif opts.command == "worked":
        from pathlib import Path
        from .worked import render
        if opts.overlay:
            raise SystemExit("The worked example is written for the default settings. Run it without --overlay; "
                             "use replay or explain to see another settings profile.")
        text = render(engine)
        if opts.write:
            Path(engine.root, "docs", "WORKED_EXAMPLE.md").write_text(text, encoding="utf-8")
            print("wrote docs/WORKED_EXAMPLE.md")
        else:
            print(text)
    elif opts.command == "audit":
        from .audit import audit
        report = audit(engine, None if opts.step is None else [opts.step])
        found = report["findings"]
        print(f"Checked {report['checked']} results (every job and person at every step) against: tier, score, "
              "evidence, authority, doubt, time, action, wording, list.")
        print("Every check held." if not found else f"{len(found)} problem{'s' if len(found) != 1 else ''} found:")
        for f in found:
            print(f"  step {f['step']} · {f['job']} · {f['person']} · {f['check']}: {f['detail']}")
        raise SystemExit(1 if found else 0)
    elif opts.command == "publish":
        for path in engine.publish(last if opts.step is None else opts.step):
            print("wrote", path)


if __name__ == "__main__":
    main()
