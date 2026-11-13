"""Repeatable scale benchmark on SYNTHETIC data.

    python3 bench/benchmark.py --n 100000

What it does
  1. Generates N synthetic candidates (seeded, so every run is the same) in the same
     shapes the ATS and client-database adapters read, and adds them to the supplied sample.
  2. Loads everything through the real adapters, text reader and resolver, into the
     running-service form of the engine (shortlist.pipeline.Live).
  3. Delivers events one at a time, as a running service would receive them, and times
     each: new jobs arriving, then candidate updates.
  4. Times what a recruiter's page asks for: one screenful of a list, a screenful deep
     in the list, and one person's full explanation.
  5. With --workers 1,2,4: builds the same lists on 1, 2 and 4 worker processes of this
     machine (shortlist/workers.py), checks the lists are identical, and times each.

What it does and does not show
  * It measures speed and memory of this code on this machine. Those numbers are MEASURED.
  * It says nothing about match quality: the synthetic people are template text.
  * It runs in one process with everything in memory. Network, database and source-API
    time are not included; those would be ESTIMATES and are discussed in docs/ARCHITECTURE.md.
  * The worker figures are several processes on ONE machine sharing the loaded data. They
    measure how far the matching step speeds up when split; they are not a distributed system.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import random
import resource
import statistics
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shortlist import config  # noqa: E402
from shortlist.matching import Shortlist  # noqa: E402
from shortlist.model import Batch, Event  # noqa: E402
from shortlist.pipeline import Live, cards_payload, detail_payload  # noqa: E402
from shortlist.sources import Loader, load_source  # noqa: E402
from shortlist.timeutil import parse_when  # noqa: E402
from shortlist.workers import Workers  # noqa: E402

PLACES = [("Spokane", "WA", "Washington"), ("Seattle", "WA", "Washington"), ("Portland", "OR", "Oregon"),
          ("Boise", "ID", "Idaho"), ("Columbus", "OH", "Ohio"), ("Dayton", "OH", "Ohio"), ("Denver", "CO", "Colorado"),
          ("Austin", "TX", "Texas"), ("Phoenix", "AZ", "Arizona"), ("Sacramento", "CA", "California")]
PROFESSIONS = [("Physical Therapist", 0.30), ("Physician", 0.12), ("Registered Nurse", 0.33),
               ("Occupational Therapist", 0.13), ("Speech-Language Pathologist", 0.12)]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def pick(rng: random.Random, weighted):
    r, acc = rng.random(), 0.0
    for value, weight in weighted:
        acc += weight
        if r <= acc:
            return value
    return weighted[-1][0]


def synthetic_people(n: int, seed: int) -> tuple[dict, dict]:
    """N synthetic candidates as (ATS export, client-database dump). Every record is marked synthetic."""
    rng = random.Random(seed)
    candidates, people, journal = [], [], []
    for i in range(1, n + 1):
        cid = f"S{i:06d}"
        profession = pick(rng, PROFESSIONS)
        city, st, state = rng.choice(PLACES)
        updated = date(2026, 11, 6) - timedelta(days=rng.randint(0, 400))
        start_year, start_month = rng.randint(2012, 2026), rng.randint(1, 10)
        if profession == "Physical Therapist":
            home_health = rng.random() < 0.45
            tools = []
            if home_health:
                tools = ["independent home visits"] + (["OASIS start-of-care documentation"] if rng.random() < 0.7 else []) \
                        + (["Homecare Homebase"] if rng.random() < 0.5 else [])
            setting = "home-health PT" if home_health else "outpatient PT"
            cv = (f"DPT, Synthetic University, {state}, completed {start_year - 1}. Synthetic Care {i % 97}, {setting}, "
                  f"{start_year}-{start_month:02d} to present" + (": " + ", ".join(tools) if tools else "") +
                  f". Candidate reports active {state} PT license.")
            skills = ["home health" if home_health else "outpatient rehabilitation"]
        elif profession == "Physician":
            abem = rng.random() < 0.6
            cv = (f"{'Emergency' if abem else 'Family'}-medicine residency at Synthetic Medical Center, completed {start_year}. "
                  f"Emergency-department attending, {start_year} to present. Candidate reports "
                  f"{'ABEM certification' if abem else 'family-medicine board certification, ATLS, ACLS, PALS and BLS'} and active {state} license.")
            skills = ["emergency-department practice"]
        else:
            cv = f"Synthetic Hospital {i % 53}, {profession.lower()}, {start_year}-{start_month:02d} to present. Candidate reports active {state} license."
            skills = []
        roll = rng.random()
        if roll < 0.70:
            ops = "Unassigned; no recorded contact restriction; no current placement recorded."
        elif roll < 0.92:
            r = rng.randint(1, 60)
            ops = f"Assigned recruiter R{r}; candidate requested that opportunities go through R{r}."
        else:
            ops = "Unassigned; contact_allowed=true; no placement recorded."
        candidates.append({
            "id": cid, "first_name": "Synthetic", "last_name": cid, "email": f"{cid.lower()}@synthetic.invalid",
            "updated_at": updated.isoformat(), "profession": profession, "specialty": None,
            "address": {"city": city, "state": st}, "skills": skills, "preferences": {"states": [st]},
            "available_from": None, "expected_pay": None, "status": "lead", "source_text": "SYNTHETIC",
            "documents": [{"id": f"{cid}-CV", "type": "resume_text", "received_at": updated.isoformat(), "text": cv}],
            "operations": {"source_id": f"{cid}-OPS", "recorded_at": updated.isoformat(), "text": ops},
        })
        people.append({"person_pk": i, "ats_candidate_id": cid, "email_address": f"{cid.lower()}@synthetic.invalid",
                       "last_updated": updated.isoformat()})
        if rng.random() < 0.6:          # six in ten have said something recently
            said = date(2026, 11, 6) - timedelta(days=rng.randint(0, 45))
            if profession == "Physician":
                body = rng.choice([
                    "I could cover both December 5 and 6 day shifts in Dayton. My minimum is $%d/hour." % rng.choice([220, 230, 240, 255]),
                    "I cannot work December 5 or 6.",
                    "Please do not contact me about any jobs until January 15, 2027.",
                ])
            else:
                target = rng.choice(["Spokane", city])
                body = (f"I am open to {target} now. I can start {MONTHS[rng.choice([10, 11, 11, 11])]} {rng.choice([1, 1, 8, 15])}, "
                        f"work weekdays for thirteen weeks, and need at least ${rng.choice([2200, 2300, 2400, 2500, 2600]):,} per week for forty hours.")
            journal.append({"entry_pk": f"{cid}-MSG", "person_fk": i, "entry_type": "candidate_message",
                            "created_on": said.isoformat(), "body": body})
    ats = {"synthetic": True, "source": "synthetic_ats", "data": {"candidates": candidates, "jobs": []}}
    db = {"synthetic": True, "source": "synthetic_client_database", "tables": {"people": people, "journal_entries": journal, "job_notes": []}}
    return ats, db


def synthetic_jobs(k: int) -> dict:
    """K synthetic job postings as VMS feed events, modelled on the sample's own postings. Marked synthetic."""
    vms = json.loads((ROOT / "assessment-data" / "vms_response.json").read_text(encoding="utf-8"))
    templates = [next(r for r in vms["requisitions"] if r["requisitionNumber"] == "VMS-901"), vms["changeFeed"][0]["record"]]
    feed = []
    for i in range(1, k + 1):
        record = copy.deepcopy(templates[i % 2])
        record["requisitionNumber"] = f"SYN-J{i:03d}"
        record["positionName"] = f"{record.get('positionName', 'Physical Therapist')} (synthetic {i})"
        feed.append({"eventId": f"SYN-EV-J{i:03d}", "occurredAt": f"2026-11-07T19:{i % 60:02d}:00Z", "eventType": "requisition.created", "record": record})
    return {"synthetic": True, "exported_at": "2026-11-07T18:00:00Z", "requisitions": [], "changeFeed": feed}


