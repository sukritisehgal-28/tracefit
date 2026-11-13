# Tracefit

Candidate–job matching for recruiters. When a job arrives, Tracefit checks every person against it in two stages: **can they do this job** (required rules, pass or fail), then **how good a fit are they** (a score out of 100 from six named dimensions). Every result shows the quote and source behind it, what is still unknown, and what the recruiter is allowed to do next. It recommends. It contacts nobody and submits nothing.

![The recruiter page: the home-health job, its ranked list and the rank-by controls](docs/screenshot.png)

## Run it

Python 3.11 or newer. Nothing to install, no account, no network.

```bash
python3 -m shortlist
```

Then open http://127.0.0.1:8765.

The product is Tracefit; its code package and commands are named `shortlist`, after the list of people it builds for each job.

```bash
python3 -m unittest discover -s tests
```

| Command | What it shows |
|---|---|
| `python3 -m shortlist replay` | The demo in text: the 18:00 snapshot, the 18:05 new job, the 18:10 candidate update |
| `python3 -m shortlist baseline` | A simple field-and-keyword matcher on the same data, beside Tracefit (the comparison in the evaluation, run) |
| `python3 -m shortlist worked` | Four candidates evaluated side by side ([docs/WORKED_EXAMPLE.md](docs/WORKED_EXAMPLE.md)) |
| `python3 -m shortlist audit` | The checker: every result against the product's own rules |
| `python3 -m shortlist burst` | Eight **synthetic** events at the same moment on the supplied data: what changed, and the same lists in 30 delivery orders |
| `python3 -m shortlist explain VMS-901 C2` | Every rule for one person and one job |
| `python3 -m shortlist coverage` | Every rule ran for every pair; what the text reader could not read |
| `python3 -m shortlist health` | Data problems the tool worked around |
| `python3 -m shortlist publish` | Write the shortlists to the simulated destinations (local files in `out/`) |
| `python3 tests/scenario_base.py` | 110 synthetic situations, each passing or failing by name |
| `python3 tests/test_rewording.py` | 163 reworded sentences: a "no", a doubt or a passing mention is not read as a "yes" |
| `python3 tests/test_own_criteria.py` | 37 typed phrases and 22 institutions against synthetic résumés, with the known limits listed |
| `python3 bench/benchmark.py --n 100000` | The synthetic scale benchmark |
| `python3 bench/benchmark.py --n 20000 --workers 1,2,4` | The same lists built on 1, 2 and 4 worker processes, checked identical and timed (also run at 100,000 on a 16-core cloud machine; see the architecture doc) |

Add `--overlay feed_only` or `--overlay strict_pay` to a `python3 -m shortlist` command (all but `worked`) to see another client's settings.

## Five-minute demo

1. **Home Health PT.** The banner says the account manager's note is ahead of the vendor feed; **See details** shows both versions and their sources. The list is one ranked list of the 12 people a recruiter could act on, top 10 first, then **Show next 2**.
2. **The list.** Each card shows the score, one line on what sets the person apart, and the next action; the evidence is one click away, in the person's panel. R01, R02 and R03 differ in the supplied file only by id and email, so the page groups them, says their documents are identical, marks their order arbitrary, and names what to ask each to tell them apart. The people ruled out are behind the **not a fit** link above the list. R08 **needs an answer** and sits ninth, where the score puts them, with what is missing. R11 passes everything but asked not to be contacted, so R11 is listed last.
3. **Open R04.** **Why this score** draws the six dimensions as a radar: R04 loses 30 points on evidence because "I have three years" is not supported by the dated history. Every rule below opens to its quote. **Show everything** opens it all.
4. **Rank by.** Pick **Closest to our work** (setting at 40%) or **Within budget**, or move a slider: the list re-ranks. Tick **Can act now** and R11 leaves. Under **Add your own**, type any phrase (`sports injury`) or any school or institution: the list narrows to the people whose own documents say it, and the card shows the sentence. Weights and filters never move anyone past a required rule.
5. **Compare** R04, R06 and R09: radars overlaid, rows that differ highlighted, plus what each lists on their résumé that no rule checks.
6. **Demo → A new job arrives.** The outpatient job appears, already matched. Blair, an outpatient therapist since 2016, is the top match at 96, well clear of the rest, and goes through recruiter R2.
7. **How this list was made**, under the list. For the home-health job: 16 people on file, 12 on the list, 4 not a fit. **Show the whole process** opens the points that decided it, the weights drawn as a web, and every step from the sources to the next action, with the count on each rule.
8. **Demo → A candidate sends an update.** Alex can now only start 8 December. One result changes; **View** opens it on the rule that changed, with the old message shown as replaced.
9. **Demo → Settings profile → Vendor feed only.** With the account manager's note ignored, nobody qualifies for the home-health job. That is the client's first open question.

## How it works

**From the sources to a recruiter's list.** Every step keeps the source, the date and the exact words, so each result can be traced back.

