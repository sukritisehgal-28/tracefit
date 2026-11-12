# Architecture and approach

## The idea

The same person and the same job are described differently, at different times, by sources of different authority, and much of what matters is in free text. So Shortlist keeps every fact with its source, date and exact words, decides what is currently believed without throwing the rest away, and then asks two separate questions about each person: **can they do this job**, and **how good a fit are they**. A recruiter sees the answer, the quote behind it, what is still unknown, and what they may do next.

```mermaid
flowchart LR
  S1[ATS] --> AD
  S2[VMS feed] --> AD
  S3[Client database] --> AD
  S4[Our system] --> AD
  S5[Supplement] --> AD
  AD["Adapters<br/>one per source"] -->|"evidence + claims"| RD["Read text<br/>55 rules"]
  MR["Model reader<br/>optional, off"] -.->|proposals| GD{"Guard"}
  GD -.->|"accepted claims"| RS
  RD --> RS["Resolve<br/>who is who, what is current"]
  RS --> GT{"Gates<br/>required rules"}
  GT -->|"one fails"| NF["Not a fit"]
  GT -->|"one unknown"| NA["Needs an answer"]
  GT -->|"all pass"| DM["Six quality dimensions"]
  DM --> SC["Score 0-100<br/>with range"]
  SC --> RK["Order, tier,<br/>similar people"]
  PR["Permission rules"] --> NX["Next action"]
  RK --> NX
  NX --> UI["Recruiter page"]
  NX --> DS["Destinations<br/>(simulated)"]
  CF[("Settings")] -.-> GT
  CF -.-> DM
  CK["Checker"] -.->|audits every result| NX
```