def timed(fn, runs: int = 1):
    samples, result = [], None
    for _ in range(runs):
        started = time.perf_counter()
        result = fn()
        samples.append(time.perf_counter() - started)
    return result, statistics.median(samples), min(samples), max(samples)


def spread(samples: list) -> dict:
    ordered = sorted(samples)
    return {"median_s": statistics.median(ordered), "min_s": ordered[0], "max_s": ordered[-1],
            "p95_s": ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))], "runs": len(ordered)}


def peak_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def now_mb() -> float:
    """Memory this process holds right now, as the operating system reports it."""
    try:
        return int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout.strip()) / 1024
    except (ValueError, OSError):
        return float("nan")


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic scale benchmark")
    ap.add_argument("--n", type=int, default=100_000, help="number of synthetic candidates")
    ap.add_argument("--jobs", type=int, default=10, help="number of synthetic job postings delivered as events")
    ap.add_argument("--updates", type=int, default=200, help="number of synthetic candidate updates delivered as events")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="out/benchmark.json")
    ap.add_argument("--workers", default="1", help="worker counts to compare, for example 1,2,4 (1 = this process alone)")
    opts = ap.parse_args()
    rng = random.Random(opts.seed)

    cfg = config.load()
    (ats, db), t_generate, _, _ = timed(lambda: synthetic_people(opts.n, opts.seed))
    cfg["sources"] += [
        {"name": "synthetic_ats", "adapter": "ats", "label": "Synthetic ATS", "system_id": "synthetic_ats", "path": "(generated)", "data": ats},
        {"name": "synthetic_db", "adapter": "client_db", "label": "Synthetic client DB", "system_id": "synthetic_client_database",
         "path": "(generated)", "data": db, "candidate_source": "synthetic_ats"},
        {"name": "synthetic_vms", "adapter": "vms", "label": "Synthetic VMS feed", "system_id": "synthetic_vms", "path": "(generated)",
         "data": synthetic_jobs(opts.jobs), "date_format": "%m/%d/%Y"},
    ]
    ctx = {"system_ids": {s.get("system_id", s["name"]): s["name"] for s in cfg["sources"]}}
    batches, t_adapters, _, _ = timed(lambda: [load_source(s, ctx, ROOT) for s in cfg["sources"]])
    for s in cfg["sources"]:                      # the raw input is no longer needed once translated
        s.pop("data", None)
    del ats, db
    problems = [i for b in batches for i in b.issues if i.severity == "error"]
    assert not problems, problems
    events = sorted((e for b in batches for e in b.events), key=lambda e: e.at)
    snapshot = parse_when(cfg["clock"]["snapshot_at"])[0]
    live, t_world, _, _ = timed(lambda: Live(cfg, batches, snapshot))     # reads the text, links and resolves; then freezes the loaded data
    world = live.world
    n_people = len(world.people)
    mem_loaded = now_mb()
    by_prof = {k: len(v) for k, v in world.by_profession.items()}

    rows = []
    # 1. the jobs already open at the snapshot: each shortlist is built once
    for key in live.open_jobs():
        started = time.perf_counter()
        sl = live.shortlist(key)
        sl.ranked()
        rows.append({"what": f"shortlist for a job already open ({key[4:]})", **spread([time.perf_counter() - started]),
                     "evaluated": sl.stats["evaluated"], "set_aside_by_profession": sl.stats["excluded_by_profession"], "tiers": dict(sl.stats["tiers"])})

    # 2. new jobs arrive, one event at a time: apply the event, build the shortlist, rank it, say what changed
    samples, checked = [], 0
    job_events = [e for e in events if e.kind == "job.created"]
    for e in job_events:
        started = time.perf_counter()
        changes = live.ingest(e)
        live.shortlist(changes[0]["job"]).ranked()
        samples.append(time.perf_counter() - started)
        checked = live.shortlist(changes[0]["job"]).stats["evaluated"]
    rows.append({"what": f"NEW JOB arrives -> ranked shortlist ({len(job_events)} jobs, one at a time)", **spread(samples),
                 "evaluated": checked, "set_aside_by_profession": n_people - checked, "tiers": {}})
    open_jobs = live.open_jobs()
    footprints = [live.shortlist(k).footprint() / 1e6 for k in open_jobs]
    mem_per_list = max(footprints)

    # 3. what the page asks for. Each figure includes turning the answer into JSON, as the server does.
    big = max((live.shortlist(k) for k in open_jobs), key=lambda sl: sl.stats["evaluated"])
    size = {}

    def first_page():
        size["page"] = len(json.dumps(cards_payload(world, big, "best", 0, 25), default=str))

    def deep_page():
        total = sum(big.stats["tiers"].values())
        json.dumps(cards_payload(world, big, "not_fit", max(0, big.stats["tiers"]["not_fit"] - 25), 25), default=str)
        return total

    people_in_list = list(big.by_person)

    def one_explanation():
        size["detail"] = len(json.dumps(detail_payload(world, big, rng.choice(people_in_list)), default=str))

    for what, fn, runs in (("first screenful of a list (25 people)", first_page, 200), ("last screenful of the same list", deep_page, 200),
                           ("one person opened: every rule, with evidence", one_explanation, 500)):
        samples = []
        for _ in range(runs):
            started = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - started)
        rows.append({"what": what, **spread(samples), "evaluated": big.stats["evaluated"], "set_aside_by_profession": 0, "tiers": {}})

    # 4. candidate updates arrive, one event at a time: re-resolve that person, re-check them against every open job,
    #    replace their row in each shortlist, say what changed
    targets = [p.key for p in world.people.values() if p.key.startswith("person:synthetic_ats:")]
    samples, moved = [], 0
    for k in range(opts.updates):
        target = rng.choice(targets)
        L = Loader({"name": "synthetic_updates", "label": "Synthetic updates"}, {})
        b = Batch()
        L.evidence(f"UPD-{k}", "candidate_message", target, f"2026-11-07T20:{k % 60:02d}:{k // 60 % 60:02d}Z", "candidate", f"Synthetic update {k}",
                   text=f"My start date has changed. I cannot start until December {8 + k % 3}.", into=b)
        event = Event(id=f"UPD-{k}", at=parse_when(f"2026-11-07T20:{k % 60:02d}:{k // 60 % 60:02d}Z")[0], kind="candidate.updated",
                      label=f"Synthetic update {k}", source="synthetic_updates", batch=b)
        started = time.perf_counter()
        moved += len(live.ingest(event))
        samples.append(time.perf_counter() - started)
    rows.append({"what": f"CANDIDATE UPDATE -> their row replaced in every open job ({len(open_jobs)} open)", **spread(samples),
                 "evaluated": len(open_jobs), "set_aside_by_profession": 0, "tiers": {}})

    # 5. the first read of a list after an update puts it back in order
    samples = []
    for _ in range(5):
        big.dirty = True
        started = time.perf_counter()
        big.ranked()
        samples.append(time.perf_counter() - started)
    rows.append({"what": "a list put back in order on its first read after an update", **spread(samples),
                 "evaluated": big.stats["evaluated"], "set_aside_by_profession": 0, "tiers": {}})

    # 5b. a recruiter's own weights and filters: the whole list re-scored and re-ordered, then a screenful served
    samples = []
    tilts = [{"pay": 35, "readiness": 10}, {"depth": 30, "setting": 30}, {"readiness": 40}, {"evidence": 40, "pay": 0}]
    for k in range(8):
        started = time.perf_counter()
        cards_payload(world, big, "best", 0, 25, tilts[k % len(tilts)], ["act_now"] if k % 2 else [])
        samples.append(time.perf_counter() - started)
    rows.append({"what": "a recruiter moves a weight or a filter: the whole list re-scored, first screenful served", **spread(samples),
                 "evaluated": big.stats["evaluated"], "set_aside_by_profession": 0, "tiers": {}})

    # 6. the same job with the profession index off: every rule for every person
    job = world.jobs[big.job.key]
    started = time.perf_counter()
    everyone = Shortlist(world, job, cfg, pool=list(world.people.values()), compact_rows=True)
    t_everyone = time.perf_counter() - started
    rows.append({"what": "one job with the profession index off: every rule for every person", **spread([t_everyone]),
                 "evaluated": everyone.stats["evaluated"], "set_aside_by_profession": 0, "tiers": {}})
    per_pair_us = t_everyone / max(everyone.stats["evaluated"], 1) * 1e6
    del everyone

    # 7. the same lists built on several worker processes of this machine. One worker is this process alone.
    #    Each figure includes handing out the work, getting the rows back, putting the list together and ranking it.
    counts = sorted({max(1, int(x)) for x in opts.workers.split(",") if x.strip()})
    scaling = []
    if counts != [1]:
        jobs_open = [world.jobs[k] for k in live.open_jobs()]
        everyone_job = copy.copy(job)
        look = lambda sl: [(m.person, m.tier, m.score, m.low, m.high) for m in sl.ranked()]      # noqa: E731
        reference = None

        def med(fn, runs):
            return timed(fn, runs)[1]

        for n in counts:
            if n == 1:
                one = lambda: Shortlist(world, job, cfg, compact_rows=True)                        # noqa: E731
                many = lambda: [Shortlist(world, j, cfg, compact_rows=True).ranked() for j in jobs_open]   # noqa: E731
                whole = lambda: Shortlist(world, job, cfg, pool=list(world.people.values()), compact_rows=True).ranked()   # noqa: E731
                reference = look(one())
                row = {"workers": 1, "one_job_s": med(lambda: one().ranked(), 5), "all_jobs_s": med(many, 3), "everyone_s": med(whole, 2),
                       "identical": True, "largest_worker_mb": None}
            else:
                cfg_all = copy.deepcopy(cfg)
                cfg_all.setdefault("matching", {})["block_on_profession"] = False               # every rule for every person
                with Workers(world, cfg, n) as ws:
                    same = look(ws.shortlist(job)) == reference
                    row = {"workers": n, "one_job_s": med(lambda: ws.shortlist(job).ranked(), 5),
                           "all_jobs_s": med(lambda: [sl.ranked() for sl in ws.shortlists(jobs_open).values()], 3), "identical": same}
                with Workers(world, cfg_all, n) as ws:
                    row["everyone_s"] = med(lambda: ws.shortlist(everyone_job).ranked(), 2)
                kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
                row["largest_worker_mb"] = kids / (1024 * 1024) if sys.platform == "darwin" else kids / 1024
            assert row["identical"], f"{n} workers gave a different list"
            scaling.append(row)
        scaling_meta = {"one_job_people": len(reference), "jobs": len(jobs_open), "everyone_people": n_people, "cores": os.cpu_count()}

    report = {
        "label": "SYNTHETIC benchmark. Speed and memory are measured; match quality is not assessed.",
        "machine": {"platform": platform.platform(), "processor": platform.processor() or platform.machine(),
                    "python": platform.python_version(), "single_process": True},
        "inputs": {"synthetic_candidates": opts.n, "synthetic_jobs": opts.jobs, "synthetic_updates": opts.updates, "seed": opts.seed,
                   "people_in_world": n_people, "open_jobs_at_end": len(open_jobs),
                   "evidence_items": len(world.evidence), "facts_extracted": world.claim_count, "people_by_profession": by_prof},
        "load": {"generate_synthetic_s": t_generate, "adapters_s": t_adapters, "read_resolve_s": t_world,
                 "people_per_second": n_people / (t_adapters + t_world)},
        "events": rows, "per_pair_microseconds": per_pair_us, "results_moved_by_updates": moved,
        "page_bytes": size, "memory_after_load_mb": mem_loaded, "largest_shortlist_mb": mem_per_list, "all_shortlists_mb": sum(footprints),
        "memory_at_end_mb": now_mb(), "peak_memory_mb": peak_mb(),
    }
    if scaling:
        report["workers"] = {"note": "Worker processes on one machine, forked from the loaded process. Measured. Not a distributed deployment.",
                             **scaling_meta, "rows": scaling}
    out = ROOT / opts.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    unit = (lambda s: f"{s * 1000:7.1f}ms" if s < 1 else f"{s:8.2f}s")
    print(f"SYNTHETIC benchmark: {opts.n:,} generated candidates + the supplied sample = {n_people:,} people")
    print(f"machine: {report['machine']['platform']}, Python {report['machine']['python']}, one process, in memory")
    print("\nLOAD (one-off; after this, changes are applied one at a time)")
    print(f"  adapters: translate source records      {t_adapters:8.2f} s")
    print(f"  read text, link, resolve                {t_world:8.2f} s   ({report['load']['people_per_second']:,.0f} people/s end to end)")
    print(f"  evidence items {len(world.evidence):,}; dated facts {world.claim_count:,}")
    print(f"  memory after load {mem_loaded:,.0f} MB; the largest shortlist holds {mem_per_list:,.1f} MB of rows, all {len(open_jobs)} together "
          f"{sum(footprints):,.1f} MB; at the end {report['memory_at_end_mb']:,.0f} MB; peak {report['peak_memory_mb']:,.0f} MB")
    print(f"\nEVENTS AND READS ({len(open_jobs)} open jobs by the end)")
    print(f"  {'what':<74} {'median':>9} {'min':>9} {'p95':>9} {'max':>9} {'runs':>5} {'people':>8}")
    for r in rows:
        print(f"  {r['what']:<74} {unit(r['median_s']):>9} {unit(r['min_s']):>9} {unit(r['p95_s']):>9} {unit(r['max_s']):>9} {r['runs']:>5} {r['evaluated']:>8,}")
    print(f"\n  one person against one job, all {len(cfg['rules']) + len(cfg['gates'])} rules: {per_pair_us:,.0f} microseconds on average across all professions")
    print(f"  a screenful of 25 people is {size.get('page', 0):,} bytes; one explanation is about {size.get('detail', 0):,} bytes")
    print(f"  {opts.updates} updates moved {moved} results between tiers")
    if scaling:
        base = scaling[0]
        x = lambda r, k: f"{unit(r[k]).strip():>9} {base[k] / r[k]:4.1f}x"                        # noqa: E731
        print(f"\nWORKERS (processes on this one machine, {scaling_meta['cores']} cores; every list checked identical to the one-process list)")
        print(f"  {'workers':<8} {'one new job (' + format(scaling_meta['one_job_people'], ',') + ' people)':>30} "
              f"{'all ' + str(scaling_meta['jobs']) + ' open jobs rebuilt':>28} {'every rule, all ' + format(scaling_meta['everyone_people'], ',') + ' people':>34} {'largest worker':>15}")
        for r in scaling:
            mem = "this process" if r["largest_worker_mb"] is None else f"{r['largest_worker_mb']:,.0f} MB"
            print(f"  {r['workers']:<8} {x(r, 'one_job_s'):>30} {x(r, 'all_jobs_s'):>28} {x(r, 'everyone_s'):>34} {mem:>15}")
    print(f"  written to {opts.out}")


if __name__ == "__main__":
    main()