```mermaid
flowchart TD
  A["Sources<br/>ATS, VMS feed, client database,<br/>our system, extra packet"] --> B["Adapters<br/>one per source"]
  B --> C["Evidence and dated facts<br/>each keeps its source, date and words"]
  C --> D["Who is who, what is current<br/>joined only on a reference or an email"]
  D --> E{"Required rules<br/>profession, setting, skills, license,<br/>location, start date, schedule"}
  E -->|"a rule is not met"| F["Not a fit<br/>kept off the list, counted"]
  E -->|"no answer yet"| G["Needs an answer<br/>stays on the list, with the question"]
  E -->|"all met"| H["Six marks out of 100<br/>setting, skills, depth,<br/>evidence, readiness, pay"]
  G --> H
  H --> I["One ranked list per job<br/>score with a range"]
  P["Permission rules<br/>job open, contact, ownership"] --> J
  I --> J["Next action<br/>reach out, go through the owner,<br/>ask first, or do not contact"]
  J --> K["Recruiter page"]
  J --> L["Destinations<br/>simulated: local files only"]
```

**When something changes.** Only what the event touches is redone. Events that carry the same timestamp give the same lists whichever arrives first.

```mermaid
flowchart TD
  EV(["An event arrives"]) --> DUP{"Seen this event<br/>id before?"}
  DUP -->|"yes"| SKIP["Applied once only"]
  DUP -->|"no"| KIND{"What changed?"}
  KIND -->|"new job"| NJ["Build its list once:<br/>every rule for everyone<br/>in its profession"]
  KIND -->|"job changed"| JC["Rebuild that one list,<br/>report only who moved"]
  KIND -->|"job filled"| JX["Withdraw its list"]
  KIND -->|"candidate update"| CU["Re-read that person,<br/>replace their row<br/>in each open job"]
  KIND -->|"a kind the tool<br/>does not know"| UK["Ignored, listed<br/>under data problems"]
  CU --> TIE{"Two statements at the<br/>same moment that disagree?"}
  TIE -->|"yes"| Q["Neither is taken:<br/>the rule becomes a question"]
  TIE -->|"no"| NEW["The newest counts; the older<br/>stays visible as replaced"]
  NJ --> OUT["What changed, rule by rule,<br/>with the record behind it"]
  JC --> OUT
  JX --> OUT
  Q --> OUT
  NEW --> OUT
```

**The demo, in time order.** The first three steps are the supplied data. The last is a synthetic burst (`python3 -m shortlist burst`).

```mermaid
sequenceDiagram
  participant S as Sources
  participant T as Tracefit
  participant R as Recruiter page
  S->>T: 18:00 snapshot: 16 people, 3 jobs
  T->>R: Home Health PT: 12 on the list, 2 need an answer
  S->>T: 18:05 new job VMS-904
  T->>R: Outpatient PT matched at once, Blair first at 96
  S->>T: 18:10 Alex can only start 8 December
  T->>R: Alex for VMS-901: Match to Not a fit (start date)
  S-->>T: 18:15 eight events at the same moment (synthetic)
  T-->>R: Same lists in any delivery order, same as a full rebuild
```

## What is in it

| Folder | What is in it |
|---|---|
| `config/` | Every setting: sources, destinations, rules, dimension weights and presets, thresholds, source precedence, freshness windows, divisions, vocabulary. Overlays are alternative client profiles. |
| `shortlist/sources/`, `shortlist/sinks/` | One adapter per source and per destination. Matching imports neither. |
| `shortlist/extract.py`, `resume.py` | Reading text into dated facts with their exact words; reading whole résumés for the person's panel. |
| `shortlist/world.py` | Who is who, what is currently believed, what it replaced. |
| `shortlist/rules.py`, `dimensions.py`, `matching.py` | The required rules, the quality score, order, next action. |
| `shortlist/pipeline.py`, `server.py`, `web/` | Replay, the running-service path, the page. |
| `shortlist/audit.py`, `model_reader.py`, `workers.py` | The checker; an optional guarded language-model reader (off); list building on several worker processes (used by the benchmark). |
| `shortlist/baseline.py` | A simple matcher for comparison only. Not part of the product. |
| `tests/`, `bench/` | 231 tests, 110 scenarios, the rewording sweep; the synthetic benchmark. |

## What is real and what is simulated

| Part | Status |
|---|---|
| The four supplied sources and the supplement, text reading, resolution, required rules, quality score, replay, recruiter page, checker | Implemented, on the supplied data |
| Recruiter weights, presets, filters and typed criteria (a phrase, a school); source precedence; résumé background and links | Implemented |
| Working at size: a screenful at a time, explanations on demand, events applied one at a time | Implemented; speed measured on **synthetic** data |
| Destinations (JSON, CSV, notifications) | **Simulated**: local files only |
| Language-model reader | Built behind a guard, **off, never run against a real model**; tested only with made-up stand-ins |
| Database, queue, several machines, live connections, logins | Designed, not built |
| Credential checks | Not done: every license and certificate is shown as reported |

The text reader is pattern-based and is the weakest part: wording unlike the sample's can be left unread or misread. [Evaluation](docs/EVALUATION.md) says what independent review found and what is still open.

More: [Architecture](docs/ARCHITECTURE.md), also as [one illustrated page](docs/architecture.html) (open the file in a browser after cloning; GitHub shows its source) · [Evaluation](docs/EVALUATION.md) · [Handoff](docs/HANDOFF.md), with the [questions for the client](docs/HANDOFF.md#questions-for-the-client) · [Worked example](docs/WORKED_EXAMPLE.md)