1. **Translate.** One adapter per source turns records into *evidence* (a record, résumé, note or message) and *claims* (one fact from one piece of evidence). Adapters decide nothing.
2. **Read.** 55 text rules turn free text into claims, each keeping its clause. They tell a skill described from a skill denied ("I have not used…") and from a skill merely mentioned as missing ("No OASIS detail supplied"), and respect who is speaking: a recruiter's note cannot assert a candidate's skill. A sentence with a negation or doubt the reader cannot place never yields a yes; it is left as a question.
3. **Resolve.** Records join into one person only on an explicit reference or an identical email, never on a name or a shared `info@` mailbox. Every person keeps a unique id (source plus that source's record id); two people who share a name stay apart, and their cards show which record each is. For each fact the configured **source precedence** decides which statement counts (newest by default), and the others stay visible as replaced. A fuller rendition of a résumé is the same evidence, not a second source.
4. **Gates.** Required rules, each met, not met or unknown, with the quote on both sides.
5. **Score.** For each person, six dimensions computed from those same rule results.
6. **Decide.** Tier, order, grouping of identical people, and a next action from the permission rules.

## Matching and scoring

### Stage 1: gates decide who qualifies

| Rule | Required by default | Reads |
|---|---|---|
| Profession | Yes | Structured profession |
| Experience in the job's care setting | When the posting requires it | Dated roles, claims, notes ("PT" alone does not say home health or outpatient) |
| Skills and tools | When the posting requires them | Résumé, profile, messages; a candidate's own denial wins |
| License in the job's state | Yes | As reported, never as verified |
| Board and certificates | Yes | Including the "other boards" route for the ED job |
| Location | Yes | Home, preferences, later withdrawals |
| Start date, schedule and length | Yes | Messages and records |
| Shift dates | Yes | Each listed shift against what the candidate offered, declined, or an assignment occupies |
| Pay | No: preferred | The ask against the approved rate |

A required rule not met means **not a fit**. One that cannot be answered means **needs an answer**: missing information is a question, never a no. No score can lift anyone past a gate. A license, or a shift a doctor declined, is not something a better résumé can make up for.

### Stage 2: the quality score ranks those who pass

| Dimension | Default weight | 100 means | Lowered by |
|---|---|---|---|
| Setting | 25 | Dated experience in the job's setting, or at an employer the client names | Experience with no dates (70) |
| Skills | 20 | Every skill the job asks for | Missing or denied skills; preferred count half |
| Depth | 15 | Documented years at twice the minimum (at least three) | Fewer years; claimed years count at most half |
| Evidence | 15 | Nothing contradicts or outdates the facts used | A claim the records do not support (−30), a newer message that could not be read (−30), an answer past its freshness window (−15) |
| Readiness | 15 | Start, schedule and shifts met, replied in the last week | Unmet availability; no recent reply |
| Pay | 10 | Asks at or below the approved rate | 10 points for each 1% above |

The score is the weighted average of the dimensions that apply. An unknown answer counts as half and widens the **range**: the score if every unknown turned out no, and if every one turned out yes. **Strong match** means every gate passes and the score holds at or above 80 even if every unknown turns out no; otherwise **possible match**.

Every point traces to a rule result and its quote, so the person panel shows each dimension as a radar and a line of plain words. [WORKED_EXAMPLE.md](WORKED_EXAMPLE.md) walks four real candidates through both stages; under the old count-the-rules score three of them tied at 100.

**Why rules and not a similarity model.** The recruiter must be able to check every result; the policies (which version of a requirement applies, whether pay is a ceiling, who may be contacted) are the client's decisions and must be visible and changeable; and the same input must give the same output with no paid service. The cost is that facts must first be read out of text. That limit is measured, not hidden ([EVALUATION.md](EVALUATION.md), experiment 8).

### Similarly qualified candidates

A list is ordered by tier; then anyone who must not be contacted goes last, under their own heading; then score; then three operational tie-breakers (can be acted on sooner, fewer open questions, heard from more recently). People still level are **grouped**: three of the sample's profiles have identical evidence, and the page says their order is arbitrary rather than invent a difference. Scores within 3 points are marked comparable. **Compare** puts two or three people side by side, rule by rule, with sources, dates and overlaid radars. No tie-breaker uses a personal attribute.

## What a client can configure

Everything below is in `config/default.toml`, with per-division overrides and whole-profile overlays. The page also lets a recruiter change the weights and filters for themselves, without touching the settings.

| Setting | Example |
|---|---|
| Which rules are gates | `[rules.pay] level = "hard"` makes pay a ceiling (the `strict_pay` overlay) |
| Dimension weights, per division | Locums weigh readiness 30 and pay 5 |
| Presets on the page | "Experience first", "Can start soonest", "Within budget" |
| Target employers | Experience at agencies the client names counts as full setting experience |
| Source precedence, per fact | `"req.start" = ["vms", "ats"]`: the vendor feed is the authority on start dates |
| Requirement changes from notes | `provisional` (use and flag), `feed_only`, `trusted`; and whose notes count |
| Freshness windows | Availability 30 days, 14 for locums |
| Who may assert what | A recruiter's note may record availability, never a skill |
| Thresholds, unknown credit, tie margin, top N | Strong at 80; unknown counts half |
| Vocabulary | Professions, settings, skills, certificates: a new division is configuration, not code |

Deliberately fixed: every rule runs for every pair; every fact keeps its source; credentials are never called verified; nothing is sent. Settings that name a rule the engine does not have are refused at start.

## Sources and destinations

The matching modules import no adapter, and no adapter imports matching code; a test reads the imports to keep it that way. **Adding a source** is one function that emits evidence and claims, plus a `[[sources]]` block: the supplement arrived in a different shape and went in as a fifth source with a 35-line adapter. **Adding a destination** is one function that receives a plain payload (job, people, tier, score, next action, questions). A test adds a synthetic source and destination at run time and checks that nobody else's result moves.

## Responding to change

```mermaid
sequenceDiagram
  participant V as VMS feed
  participant O as Our system
  participant L as Live engine
  participant S as Shortlists
  participant P as Recruiter page
  Note over L,S: 18:00 snapshot loaded, open jobs ranked
  V->>L: 18:05 new requisition VMS-904
  L->>S: build VMS-904 once: every rule, everyone in the profession
  L-->>P: New job VMS-904: 1 strong, 11 possible, 1 needs an answer
  O->>L: 18:10 Alex can only start 8 December
  L->>L: re-read and re-resolve Alex only
  L->>S: replace Alex's row in each open job, nobody else's
  L-->>P: Alex for VMS-901: Strong match to Not a fit (start date)
```

A job whose requirements change is rebuilt and only the people who moved are reported; a job that closes withdraws its list; a reopened job is announced as reopened. An event delivered twice is applied once; events out of order are applied by their own dates. A test checks that applying events one at a time gives exactly the rows, totals and messages of rebuilding from scratch, on the sample and all 102 scenarios.

## Scale, latency and cost

Each piece of work is tied to what changed, not to the size of the database:

| Work | Grows with | When |
|---|---|---|
| Read and resolve a record | That record | Once, on arrival |
| Build a job's shortlist | People in its profession (an index sets the rest aside; unknown profession stays in) | Once, when the job arrives or changes |
| Apply a candidate update | Open jobs in their profession | Once per update |
| Show a screenful; open one person | 25 rows; one person | On each view |
| A recruiter's own weights or filters | One list | On each change |

A shortlist is one compact row per person per job (tier, score, dimensions, next action, the line on the card); the full explanation is rebuilt when someone opens that person, and a test checks it cannot differ. The page asks for 25 people at a time. A destination gets the top 200 per job and the totals.

**Measured** on synthetic people, one process, in memory, on an 8 GB laptop. Before the 100,000 run the laptop had about 5 GB of swap in use by other apps, so its worst cases include waiting on the disk; the 20,000 run peaks at 355 MB and is the cleaner measurement.

| | 20,000 people | 100,000 people |
|---|---|---|
| One-off load: translate, read, link, resolve | 9.1 s | 52 s |
| People in the job's profession | 6,126 | 30,047 |
| **New job → ranked shortlist** (median of 11) | **0.74 s** | **5.0 s** (3.8 to 6.5) |
| **Candidate update → row replaced in 13 open jobs** (median of 200) | **0.2 ms** | **1.6 ms** (p95 9.8) |
| A recruiter moves a weight or filter: list re-scored, first screenful | 2.7 ms | 6.5 ms |
| A screenful of 25 / one person opened | 2.3 ms / 0.5 ms | 3.5 ms / 0.9 ms |
| Rows held for all 13 open jobs | 79 MB | 354 MB |

One person against one job, all rules and dimensions, costs 130 to 190 microseconds. Full output: [benchmark_20k.txt](benchmark_20k.txt), [benchmark_100k.txt](benchmark_100k.txt). Reproduce with `python3 bench/benchmark.py --n 100000`.

**Estimates, not measured.** A thousand open jobs in one profession would make a candidate update re-check about a thousand pairs: roughly 0.2 s. Rebuilding a thousand lists of 30,000 after a settings change: about 80 minutes of one core, divisible across workers. **Cost:** with no model the only running cost is one modest machine. A model reader, if the client wanted one, is paid once per document read (on arrival, and again only if it changes), never per match; answers are saved and replayed.

**Validation with the client:** replay a real day of events at production volume against a copy of their data, record event-to-shortlist time at the median and 95th percentile, and watch memory over a week. The benchmark script is that harness with synthetic adapters in place of real ones.

### As a running service (designed, not built)

```mermaid
flowchart LR
  SRC[Source systems] --> CON[Connectors]
  CON --> Q[Event queue<br/>ordered per person and job]
  Q --> W[Match workers<br/>split by profession]
  W --> DB[(Database<br/>evidence, claims, rows, outbox)]
  DB --> API[API servers] --> PG[Recruiter page]
  DB --> OR[Outbox relay] --> DST[Destinations]
  classDef planned stroke-dasharray: 5 5
  class CON,Q,DB,OR planned
```

Dashed parts are designed, not built. Each plugs into a seam that exists today: an adapter already takes one record at a time, `Live.ingest` takes one event, `World.apply` takes one batch, and `Shortlist.page` and `patch` read and replace one row. Writes and reads meet only in the database, so a page never waits for a shortlist to build. Build order: store evidence and rows; add the queue and one real connector and replay a real day; add sign-in and a change log; split workers only if the replay says a new job takes too long.

## Reading text safely, and an optional model

A false yes puts someone on a shortlist they should not be on, so the reader is built to leave text unread rather than misread it. A sweep of 124 reworded sentences checks that a "no" or an "unsure" is never read as a "yes", and that every way of saying "don't contact me" is caught. When a newer message on a topic cannot be read, an older yes on that topic becomes a question instead of standing.

A language model can be switched on as a **second reader** (off by default, never run here). Its proposals count only if a guard of ordinary code agrees: the quote is word for word in one sentence, mentions the thing claimed outside any understood negation, uses the vocabulary, comes from a kind of document allowed to say it, and comes from a sentence with no doubt. Disagreement with the rules becomes a question. It can never lift a no-contact request. Against a stand-in model that says yes to everything, nobody's tier rises. Sending candidate text to a provider needs the client's agreement first.

## The checker

`python3 -m shortlist audit` reads every result and confirms the product's own rules hold: the tier follows from the gates; the score is exactly the weighted average of its dimensions; every quote was really recorded by its evidence; no skill counts on a recruiter's impression; nothing counts on a doubtful or future-dated sentence; nobody blocked or owned by another recruiter is marked "reach out"; nothing is called verified; the list is in its stated order. It is clean on the sample, both overlays, all 102 scenarios and with the lying model on, and a test plants twelve kinds of mistake to show each check catches its own.

## What is general, and what is tied to this sample

General, and tested on synthetic data that is not the sample: adapters and failure handling, identity, precedence, every rule, both scoring stages, events, paging, the checker. No code names a person, job or record.

Tied to this sample, and first to revisit with real data: the text reader's 55 rules and the six meanings it recognises in job notes; the vocabulary for these two divisions; and the weights, which are reasoned defaults, not calibrated. Calibrating them needs outcomes the client has and the sample does not: who was submitted, interviewed and placed.
