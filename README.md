# Tracefit

Candidate–job matching for recruiters. When a job arrives, Tracefit checks every person against it in two stages: **can they do this job** (required rules: met, not met, or unknown), then **how good a fit are they** (a score out of 100 from six named marks). Every result shows the quote and source behind it, what is still unknown, and what the recruiter may do next. It recommends; it contacts nobody and submits nothing.

**Live demo:** https://tracefit-434887347109.us-central1.run.app (the same page, hosted, with only the fictional sample data).

## Run it

Python 3.11 or newer. Nothing to install, no account, no network.

```bash
python3 -m shortlist
```

Then open http://127.0.0.1:8765. To run the tests:

```bash
python3 -m unittest discover -s tests
```

The product is Tracefit; its code package and commands are named `shortlist`, after the list it builds for each job.

| Command | What it shows |
|---|---|
| `python3 -m shortlist replay` | The demo in text: the 18:00 snapshot, the 18:05 new job, the 18:10 candidate update |
| `python3 -m shortlist explain VMS-901 C2` | Every rule for one person and one job, with its quotes |
| `python3 -m shortlist baseline` | A simple field-and-keyword matcher on the same data, beside Tracefit |
| `python3 -m shortlist audit` | The checker: every result against the product's own rules |
| `python3 -m shortlist burst` | Eight **synthetic** events at the same moment: the same lists in any delivery order |
| `python3 -m shortlist worked` | Four candidates taken through both stages ([docs/WORKED_EXAMPLE.md](docs/WORKED_EXAMPLE.md)) |
| `python3 -m shortlist coverage` / `health` / `publish` | Every rule ran for every pair · data problems worked around · write the shortlists to the simulated destinations |
| `python3 tests/model_run.py` | The language-model reader's one recorded run, replayed with no key |
| `python3 bench/benchmark.py --n 100000` | The **synthetic** scale benchmark |

Add `--overlay feed_only` or `--overlay strict_pay` to a `shortlist` command to see another client's settings.

### Enable the OpenAI second reader

Create an API key in the [OpenAI dashboard](https://platform.openai.com/api-keys), copy `.env.example` to `.env`, and fill in `OPENAI_API_KEY` locally:

```bash
cp .env.example .env
chmod 600 .env
```

Then start the page with the OpenAI settings profile:

```bash
python3 -m shortlist --overlay openai
```

The backend reads the key from `.env` or the process environment (environment values take priority), following [OpenAI's authentication guidance](https://developers.openai.com/api/reference/overview#authentication). `.env` files are excluded from Git, Docker builds and Cloud Run uploads. The key never goes to the browser. No extra package is needed.

Selecting this profile sends candidate and job text to OpenAI and uses your API billing. It uses the existing `gpt-4o` model setting; optionally set `OPENAI_MODEL` to override it. The model reviews each parser assertion and proposes missing facts. Unconfirmed assertions become questions; guarded corrections can replace a positive parse. Explicit denials, no-contact requests, source authority and requirement-note policy remain enforced. Refusals and outages leave unreviewed facts as questions rather than silently trusting the pattern reader.

Answers are cached in `out/openai_reads.json` by model, reader version, vocabulary, timestamp, document and parser claims. The page also offers this profile under **Demo → Settings profile**. To run it in text, use `python3 -m shortlist replay --overlay openai`.

The OpenAI profile also enables `verify_claims = true`: a **separate source-entailment check** of the final positive claims, using a fresh request and checker prompt without the extractor's verdicts. It can withhold a fact, but cannot invent a correction or remove a contact restriction. Missing checks, refusals and uncertainty turn the affected result into a question visible to the recruiter. This is a second model check, using the same model by default; shared errors are still possible. It adds an API request per document with positive claims, cached separately. `python3 bench/reader_eval.py --checked` replays the checked synthetic evaluation; add `--sample` for the supplied packet or `--live` to ask about uncached inputs.

`python3 bench/reader_eval.py` replays the saved real-model readings on 20 labelled **synthetic regression cases**: the reviewed reader passes 20, compared with 7 for the pattern reader alone. `--live` repeats the API requests for uncached inputs. These cases helped develop the reader; they are not a blind accuracy test. `--sample` audits the supplied fictional packet. The original 100,000-candidate benchmarks used the offline reader; model ingestion latency and cost at that scale have not been measured.

## Five-minute demo

1. **Home Health PT.** The banner says the account manager's note is ahead of the vendor feed; **See details** shows both versions and their sources.
2. **The list.** Twelve people, best first. R01, R02 and R03 differ only by id and email, so the page groups them, says their order is arbitrary, and names what to ask to tell them apart. R11 asked not to be contacted and is listed last.
3. **Open R04.** **Why this score** shows the six marks: R04 loses evidence points because "three years" is not supported by the dated history. Every rule opens to its quote.
4. **Rank by.** Pick **Within budget** or move the Pay slider: R06, who asks above the approved rate, drops from sixth to eighth. Type a skill (`sports injury`) and the list narrows to who says it.
5. **Compare** R04, R06 and R09 side by side.
6. **Demo → A new job arrives.** The outpatient job appears already matched, with Blair first at 96.
7. **Demo → A candidate sends an update.** Alex can now only start 8 December; one result changes.
8. **Demo → Settings profile → Vendor feed only.** With the account manager's note ignored, nobody qualifies for the home-health job: the client's first open question.

## How it works

```mermaid
flowchart TD
  A["Sources<br/>ATS, VMS feed, client database,<br/>our system, supplement"] --> B["Adapters, one per source"]
  B --> C["Dated facts<br/>each keeps its source and words"]
  C --> D["Who is who, what is current"]
  D --> E{"Required rules"}
  E -->|"a rule is not met"| F["Not a fit"]
  E -->|"no answer yet"| G["Needs an answer"]
  E -->|"all met"| H["Score from six marks"]
  G --> H
  H --> I["One ranked list per job"]
  P["Permission rules<br/>job open, contact, ownership"] --> J
  I --> J["Next action"]
  J --> K["Recruiter page"]
  J --> L["Destinations (simulated)"]
```

When an event arrives, only what it touches is redone: a new job is matched once, a candidate update replaces that person's row in each open job, and a filled job withdraws its list. Applying events one at a time gives exactly the same lists as rebuilding from scratch.

## What is in it

| Folder | What is in it |
|---|---|
| `shortlist/` | The product. `sources/` and `sinks/` hold one adapter per source and destination (matching imports neither); `extract.py` and `resume.py` read text; `world.py` decides who is who; `rules.py`, `dimensions.py` and `matching.py` hold the rules, the score and the list; `pipeline.py` and `server.py` run events and serve `web/`; `audit.py` is the checker; `model_reader.py` is the optional language-model reader; `workers.py` splits work across processes; `baseline.py` is the comparison matcher. |
| `config/` | Every setting: rules, weights, presets, thresholds, source precedence, freshness, vocabulary. `overlays/` holds other client profiles and the optional OpenAI profile. |
| `tests/` | Automated tests, 110 synthetic scenarios, the rewording sweep, inputs unlike the sample, semantic-reader regressions, and `model_run.py`. |
| `recorded/` | The language-model reader's saved answers, so its run replays without a key. |
| `bench/` | The synthetic scale benchmark, run on a laptop and on Google Cloud; outputs in `docs/benchmark_*.txt`. |
| `docs/` | The written explanation (below), the benchmark outputs, the illustrated architecture (`architecture.pdf`), and a draft first message to the client. |
| `assessment-data/` | The supplied sample, unchanged. |
| `Dockerfile` | The hosted demo. |

## What is real and what is simulated

| Part | Status |
|---|---|
| Reading the supplied sources, rules, score, events, recruiter page, checker | Implemented, on the supplied data |
| Speed at 20,000 and 100,000 people | Measured on **synthetic** data, on a laptop and on a 16-core Google Cloud machine (a new job at 100,000 people: 7.1 s on one worker, 1.2 s on sixteen) |
| Destinations (JSON, CSV, notifications) | **Simulated**: local files only |
| Language-model reader | **Off by default**. The OpenAI profile reviews parser claims and reads missing facts. New evaluation: `python3 bench/reader_eval.py`; the earlier augmentation-only experiment is preserved in `tests/model_run.py`. |
| Database, queue, several machines, live connections, sign-in | Designed, not built |
| Credential checks | Not done: every license and certificate is shown as reported |

The offline text reader is pattern-based. The optional OpenAI reader reviews those interpretations and reads unfamiliar phrasing, with uncertainty preserved. Neither path has been calibrated on real client data. [Evaluation](docs/EVALUATION.md) distinguishes the old experiment, the new regression results and remaining limits.

## Read more

- [Architecture](docs/ARCHITECTURE.md): matching, scoring, configuration, scale and cost. Also as an [illustrated PDF](docs/architecture.pdf).
- [Evaluation](docs/EVALUATION.md): why the results are useful, the tests, and the limits.
- [Handoff](docs/HANDOFF.md): assumptions, [questions for the client](docs/HANDOFF.md#questions-for-the-client), what is built, onboarding, and how AI was used.
- [Worked example](docs/WORKED_EXAMPLE.md): four candidates, rule by rule.
